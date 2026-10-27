"""
app/main.py
────────────
FastAPI Application Entry Point

Configures the app, registers routers, sets up logging, and handles
startup/shutdown lifecycle events (DB pool warm-up, graph pre-load).
"""

import logging
import os
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api.routes.health import router as health_router
from app.api.routes.query import router as query_router
from app.config import get_settings

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

settings = get_settings()

# ── App Factory ───────────────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=(
            "AI-powered natural language analytics engine. "
            "Ask business questions in plain English, get SQL-powered answers."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.debug else ["https://yourdomain.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request Timing Middleware ─────────────────────────────────────────────
    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000)
        response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
        return response

    # ── Global Exception Handler ──────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "An internal error occurred."},
        )

    # ── Startup ───────────────────────────────────────────────────────────────
    @app.on_event("startup")
    async def startup():
        logger.info("=" * 60)
        logger.info("  %s — Starting up", settings.app_name)
        logger.info("  Environment : %s", settings.app_env)
        logger.info("  LLM (SQL)   : %s", settings.llm_sql_model)
        logger.info("  LLM (Intent): %s", settings.llm_intent_model)
        logger.info("=" * 60)

        # Pre-warm the LangGraph agent (loads LLM clients, semantic loader, etc.)
        try:
            from app.agent.graph import get_graph
            get_graph()
            logger.info("[Startup] LangGraph agent graph compiled and ready.")
        except Exception as exc:
            logger.warning("[Startup] Graph pre-warm failed: %s", exc)

        # Pre-warm SQL Server connection pool
        try:
            from app.db.sqlserver import get_engine
            engine = get_engine()
            # Touch the engine to validate connectivity (pool_pre_ping handles the rest)
            with engine.connect() as conn:
                from sqlalchemy import text
                conn.execute(text("SELECT 1"))
            logger.info("[Startup] SQL Server connection pool warmed up.")
        except Exception as exc:
            logger.warning("[Startup] SQL Server not reachable: %s", exc)

    # ── Shutdown ──────────────────────────────────────────────────────────────
    @app.on_event("shutdown")
    async def shutdown():
        try:
            from app.db.sqlserver import get_engine
            get_engine().dispose()
            logger.info("[Shutdown] SQL Server connection pool disposed.")
        except Exception:
            pass

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(query_router)
    app.include_router(health_router)

    @app.get("/", include_in_schema=False)
    async def root():
        frontend_index = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html"))
        if os.path.exists(frontend_index):
            return FileResponse(frontend_index)
        return {
            "service": settings.app_name,
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()


# ── Dev Server Entry Point ────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
    )
