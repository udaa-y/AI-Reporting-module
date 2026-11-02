"""
app/agent/nodes/formatter.py
──────────────────────────────
Node 8 — Response Formatter

Assembles the final structured response payload and generates a business
insight analysis of the results (see app/agent/insights.py).

This is the last node in the happy path. The response is designed to
be transparent — it always includes the generated SQL so the user
(or a downstream client) can verify what was run.
"""

import logging

from app.agent.insights import fallback_analysis, generate_insight
from app.agent.state import QueryAgentState
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def formatter_node(state: QueryAgentState) -> QueryAgentState:
    """
    LangGraph Node 8 — Response Formatter.

    Reads:   state.execution_result, state.generated_sql,
             state.enriched_query, state.clarifications_made
    Writes:  state.nl_summary, state.final_response, state.pipeline_stage
    """
    exec_result = state.get("execution_result") or {}
    columns = exec_result.get("columns", [])
    rows = exec_result.get("rows", [])
    row_count = exec_result.get("row_count", 0)
    exec_time = exec_result.get("execution_time_ms", 0)

    generated_sql = state.get("generated_sql", "")
    clarifications = state.get("clarifications_made", [])

    logger.info("[Node 8] Formatting response — row_count=%d", row_count)

    # ── Generate business insight ──────────────────────────────────────────────
    summary_error = None
    if rows:
        try:
            nl_summary, summary_error = generate_insight(
                state.get("raw_query", ""), columns, rows, row_count
            )
        except Exception as exc:  # never let insight generation break the response
            nl_summary, summary_error = "", str(exc)
        if not nl_summary:
            logger.warning("[Node 8] Insight generation failed: %s", summary_error)
            try:
                nl_summary = fallback_analysis(columns, rows, row_count, summary_error)
            except Exception:
                nl_summary = f"Query returned {row_count} row(s)."
    else:
        nl_summary = "The query returned no results."

    # ── Assemble Final Response ───────────────────────────────────────────────
    final_response = {
        "status": "success",
        "user_query": state.get("raw_query", ""),
        "generated_sql": generated_sql,
        "clarifications_made": [
            {"question": c["question"], "answer": c["answer"]}
            for c in clarifications
        ],
        "columns": columns,
        "data": rows,
        "row_count": row_count,
        "summary": nl_summary,
        "summary_error": summary_error,
        "execution_time_ms": exec_time,
        "sql_attempts": state.get("sql_attempt_count", 1),
        "data_freshness": "live",
    }

    return {
        **state,
        "nl_summary": nl_summary,
        "final_response": final_response,
        "pipeline_stage": "complete",
    }


def error_formatter_node(state: QueryAgentState) -> QueryAgentState:
    """
    Formats a user-friendly error response when the pipeline fails
    (e.g., max retries exceeded, irrelevant query, validation failure).
    """
    error_msg = state.get("error_message") or "The query could not be processed."
    validation_errors = (state.get("validation_result") or {}).get("errors", [])
    last_error = state.get("last_db_error", "")

    # Build a user-friendly explanation
    if state.get("intent_status") == "irrelevant":
        user_message = (
            "This query doesn't appear to be related to your business data. "
            "Please ask a data analytics question."
        )
    elif validation_errors:
        user_message = f"The query could not be validated: {validation_errors[0]}"
    elif last_error:
        attempt_count = state.get("sql_attempt_count", 1)
        user_message = (
            f"The query could not be resolved automatically after {attempt_count} attempts. "
            f"Database/datatype error: {last_error[:250]}. "
            "Human intervention or manual review is required."
        )
    else:
        user_message = error_msg

    final_response = {
        "status": "error",
        "user_query": state.get("raw_query", ""),
        "message": user_message,
        "generated_sql": state.get("generated_sql"),
        "attempts_made": state.get("sql_attempt_count", 1),
        "requires_human_intervention": bool(last_error or validation_errors),
        "last_error": last_error or (validation_errors[0] if validation_errors else None),
        "clarifications_made": [],
        "data": [],
        "row_count": 0,
    }

    return {
        **state,
        "final_response": final_response,
        "pipeline_stage": "error",
    }
