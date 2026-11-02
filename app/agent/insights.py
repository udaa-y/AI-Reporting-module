"""
app/agent/insights.py
─────────────────────
LLM-powered business insights over a query result set.

  generate_insight()      - narrative analysis shown with every query result
  answer_data_question()  - follow-up Q&A over the result data already retrieved

Both build their prompt from the result's own column names plus an exact,
pre-computed data profile (app/utils/data_profile.py), and automatically back
off to smaller output / input budgets if the API limit is hit.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.utils.data_profile import format_rows, profile_data

logger = logging.getLogger(__name__)
settings = get_settings()

INSIGHT_SYSTEM_PROMPT = """\
You are a senior business analyst writing for management. You receive the user's
question, the column names of the result, the data rows and an exact PRE-COMPUTED
DATA PROFILE (totals, averages, per-entity averages, top groups with % share,
monthly trends). Write a detailed, number-focused business analysis in plain
English. Do not mention SQL, tables or technical terms.

Use these markdown sections (skip any that don't apply):
**Overview** - what the data covers (record count, date range, who/what) and the headline answer with exact figures.
**Key Numbers** - bullets with totals, averages (per record and per customer/patient where available), highest/lowest.
**Breakdown** - bullets ranking the main categories (e.g. by service type, facility, gender, nationality) with values and % share; point out concentration.
**Trend / Comparison** - for dates or periods: period-over-period change (absolute and %), best/worst period, growth or decline.
**Business Takeaways** - 2-3 bullets: what stands out, risks/opportunities, suggested follow-up.

Rules:
- Base every figure on the data or the profile. Never invent numbers. Prefer the profile for totals, averages and shares.
- Interpret columns by their names (e.g. Cost -> revenue/spend, Quantity -> volume, PatientName -> customers).
- Keep each bullet short so the full report fits in the allowed length.
- If data was truncated, say the row-level view is partial but totals cover all rows.
"""

INSIGHT_USER_PROMPT = """\
User question: "{query}"

Result columns: {columns}

Data ({row_count} rows total):
{sample_data}

DATA PROFILE (computed over all rows):
{profile}

Write the business analysis.
"""

QA_SYSTEM_PROMPT = """\
You are a business data analyst answering follow-up questions about a result set
that was already retrieved. You are given the original question, column names,
data rows and an exact pre-computed DATA PROFILE.

Rules:
- Answer ONLY from the provided data and profile. Show the key numbers and, when
  you calculate something (average, share, difference), state the figures used.
- Be concise, numeric and business-oriented; use short bullets when helpful.
- If the answer cannot be derived from these columns/rows, say exactly what is
  missing and suggest what new question to ask the database instead.
- If the rows are truncated, rely on the profile for totals and say when a detail is partial.
"""

QA_USER_PROMPT = """\
Original question: "{query}"

Result columns: {columns}

Data ({row_count} rows total):
{sample_data}

DATA PROFILE (computed over all rows):
{profile}

Follow-up question: "{question}"
"""


def _llm(max_tokens: int) -> ChatOpenAI:
    kwargs = {
        "model": settings.summary_model or settings.llm_intent_model,
        "temperature": 0.2,
        "api_key": settings.openai_api_key,
        "max_tokens": max_tokens,
    }
    if settings.clean_openai_base_url:
        kwargs["base_url"] = settings.clean_openai_base_url
    return ChatOpenAI(**kwargs)


def _budgets(max_rows: int) -> List[Tuple[int, int]]:
    """(output_tokens, input_rows) attempts, largest first."""
    out = settings.summary_max_output_tokens
    attempts = [(out, max_rows), (max(out // 2, 150), min(max_rows, 15)), (150, 5)]
    seen, result = set(), []
    for a in attempts:
        if a not in seen:
            seen.add(a)
            result.append(a)
    return result


def _run(system: str, user_template: str, fmt: Dict[str, Any],
         columns: list, rows: list, max_rows: int) -> Tuple[str, Optional[str]]:
    """Run with progressive back-off. Returns (text, last_error)."""
    last_error = None
    profile = profile_data(columns, rows)
    for out_tokens, in_rows in _budgets(max_rows):
        try:
            messages = [
                SystemMessage(content=system),
                HumanMessage(content=user_template.format(
                    columns=", ".join(str(c) for c in columns),
                    sample_data=format_rows(columns, rows, in_rows),
                    profile=profile,
                    **fmt,
                )),
            ]
            resp = _llm(out_tokens).invoke(messages)
            text = (resp.content or "").strip()
            if text:
                logger.info("[Insights] Generated (max_tokens=%d, rows=%d).", out_tokens, in_rows)
                return text, None
            last_error = "Model returned an empty response."
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                "[Insights] Attempt failed (max_tokens=%d, rows=%d): %s",
                out_tokens, in_rows, last_error[:400],
            )
    return "", last_error


def generate_insight(query: str, columns: list, rows: list, row_count: int) -> Tuple[str, Optional[str]]:
    return _run(
        INSIGHT_SYSTEM_PROMPT, INSIGHT_USER_PROMPT,
        {"query": query, "row_count": row_count},
        columns, rows, settings.summary_max_input_rows,
    )


def answer_data_question(question: str, original_query: str, columns: list,
                         rows: list, row_count: int) -> Tuple[str, Optional[str]]:
    return _run(
        QA_SYSTEM_PROMPT, QA_USER_PROMPT,
        {"query": original_query, "question": question, "row_count": row_count},
        columns, rows, settings.qa_max_input_rows,
    )


def fallback_analysis(columns: list, rows: list, row_count: int, error: Optional[str]) -> str:
    """Used only if every LLM attempt fails. Never raises."""
    why = (error or "unknown error").replace("\n", " ")[:220]
    return (
        f"AI narrative is unavailable right now ({why}).\n\n"
        f"**Computed analysis of {row_count} record(s):**\n" + profile_data(columns, rows)
    )
