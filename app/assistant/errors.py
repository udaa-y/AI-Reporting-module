"""
app/assistant/errors.py
────────────────────────
Turns exceptions from the LLM provider / database into a clear error code and
message, so "something went wrong" always says WHAT went wrong.

  llm_credits      provider account out of credits (HTTP 402)
  llm_rate_limit   too many requests / free or daily quota used up (429)
  llm_auth         API key rejected (401/403)
  llm_model        model name not found at the provider (404)
  llm_bad_request  provider refused the request (400): unsupported feature, prompt too long…
  llm_unreachable  network problem or timeout reaching the provider
  database         SQL Server error
  internal         anything else (see the server log)
"""

from typing import Dict

_MESSAGES = {
    "llm_credits": "The AI provider account has run out of credits. Top up the account or switch to a paid API key.",
    "llm_rate_limit": "The AI provider is limiting requests (too many requests, or the free/daily quota is used up). Wait a minute, or use a paid plan.",
    "llm_auth": "The AI provider rejected the API key. Check OPENAI_API_KEY (and OPENAI_BASE_URL) in .env.",
    "llm_model": "The configured AI model was not found at the provider. Check the model names in .env.",
    "llm_bad_request": "The AI provider refused the request (the model may not support a feature we use, or the prompt is too long).",
    "llm_unreachable": "Couldn't reach the AI provider (network problem or timeout).",
    "database": "The database query failed.",
    "internal": "Something went wrong while answering.",
}


def _chain(exc: BaseException):
    """The exception and everything it was raised from (LangChain/LangGraph may wrap errors)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def describe_error(exc: BaseException) -> Dict[str, str]:
    """{code, message, detail} for an exception raised while answering."""
    import openai
    from sqlalchemy.exc import SQLAlchemyError

    code = "internal"
    for e in _chain(exc):
        status = getattr(e, "status_code", None)
        text = str(e).lower()
        if status == 402 or "insufficient credits" in text or "requires more credits" in text:
            code = "llm_credits"
        elif isinstance(e, openai.RateLimitError) or status == 429:
            code = "llm_rate_limit"
        elif isinstance(e, (openai.AuthenticationError, openai.PermissionDeniedError)) or status in (401, 403):
            code = "llm_auth"
        elif isinstance(e, openai.NotFoundError) or status == 404:
            code = "llm_model"
        elif isinstance(e, openai.BadRequestError) or status == 400:
            code = "llm_bad_request"
        elif isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
            code = "llm_unreachable"
        elif isinstance(e, SQLAlchemyError):
            code = "database"
        else:
            continue
        exc = e
        break
    return {
        "code": code,
        "message": _MESSAGES[code],
        "detail": f"{type(exc).__name__}: {exc}"[:600],
    }
