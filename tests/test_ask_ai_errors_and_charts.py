"""
tests/test_ask_ai_errors_and_charts.py
───────────────────────────────────────
Clear error reporting for LLM/provider failures, and validation of the chart
the LLM chooses for a result.
"""

import httpx
import openai
import pytest

from app.assistant.errors import describe_error
from app.vanna_ai.analyst import validate_chart

from tests.test_auth_and_assistant import client, db, login, pipeline  # noqa: F401 (fixtures)


def _status_error(cls, status, message="error"):
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.example/v1/chat"))
    return cls(message, response=response, body=None)


@pytest.mark.parametrize("exc, code", [
    (_status_error(openai.APIStatusError, 402, "This request requires more credits"), "llm_credits"),
    (_status_error(openai.RateLimitError, 429), "llm_rate_limit"),
    (_status_error(openai.AuthenticationError, 401), "llm_auth"),
    (_status_error(openai.NotFoundError, 404, "model not found"), "llm_model"),
    (_status_error(openai.BadRequestError, 400), "llm_bad_request"),
    (RuntimeError("boom"), "internal"),
])
def test_describe_error_codes(exc, code):
    err = describe_error(exc)
    assert err["code"] == code and err["message"] and type(exc).__name__ in err["detail"]


def test_wrapped_errors_are_found():
    try:
        try:
            raise _status_error(openai.RateLimitError, 429)
        except Exception as inner:
            raise RuntimeError("graph failed") from inner
    except RuntimeError as outer:
        assert describe_error(outer)["code"] == "llm_rate_limit"


def test_llm_failure_reaches_the_chat_with_code(client, pipeline, monkeypatch):  # noqa: F811
    import app.agent.nodes.sql_generator as sg

    class Broke:
        def invoke(self, _):
            raise _status_error(openai.APIStatusError, 402, "This request requires more credits")

    monkeypatch.setattr(sg, "get_sql_llm", lambda: Broke())
    login(client)                                         # admin: sees technical detail
    reply = client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "How many visits?"}).json()["messages"][1]
    assert reply["payload"]["error_code"] == "llm_credits"
    assert "credits" in reply["content"] and "402" not in reply["content"]
    assert "requires more credits" in reply["payload"]["detail"]

    client.post("/auth/logout")
    login(client, "reception")                            # staff: no raw provider text
    reply = client.post("/assistant/ask", json={"force_llm": True, "scope": "appointments", "question": "How many visits?"}).json()["messages"][1]
    assert reply["payload"]["error_code"] == "llm_credits" and "detail" not in reply["payload"]


ROWS = [{"Doctor": f"Dr {i}", "Department": "ENT" if i % 2 else "Cardio", "Appointments": 10 * i, "Revenue": 1000.5 * i}
        for i in range(1, 10)]
COLS = ["Doctor", "Department", "Appointments", "Revenue"]


def test_validate_chart_keeps_good_choice():
    spec = validate_chart({"type": "horizontal_bar", "title": "Top doctors", "x": "Doctor",
                           "y": ["Appointments"], "sort": "desc", "limit": 5}, COLS, ROWS)
    assert spec == {"type": "horizontal_bar", "title": "Top doctors", "x": "Doctor", "y": ["Appointments"],
                    "series": None, "sort": "desc", "limit": 5}


def test_validate_chart_rejects_or_repairs_bad_choices():
    assert validate_chart({"type": "line", "x": "Nope", "y": ["Appointments"]}, COLS, ROWS) is None
    assert validate_chart({"type": "bar", "x": "Doctor", "y": ["Doctor"]}, COLS, ROWS) is None   # y not numeric
    assert validate_chart({"type": "radar", "x": "Doctor", "y": ["Appointments"]}, COLS, ROWS) is None
    assert validate_chart({"type": "stacked_bar", "x": "Doctor", "y": ["Appointments"]}, COLS, ROWS) is None
    assert validate_chart({"type": "stat", "y": ["Appointments"]}, COLS, ROWS) is None          # needs 1 row
    donut = validate_chart({"type": "donut", "x": "Doctor", "y": ["Appointments"]}, COLS, ROWS)
    assert donut["type"] == "horizontal_bar"                                                     # 9 slices → bars
    stacked = validate_chart({"type": "stacked_bar", "x": "Department", "series": "Doctor",
                              "y": ["Appointments", "Revenue"]}, COLS, ROWS)
    assert stacked["y"] == ["Appointments"] and stacked["series"] == "Doctor"
