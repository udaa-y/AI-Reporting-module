"""
tests/test_branches.py
───────────────────────
Branch isolation: Super Admins see any branch or all; admins and staff only
their own — enforced in the KPIs, the appointments chart and Ask AI (where the
LLM's SQL is rewritten so every table only exposes one branch).
"""

import pytest
import sqlglot
from fastapi import HTTPException

import app.agent.nodes.executor as executor_mod
from app.security.auth import CurrentUser, resolve_branch
from app.utils.sql_safety import apply_branch_scope

from tests.test_auth_and_assistant import client, db, login, pipeline  # noqa: F401 (fixtures)

SUPER = CurrentUser(1, "owner", "Owner", "superadmin", None, None)
ADMIN = CurrentUser(2, "adm", "Admin", "admin", None, 2)
STAFF = CurrentUser(3, "rec", "Rec", "staff", None, 1)
LOST = CurrentUser(4, "x", "X", "staff", None, None)


def test_resolve_branch_rules():
    assert resolve_branch(SUPER, None) is None and resolve_branch(SUPER, 3) == 3
    assert resolve_branch(ADMIN, None) == 2 and resolve_branch(ADMIN, 2) == 2
    for user, asked in ((ADMIN, 3), (STAFF, 2), (LOST, None)):
        with pytest.raises(HTTPException) as e:
            resolve_branch(user, asked)
        assert e.value.status_code == 403


RULES = {
    "appointment": {"column": "BranchId"},
    "patient": {"column": "BranchId",
                "linked": "Id IN (SELECT PatientId FROM dbo.Appointment WHERE BranchId IN ({branch_ids}))"},
    "employee": {"column": "BranchId",
                 "linked": "Id IN (SELECT DoctorId FROM dbo.Appointment WHERE BranchId IN ({branch_ids}))"},
}


def test_branch_rules_come_from_the_semantic_yaml():
    from app.semantic.retriever import get_retriever
    rules = get_retriever()._loader.load_branch_rules()
    assert {"appointment", "patient", "employee", "branchmaster", "serviceprescription"} <= set(rules)
    assert "{branch_ids}" in rules["patient"]["linked"]


def test_apply_branch_scope_rewrites_every_table():
    sql = ("WITH v AS (SELECT PatientId FROM dbo.Appointment WHERE VisitDate > '2026-01-01') "
           "SELECT TOP 10 e.Name, COUNT(*) AS N FROM v JOIN Patient p ON p.Id = v.PatientId "
           "JOIN Appointment a ON a.PatientId = p.Id JOIN Employee e ON e.Id = a.DoctorId GROUP BY e.Name")
    out = apply_branch_scope(sql, [7], RULES)
    sqlglot.parse_one(out, read="tsql")                          # still valid T-SQL
    assert out.count("(SELECT * FROM dbo.Appointment WHERE BranchId IN (7)) AS") == 1   # CTE source
    assert "(SELECT * FROM Appointment WHERE BranchId IN (7)) AS a" in out              # joined table
    assert "FROM Patient WHERE BranchId IN (7) OR Id IN (SELECT PatientId FROM dbo.Appointment WHERE BranchId IN (7))" in out
    assert "FROM Employee WHERE BranchId IN (7) OR Id IN (SELECT DoctorId FROM dbo.Appointment WHERE BranchId IN (7))" in out
    assert "FROM v JOIN" in out                                  # CTE reference left alone
    assert "TOP 10" in out


def test_apply_branch_scope_only_inlines_integers():
    with pytest.raises(ValueError):
        apply_branch_scope("SELECT * FROM Appointment", ["1; DROP TABLE x"], RULES)


# ── Branch list ───────────────────────────────────────────────────────────────

def test_branch_list_per_role(client):
    login(client)
    body = client.get("/dashboard/branches").json()
    assert body["can_switch"] and [b["name"] for b in body["branches"]] == ["Downtown Clinic", "Airport Clinic", "Marina Clinic"]
    assert body["branches"][0]["is_hq"]
    client.post("/auth/logout")
    login(client, "reception")
    body = client.get("/dashboard/branches").json()
    assert not body["can_switch"] and [b["id"] for b in body["branches"]] == [1]


# ── KPIs & chart ─────────────────────────────────────────────────────────────

@pytest.fixture
def captured(monkeypatch):
    import app.api.routes.appointment_charts as charts
    import app.api.routes.dashboard as dash
    seen = []
    monkeypatch.setattr(dash, "_run", lambda sql, params: seen.append((str(sql), params)) or {})
    monkeypatch.setattr(charts, "_fetch", lambda sql, params, exp: seen.append((sql, params)) or [])
    dash._cache.clear()
    charts._cache.clear()
    return seen


KPI = {"from_date": "2026-01-01", "to_date": "2026-01-31"}


def test_kpis_follow_branch(client, captured):
    login(client, "reception")
    assert client.get("/dashboard/kpis", params={**KPI, "branch_id": 2}).status_code == 403
    assert client.get("/dashboard/kpis", params=KPI).json()["branch_id"] == 1
    sqls = [s for s, _ in captured]
    for alias in ("a", "p", "sp", "mp"):
        assert any(f"{alias}.BranchId = :branch_id" in s for s in sqls)
    assert all(p["branch_id"] == 1 for _, p in captured)

    captured.clear()
    client.post("/auth/logout")
    login(client)                                                 # superadmin, all branches
    assert client.get("/dashboard/kpis", params=KPI).json()["branch_id"] is None
    assert not any("BranchId" in s for s, _ in captured)


def test_chart_follows_branch(client, captured):
    login(client, "branchadmin")
    body = {"from_date": "2026-01-01", "to_date": "2026-03-31"}
    assert client.post("/dashboard/appointments/series", json={**body, "branch_id": 3}).status_code == 403
    client.post("/dashboard/appointments/series", json=body)
    client.post("/dashboard/appointments/list", json=body)
    assert all("a.BranchId = :branch_id" in s and p["branch_id"] == 2 for s, p in captured)
    meta = client.get("/dashboard/appointments/meta").json()
    assert "branch" not in [b["key"] for b in meta["breakdowns"]]

    client.post("/auth/logout")
    login(client)
    meta = client.get("/dashboard/appointments/meta").json()       # all branches: can compare them
    assert "branch" in [b["key"] for b in meta["breakdowns"]]


# ── Ask AI ────────────────────────────────────────────────────────────────────

def test_ask_ai_is_scoped_to_branch(client, pipeline, monkeypatch):
    executed = []
    monkeypatch.setattr(executor_mod, "_execute_sync",
                        lambda sql: executed.append(sql) or {"columns": ["N"], "rows": [{"N": 5}], "row_count": 1})
    pipeline["sql"] = "SELECT COUNT(*) AS N FROM Appointment a JOIN Patient p ON p.Id = a.PatientId"

    login(client, "reception")
    assert client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "Visits?", "branch_id": 2}).status_code == 403
    reply = client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "Visits?"}).json()["messages"][1]
    assert "WHERE BranchId IN (1))" in executed[-1] and "Patient WHERE BranchId IN (1) OR" in executed[-1]
    assert reply["payload"]["branch_ids"] == [1] and "BranchId" not in reply["payload"]["base_sql"]

    learned = []
    import app.vanna_ai.training as training
    monkeypatch.setattr(training, "learn_example", lambda q, s, *a, **k: learned.append(s))
    client.post(f"/assistant/messages/{reply['id']}/feedback", json={"correct": True})
    assert learned and "BranchId" not in learned[0]               # examples stay branch-neutral

    client.post("/auth/logout")
    login(client)
    client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "Visits?", "branch_id": 3})
    assert "WHERE BranchId IN (3))" in executed[-1]
    client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "Visits?"})
    assert "BranchId" not in executed[-1]                          # all branches
