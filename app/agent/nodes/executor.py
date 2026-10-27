"""
app/agent/nodes/executor.py
─────────────────────────────
Node 6 — SQL Executor

Executes the validated T-SQL query against SQL Server using the
connection pool. Runs synchronously inside run_in_threadpool() to
avoid blocking FastAPI's async event loop.

On success: populates state.execution_result
On failure: sets state.last_db_error so Node 7 (corrector) can fix it
"""

import asyncio
import logging
import time
from typing import Any

from fastapi.concurrency import run_in_threadpool

from app.agent.state import ExecutionResult, QueryAgentState
from app.config import get_settings
from app.db.sqlserver import execute_query_sync, get_db_session

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
    Wraps the async executor using asyncio.run() or the existing event loop.
    """
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
