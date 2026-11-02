"""
app/agent/nodes/executor.py
─────────────────────────────
Node 6 — SQL Executor

Executes the validated T-SQL query against SQL Server using the
connection pool. Runs synchronously inside run_in_threadpool() to
avoid blocking FastAPI's async event loop.

On success: populates state.execution_result (+ state.sql_review from Vanna)
On failure: sets state.last_db_error so Node 7 (corrector) can fix it —
            also used when the Vanna review confidently rejects the SQL
"""

import asyncio
import logging
import time
from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.agent.state import ExecutionResult, QueryAgentState
from app.config import get_settings
from app.db.sqlserver import execute_query_sync, get_db_session
from app.vanna_ai.reviewer import (
    collect_review,
    feedback_for_generator,
    should_retry,
    start_review,
)

logger = logging.getLogger(__name__)
settings = get_settings()


async def executor_node_async(state: QueryAgentState) -> QueryAgentState:
    """
    Async wrapper for the SQL executor.

    Uses run_in_threadpool() to execute the blocking SQL Server call
    without starving the FastAPI async event loop.
    """
    sql = state.get("generated_sql") or (
        state.get("validation_result", {}) or {}
    ).get("sanitized_sql")

    if not sql:
        return {
            **state,
            "last_db_error": "No SQL available to execute.",
            "pipeline_stage": "execution_failed",
        }

    logger.info("[Node 6] Executing SQL against SQL Server...")
    start_time = time.perf_counter()

    try:
        result = await run_in_threadpool(_execute_sync, sql)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            "[Node 6] Execution SUCCESS — rows=%d, time=%.0fms",
            result["row_count"], elapsed_ms,
        )

        execution_result: ExecutionResult = {
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
            "execution_time_ms": round(elapsed_ms, 1),
        }

        return {
            **state,
            "execution_result": execution_result,
            "last_db_error": None,
            "pipeline_stage": "executed",
        }

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        error_str = str(exc)

        logger.warning(
            "[Node 6] Execution FAILED (%.0fms) — %s", elapsed_ms, error_str[:300]
        )

        return {
            **state,
            "execution_result": None,
            "last_db_error": error_str,
            "pipeline_stage": "execution_failed",
        }


def executor_node(state: QueryAgentState) -> QueryAgentState:
    """
    Synchronous entry point for LangGraph (which calls nodes synchronously).

    The Vanna SQL review starts first and runs in parallel with the database
    query, so it only adds latency when it is slower than the query itself.
    """
    question = state.get("enriched_query") or state.get("raw_query", "")
    review_future = start_review(question, state.get("generated_sql") or "")
    result = _execute_node(state)
    return _apply_review(result, review_future)


def _apply_review(result: QueryAgentState, review_future) -> QueryAgentState:
    """Attach the Vanna review; send confidently-wrong SQL back for correction."""
    if result.get("last_db_error"):
        if review_future is not None:
            review_future.cancel()      # DB error already drives a correction
        return {**result, "sql_review": None}

    review = collect_review(review_future)
    result = {**result, "sql_review": review}
    if review:
        logger.info(
            "[Node 6] Vanna review: %s (confidence %.2f)",
            review["verdict"], review.get("confidence", 0),
        )

    if should_retry(review) and result.get("sql_attempt_count", 0) < settings.max_sql_retries:
        logger.info("[Node 6] Review rejected the SQL — routing back for correction.")
        result.update(
            execution_result=None,
            last_db_error=feedback_for_generator(review),
            pipeline_stage="review_rejected",
        )
    return result


def _execute_node(state: QueryAgentState) -> QueryAgentState:
    """Run the async executor from LangGraph's synchronous context."""
    try:
        loop = asyncio.get_running_loop()
        # If there's a running loop (e.g., in a test), create a task
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, executor_node_async(state))
            return future.result()
    except RuntimeError:
        # No running event loop — safe to call asyncio.run()
        return asyncio.run(executor_node_async(state))


def _execute_sync(sql: str) -> dict[str, Any]:
    """Run query in a DB session. Called via run_in_threadpool."""
    with get_db_session() as session:
        return execute_query_sync(sql, session)
