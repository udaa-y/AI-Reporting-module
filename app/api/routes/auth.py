"""
app/api/routes/auth.py
───────────────────────
  POST /auth/login    { username, password } → sets the session cookie
  POST /auth/logout   ends the session
  GET  /auth/me       the signed-in user (401 if not signed in)
"""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.assistant.scopes import scopes_for
from app.config import get_settings
from app.security.auth import CurrentUser, authenticate, end_session, require_user

router = APIRouter(prefix="/auth", tags=["Auth"])
settings = get_settings()


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


def _me(user: CurrentUser) -> dict:
    return {**user.public(), "scopes": [s["key"] for s in scopes_for(user.role)]}


@router.post("/login")
async def login(req: LoginRequest, response: Response):
    user, token = await run_in_threadpool(authenticate, req.username, req.password)
    response.set_cookie(
        settings.session_cookie_name, token,
        max_age=settings.session_max_hours * 3600,
        httponly=True, samesite="lax", secure=settings.session_cookie_secure, path="/",
    )
    return _me(user)


@router.post("/logout")
async def logout(request: Request, response: Response):
    await run_in_threadpool(end_session, request.cookies.get(settings.session_cookie_name))
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"status": "signed_out"}


@router.get("/me")
async def me(user: CurrentUser = Depends(require_user)):
    return _me(user)
