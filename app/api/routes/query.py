"""
app/api/routes/query.py
────────────────────────
Main Query API Endpoint

Provides two endpoints:
  POST /query          — Submit a new natural language query
  POST /query/clarify  — Resume a paused query with user's clarification answer

The clarification flow uses LangGraph's interrupt() mechanism:
  1. POST /query returns {status: "clarification_needed", question: "..."}
  2. Client shows the question, user answers
  3. POST /query/clarify resumes the graph with the answer
"""

import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from langgraph.types import Command

from app.agent.graph import get_graph
from app.agent.state import QueryAgentState

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/query", tags=["Query"])


# ── Request / Response Models ──────────────────────────────────────────────────

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=3, description="The natural language question")
    user_id: str = Field(default="anonymous", description="User identifier")
    session_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Session ID — reuse across follow-up queries in same conversation",
    )


class ClarifyRequest(BaseModel):
    session_id: str = Field(..., description="Session ID from the original query response")
    answer: str = Field(..., min_length=1, description="User's answer to the clarification question")
    user_id: str = Field(default="anonymous")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _build_initial_state(req: QueryRequest) -> QueryAgentState:
    """Build the initial LangGraph state from an incoming request."""
    return {
        "messages": [],
        "session_id": req.session_id,
        "user_id": req.user_id,
        "raw_query": req.query,
        "enriched_query": req.query,
        "intent_status": "unknown",
        "ambiguity_type": None,
        "intent_confidence": 0.0,
        "clarification_question": None,
        "clarification_options": None,
        "clarifications_made": [],
        "retrieved_tables": [],
        "semantic_definitions": [],
        "few_shot_examples": [],
        "approved_joins": [],
        "generated_sql": None,
        "sql_attempt_count": 0,
        "last_db_error": None,
        "validation_result": None,
        "execution_result": None,
        "nl_summary": None,
        "final_response": None,
        "error_message": None,
        "pipeline_stage": "init",
    }


def _thread_config(session_id: str, user_id: str) -> dict:
    """Build LangGraph thread config for state persistence."""
    return {"configurable": {"thread_id": f"{user_id}:{session_id}"}}


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("")
async def submit_query(req: QueryRequest):
    """
    Submit a natural language query for processing.

    Returns either:
    - A complete result (status: "success" or "error")
    - A clarification request (status: "clarification_needed")
    """
    logger.info(
        "[API] New query — user=%s, session=%s, query=%r",
        req.user_id, req.session_id, req.query[:100],
    )

    graph = get_graph()
    config = _thread_config(req.session_id, req.user_id)
    initial_state = _build_initial_state(req)

    start = time.perf_counter()

    try:
        # Stream the graph execution — collect interrupt or final output
        result_state = None
        interrupt_value = None

        for event in graph.stream(initial_state, config=config, stream_mode="values"):
            result_state = event

        # Check if the graph was interrupted for clarification
        # LangGraph surfaces interrupts via get_state()
        state_snapshot = graph.get_state(config)

        if state_snapshot.next and "clarifier" in state_snapshot.next:
            # Graph is paused — extract the clarification question
            current = state_snapshot.values
            total_ms = round((time.perf_counter() - start) * 1000)
            logger.info("[API] Graph paused for clarification (%.0fms)", total_ms)
            return JSONResponse({
                "status": "clarification_needed",
                "session_id": req.session_id,
                "question": current.get("clarification_question"),
                "options": current.get("clarification_options", []),
                "ambiguity_type": current.get("ambiguity_type"),
                "processing_time_ms": total_ms,
            })

        # Graph completed — return final response
        final = (result_state or {}).get("final_response") or {}
        total_ms = round((time.perf_counter() - start) * 1000)
        final["total_processing_time_ms"] = total_ms

        logger.info(
            "[API] Query complete — status=%s, rows=%d, time=%.0fms",
            final.get("status"), final.get("row_count", 0), total_ms,
        )
        return JSONResponse(jsonable_encoder(final))

    except Exception as exc:
        logger.exception("[API] Unhandled error processing query: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/clarify")
async def resume_with_clarification(req: ClarifyRequest):
    """
    Resume a paused query graph with the user's clarification answer.

    The graph was interrupted at the clarifier node waiting for this input.
    LangGraph resumes from the saved checkpoint using the session_id.
    """
    logger.info(
        "[API] Clarification received — session=%s, answer=%r",
        req.session_id, req.answer[:100],
    )

    graph = get_graph()
    config = _thread_config(req.session_id, req.user_id)

    start = time.perf_counter()

    try:
        # Resume the interrupted graph by passing the user's answer
        # as the interrupt resume value via LangGraph Command
        result_state = None
        for event in graph.stream(
            Command(resume=req.answer),
            config=config,
            stream_mode="values",
        ):
            result_state = event

        # Check if the graph was paused again for another clarification
        state_snapshot = graph.get_state(config)
        if state_snapshot.next and "clarifier" in state_snapshot.next:
            current = state_snapshot.values
            total_ms = round((time.perf_counter() - start) * 1000)
            logger.info("[API] Graph paused for another clarification (%.0fms)", total_ms)
            return JSONResponse({
                "status": "clarification_needed",
                "session_id": req.session_id,
                "question": current.get("clarification_question"),
                "options": current.get("clarification_options", []),
                "ambiguity_type": current.get("ambiguity_type"),
                "processing_time_ms": total_ms,
            })

        # Graph completed — extract final response
        final = (result_state or {}).get("final_response")
        if not final and state_snapshot.values:
            final = state_snapshot.values.get("final_response") or {}

        total_ms = round((time.perf_counter() - start) * 1000)
        final["total_processing_time_ms"] = total_ms

        logger.info(
            "[API] Resumed query complete — status=%s, rows=%d, time=%.0fms",
            final.get("status"), final.get("row_count", 0), total_ms,
        )
        return JSONResponse(jsonable_encoder(final))

    except Exception as exc:
        logger.exception("[API] Error resuming query: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
