"""
tests/test_auth_and_assistant.py
─────────────────────────────────
Login, sessions, role-based sections and the Ask AI chat — end to end through
the FastAPI app and the real LangGraph pipeline, with the LLM and SQL Server
replaced by stubs and the app.* tables on in-memory SQLite.
"""

import datetime as dt
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.agent.graph as graph_mod
import app.agent.nodes.executor as executor_mod
import app.agent.nodes.sql_generator as sqlgen_mod
import app.assistant.store as store_mod
import app.security.auth as auth_mod
from app.config import get_settings
from app.db.app_tables import employee_login, metadata

settings = get_settings()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def attach(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS app")
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS dbo")

    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE dbo.BranchMaster (BranchId INTEGER PRIMARY KEY, BranchName TEXT, "
                             "BranchCode TEXT, BranchRegion TEXT, BranchAddress TEXT, HQ_Branch INTEGER)")
        conn.exec_driver_sql("INSERT INTO dbo.BranchMaster VALUES (1, 'Downtown Clinic', 'DTC', 'Central', '', 1), "
                             "(2, 'Marina Clinic', 'MRC', 'West', '', 0), (3, 'Airport Clinic', 'APC', 'East', '', 0)")
    Session = sessionmaker(bind=engine)

    @contextmanager
    def session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(auth_mod, "get_db_session", session)
    monkeypatch.setattr(store_mod, "get_db_session", session)
    import app.api.routes.dashboard as dashboard_mod
    monkeypatch.setattr(dashboard_mod, "get_db_session", session)
    auth_mod._session_cache.clear()
    auth_mod._failures.clear()

    with session() as s:
        for username, role, branch in (("admin", "superadmin", None), ("reception", "staff", 1),
                                       ("branchadmin", "admin", 2)):
            s.execute(insert(employee_login).values(
                Username=username, PasswordHash=auth_mod.hash_password("secret-pass"),
                DisplayName=username.title(), Role=role, BranchId=branch, IsActive=True,
                CreatedAt=dt.datetime(2026, 1, 1)))
    return session


@pytest.fixture
def pipeline(monkeypatch):
    """Real graph; stubbed intent LLM, SQL LLM and database."""
    seen = {"prompts": [], "intent": "clear"}

    def intent(state):
        if seen["intent"] == "ambiguous" and not state.get("clarifications_made"):
            return {**state, "intent_status": "ambiguous",
                    "clarification_question": "Which period?", "clarification_options": ["This month", "This year"]}
        return {**state, "intent_status": "clear", "intent_confidence": 0.9}

    class LLM:
        def invoke(self, messages):
            seen["prompts"].append(messages[0].content)
            return type("R", (), {"content": seen.get("sql", "SELECT COUNT(*) AS Visits FROM Appointment")})()

    monkeypatch.setattr(graph_mod, "intent_analyzer_node", intent)
    monkeypatch.setattr(graph_mod, "_graph", None)
    monkeypatch.setattr(sqlgen_mod, "get_sql_llm", lambda: LLM())
    monkeypatch.setattr(executor_mod, "_execute_sync",
                        lambda sql: {"columns": ["Visits"], "rows": [{"Visits": 42}], "row_count": 1})
    monkeypatch.setattr(settings, "vanna_review_enabled", False)
    yield seen
    graph_mod._graph = None


@pytest.fixture
def client(db):
    from app.main import app
    return TestClient(app)


def login(client, username="admin", password="secret-pass"):
    return client.post("/auth/login", json={"username": username, "password": password})


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_password_hashing():
    h = auth_mod.hash_password("correct horse")
    assert h.startswith("pbkdf2_sha256$") and "correct horse" not in h
    assert auth_mod.verify_password("correct horse", h)
    assert not auth_mod.verify_password("wrong", h)


def test_routes_require_login(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/dashboard/kpis", params={"from_date": "2026-01-01", "to_date": "2026-01-31"}).status_code == 401
    assert client.get("/assistant/scopes").status_code == 401
    assert client.get("/health").status_code == 200


def test_login_me_logout(client):
    r = login(client)
    assert r.status_code == 200 and r.json()["role"] == "superadmin" and r.json()["can_switch_branch"]
    assert settings.session_cookie_name in r.cookies
    assert client.get("/auth/me").json()["username"] == "admin"
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


def test_wrong_password_and_lockout(client):
    for _ in range(settings.login_max_failures):
        assert login(client, password="nope").status_code == 401
    assert login(client).status_code == 429          # locked even with the right password


def test_vanna_admin_routes_are_superadmin_only(client):
    for user in ("reception", "branchadmin"):
        login(client, user)
        assert client.post("/vanna/train").status_code == 403
        assert client.get("/assistant/diagnostics").status_code == 403
        client.post("/auth/logout")


# ── Sections & roles ──────────────────────────────────────────────────────────

def test_staff_only_sees_their_sections(client):
    login(client, "reception")
    keys = [s["key"] for s in client.get("/assistant/scopes").json()["scopes"]]
    assert keys == ["appointments", "patients"]
    r = client.post("/assistant/ask", json={"force_llm": True, "scope": "services", "question": "Revenue by service?"})
    assert r.status_code == 403


def test_admin_and_superadmin_see_all_sections(client):
    for user in ("admin", "branchadmin"):
        login(client, user)
        keys = [s["key"] for s in client.get("/assistant/scopes").json()["scopes"]]
        assert keys == ["appointments", "services", "medicines", "patients", "doctors", "complaints"]
        client.post("/auth/logout")


# ── Ask AI ────────────────────────────────────────────────────────────────────

def test_ask_uses_only_section_tables(client, pipeline):
    login(client)
    r = client.post("/assistant/ask", json={"force_llm": True, "scope": "patients", "question": "How many visits?"})
    assert r.status_code == 200
    user_msg, reply = r.json()["messages"]
    assert reply["payload"]["status"] == "success" and reply["payload"]["rows"] == [{"Visits": 42}]
    prompt = pipeline["prompts"][0]
    assert "[Table: Patient" in prompt and "[Table: Appointment" in prompt
    assert "ServicePrescription" not in prompt and "MedicinePrescription" not in prompt


def test_sql_outside_section_is_blocked(client, pipeline):
    login(client)
    pipeline["sql"] = "SELECT SUM(Cost) FROM ServicePrescription"
    reply = client.post("/assistant/ask", json={"force_llm": True, "scope": "patients", "question": "Total revenue?"}).json()["messages"][1]
    assert reply["payload"]["status"] == "error"
    assert "ServicePrescription".lower() in reply["payload"]["message"].lower()


def test_history_limits_and_ownership(client, pipeline, monkeypatch):
    login(client)
    monkeypatch.setattr(settings, "chat_max_conversations", 3)
    ids = [client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": f"Question {i}?"})
           .json()["conversation"]["id"] for i in range(5)]
    listed = [c["id"] for c in client.get("/assistant/conversations").json()["conversations"]]
    assert listed == ids[::-1][:3]                     # newest 3 kept
    assert client.get(f"/assistant/conversations/{ids[0]}").status_code == 404

    monkeypatch.setattr(settings, "chat_max_messages", 4)
    conv = ids[-1]
    assert client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "One more?", "conversation_id": conv}).status_code == 200
    assert client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "And another?", "conversation_id": conv}).status_code == 409

    client.post("/auth/logout")
    login(client, "reception")
    assert client.get(f"/assistant/conversations/{conv}").status_code == 404   # not theirs


def test_clarification_flow(client, pipeline):
    login(client)
    pipeline["intent"] = "ambiguous"
    first = client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "How many visits?"}).json()
    reply = first["messages"][1]
    assert reply["payload"]["status"] == "clarification_needed" and reply["payload"]["options"]
    second = client.post("/assistant/clarify", json={"conversation_id": first["conversation"]["id"], "answer": "This month"}).json()
    assert second["messages"][1]["payload"]["status"] == "success"
    conv = client.get(f"/assistant/conversations/{first['conversation']['id']}").json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant", "user", "assistant"]


def test_insights_are_cached_and_feedback_learns(client, pipeline, monkeypatch):
    login(client)
    calls = []

    def fake_analysis(question, sql, columns, rows, tables=None):
        calls.append(tables)
        return {"analysis": "**Answer** 42 visits in total.\n\n**Key Metrics**\n- Total: 42",
                "followup_questions": ["By doctor?"], "analysis_types": ["kpi"], "engine": "vanna"}

    import app.vanna_ai.analyst as analyst
    monkeypatch.setattr(analyst, "generate_analysis", fake_analysis)
    learned = []
    import app.vanna_ai.training as training
    monkeypatch.setattr(training, "learn_example", lambda q, s, *a, **k: learned.append((q, s)))

    reply = client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "How many visits?"}).json()["messages"][1]
    first = client.post(f"/assistant/messages/{reply['id']}/insights").json()
    assert first["content"] == "42 visits in total."
    assert first["insights"]["details"].startswith("**Key Metrics**")
    assert calls == [["Appointment", "Patient", "Employee", "BranchMaster"]]
    client.post(f"/assistant/messages/{reply['id']}/insights")
    assert len(calls) == 1                             # cached on the message

    assert client.post(f"/assistant/messages/{reply['id']}/feedback", json={"correct": True}).json()["learned"]
    assert learned == [("How many visits?", "SELECT TOP 1000 COUNT(*) AS Visits FROM Appointment")]
