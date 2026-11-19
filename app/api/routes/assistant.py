"""
app/api/routes/assistant.py
────────────────────────────
"Ask AI" chat: scoped questions, chat history and answer enrichment.

  GET    /assistant/scopes                       sections this user may ask about
  GET    /assistant/conversations                recent conversations
  GET    /assistant/conversations/{id}           one conversation with messages
  DELETE /assistant/conversations/{id}
  POST   /assistant/ask                          { scope, question, conversation_id?, branch_id?,
                                                   compare_branch_ids?, template_id?, force_llm? }
  POST   /assistant/clarify                      { conversation_id, answer }
  POST   /assistant/messages/{id}/insights       Vanna analysis + follow-ups (cached on the message)
  POST   /assistant/messages/{id}/feedback       { correct } — 👍 teaches Vanna
  GET    /assistant/diagnostics                  (admin) checks the AI models + database

Answering order (cheapest first):
  1. Query library (app/assistant/library.py) — a saved template matches the
     question → run its SQL directly. No LLM call at all.
  2. Close but not certain → "Did you mean…?" suggestions (still no LLM).
  3. Otherwise the LangGraph pipeline (LLM writes the SQL), restricted to the
     tables of the chosen section (app/assistant/scopes.py).
Every answer gets a free Python summary; the AI narrative runs only when the
user clicks "Explain this" (/messages/{id}/insights).
"""

import datetime as dt
import logging
import re
import time
from collections import Counter
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from langgraph.types import Command
from pydantic import BaseModel, Field

import app.agent.nodes.executor as executor
from app.agent.graph import get_graph
from app.analytics.engine import quick_summary
from app.assistant import store
from app.assistant.errors import describe_error
from app.assistant.library import Match, get_library, parse_period, parse_top_n, terms
from app.assistant.scopes import can_use, get_scope, scopes_for
from app.config import get_settings
from app.security.auth import CurrentUser, require_superadmin, require_user, resolve_branch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/assistant", tags=["Ask AI"])
settings = get_settings()

_answered_by = Counter()        # library / suggestions / llm — shown in /diagnostics


class AskRequest(BaseModel):
    scope: str
    question: str = Field(..., min_length=3, max_length=1000)
    conversation_id: Optional[int] = None
    branch_id: Optional[int] = None         # branch selected at the top (empty = all; Super Admin)
    compare_branch_ids: List[int] = Field(default_factory=list)   # Super Admin "Compare branches"
    template_id: Optional[str] = None       # run this library template ("Did you mean…?" choice)
    force_llm: bool = False                 # skip the library ("Ask AI to write a fresh query")


class ClarifyRequest(BaseModel):
    conversation_id: int
    answer: str = Field(..., min_length=1, max_length=500)


class FeedbackRequest(BaseModel):
    correct: bool
    comment: str = Field(default="", max_length=1000)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _thread(user: CurrentUser, conversation_id: int) -> dict:
    return {"configurable": {"thread_id": f"{user.id}:conv-{conversation_id}"}}


def _initial_state(user: CurrentUser, conversation_id: int, question: str, scope,
                   branch_ids: Optional[List[int]], hint: str = "") -> dict:
    return {
        "messages": [], "session_id": f"conv-{conversation_id}", "user_id": str(user.id),
        "raw_query": question, "enriched_query": question + hint,
        "scope_key": scope.key, "scope_tables": list(scope.all_tables), "branch_ids": branch_ids,
        "intent_status": "unknown", "ambiguity_type": None, "intent_confidence": 0.0,
        "clarification_question": None, "clarification_options": None, "clarifications_made": [],
        "retrieved_tables": [], "semantic_definitions": [], "few_shot_examples": [],
        "approved_joins": [], "generated_sql": None, "sql_attempt_count": 0,
        "last_db_error": None, "validation_result": None, "execution_result": None,
        "sql_review": None, "nl_summary": None, "final_response": None,
        "error_message": None, "pipeline_stage": "init",
    }


def _run(graph_input, config: dict, question: str, scope_key: str, show_detail: bool) -> tuple:
    """Run the graph; returns (content, payload) for the assistant message."""
    graph = get_graph()
    try:
        last = None
        for event in graph.stream(graph_input, config=config, stream_mode="values"):
            last = event
        snapshot = graph.get_state(config)
    except Exception as exc:
        err = describe_error(exc)
        logger.error("[Assistant] Answer failed [%s] %s", err["code"], err["detail"])
        logger.debug("[Assistant] Traceback", exc_info=exc)
        payload = {"status": "error", "error_code": err["code"], "message": err["message"],
                   "scope": scope_key}
        if show_detail:                       # provider/DB error text: admins only
            payload["detail"] = err["detail"]
        return err["message"], payload

    if snapshot.next and "clarifier" in snapshot.next:
        values = snapshot.values
        q = values.get("clarification_question") or "Could you clarify your question?"
        return q, {"status": "clarification_needed", "question": q,
                   "options": values.get("clarification_options") or [], "scope": scope_key}

    final = (last or {}).get("final_response") or snapshot.values.get("final_response") or {}
    if final.get("status") != "success":
        msg = final.get("message") or "I couldn't answer that from the data in this section."
        return msg, {"status": "error", "message": msg, "scope": scope_key}

    rows, count = final.get("data", []), final.get("row_count", 0)
    columns = final.get("columns", [])
    content = quick_summary(columns, rows)
    return content, {
        "status": "success",
        "source": "llm",
        "scope": scope_key,
        "question": final.get("user_query") or question,
        "sql": final.get("generated_sql"),
        "base_sql": final.get("base_sql"),
        "columns": final.get("columns", []),
        "rows": rows,
        "row_count": count,
        "sql_review": final.get("sql_review"),
        "sql_attempts": final.get("sql_attempts"),
        "execution_time_ms": final.get("execution_time_ms"),
        "clarifications": final.get("clarifications_made", []),
    }


_ANSWER_RE = re.compile(r"\*\*Answer\*\*\s*[-:–]?\s*(.+?)(?=\n\s*\*\*[^*\n]+\*\*|\Z)", re.S)


def _split_analysis(markdown: str) -> tuple:
    """Pull the short **Answer** section out of the Vanna report (rest = details)."""
    m = _ANSWER_RE.search(markdown or "")
    if not m:
        first, _, rest = (markdown or "").strip().partition("\n\n")
        return first.strip(), rest.strip()
    return m.group(1).strip(), (markdown[:m.start()] + markdown[m.end():]).strip()


def _check_scope(user: CurrentUser, key: str):
    if not can_use(user.role, key):
        raise HTTPException(403, "You don't have access to this section.")
    return get_scope(key)


def _branch_context(user: CurrentUser, req: AskRequest):
    """(branch ids to restrict to or None for all, compare mode?, can compare branches?)"""
    if req.compare_branch_ids:
        if not user.is_superadmin:
            raise HTTPException(403, "Comparing branches is available to Super Admins only.")
        ids = sorted(set(req.compare_branch_ids))
        if len(ids) < 2:
            raise HTTPException(400, "Pick at least two branches to compare.")
        return ids, True, True
    branch = resolve_branch(user, req.branch_id)
    if branch is None:
        return None, False, user.is_superadmin          # Super Admin on "All branches"
    return [branch], False, False


_OWN_BRANCH = re.compile(r"\b(my|our|this)\s+(branch|clinic)\b", re.I)


def _asks_branch_comparison(question: str) -> bool:
    return "branch" in terms(question) and not _OWN_BRANCH.search(question)


def _branch_rules() -> dict:
    from app.semantic.retriever import get_retriever
    return get_retriever()._loader.load_branch_rules()


def _compare_chart(spec: Optional[dict]) -> Optional[dict]:
    """A template's chart, adapted after a Branch column was added for comparison."""
    if not spec:
        return None
    kind = spec.get("type")
    if kind in ("line", "area"):
        return {**spec, "series": "Branch", "y": spec.get("y", [])[:1]}
    if kind in ("bar", "horizontal_bar", "donut") and spec.get("x"):
        return {**spec, "type": "grouped_bar", "series": "Branch", "y": spec.get("y", [])[:1]}
    if kind == "stat":
        return {"type": "grouped_bar" if len(spec.get("y", [])) > 1 else "bar", "x": "Branch",
                "y": spec.get("y", [])[:3], "title": spec.get("title", "")}
    return None


def _answer_from_template(match: Match, scope, branch_ids: Optional[List[int]], compare: bool,
                          show_detail: bool) -> tuple:
    """Run a library template's SQL — no LLM. Same safety checks as LLM SQL."""
    from app.assistant.library import render_sql
    from app.utils.sql_safety import add_branch_dimension, apply_branch_scope, validate_sql
    from app.vanna_ai.analyst import validate_chart

    t = match.template
    rules = _branch_rules()
    try:
        sql = render_sql(t, match.params)
        if compare and not t.branch_compare:
            sql = add_branch_dimension(sql, rules)
        check = validate_sql(sql, allowed_tables={x.lower() for x in scope.all_tables})
        if not check.passed:
            raise ValueError("; ".join(check.errors))
        base_sql = check.sanitized_sql
        run_sql = apply_branch_scope(base_sql, branch_ids, rules) if branch_ids else base_sql
        start = time.perf_counter()
        result = executor._execute_sync(run_sql)
        elapsed = round((time.perf_counter() - start) * 1000, 1)
    except Exception as exc:
        err = describe_error(exc)
        logger.error("[Assistant] Template %s failed [%s] %s", t.id, err["code"], err["detail"])
        payload = {"status": "error", "error_code": err["code"], "scope": scope.key,
                   "message": f"The saved query “{t.id}” could not run: {err['message']}",
                   "template_id": t.id}
        if show_detail:
            payload["detail"] = err["detail"]
        return payload["message"], payload

    columns, rows = result["columns"], result["rows"]
    chart = t.chart if isinstance(t.chart, dict) else ({"type": t.chart} if t.chart else None)
    if compare and not t.branch_compare:
        chart = _compare_chart(chart)
    chart = validate_chart(chart, columns, rows) if chart else None
    period = match.params.get("period")
    return quick_summary(columns, rows), {
        "status": "success",
        "source": "library",
        "template_id": t.id,
        "scope": scope.key,
        "question": match.question_text(),
        "period_label": period.label if period else None,
        "sql": run_sql,
        "base_sql": base_sql,
        "columns": columns,
        "rows": rows,
        "row_count": result["row_count"],
        "execution_time_ms": elapsed,
        "chart": chart,
        "sql_review": None,
        "sql_attempts": 1,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────
# Plain `def` endpoints: FastAPI runs them in a worker thread, so slow LLM and
# database calls never block other requests.

@router.get("/scopes")
def list_scopes(user: CurrentUser = Depends(require_user)):
    return {"scopes": scopes_for(user.role)}

@router.get("/usage")
def usage(user: CurrentUser = Depends(require_user)):
    return store.ai_usage(user.id)

@router.get("/conversations")
def conversations(user: CurrentUser = Depends(require_user)):
    return {"conversations": store.list_conversations(user.id),
            "max_messages": settings.chat_max_messages}


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: int, user: CurrentUser = Depends(require_user)):
    conv = store.get_conversation(user.id, conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    return conv


@router.delete("/conversations/{conversation_id}")
def remove_conversation(conversation_id: int, user: CurrentUser = Depends(require_user)):
    if not store.delete_conversation(user.id, conversation_id):
        raise HTTPException(404, "Conversation not found.")
    return {"status": "deleted"}


@router.post("/ask")
def ask(req: AskRequest, user: CurrentUser = Depends(require_user)):
    scope = _check_scope(user, req.scope)
    branch_ids, compare, can_compare = _branch_context(user, req)
    question = req.question.strip()

    if req.conversation_id:
        conv = store.get_conversation(user.id, req.conversation_id)
        if not conv:
            raise HTTPException(404, "Conversation not found.")
        conv.pop("messages", None)
        if store.message_count(conv["id"]) + 2 > settings.chat_max_messages:
            raise HTTPException(409, "This chat is full. Start a new chat to keep asking.")
    else:
        conv = store.create_conversation(user.id, question, scope.key)

    user_msg = store.add_message(conv["id"], "user", question,
                                 {"scope": scope.key, "branch_ids": branch_ids, "compare": compare},
                                 scope=scope.key)
    library = get_library()
    match, suggestions = None, []

    if not user.is_superadmin and _asks_branch_comparison(question):
        # Admins/staff only see their own branch: answer without any LLM call.
        content = ("Comparing branches is available to Super Admins. You can compare your branch "
                   "with its own past — for example “appointments this month vs last month”.")
        payload = {"status": "info", "scope": scope.key}
        _answered_by["info"] += 1
    else:
        if req.template_id:
            t = library.get(req.template_id)
            if t and (not t.branch_compare or can_compare) and ("*" in t.scopes or scope.key in t.scopes):
                params, _ = library._params(t, parse_period(question), parse_top_n(question), dt.date.today())
                match = Match(t, t.questions[0] if t.questions else question, params or {}, True, 1.0)
        elif not req.force_llm:
            match, suggestions = library.match(question, scope.key, can_compare)

        if match:
            content, payload = _answer_from_template(match, scope, branch_ids, compare, user.is_admin)
            _answered_by["library"] += 1
        elif suggestions:
            content = "I have verified answers for similar questions. Did you mean one of these?"
            payload = {"status": "suggestions", "scope": scope.key,
                       "suggestions": [{"template_id": m.template.id, "question": m.question_text()}
                                       for m in suggestions]}
            _answered_by["suggestions"] += 1
        elif store.ai_usage(user.id)["remaining"] == 0:
            # Daily AI limit reached: no LLM call. Saved (library) questions still work.
            usage = store.ai_usage(user.id)
            content = (f"You've used all {usage['limit']} AI questions for the last "
                       f"{usage['window_hours']} hours. Saved questions still work — pick one of the "
                       f"suggestions, or ask again once your limit resets.")
            payload = {"status": "limit", "scope": scope.key, "resets_at": usage["resets_at"]}
            _answered_by["limit"] += 1
        else:
            hint = ""
            if compare:
                names = ", ".join(b["name"] for b in _branch_names(branch_ids))
                hint = (f"\n[Compare these branches side by side: {names}. Join BranchMaster on BranchId "
                        f"and include BranchMaster.BranchName AS Branch as a column, grouping by it.]")
            content, payload = _run(_initial_state(user, conv["id"], question, scope, branch_ids, hint),
                                    _thread(user, conv["id"]), question, scope.key, user.is_admin)
            _answered_by["llm"] += 1
            # Count it unless the AI provider itself failed (no credits, bad key, outage…).
            if not str(payload.get("error_code") or "").startswith("llm_"):
                store.record_ai_question(user.id, question)

    payload.update(branch_ids=branch_ids, compare=compare)
    if payload.get("status") == "success":
        period = (match and match.params.get("period")) or (parse_period(question) or (None,))[0]
        payload["related"] = library.related(question, scope.key, can_compare,
                                             exclude=payload.get("template_id"), period=period)
    reply = store.add_message(conv["id"], "assistant", content, payload)
    return {"conversation": {**conv, "scope": scope.key}, "messages": [user_msg, reply],
            "usage": store.ai_usage(user.id)}


def _branch_names(ids: Optional[List[int]]) -> List[dict]:
    from sqlalchemy import bindparam, text

    from app.db.sqlserver import get_db_session
    if not ids:
        return []
    stmt = text("SELECT BranchId, BranchName FROM dbo.BranchMaster WHERE BranchId IN :ids") \
        .bindparams(bindparam("ids", expanding=True))
    try:
        with get_db_session() as session:
            return [{"id": r[0], "name": r[1]} for r in session.execute(stmt, {"ids": list(ids)})]
    except Exception:
        return [{"id": i, "name": f"Branch {i}"} for i in ids]


@router.post("/clarify")
def clarify(req: ClarifyRequest, user: CurrentUser = Depends(require_user)):
    conv = store.get_conversation(user.id, req.conversation_id)
    if not conv:
        raise HTTPException(404, "Conversation not found.")
    scope = _check_scope(user, conv["scope"])
    config = _thread(user, conv["id"])

    user_msg = store.add_message(conv["id"], "user", req.answer, {"scope": scope.key})
    snapshot = get_graph().get_state(config)
    if not (snapshot.next and "clarifier" in snapshot.next):
        content = "That question has expired. Please ask it again."
        reply = store.add_message(conv["id"], "assistant", content,
                                  {"status": "error", "message": content, "scope": scope.key})
    else:
        question = snapshot.values.get("raw_query", "")
        content, payload = _run(Command(resume=req.answer), config, question, scope.key, user.is_admin)
        if payload.get("status") == "success":
            payload["related"] = get_library().related(question, scope.key, user.is_superadmin, exclude=None)
        reply = store.add_message(conv["id"], "assistant", content, payload)
    conv.pop("messages", None)
    return {"conversation": conv, "messages": [user_msg, reply]}


@router.post("/messages/{message_id}/insights")
def insights(message_id: int, user: CurrentUser = Depends(require_user)):
    from app.vanna_ai.analyst import generate_analysis

    msg = store.get_message(user.id, message_id)
    if not msg or msg["role"] != "assistant":
        raise HTTPException(404, "Message not found.")
    payload = msg["payload"]
    if payload.get("insights"):
        return {"content": msg["content"], "insights": payload["insights"]}
    if payload.get("status") != "success" or not payload.get("rows"):
        raise HTTPException(400, "There is no result to analyse for this message.")

    tables = list(get_scope(payload["scope"]).all_tables) if can_use(user.role, payload.get("scope", "")) else None
    result = generate_analysis(payload["question"], payload.get("sql") or "",
                               payload["columns"], payload["rows"], tables=tables)
    answer, details = _split_analysis(result.get("analysis", ""))
    payload["insights"] = {
        "answer": answer,
        "details": details,
        "followups": result.get("followup_questions", []),
        "chart": result.get("chart"),              # chart the AI chose for this data (or None)
        "analysis_types": result.get("analysis_types", []),
        "engine": result.get("engine"),
        "error": result.get("error"),
    }
    content = answer or msg["content"]
    if not result.get("error"):          # keep failures retryable
        store.update_message(message_id, content, payload)
    return {"content": content, "insights": payload["insights"]}


@router.post("/messages/{message_id}/feedback")
def feedback(message_id: int, req: FeedbackRequest, user: CurrentUser = Depends(require_user)):
    msg = store.get_message(user.id, message_id)
    if not msg or msg["payload"].get("status") != "success":
        raise HTTPException(404, "Message not found.")
    payload = msg["payload"]
    learned = False
    sql = payload.get("base_sql") or payload.get("sql")   # never teach a branch-specific filter
    # Library answers are already saved templates; only LLM-written SQL is learned.
    if req.correct and sql and payload.get("source") != "library":
        from app.vanna_ai.training import learn_example
        try:
            learn_example(payload["question"], sql, payload.get("scope") or "*",
                          branch_compare=bool(payload.get("compare")))
            learned = True
        except Exception as exc:
            logger.warning("[Assistant] Could not learn example: %s", exc)
    else:
        logger.info("[Assistant] Answer marked wrong — question=%r comment=%r",
                    payload.get("question", "")[:200], req.comment[:300])
    payload["feedback"] = {"correct": req.correct, "comment": req.comment}
    store.update_message(message_id, msg["content"], payload)
    return {"status": "success", "learned": learned}


@router.get("/diagnostics")
def diagnostics(user: CurrentUser = Depends(require_superadmin)):
    """
    Admin check: sends a tiny prompt to every configured model and pings the
    database, reporting exactly which one fails and why. Open in the browser:
    /assistant/diagnostics
    """
    import time
    from openai import OpenAI

    from app.db.sqlserver import check_sqlserver_health

    kwargs = {"api_key": settings.openai_api_key or "missing", "timeout": 30}
    if settings.clean_openai_base_url:
        kwargs["base_url"] = settings.clean_openai_base_url
    client = OpenAI(**kwargs)

    roles = {
        "LLM_SQL_MODEL (SQL generation)": settings.llm_sql_model,
        "LLM_INTENT_MODEL (question check)": settings.llm_intent_model,
        "VANNA_MODEL (SQL review)": settings.vanna_model or settings.llm_sql_model,
        "VANNA_FAST_MODEL (analysis, follow-ups, chart)": settings.vanna_fast_model or settings.llm_intent_model,
    }
    results, tested = [], {}
    for role, model in roles.items():
        if model not in tested:
            start = time.perf_counter()
            try:
                client.chat.completions.create(
                    model=model, max_tokens=5,
                    messages=[{"role": "user", "content": "Reply with: ok"}],
                )
                tested[model] = {"ok": True, "ms": round((time.perf_counter() - start) * 1000)}
            except Exception as exc:
                err = describe_error(exc)
                tested[model] = {"ok": False, "error_code": err["code"], "message": err["message"],
                                 "detail": err["detail"]}
        results.append({"role": role, "model": model, **tested[model]})

    return {
        "library": {"templates": len(get_library().templates()),
                    "answered_since_restart": dict(_answered_by)},
        "provider": settings.clean_openai_base_url or "https://api.openai.com/v1",
        "api_key_set": bool(settings.openai_api_key),
        "models": results,
        "database": "ok" if check_sqlserver_health() else "unreachable",
    }
