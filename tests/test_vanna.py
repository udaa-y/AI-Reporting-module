"""
tests/test_vanna.py
────────────────────
Vanna integration tests. The LLM is replaced by a stub, so these run
offline: training from the semantic layer, SQL review routing, the
analyst and learning from user feedback.
"""

import json
from concurrent.futures import Future

import pytest
import yaml

from app.config import get_settings
from app.vanna_ai import analyst, reviewer, training
from app.vanna_ai.engine import ClinicVannaMemory


class StubVanna(ClinicVannaMemory):
    """In-memory Vanna whose LLM returns canned answers."""

    def __init__(self, replies):
        super().__init__(config={})
        self.replies = replies
        self.prompts = []

    def submit_prompt(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        reply = self.replies(prompt, kwargs) if callable(self.replies) else self.replies
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def stub(monkeypatch):
    def install(replies):
        vn = StubVanna(replies)
        training.train_from_semantic_layer(vn)
        import app.vanna_ai.engine as engine
        monkeypatch.setattr(engine, "_vanna", vn)
        return vn
    return install


# ── Training ──────────────────────────────────────────────────────────────────

def test_table_to_ddl_skips_placeholder_descriptions():
    ddl = training.table_to_ddl({
        "table_name": "Visit", "schema_name": "dbo",
        "columns": [
            {"name": "Id", "type": "int", "IS_NULLABLE": "NO", "description": "Column Id"},
            {"name": "Status", "type": "varchar", "description": "C = completed, X = cancelled"},
        ],
    })
    assert ddl == ("CREATE TABLE [dbo].[Visit] (\n    [Id] INT NOT NULL,\n"
                   "    [Status] VARCHAR -- C = completed, X = cancelled\n);")


def test_join_doc_uses_table_names_not_aliases():
    doc = training.join_to_doc({
        "from_table": "Patient", "to_table": "Appointment", "join_type": "INNER JOIN",
        "condition": "p.Id = a.PatientId", "aliases": {"Patient": "p", "Appointment": "a"},
    })
    assert "Patient.Id = Appointment.PatientId" in doc


def test_trains_from_project_semantic_layer(stub):
    vn = stub("")
    df = vn.get_training_data()
    counts = df.training_data_type.value_counts().to_dict()
    assert counts["ddl"] == 6
    docs = " ".join(df[df.training_data_type == "documentation"].content)
    assert "Appointment.DoctorId = Employee.Id" in docs
    assert "Add domain-specific notes" not in docs
    assert "Add your own business rules" not in docs   # HTML comment stripped


# ── SQL review ────────────────────────────────────────────────────────────────

def test_review_parses_verdict_and_includes_schema(stub):
    vn = stub(json.dumps({"verdict": "incorrect", "confidence": 0.9,
                          "issues": ["Counts prescriptions, not visits"],
                          "explanation": "Double counting", "suggested_sql": None}))
    review = reviewer.review_sql("How many visits last month?", "SELECT COUNT(*) FROM Appointment")
    assert review["verdict"] == "incorrect" and review["confidence"] == 0.9
    system_prompt = vn.prompts[0][0][0]["content"]
    assert "CREATE TABLE [dbo].[Appointment]" in system_prompt
    assert reviewer.should_retry(review)


def test_review_failure_is_skipped_not_raised(stub):
    stub(TimeoutError("slow"))
    review = reviewer.review_sql("q", "SELECT 1")
    assert review["verdict"] == "skipped" and not reviewer.should_retry(review)


def _done(value):
    f = Future()
    f.set_result(value)
    return f


def test_executor_routes_confident_rejection_back_to_generator():
    from app.agent.nodes.executor import _apply_review
    ok = {"execution_result": {"rows": [{"a": 1}]}, "last_db_error": None, "sql_attempt_count": 1}
    bad = {"verdict": "incorrect", "confidence": 0.95, "issues": ["wrong join"],
           "explanation": "", "suggested_sql": None}

    out = _apply_review(dict(ok), _done(bad))
    assert out["execution_result"] is None and "wrong join" in out["last_db_error"]

    # Out of retries: keep the results and show the warning instead.
    last = _apply_review({**ok, "sql_attempt_count": get_settings().max_sql_retries}, _done(bad))
    assert last["execution_result"] and not last["last_db_error"]
    assert last["sql_review"]["verdict"] == "incorrect"

    # Low confidence never discards results.
    unsure = _apply_review(dict(ok), _done({**bad, "confidence": 0.3}))
    assert unsure["execution_result"] and not unsure["last_db_error"]


# ── Analyst ───────────────────────────────────────────────────────────────────

ROWS = [{"VisitMonth": f"2025-{m:02d}", "Visits": 100 + m * 5} for m in range(1, 10)]


def test_analysis_runs_both_calls_and_caches(stub):
    analyst.clear_cache()

    def reply(prompt, kwargs):
        if kwargs.get("json"):
            return json.dumps({"questions": ["Visits by doctor?", "Visits by gender?"]})
        return "**Answer** Visits rose 40%."

    vn = stub(reply)
    first = analyst.generate_analysis("Monthly visits", "SELECT ...", ["VisitMonth", "Visits"], ROWS)
    assert first["analysis"].startswith("**Answer**")
    assert first["followup_questions"] == ["Visits by doctor?", "Visits by gender?"]
    assert "trend" in first["analysis_types"] and not first["cached"]
    findings_prompt = [p for p, k in vn.prompts if not k.get("json")][0][1]["content"]
    assert "Trend of Visits by month" in findings_prompt

    calls = len(vn.prompts)
    again = analyst.generate_analysis("Monthly visits", "SELECT ...", ["VisitMonth", "Visits"], ROWS)
    assert again["cached"] and len(vn.prompts) == calls


def test_analysis_falls_back_to_computed_findings(stub):
    analyst.clear_cache()
    stub(RuntimeError("API down"))
    out = analyst.generate_analysis("Monthly visits", "SELECT 1", ["VisitMonth", "Visits"], ROWS)
    assert out["engine"] == "computed" and "Trend of Visits" in out["analysis"]
    assert out["error"]


# ── Feedback learning ─────────────────────────────────────────────────────────

def test_learn_example_persists_and_dedupes(stub, tmp_path, monkeypatch):
    vn = stub("")
    path = tmp_path / "learned.yaml"
    monkeypatch.setattr(training.settings, "vanna_learned_examples_path", str(path))

    training.learn_example("Visits per doctor?", "SELECT DoctorId, COUNT(*) FROM Appointment GROUP BY DoctorId")
    training.learn_example("Visits per doctor?", "SELECT DoctorId, COUNT(*) FROM Appointment GROUP BY DoctorId")

    saved = yaml.safe_load(path.read_text())["examples"]
    assert len(saved) == 1
    assert vn.get_similar_question_sql("visits per doctor")[0]["question"] == "Visits per doctor?"
