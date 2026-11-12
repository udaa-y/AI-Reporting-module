"""
tests/test_library.py
──────────────────────
Query library: period parsing, wording-tolerant matching (and refusing near
misses), templating learned SQL, and the Ask AI flow that answers from the
library without calling the LLM — including Super Admin branch comparison.
"""

import datetime as dt

import pytest
import sqlglot

import app.agent.nodes.executor as executor_mod
import app.agent.nodes.sql_generator as sqlgen_mod
from app.assistant import library as library_mod
from app.assistant.library import Library, parse_period, render_sql, templatize
from app.utils.sql_safety import add_branch_dimension

from tests.test_auth_and_assistant import client, db, login, pipeline  # noqa: F401 (fixtures)

TODAY = dt.date(2026, 11, 12)


@pytest.mark.parametrize("text, label, start, end", [
    ("appointments last month", "last month", "2026-10-01", "2026-11-01"),
    ("visits this year", "this year", "2026-01-01", "2027-01-01"),
    ("patients in Q1 2026", "Q1 2026", "2026-01-01", "2026-04-01"),
    ("between 1 Jan and 15 Mar 2026", "01 Jan 2026 – 15 Mar 2026", "2026-01-01", "2026-03-16"),
    ("in the last 7 days", "last 7 days", "2026-11-06", "2026-11-13"),
    ("in March", "March 2026", "2026-03-01", "2026-04-01"),
    ("in 2025", "2025", "2025-01-01", "2026-01-01"),
    ("year to date", "year to date", "2026-01-01", "2026-11-13"),
    ("last week", "last week", "2026-11-02", "2026-11-09"),
])
def test_parse_period(text, label, start, end):
    period, _ = parse_period(text, TODAY)
    assert (period.label, str(period.start), str(period.end)) == (label, start, end)


@pytest.fixture
def lib():
    return library_mod.get_library()


@pytest.mark.parametrize("question, template, period", [
    ("Which doctors saw the most patients last month?", "appointments_per_doctor", "last month"),
    ("busiest physicians this year", "appointments_per_doctor", "this year"),
    ("appointments per doctor", "appointments_per_doctor", "this month"),       # template default
    ("doctors with the fewest visits this month", "least_busy_doctors", "this month"),
    ("monthly visits in 2025", "appointments_by_month", "2025"),
    ("Compare appointments this month vs last month", "appointments_vs_previous", "this month"),
    ("how many appointments were cancelled last week", "cancelled_appointments", "last week"),
])
def test_rewordings_match(lib, question, template, period):
    best, _ = lib.match(question, "appointments", False, TODAY)
    assert best and best.template.id == template and best.params["period"].label == period


def test_top_n_is_taken_from_the_question(lib):
    best, _ = lib.match("Top 5 doctors by visits in Q1 2026", "appointments", False, TODAY)
    assert best.params["top_n"] == 5
    assert "TOP 5" in render_sql(best.template, best.params) and "'2026-04-01'" in render_sql(best.template, best.params)


@pytest.mark.parametrize("question", [
    "cancelled appointments per doctor last month",    # adds a condition the template can't do
    "average age of patients",                          # nothing similar enough
])
def test_near_misses_are_not_answered_from_the_library(lib, question):
    best, _ = lib.match(question, "appointments", False, TODAY)
    assert best is None


def test_branch_templates_are_super_admin_only(lib):
    assert lib.match("appointments by branch this month", "appointments", False, TODAY)[0] is None
    best, _ = lib.match("appointments by branch this month", "appointments", True, TODAY)
    assert best.template.id == "appointments_by_branch"


def test_every_starter_template_renders_valid_tsql(lib):
    for t in lib.templates():
        params, _ = lib._params(t, None, None, TODAY)
        sqlglot.parse_one(render_sql(t, params), read="tsql")


def test_templatize_turns_dates_and_top_n_into_slots():
    sql = ("SELECT TOP 5 e.Name, COUNT(*) AS N FROM Appointment a JOIN Employee e ON e.Id = a.DoctorId "
           "WHERE a.VisitDate >= '2026-10-01' AND a.VisitDate < '2026-11-01' GROUP BY e.Name ORDER BY N DESC")
    t = templatize("Top 5 doctors by visits last month", sql, "appointments", today=TODAY)
    assert t.questions == ["Top {top_n} doctors by visits {period}"]
    assert "{date_from}" in t.sql and "{date_to}" in t.sql and "TOP {top_n}" in t.sql and not t.fixed
    q1 = parse_period("Q1 2026", TODAY)[0]
    assert "'2026-01-01'" in render_sql(t, {"period": q1, "top_n": 3}) and "TOP 3" in render_sql(t, {"period": q1, "top_n": 3})


def test_templatize_keeps_relative_sql_fixed_to_its_period():
    sql = "SELECT COUNT(*) FROM Appointment a WHERE a.VisitDate >= DATEADD(month, -1, GETDATE())"
    t = templatize("appointments last month", sql, "appointments", today=TODAY)
    assert t.fixed == {"period": "last:month"} and t.sql == sql


def test_learned_rephrasing_joins_the_existing_template(tmp_path):
    lib = Library(str(tmp_path), str(tmp_path / "learned.yaml"))
    sql = "SELECT COUNT(*) AS N FROM Appointment a WHERE a.VisitDate >= '2026-10-01' AND a.VisitDate < '2026-11-01'"
    first = lib.learn("how many visits last month", sql, "appointments")
    second = lib.learn("total visit count for last month", sql, "appointments")
    assert first == second and len(lib.templates()) == 1
    assert len(lib.templates()[0].questions) == 2
    reloaded = Library(str(tmp_path), str(tmp_path / "learned.yaml"))
    best, _ = reloaded.match("visit count in March", "appointments", False, TODAY)  # new period, same question
    assert best and "'2026-03-01'" in render_sql(best.template, best.params)


def test_add_branch_dimension():
    rules = {"appointment": {"column": "BranchId"}}
    sql = ("SELECT TOP 10 e.Name AS Doctor, COUNT(*) AS N FROM Appointment a JOIN Employee e ON e.Id = a.DoctorId "
           "GROUP BY e.Name ORDER BY N DESC")
    out = add_branch_dimension(sql, rules)
    assert out.startswith("SELECT cmp_br.BranchName AS Branch, e.Name AS Doctor") and "TOP" not in out
    assert "LEFT JOIN BranchMaster AS cmp_br ON cmp_br.BranchId = a.BranchId" in out
    assert "GROUP BY e.Name, cmp_br.BranchName" in out
    single = add_branch_dimension("SELECT COUNT(*) AS N FROM Appointment a", rules)
    assert "GROUP BY cmp_br.BranchName" in single


# ── Ask AI flow ───────────────────────────────────────────────────────────────

@pytest.fixture
def no_llm(pipeline, monkeypatch):
    """Fails the test if anything tries to call the SQL LLM; records executed SQL."""
    class Forbidden:
        def invoke(self, _):
            raise AssertionError("LLM was called")
    executed = []
    monkeypatch.setattr(sqlgen_mod, "get_sql_llm", lambda: Forbidden())
    monkeypatch.setattr(executor_mod, "_execute_sync", lambda sql: executed.append(sql) or {
        "columns": ["Doctor", "Appointments", "Patients"],
        "rows": [{"Doctor": "Dr A", "Appointments": 40, "Patients": 30},
                 {"Doctor": "Dr B", "Appointments": 25, "Patients": 20}], "row_count": 2})
    return executed


def ask(client, **body):
    return client.post("/assistant/ask", json={"scope": "appointments", **body}).json()["messages"][1]


def test_library_answer_uses_no_llm(client, no_llm):
    login(client, "reception")
    reply = ask(client, question="Which doctors saw the most patients last month?")
    p = reply["payload"]
    assert p["source"] == "library" and p["template_id"] == "appointments_per_doctor"
    assert p["period_label"] == "last month" and p["chart"]["type"] == "horizontal_bar"
    assert "IN (1)" in no_llm[-1]                                  # still branch-scoped
    assert "**65** appointments" in reply["content"] and p["related"]


def test_suggestions_then_pick_one(client, no_llm):
    login(client, "reception")
    reply = ask(client, question="cancelled appointments per doctor last month")
    assert reply["payload"]["status"] == "suggestions"
    choice = reply["payload"]["suggestions"][0]
    picked = ask(client, question=choice["question"], template_id=choice["template_id"])
    assert picked["payload"]["source"] == "library" and picked["payload"]["template_id"] == choice["template_id"]


def test_staff_branch_comparison_is_explained_without_llm(client, no_llm):
    login(client, "reception")
    reply = ask(client, question="Compare appointments across branches this month")
    assert reply["payload"]["status"] == "info" and "Super Admins" in reply["content"]
    assert client.post("/assistant/ask", json={"scope": "appointments", "question": "x per y",
                                               "compare_branch_ids": [1, 2]}).status_code == 403


def test_super_admin_compares_selected_branches(client, no_llm):
    login(client)
    reply = ask(client, question="appointments per doctor this month", compare_branch_ids=[2, 1])
    p = reply["payload"]
    assert p["source"] == "library" and p["compare"] and p["branch_ids"] == [1, 2]
    assert "cmp_br.BranchName AS Branch" in no_llm[-1] and "IN (1, 2)" in no_llm[-1]
    assert p["chart"] is None or p["chart"]["type"] in ("grouped_bar", "stacked_bar", "bar")


def test_super_admin_all_branches_uses_branch_templates(client, no_llm):
    login(client)
    reply = ask(client, question="which branch had the most appointments last month")
    assert reply["payload"]["template_id"] == "appointments_by_branch"
    assert "BranchId IN" not in no_llm[-1]                         # all branches: no filter
