"""
app/agent/nodes/intent_analyzer.py
────────────────────────────────────
Node 1 — Intent Analyzer

Classifies the user's query as: clear / ambiguous / irrelevant.
Uses a fast, cheap model (GPT-4o-mini) with Pydantic structured output.

This node runs FIRST and acts as the traffic cop:
  - clear      → proceed to context retrieval (Node 2)
  - ambiguous  → go to clarification (Node 3)
  - irrelevant → terminate with explanation
"""

import logging
from typing import List, Literal, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.agent.prompts.intent import INTENT_SYSTEM_PROMPT, INTENT_USER_PROMPT
from app.agent.state import QueryAgentState
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Pydantic Output Schema ────────────────────────────────────────────────────

class IntentAnalysisResult(BaseModel):
    """Structured output from the intent classification LLM call."""

    status: Literal["clear", "ambiguous", "irrelevant"] = Field(
        description="Classification of the user query intent"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score for the classification (0.0 to 1.0)"
    )
    ambiguity_type: Optional[
        Literal[
            "missing_filter",
            "column_conflict",
            "vague_metric",
            "multi_interpretation",
            "out_of_scope",
        ]
    ] = Field(
        default=None,
        description="Type of ambiguity if status is 'ambiguous'"
    )
    reasoning: str = Field(
        description="Short internal reasoning for the classification"
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description=(
            "The question to ask the user. Required when status is "
            "'ambiguous' or 'irrelevant'. Should be concise and specific."
        ),
    )
    clarification_options: Optional[List[str]] = Field(
        default=None,
        description=(
            "2–4 multiple-choice options for the clarification question "
            "when applicable. Null for open-ended questions."
        ),
    )


# ── LLM Setup ────────────────────────────────────────────────────────────────

def _build_intent_llm() -> ChatOpenAI:
    """Return a structured-output LLM for intent classification."""
    kwargs = {
        "model": settings.llm_intent_model,
        "temperature": 0.0,
        "api_key": settings.openai_api_key,
        "max_tokens": 500,
    }
    if settings.clean_openai_base_url:
        kwargs["base_url"] = settings.clean_openai_base_url
    llm = ChatOpenAI(**kwargs)
    return llm.with_structured_output(IntentAnalysisResult)


_intent_llm = None


def get_intent_llm():
    global _intent_llm
    if _intent_llm is None:
        _intent_llm = _build_intent_llm()
    return _intent_llm


# ── Helper: Format available tables/metrics for the prompt ──────────────────

def _format_table_list(tables: list) -> str:
    if not tables:
        return "No tables loaded yet. Treat all queries as potentially valid."
    lines = []
    for t in tables:
        if isinstance(t, dict):
            name = t.get("table_name") or t.get("table", "")
            desc = (t.get("description") or "").strip()
            notes = (t.get("business_notes") or "").strip()
            details = []
            if desc and desc != f"Table {name} in dbo schema.":
                details.append(desc)
            if notes and notes != "Add domain-specific notes and filter rules here.":
                details.append(f"Notes: {notes}")

            # Highlight key columns or columns with custom descriptions
            cols = []
            for col in t.get("columns", []):
                cdesc = (col.get("description") or "").strip()
                cname = col.get("name", "")
                if cdesc and cdesc != f"Column {cname}":
                    cols.append(f"{cname} ({cdesc})")
                elif cname.lower() in [
                    "bp", "date", "dob", "firstname", "lastname",
                    "patientid", "doctorid", "visitdate", "patientappointmentid"
                ]:
                    cols.append(cname)
            if cols:
                details.append(f"Key columns: {', '.join(cols[:8])}")

            detail_str = f" — {'; '.join(details)}" if details else ""
            lines.append(f"  - {name}{detail_str}")
        else:
            lines.append(f"  - {t}")
    return "\n".join(lines)


def _format_metric_list(metrics: list) -> str:
    if not metrics:
        return "No metrics defined yet."
    return "\n".join(f"  - {m['name']}: {m['definition']}" for m in metrics)


# ── Node Function ─────────────────────────────────────────────────────────────

def intent_analyzer_node(state: QueryAgentState) -> QueryAgentState:
    """
    LangGraph Node 1 — Intent Analyzer.

    Reads:   state.raw_query
    Writes:  state.intent_status, state.ambiguity_type,
             state.intent_confidence, state.clarification_question,
             state.clarification_options, state.pipeline_stage
    """
    logger.info("[Node 1] Intent analysis — query: %r", state["raw_query"])

    # Build context for the prompt (use loaded whitelisted tables with descriptions and notes)
    tables_for_prompt = state.get("retrieved_tables", [])
    if not tables_for_prompt:
        from app.semantic.retriever import get_retriever
        tables_for_prompt = get_retriever()._loader.load_all_tables()

    table_list_str = _format_table_list(tables_for_prompt)
    metric_list_str = _format_metric_list(
        state.get("semantic_definitions", [])
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", INTENT_SYSTEM_PROMPT),
        ("human", INTENT_USER_PROMPT),
    ])

    chain = prompt | get_intent_llm()

    result: IntentAnalysisResult = chain.invoke({
        "table_list": table_list_str,
        "metric_list": metric_list_str,
        "user_query": state["raw_query"],
    })

    logger.info(
        "[Node 1] Result: status=%s, confidence=%.2f, ambiguity_type=%s",
        result.status, result.confidence, result.ambiguity_type,
    )

    return {
        **state,
        "intent_status": result.status,
        "intent_confidence": result.confidence,
        "ambiguity_type": result.ambiguity_type,
        "clarification_question": result.clarification_question,
        "clarification_options": result.clarification_options,
        "pipeline_stage": "intent_analyzed",
        # Set enriched_query to raw_query initially (updated after clarification)
        "enriched_query": state.get("enriched_query") or state["raw_query"],
    }
