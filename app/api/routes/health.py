"""
app/api/routes/health.py
─────────────────────────
Health check endpoints for operational monitoring.

GET /health        — Basic liveness check
GET /health/ready  — Readiness check (DB + dependencies)
"""

import logging
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def liveness():
    """Basic liveness probe — always returns 200 if the server is running."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness():
    """
    Readiness probe — checks all critical dependencies.
    Returns 200 if ready, 503 if any dependency is down.
    """
    checks = {}

    # ── SQL Server ────────────────────────────────────────────────────────────
    try:
        from app.db.sqlserver import check_sqlserver_health
        checks["sqlserver"] = "ok" if check_sqlserver_health() else "unavailable"
    except Exception as exc:
        checks["sqlserver"] = f"error: {str(exc)[:80]}"

    # ── Redis ─────────────────────────────────────────────────────────────────
    try:
        import redis as redis_lib
        from app.config import get_settings
        r = redis_lib.from_url(get_settings().redis_url, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable"

    # ── Qdrant ────────────────────────────────────────────────────────────────
    try:
        from qdrant_client import QdrantClient
        from app.config import get_settings
        s = get_settings()
        client = QdrantClient(url=s.qdrant_url, timeout=2)
        client.get_collections()
        checks["qdrant"] = "ok"
    except Exception:
        checks["qdrant"] = "unavailable"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
        status_code=status_code,
    )
