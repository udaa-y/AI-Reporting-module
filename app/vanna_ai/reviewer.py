"""
app/vanna_ai/reviewer.py
─────────────────────────
Vanna SQL reviewer — a second, independent check that the generated SQL
actually answers the user's question (the sqlglot validator only proves it
is *safe*, not that it is *right*).

The review uses Vanna's trained knowledge (DDL, relationships, business
rules, verified examples) and runs IN PARALLEL with query execution, so on
the happy path it adds no latency. If it is confident the SQL is wrong, the
graph sends its feedback to the SQL generator for a corrected attempt.
"""

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Optional

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="vanna-review")

REVIEW_SYSTEM_PROMPT = """\
You are a senior Microsoft SQL Server analyst reviewing a T-SQL query written by
another AI for a clinic's reporting tool. Decide whether the query correctly
answers the user's question given the database knowledge below.

Mark it "incorrect" ONLY for problems that make the answer wrong, for example:
- a table or column that does not exist, or the wrong column for the concept asked
- a join that is not one of the approved relationships, or a wrong join key
- missing/incorrect filters (date range, status, person, department ...)
- wrong aggregation or grouping; double counting caused by joining one-to-many
  tables before aggregating (e.g. COUNT(*) after joining prescriptions)
- integer division where a decimal average/percentage was asked for
- the query answers a different question than the one asked
Style, aliases, formatting, TOP N limits and harmless extra columns are NOT errors.
Use "uncertain" when the business rules needed to judge are missing.

{context}

Respond with JSON only:
{{"verdict": "correct" | "incorrect" | "uncertain",
  "confidence": <0.0-1.0>,
  "issues": ["<short concrete issue>", ...],
  "explanation": "<one sentence for the end user>",
  "suggested_sql": "<corrected T-SQL or null>"}}
"""

REVIEW_USER_PROMPT = """\
User question: "{question}"

Generated T-SQL:
{sql}
"""


def review_sql(question: str, sql: str) -> dict:
    """Synchronously review one SQL statement. Never raises."""
    from app.vanna_ai.engine import context_for, get_vanna_or_none

    vn = get_vanna_or_none()
    if vn is None:
        return _skipped("Vanna is disabled or unavailable.")
    try:
        raw = vn.submit_prompt(
            [
                vn.system_message(REVIEW_SYSTEM_PROMPT.format(context=context_for(question))),
                vn.user_message(REVIEW_USER_PROMPT.format(question=question, sql=sql)),
            ],
            max_tokens=500,
            json=True,
            timeout=settings.vanna_review_timeout_seconds,
        )
        return _parse(raw)
    except Exception as exc:
        logger.warning("[Vanna Review] Failed: %s", str(exc)[:300])
        return _skipped(f"Review unavailable: {str(exc)[:120]}")


def start_review(question: str, sql: str) -> Optional[Future]:
    """Kick off a review in the background; returns None when reviews are off."""
    if not (settings.vanna_enabled and settings.vanna_review_enabled and sql):
        return None
    return _pool.submit(review_sql, question, sql)


def collect_review(future: Optional[Future]) -> Optional[dict]:
    """Wait (bounded) for a background review. Returns None when none was started."""
    if future is None:
        return None
    try:
        return future.result(timeout=settings.vanna_review_timeout_seconds)
    except Exception:
        future.cancel()
        return _skipped("Review timed out; results shown without AI verification.")


def should_retry(review: Optional[dict]) -> bool:
    return bool(
        review
        and review.get("verdict") == "incorrect"
        and review.get("confidence", 0) >= settings.vanna_review_min_confidence
    )


def feedback_for_generator(review: dict) -> str:
    """Turn a rejection into an error message the SQL correction prompt can act on."""
    lines = ["The query ran, but a SQL review found it does NOT correctly answer the question:"]
    lines += [f"- {i}" for i in review.get("issues", [])] or [f"- {review.get('explanation', '')}"]
    if review.get("suggested_sql"):
        lines.append(f"Reviewer's suggested query (verify before using):\n{review['suggested_sql']}")
    return "\n".join(lines)


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    data = json.loads(text)
    verdict = str(data.get("verdict", "uncertain")).lower()
    if verdict not in ("correct", "incorrect", "uncertain"):
        verdict = "uncertain"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        confidence = 0.0
    issues = data.get("issues") or []
    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "issues": [str(i) for i in issues if i][:6] if isinstance(issues, list) else [str(issues)],
        "explanation": str(data.get("explanation") or ""),
        "suggested_sql": data.get("suggested_sql") or None,
    }


def _skipped(reason: str) -> dict:
    return {"verdict": "skipped", "confidence": 0.0, "issues": [],
            "explanation": reason, "suggested_sql": None}
