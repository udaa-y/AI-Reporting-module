"""
app/security/auth.py
─────────────────────
Login, server-side sessions and the FastAPI dependencies that protect routes.

  • Passwords: PBKDF2-SHA256 with a random salt (Python standard library).
  • Sessions: a random token in an httpOnly cookie; only its SHA-256 hash is
    stored (app.UserSession). Sessions end after SESSION_IDLE_HOURS without
    activity or SESSION_MAX_HOURS in total, or on logout.
  • Repeated wrong passwords lock the username for a few minutes.

Use in routes:  user = Depends(require_user)   /   Depends(require_admin)
"""

import base64
import datetime as dt
import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, func, insert, or_, select, update

from app.config import get_settings
from app.db.app_tables import employee_login, user_session
from app.db.sqlserver import get_db_session

logger = logging.getLogger(__name__)
settings = get_settings()

ROLES = ("superadmin", "admin", "staff")
_PBKDF2_ITERATIONS = 390_000


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    role: str
    employee_id: Optional[int]
    branch_id: Optional[int] = None

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "superadmin")

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "display_name": self.display_name,
                "role": self.role, "branch_id": self.branch_id,
                "can_switch_branch": self.is_superadmin}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)  # naive UTC, matches DATETIME2


# ── Passwords ─────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, digest_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except (ValueError, TypeError):
        return False


# ── Brute-force protection (per username, in memory) ─────────────────────────

_failures: Dict[str, Tuple[int, float]] = {}
_failures_lock = threading.Lock()


def _locked(username: str) -> bool:
    with _failures_lock:
        count, since = _failures.get(username, (0, 0.0))
        if count >= settings.login_max_failures:
            if time.monotonic() - since < settings.login_lockout_minutes * 60:
                return True
            _failures.pop(username, None)
        return False


def _record_failure(username: str) -> None:
    with _failures_lock:
        count, _ = _failures.get(username, (0, 0.0))
        _failures[username] = (count + 1, time.monotonic())


# ── Login / sessions ─────────────────────────────────────────────────────────

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(username: str, password: str) -> Tuple[CurrentUser, str]:
    """Check credentials and open a session. Returns (user, cookie token)."""
    username = username.strip().lower()
    if _locked(username):
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")

    with get_db_session() as s:
        row = s.execute(
            select(employee_login).where(func.lower(employee_login.c.Username) == username)
        ).mappings().first()
        if not row or not row["IsActive"] or not verify_password(password, row["PasswordHash"]):
            _record_failure(username)
            raise HTTPException(401, "Invalid username or password.")

        token = secrets.token_urlsafe(32)
        now = _now()
        # Tidy up this user's ended sessions so the table doesn't grow forever.
        s.execute(delete(user_session).where(
            user_session.c.LoginId == row["Id"],
            or_(user_session.c.ExpiresAt <= now, user_session.c.RevokedAt.is_not(None)),
        ))
        s.execute(insert(user_session).values(
            TokenHash=_token_hash(token), LoginId=row["Id"], CreatedAt=now, LastSeenAt=now,
            ExpiresAt=now + dt.timedelta(hours=settings.session_max_hours),
        ))
        s.execute(update(employee_login).where(employee_login.c.Id == row["Id"])
                  .values(LastLoginAt=now))

    with _failures_lock:
        _failures.pop(username, None)
    return _user_from_row(row), token


def end_session(token: Optional[str]) -> None:
    if not token:
        return
    token_hash = _token_hash(token)
    _session_cache.pop(token_hash, None)
    with get_db_session() as s:
        s.execute(update(user_session).where(user_session.c.TokenHash == token_hash)
                  .values(RevokedAt=_now()))


def _user_from_row(row) -> CurrentUser:
    return CurrentUser(
        id=row["Id"], username=row["Username"], display_name=row["DisplayName"],
        role=row["Role"] if row["Role"] in ROLES else "staff", employee_id=row["EmployeeId"], branch_id=row["BranchId"],
    )


# Validated sessions are cached briefly so every API call doesn't hit the DB.
_session_cache: Dict[str, Tuple[float, CurrentUser]] = {}
_CACHE_SECONDS = 30


def _load_session(token: str) -> Optional[CurrentUser]:
    token_hash = _token_hash(token)
    hit = _session_cache.get(token_hash)
    if hit and time.monotonic() - hit[0] < _CACHE_SECONDS:
        return hit[1]

    now = _now()
    idle_cutoff = now - dt.timedelta(hours=settings.session_idle_hours)
    with get_db_session() as s:
        row = s.execute(
            select(employee_login, user_session.c.LastSeenAt)
            .join(user_session, user_session.c.LoginId == employee_login.c.Id)
            .where(
                user_session.c.TokenHash == token_hash,
                user_session.c.RevokedAt.is_(None),
                user_session.c.ExpiresAt > now,
                user_session.c.LastSeenAt > idle_cutoff,
                employee_login.c.IsActive == True,  # noqa: E712 (renders "= 1" on SQL Server)
            )
        ).mappings().first()
        if not row:
            _session_cache.pop(token_hash, None)
            return None
        # Sliding inactivity window (written at most once a minute).
        if row["LastSeenAt"] < now - dt.timedelta(minutes=1):
            s.execute(update(user_session).where(user_session.c.TokenHash == token_hash)
                      .values(LastSeenAt=now))

    user = _user_from_row(row)
    if len(_session_cache) > 5000:
        _session_cache.clear()
    _session_cache[token_hash] = (time.monotonic(), user)
    return user


# ── FastAPI dependencies ─────────────────────────────────────────────────────

def require_user(request: Request) -> CurrentUser:
    token = request.cookies.get(settings.session_cookie_name)
    user = _load_session(token) if token else None
    if user is None:
        raise HTTPException(401, "Please sign in.")
    return user


def require_admin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(403, "Administrator access required.")
    return user

def require_superadmin(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    if not user.is_superadmin:
        raise HTTPException(403, "Super Admin access required.")
    return user


def resolve_branch(user: CurrentUser, requested: Optional[int]) -> Optional[int]:
    """
    The branch a request may see. Super Admins get what they asked for
    (None = all branches); everyone else is always held to their own branch.
    """
    if user.is_superadmin:
        return requested
    if user.branch_id is None:
        raise HTTPException(403, "Your login has no branch assigned. Ask a Super Admin to set one.")
    if requested is not None and requested != user.branch_id:
        raise HTTPException(403, "You can only view data for your own branch.")
    return user.branch_id    
