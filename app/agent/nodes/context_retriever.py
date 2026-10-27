"""
app/agent/nodes/context_retriever.py
───────────────────────────────────────
Node 2 — Semantic Context Retriever

Retrieves the most relevant table schemas and business metric definitions
for the current user query using vector similarity search over Qdrant.

Two-stage retrieval:
  Stage 1: Broad Qdrant vector search — top K=30 candidates
  Stage 2: Deterministic filtering — top 8–12 most relevant results

Also loads:
  - Approved join paths from the semantic YAML layer
  - Verified few-shot Q→SQL examples matching this query type

Falls back gracefully if Qdrant is not available (dev mode).
"""

import logging
from typing import List

from app.agent.state import (
    FewShotExample,
    QueryAgentState,
    RetrievedTable,
    SemanticDefinition,
)
from app.config import get_settings
from app.semantic.retriever import SemanticRetriever, get_retriever

logger = logging.getLogger(__name__)
settings = get_settings()


def context_retriever_node(state: QueryAgentState) -> QueryAgentState:
    """
    LangGraph Node 2 — Semantic Context Retriever.

    Reads:   state.enriched_query (or raw_query)
    Writes:  state.retrieved_tables, state.semantic_definitions,
             state.approved_joins, state.few_shot_examples
    """
    query = state.get("enriched_query") or state.get("raw_query", "")
    logger.info("[Node 2] Retrieving context for query: %r", query[:100])

    retriever = get_retriever()

    try:
        tables: List[RetrievedTable] = retriever.retrieve_tables(query)
        metrics: List[SemanticDefinition] = retriever.retrieve_metrics(query)
        joins: List[dict] = retriever.get_approved_joins(tables)
        few_shots: List[FewShotExample] = retriever.retrieve_few_shots(query)

        logger.info(
            "[Node 2] Retrieved: tables=%d, metrics=%d, joins=%d, few_shots=%d",
            len(tables), len(metrics), len(joins), len(few_shots),
        )
    except Exception as exc:
        logger.warning(
            "[Node 2] Retriever error (falling back to empty context): %s", exc
        )
        tables, metrics, joins, few_shots = [], [], [], []

    return {
        **state,
        "retrieved_tables": tables,
        "semantic_definitions": metrics,
        "approved_joins": joins,
        "few_shot_examples": few_shots,
        "pipeline_stage": "context_retrieved",
    }
