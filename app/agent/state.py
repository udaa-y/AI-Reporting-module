"""
app/agent/state.py
───────────────────
Defines the complete LangGraph agent state — the single source of truth
that flows through every node in the pipeline.

Every field is typed explicitly so LangGraph can validate transitions
and so IDE tooling provides full auto-complete across all nodes.
"""

from typing import Annotated, Any, Dict, List, Literal, Optional
from typing_extensions import TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class RetrievedTable(TypedDict):
    """A table entry retrieved from the vector store."""
    table_name: str
    schema_name: str
    description: str
    columns: List[Dict[str, Any]]
    relevance_score: float


class SemanticDefinition(TypedDict):
    """A business metric or concept from the YAML semantic layer."""
    name: str
    aliases: List[str]
    definition: str
    calculation: Optional[str]
    table_dependencies: List[str]
    filters: List[str]


class FewShotExample(TypedDict):
    """A verified past question→SQL pair used for few-shot prompting."""
    question: str
    sql: str
    tables_used: List[str]


class ClarificationItem(TypedDict):
    """A clarification question + the user's answer."""
    question: str
    answer: str


class ValidationResult(TypedDict):
    """Output of the sqlglot AST validator."""
    passed: bool
    errors: List[str]
    sanitized_sql: Optional[str]       # SQL after TOP N injection etc.


class ExecutionResult(TypedDict):
    """Output from SQL Server execution."""
    columns: List[str]
    rows: List[Dict[str, Any]]
    row_count: int
    execution_time_ms: float


class QueryAgentState(TypedDict):
    """
    The complete state object passed between all LangGraph nodes.

    Fields are populated progressively as the query moves through
    the pipeline. Optional fields start as None and are set by the
    node responsible for them.
    """

    # ── Conversation / Session ────────────────────────────────────
    messages: Annotated[List[BaseMessage], add_messages]
    session_id: str
    user_id: str

    # ── Raw User Input ────────────────────────────────────────────
    raw_query: str                          # Exactly what the user typed
    enriched_query: str                     # raw_query + clarification context

    # ── Node 1: Intent Analysis ───────────────────────────────────
    intent_status: Literal["clear", "ambiguous", "irrelevant", "unknown"]
    ambiguity_type: Optional[
        Literal[
            "missing_filter",
            "column_conflict",
            "vague_metric",
            "multi_interpretation",
            "out_of_scope",
        ]
    ]
    intent_confidence: float                # 0.0 → 1.0

    # ── Node 3: Clarification ─────────────────────────────────────
    clarification_question: Optional[str]   # Question sent to user
    clarification_options: Optional[List[str]]  # Multiple-choice options
    clarifications_made: List[ClarificationItem]  # Full history of Q&A

    # ── Node 2: Context Retrieval ─────────────────────────────────
    retrieved_tables: List[RetrievedTable]
    semantic_definitions: List[SemanticDefinition]
    few_shot_examples: List[FewShotExample]
    approved_joins: List[Dict[str, str]]    # Pre-approved JOIN paths

    # ── Node 4: SQL Generation ────────────────────────────────────
    generated_sql: Optional[str]
    sql_attempt_count: int                  # Tracks retry count (max 3)
    last_db_error: Optional[str]            # DB error from last attempt

    # ── Node 5: Validation ────────────────────────────────────────
    validation_result: Optional[ValidationResult]

    # ── Node 6: Execution ─────────────────────────────────────────
    execution_result: Optional[ExecutionResult]

    # ── Node 8: Final Response ────────────────────────────────────
    nl_summary: Optional[str]               # Plain-English result summary
    final_response: Optional[Dict[str, Any]]

    # ── Pipeline Control ──────────────────────────────────────────
    error_message: Optional[str]            # Top-level error for user display
    pipeline_stage: str                     # Current node name (for logging)
