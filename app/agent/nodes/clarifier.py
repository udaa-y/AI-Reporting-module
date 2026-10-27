"""
app/agent/nodes/clarifier.py
──────────────────────────────
Node 3 — Clarification Agent

When the Intent Analyzer detects ambiguity, this node:
  1. Formats the clarification question for the user
  2. Uses LangGraph's interrupt() to PAUSE the graph
  3. When the user responds, resumes and merges the answer into
     the enriched_query before proceeding to SQL generation

LangGraph interrupt() pauses the graph at this node and yields
control back to the API layer. The API returns the question to
the client. When the client sends the answer, the graph resumes
from the checkpoint stored in the configured checkpointer.
"""

import logging
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from app.agent.state import ClarificationItem, QueryAgentState

logger = logging.getLogger(__name__)


def clarifier_node(state: QueryAgentState) -> QueryAgentState:
    """
    LangGraph Node 3 — Clarification Agent.

    Reads:   state.clarification_question, state.clarification_options,
             state.raw_query
    Writes:  state.enriched_query, state.clarifications_made,
             state.intent_status (reset to 'clear'), state.messages
    """
    question = state.get("clarification_question") or (
        "Could you please provide more details about your query?"
    )
    options = state.get("clarification_options") or []

    logger.info("[Node 3] Clarification needed — pausing graph.")
    logger.info("[Node 3] Question: %r", question)

    # Build the interrupt payload — this is returned to the API client
    interrupt_payload = {
        "type": "clarification_needed",
        "question": question,
        "options": options,           # Empty list = open-ended question
        "ambiguity_type": state.get("ambiguity_type"),
    }

    # ── PAUSE: Graph execution stops here ──────────────────────────
    # The API layer receives interrupt_payload and sends it to the client.
    # When the client responds, LangGraph resumes with the user's answer.
    user_answer: str = interrupt(interrupt_payload)
    # ── RESUME: Graph continues with user_answer ───────────────────

    logger.info("[Node 3] User clarification received: %r", user_answer)

    # Record the clarification exchange
    clarification_record: ClarificationItem = {
        "question": question,
        "answer": user_answer,
    }
    existing_clarifications = list(state.get("clarifications_made", []))
    existing_clarifications.append(clarification_record)

    # Merge the clarification into the query so Node 4 has full context
    enriched = (
        f"{state['raw_query']}\n"
        f"[Clarification: {question} → Answer: {user_answer}]"
    )

    return {
        **state,
        "enriched_query": enriched,
        "clarifications_made": existing_clarifications,
        "intent_status": "clear",   # Reset so graph proceeds to SQL generation
        "pipeline_stage": "clarified",
        "messages": [
            AIMessage(content=question),
            HumanMessage(content=user_answer),
        ],
    }
