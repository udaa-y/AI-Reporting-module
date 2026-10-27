"""
app/agent/nodes/sql_generator.py
──────────────────────────────────
Node 4 — SQL Generator

Translates the enriched user query (with full semantic context) into
a T-SQL SELECT statement using a frontier LLM (GPT-4o / Claude 3.5).

This node builds a rich, context-grounded prompt from:
  - Retrieved table schemas (from Node 2)
  - YAML semantic definitions (business metrics)
  - Approved join paths
  - Few-shot verified examples
  - The enriched query (raw + clarifications)

On retry (self-correction flow), the previous error is also included.
"""

import logging
from typing import Optional

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from app.agent.prompts.sql_gen import (
    SQL_CORRECTION_SYSTEM_PROMPT,
    SQL_CORRECTION_USER_PROMPT,
    SQL_GENERATION_SYSTEM_PROMPT,
    SQL_GENERATION_USER_PROMPT,
)
from app.agent.state import QueryAgentState
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── LLM Setup ─────────────────────────────────────────────────────────────────

def _build_sql_llm() -> ChatOpenAI:
    kwargs = {
        "model": settings.llm_sql_model,
        "temperature": 0.0,
        "api_key": settings.openai_api_key,
        "max_tokens": 500,
    }
    if settings.clean_openai_base_url:
        kwargs["base_url"] = settings.clean_openai_base_url
    return ChatOpenAI(**kwargs)


_sql_llm = None


def get_sql_llm() -> ChatOpenAI:
    global _sql_llm
    if _sql_llm is None:
        _sql_llm = _build_sql_llm()
    return _sql_llm


# ── Context Formatters ────────────────────────────────────────────────────────

def _format_schema_context(tables: list) -> str:
    """Format retrieved tables+columns into a compact prompt-ready string."""
    if not tables:
        return "No schema context available."

    lines = []
    for t in tables:
        tname = t.get("table_name") or t.get("table", "")
        lines.append(f"\n[Table: {tname} ({t.get('schema_name', 'dbo')})]")
        if t.get("description"):
            lines.append(f"  Description: {t.get('description')}")
        cols_parts = []
        for col in t.get("columns", []):
            cname = col.get("name") or col.get("COLUMN_NAME", "")
            ctype = col.get("type") or col.get("DATA_TYPE", "")
            desc = f"/* {col['description']} */" if col.get("description") else ""
            cols_parts.append(f"{cname} {ctype} {desc}".strip())
        lines.append(f"  Columns: {', '.join(cols_parts)}")
    return "\n".join(lines)


def _format_semantic_definitions(definitions: list) -> str:
    """Format YAML semantic definitions for the prompt."""
    if not definitions:
        return "No specific business metric definitions loaded."

    lines = []
    for d in definitions:
        lines.append(f"\n[{d['name']}]")
        lines.append(f"  Also known as: {', '.join(d.get('aliases', []))}")
        lines.append(f"  Definition: {d['definition']}")
        if d.get("calculation"):
            lines.append(f"  Calculation: {d['calculation']}")
        if d.get("filters"):
            lines.append(f"  Required filters: {'; '.join(d['filters'])}")
        if d.get("table_dependencies"):
            lines.append(f"  Tables: {', '.join(d['table_dependencies'])}")
    return "\n".join(lines)


def _format_approved_joins(joins: list) -> str:
    """Format approved join paths for the prompt."""
    if not joins:
        return "No pre-approved joins defined. Use standard INNER JOIN logic."

    lines = []
    for j in joins:
        lines.append(
            f"  {j.get('from_table')} → {j.get('to_table')}: "
            f"{j.get('join_type', 'INNER JOIN')} ON {j.get('condition')}"
        )
    return "\n".join(lines)


def _format_few_shots(examples: list) -> str:
    """Format verified Q→SQL pairs as few-shot examples."""
    if not examples:
        return "No verified examples available."

    lines = []
    for i, ex in enumerate(examples[:3], 1):  # Cap at 3 examples
        lines.append(f"\nExample {i}:")
        lines.append(f"  Q: {ex['question']}")
        lines.append(f"  SQL: {ex['sql']}")
    return "\n".join(lines)


# ── Node Function ─────────────────────────────────────────────────────────────

def sql_generator_node(state: QueryAgentState) -> QueryAgentState:
    """
    LangGraph Node 4 — SQL Generator.

    Generates T-SQL from the enriched query + full semantic context.
    On retry (attempt_count > 1), uses the correction prompt.
    """
    attempt = state.get("sql_attempt_count", 0) + 1
    enriched_query = state.get("enriched_query") or state["raw_query"]
    last_error: Optional[str] = state.get("last_db_error")
    previous_sql: Optional[str] = state.get("generated_sql")

    logger.info("[Node 4] SQL generation attempt %d — query: %r", attempt, enriched_query)

    # Build shared context strings
    schema_ctx = _format_schema_context(state.get("retrieved_tables", []))
    semantic_ctx = _format_semantic_definitions(state.get("semantic_definitions", []))
    joins_ctx = _format_approved_joins(state.get("approved_joins", []))
    few_shots_ctx = _format_few_shots(state.get("few_shot_examples", []))

    llm = get_sql_llm()

    # ── First attempt: full generation ───────────────────────────────────────
    if attempt == 1 or not last_error:
        system_msg = SQL_GENERATION_SYSTEM_PROMPT.format(
            max_rows=settings.max_result_rows,
            semantic_definitions=semantic_ctx,
            schema_context=schema_ctx,
            approved_joins=joins_ctx,
            few_shot_examples=few_shots_ctx,
        )
        user_msg = SQL_GENERATION_USER_PROMPT.format(enriched_query=enriched_query)

    # ── Retry: correction mode (fed with DB error) ───────────────────────────
    else:
        logger.info(
            "[Node 4] Correction mode — fixing error: %r", last_error[:200]
        )
        system_msg = SQL_CORRECTION_SYSTEM_PROMPT.format(
            schema_context=schema_ctx,
            approved_joins=joins_ctx,
        )
        user_msg = SQL_CORRECTION_USER_PROMPT.format(
            failed_sql=previous_sql or "",
            db_error=last_error,
            enriched_query=enriched_query,
        )

    messages = [
        SystemMessage(content=system_msg),
        HumanMessage(content=user_msg),
    ]

    response = llm.invoke(messages)
    generated_sql = _clean_sql_response(response.content)

    logger.info("[Node 4] Generated SQL (%d chars).", len(generated_sql))
    logger.debug("[Node 4] SQL:\n%s", generated_sql)

    return {
        **state,
        "generated_sql": generated_sql,
        "sql_attempt_count": attempt,
        "last_db_error": None,          # Clear error — validator will set next one
        "pipeline_stage": "sql_generated",
    }


def _clean_sql_response(content: str) -> str:
    """
    Strip markdown code fences and extra whitespace from LLM response.
    The LLM sometimes wraps SQL in ```sql ... ``` despite instructions.
    """
    content = content.strip()
    # Remove ```sql ... ``` or ``` ... ``` fences
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove first and last fence lines
        inner_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                continue
            inner_lines.append(line)
        content = "\n".join(inner_lines).strip()
    return content
