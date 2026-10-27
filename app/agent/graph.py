"""
app/agent/graph.py
───────────────────
LangGraph Pipeline — The Main Agent Graph

Wires all 8 nodes into a stateful directed graph with conditional routing.
This is the orchestration engine for the entire NL-to-SQL pipeline.

Node execution order (happy path):
  intent_analyzer → context_retriever → sql_generator →
  validator → executor → formatter → END

Branching:
  intent_analyzer → clarifier (if ambiguous) → context_retriever
  intent_analyzer → error_formatter (if irrelevant) → END
  validator → sql_generator (if validation failed, retry)
  executor → sql_generator (if DB error, self-correction, retry)
  executor → error_formatter (if max retries exceeded) → END
"""

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from app.agent.nodes.clarifier import clarifier_node
from app.agent.nodes.context_retriever import context_retriever_node
from app.agent.nodes.executor import executor_node
from app.agent.nodes.formatter import error_formatter_node, formatter_node
from app.agent.nodes.intent_analyzer import intent_analyzer_node
from app.agent.nodes.sql_generator import sql_generator_node
from app.agent.nodes.validator import validator_node
from app.agent.state import QueryAgentState
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Routing Functions ─────────────────────────────────────────────────────────

def route_after_intent(state: QueryAgentState) -> str:
    """Route based on intent classification result."""
    status = state.get("intent_status", "unknown")
    if status == "clear":
        return "context_retriever"
    elif status == "ambiguous":
        return "clarifier"
    else:
        # irrelevant or unknown
        return "error_formatter"


def route_after_validation(state: QueryAgentState) -> str:
    """Route based on SQL validation result."""
    validation = state.get("validation_result") or {}
    attempt = state.get("sql_attempt_count", 0)

    if validation.get("passed"):
        return "executor"
    elif attempt < settings.max_sql_retries:
        logger.info(
            "[Graph] Validation failed — retrying SQL generation (attempt %d/%d)",
            attempt, settings.max_sql_retries,
        )
        return "sql_generator"
    else:
        logger.warning("[Graph] Max retries reached after validation failures.")
        return "error_formatter"


def route_after_execution(state: QueryAgentState) -> str:
    """Route based on SQL execution result."""
    last_error = state.get("last_db_error")
    attempt = state.get("sql_attempt_count", 0)

    if not last_error:
        return "formatter"
    elif attempt < settings.max_sql_retries:
        logger.info(
            "[Graph] DB error — triggering self-correction (attempt %d/%d)",
            attempt, settings.max_sql_retries,
        )
        return "sql_generator"   # Node 4 detects retry via last_db_error
    else:
        logger.warning("[Graph] Max retries reached after execution failures.")
        return "error_formatter"


# ── Graph Builder ─────────────────────────────────────────────────────────────

def build_graph(checkpointer=None):
    """
    Construct and compile the LangGraph StateGraph.

    Args:
        checkpointer: LangGraph checkpointer for state persistence.
                      Defaults to MemorySaver (in-process, dev-friendly).
                      Use PostgresSaver for production multi-session support.

    Returns:
        Compiled LangGraph app ready to invoke.
    """
    workflow = StateGraph(QueryAgentState)

    # ── Register Nodes ────────────────────────────────────────────────────────
    workflow.add_node("intent_analyzer", intent_analyzer_node)
    workflow.add_node("context_retriever", context_retriever_node)
    workflow.add_node("clarifier", clarifier_node)
    workflow.add_node("sql_generator", sql_generator_node)
    workflow.add_node("validator", validator_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("formatter", formatter_node)
    workflow.add_node("error_formatter", error_formatter_node)

    # ── Entry Point ───────────────────────────────────────────────────────────
    workflow.set_entry_point("intent_analyzer")

    # ── Edges ─────────────────────────────────────────────────────────────────

    # Intent → context retriever / clarifier / error
    workflow.add_conditional_edges(
        "intent_analyzer",
        route_after_intent,
        {
            "context_retriever": "context_retriever",
            "clarifier": "clarifier",
            "error_formatter": "error_formatter",
        },
    )

    # Clarifier resumes after user reply → context retriever
    workflow.add_edge("clarifier", "context_retriever")

    # Context retriever → SQL generator
    workflow.add_edge("context_retriever", "sql_generator")

    # SQL generator → validator (always)
    workflow.add_edge("sql_generator", "validator")

    # Validator → executor / retry sql_generator / error
    workflow.add_conditional_edges(
        "validator",
        route_after_validation,
        {
            "executor": "executor",
            "sql_generator": "sql_generator",
            "error_formatter": "error_formatter",
        },
    )

    # Executor → formatter / retry sql_generator / error
    workflow.add_conditional_edges(
        "executor",
        route_after_execution,
        {
            "formatter": "formatter",
            "sql_generator": "sql_generator",
            "error_formatter": "error_formatter",
        },
    )

    # Terminal nodes → END
    workflow.add_edge("formatter", END)
    workflow.add_edge("error_formatter", END)

    # ── Compile ───────────────────────────────────────────────────────────────
    cp = checkpointer or MemorySaver()
    app = workflow.compile(checkpointer=cp)

    logger.info("[Graph] LangGraph pipeline compiled successfully.")
    return app


# ── Singleton graph instance ──────────────────────────────────────────────────

_graph = None


def get_graph():
    """Return the singleton compiled graph. Build once, reuse forever."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
