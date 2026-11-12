"""
app/vanna_ai/analyst.py
────────────────────────
Vanna AI analyst — turns a query result into a clinic-focused analytical
report plus follow-up questions.

  1. app/analytics/engine.analyze()  exact statistics (ms, no LLM)
  2. Vanna context                    schema, relationships, business rules
  3. Two LLM calls IN PARALLEL        narrative report (sees the result rows)
                                      + follow-up questions and a chart choice

Results are cached per (question, SQL, data), so reopening a result or
asking twice costs nothing.
"""

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from app.analytics.engine import analyze
from app.config import get_settings
from app.utils.data_profile import classify_columns, format_rows, profile_data

logger = logging.getLogger(__name__)
settings = get_settings()

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="vanna-analyst")
_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_SIZE = 200

ANALYSIS_SYSTEM_PROMPT = """\
You are a senior healthcare operations analyst for an outpatient clinic,
writing for clinic management. You receive a user's question, the result of a
database query, and PRE-COMPUTED FINDINGS (exact statistics: KPIs, trends,
period comparisons, drivers, anomalies, forecast, patient metrics).

Write a concise, insight-rich report in markdown. Use only the sections that the
findings support, in this order:
**Answer** - one or two sentences that directly answer the question with exact figures.
**Key Metrics** - KPI bullets (totals, averages, per-patient figures).
**Trends & Comparisons** - direction, period-over-period, year-over-year / YTD, best and worst periods.
**What Drove the Change** - the main contributors from the driver findings (diagnostic).
**Unusual Activity** - anomalies and why they may matter operationally.
**Patient Insights** - visit frequency, repeat/returning patients, demographics, concentration.
**Outlook** - the forecast with its range, clearly labelled as a statistical projection.
**Recommendations** - 2-3 concrete, operational next steps (staffing, scheduling,
  service mix, follow-up care, data quality) tied to specific findings.

Rules:
- Use ONLY numbers from the findings, profile or rows. Never invent or recompute figures.
- Distinguish correlation from causation: say "coincided with" / "was driven mostly by",
  not "caused", unless the data proves it. Suggest how to confirm a hypothesis.
- If something important cannot be concluded from this result (e.g. no dates, so no
  trend), say what extra question would answer it.
- If a period was excluded as still in progress, mention it once.
- No SQL, table or column jargon - use plain clinic language. Be specific and brief.

DATABASE KNOWLEDGE (for interpreting codes, statuses and relationships):
{context}
"""

ANALYSIS_USER_PROMPT = """\
Question: "{question}"

Result: {row_count} rows; columns: {columns}

PRE-COMPUTED FINDINGS:
{findings}

DATA PROFILE:
{profile}
{sample}
Write the report.
"""

FOLLOWUP_SYSTEM_PROMPT = """\
You suggest follow-up questions for a clinic analytics assistant that turns
questions into SQL over the database described below. Every question MUST be
answerable from these tables and relationships.

{context}
"""

FOLLOWUP_USER_PROMPT = """\
The user asked: "{question}"

Result: {row_count} rows. Columns (name: kind):
{column_kinds}

Data:
{sample}

Key findings: {findings}

1. Suggest 4 follow-up questions that dig deeper - e.g. break down a driver,
   compare periods, investigate an anomaly, or look at a patient segment.
2. Choose the ONE chart that best explains this result:
   - "line" or "area": values over ordered time (dates, months, years)
   - "bar": a few categories, or periods compared side by side
   - "horizontal_bar": ranking categories (top doctors, services...), long labels
   - "stacked_bar": a category broken down by a second category ("series")
   - "grouped_bar": side-by-side bars — a "series" column (e.g. Branch) or 2-3 "y" measures
   - "line" with a "series" column: one line per branch/category over time
   - "donut": share of a total, only with 6 or fewer categories
   - "stat": a single row of 1-4 numbers
   - "table": when no chart helps
   Use ONLY the exact column names above. "y" must be numeric columns with similar
   scales (never mix e.g. counts and money on one axis).

Return JSON only:
{{"questions": ["...", "...", "...", "..."],
  "chart": {{"type": "...", "title": "<short title>", "x": "<column or null>",
            "y": ["<numeric column>"], "series": "<column or null>",
            "sort": "desc" | "asc" | "none", "limit": <max categories, e.g. 12>}}}}
"""

CHART_TYPES = {"line", "area", "bar", "horizontal_bar", "grouped_bar", "stacked_bar", "donut", "stat", "table"}


def _cache_key(question: str, sql: str, rows: List[dict]) -> str:
    h = hashlib.sha256()
    h.update(question.encode())
    h.update((sql or "").encode())
    h.update(json.dumps(rows, sort_keys=True, default=str).encode())
    return h.hexdigest()


def generate_analysis(question: str, sql: str, columns: List[str],
                      rows: List[Dict[str, Any]], tables: List[str] = None) -> Dict[str, Any]:
    """Full analysis for one result set. Never raises; degrades to computed findings."""
    key = _cache_key(question, sql, rows)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return {**_cache[key], "cached": True}

    start = time.perf_counter()
    stats = analyze(columns, rows)
    findings = "\n".join(stats["facts"]) or "No additional statistics apply to this result."
    result: Dict[str, Any] = {
        "analysis": "",
        "analysis_types": stats["analysis_types"],
        "highlights": stats["highlights"],
        "followup_questions": [],
        "chart": None,
        "engine": "vanna",
        "error": None,
    }

    from app.vanna_ai.engine import context_for, get_vanna_or_none
    vn = get_vanna_or_none()
    if vn is None:
        result.update(engine="computed", analysis=_fallback(findings, rows, columns),
                      error="Vanna is disabled or unavailable.")
        return result

    try:
        context = context_for(question, tables)
    except Exception as exc:
        logger.warning("[Vanna Analyst] Context retrieval failed: %s", exc)
        context = ""

    narrative = _pool.submit(_narrative, vn, question, columns, rows, findings, context)
    followups = _pool.submit(_followups_and_chart, vn, question, columns, rows, stats["facts"], context)

    try:
        result["analysis"] = narrative.result(timeout=60)
    except Exception as exc:
        logger.warning("[Vanna Analyst] Narrative failed: %s", str(exc)[:300])
        result.update(engine="computed", analysis=_fallback(findings, rows, columns),
                      error=f"AI narrative unavailable: {str(exc)[:160]}")
    try:
        result["followup_questions"], result["chart"] = followups.result(timeout=30)
    except Exception as exc:
        logger.warning("[Vanna Analyst] Follow-ups / chart choice failed: %s", str(exc)[:200])

    result["processing_time_ms"] = round((time.perf_counter() - start) * 1000)
    if not result["error"]:
        with _cache_lock:
            _cache[key] = result
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    return {**result, "cached": False}


def _narrative(vn, question, columns, rows, findings, context) -> str:
    n = settings.vanna_analysis_sample_rows
    sample = f"\nRESULT DATA:\n{format_rows(columns, rows, n)}\n" if n > 0 else ""
    text = vn.submit_prompt(
        [
            vn.system_message(ANALYSIS_SYSTEM_PROMPT.format(context=context or "(none)")),
            vn.user_message(ANALYSIS_USER_PROMPT.format(
                question=question, row_count=len(rows), columns=", ".join(map(str, columns)),
                findings=findings, profile=profile_data(columns, rows), sample=sample,
            )),
        ],
        model=vn.fast_model,
        temperature=0.2,
        max_tokens=settings.vanna_analysis_max_tokens,
        timeout=60,
    )
    if not text:
        raise ValueError("empty response")
    return text


def _column_kinds(columns, rows) -> Dict[str, str]:
    kinds = classify_columns(columns, rows)
    out = {}
    for c in columns:
        if c in kinds["measures"] or c in kinds["ids"]:
            out[c] = "number" if c in kinds["measures"] else "id"
        elif c in kinds["dates"]:
            out[c] = "date"
        elif c in kinds["cats"]:
            out[c] = "category"
        else:
            out[c] = "text"
    return out


def _followups_and_chart(vn, question, columns, rows, facts, context):
    """Follow-up questions + the chart the model thinks explains this data best."""
    kinds = _column_kinds(columns, rows)
    raw = vn.submit_prompt(
        [
            vn.system_message(FOLLOWUP_SYSTEM_PROMPT.format(context=context or "(none)")),
            vn.user_message(FOLLOWUP_USER_PROMPT.format(
                question=question,
                row_count=len(rows),
                column_kinds="\n".join(f"- {c}: {k}" for c, k in kinds.items()),
                sample=format_rows(columns, rows, 20),
                findings=" | ".join(facts[:8]) or "none",
            )),
        ],
        model=vn.fast_model,
        temperature=0.3,
        max_tokens=450,
        json=True,
        timeout=30,
    )
    data = json.loads(raw)
    questions = data.get("questions", []) if isinstance(data, dict) else data
    questions = [str(q).strip() for q in questions if str(q).strip()][:5]
    chart = validate_chart(data.get("chart") if isinstance(data, dict) else None, columns, rows, kinds)
    return questions, chart


def validate_chart(spec, columns, rows, kinds=None):
    """Keep the model's chart choice only if it fits the real columns; else None."""
    if not isinstance(spec, dict) or spec.get("type") not in CHART_TYPES or not rows:
        return None
    kinds = kinds or _column_kinds(columns, rows)
    numeric = [c for c, k in kinds.items() if k == "number"]
    kind = spec["type"]
    x = spec.get("x") if spec.get("x") in columns else None
    y = [c for c in (spec.get("y") or []) if c in numeric][:4]
    series = spec.get("series") if spec.get("series") in columns and spec.get("series") != x else None
    try:
        limit = max(3, min(int(spec.get("limit") or 12), 50))
    except (TypeError, ValueError):
        limit = 12
    sort = spec.get("sort") if spec.get("sort") in ("asc", "desc", "none") else "none"

    if kind == "table":
        return {"type": "table", "title": str(spec.get("title") or "")[:120]}
    if not y:
        return None
    if kind == "stat":
        if len(rows) != 1:
            return None
    elif not x:
        return None
    if kind == "stacked_bar":
        if not series:
            return None
        y = y[:1]
    elif kind == "grouped_bar":
        if series:
            y = y[:1]
        elif len(y) < 2:
            kind = "bar"
    elif kind in ("line", "area") and series:
        y = y[:1]
    else:
        series = None
    if kind == "donut":
        y = y[:1]
        if len({str(r.get(x)) for r in rows}) > 8:
            kind = "horizontal_bar"         # too many slices to read
    if kind in ("line", "area"):
        sort = "none"
        y = y[:3]
    if kind in ("grouped_bar", "stacked_bar") and len({str(r.get(series)) for r in rows} if series else ()) > 8:
        kind = "stacked_bar"                # too many groups to sit side by side    
    return {"type": kind, "title": str(spec.get("title") or "")[:120], "x": x, "y": y,
            "series": series, "sort": sort, "limit": limit}


def _fallback(findings: str, rows: list, columns: list) -> str:
    return "**Computed analysis**\n" + findings + "\n\n" + profile_data(columns, rows)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
