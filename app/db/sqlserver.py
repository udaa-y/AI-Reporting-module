"""
app/db/sqlserver.py
────────────────────
SQL Server connection management using SQLAlchemy + pyodbc.

Design notes:
  • SQL Server has no mature native async Python driver, so we use a
    synchronous SQLAlchemy engine with connection pooling.
  • All FastAPI endpoints call execute_query() via run_in_threadpool()
    to avoid blocking the async event loop.
  • The engine is a singleton — created once at startup, shared across
    all requests via the get_db() dependency.
"""

import logging
from contextlib import contextmanager
from typing import Any, Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Engine (singleton) ────────────────────────────────────────────────────────

def _create_sqlserver_engine():
    engine = create_engine(
        settings.sqlserver_connection_string,
        poolclass=QueuePool,
        pool_size=settings.sqlserver_pool_size,
        max_overflow=settings.sqlserver_max_overflow,
        pool_timeout=30,
        pool_pre_ping=True,          # Drop stale connections automatically
        pool_recycle=1800,           # Recycle connections every 30 minutes
        echo=settings.debug,         # Log SQL in debug mode
        connect_args={
            "timeout": 10,           # Connection establishment timeout (sec)
        },
    )

    # Set query governor timeout on each new connection
    @event.listens_for(engine, "connect")
    def set_query_timeout(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        # Kill any query that runs longer than SQLSERVER_QUERY_TIMEOUT seconds
        cursor.execute(
            f"SET QUERY_GOVERNOR_COST_LIMIT {settings.sqlserver_query_timeout * 1000}"
        )
        cursor.close()

    logger.info(
        "SQL Server engine created — pool_size=%d, max_overflow=%d",
        settings.sqlserver_pool_size,
        settings.sqlserver_max_overflow,
    )
    return engine


_engine = None


def get_engine():
    """Return the singleton SQL Server engine, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = _create_sqlserver_engine()
    return _engine


_SessionFactory = None


def get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SessionFactory


# ── Context Manager ───────────────────────────────────────────────────────────

@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Provide a transactional database session.
    Usage:
        with get_db_session() as session:
            results = session.execute(text("SELECT 1")).fetchall()
    """
    factory = get_session_factory()
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── FastAPI Dependency ─────────────────────────────────────────────────────────

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a DB session per request.
    Use with Depends(get_db).
    """
    with get_db_session() as session:
        yield session


# ── Cell Value Serializer ─────────────────────────────────────────────────────

def _serialize_cell(val: Any) -> Any:
    """Recursively convert database types (datetime, Decimal, UUID, bytes) to JSON primitives."""
    import datetime
    from decimal import Decimal
    import uuid

    if val is None:
        return None
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return int(val) if val % 1 == 0 else float(val)
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, bytes):
        return val.hex()
    return val


# ── Query Executor (to be called via run_in_threadpool) ──────────────────────

def execute_query_sync(
    sql: str,
    session: Session,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """
    Execute a validated T-SQL SELECT statement synchronously.

    Returns:
        {
            "columns": [...],
            "rows": [...],
            "row_count": int,
        }

    This function is SYNCHRONOUS and must be called via:
        await run_in_threadpool(execute_query_sync, sql, session)
    from async FastAPI routes.
    """
    effective_max = max_rows or settings.max_result_rows

    try:
        result = session.execute(text(sql))
        columns = list(result.keys())
        raw_rows = result.fetchmany(effective_max)

        serialized_rows = []
        for row in raw_rows:
            row_dict = {}
            for col, val in zip(columns, row):
                row_dict[col] = _serialize_cell(val)
            serialized_rows.append(row_dict)

        return {
            "columns": columns,
            "rows": serialized_rows,
            "row_count": len(serialized_rows),
        }
    except Exception as exc:
        logger.error("SQL execution error: %s | SQL: %s", exc, sql)
        raise


# ── Health Check ──────────────────────────────────────────────────────────────

def check_sqlserver_health() -> bool:
    """
    Perform a lightweight connectivity check.
    Returns True if SQL Server is reachable, False otherwise.
    """
    try:
        with get_db_session() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("SQL Server health check failed: %s", exc)
        return False


# ── Schema Introspection ──────────────────────────────────────────────────────

def get_table_names(schema: str = "dbo") -> list[str]:
    """Return all user table names in the specified schema."""
    sql = """
        SELECT TABLE_NAME
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_TYPE = 'BASE TABLE'
          AND TABLE_SCHEMA = :schema
        ORDER BY TABLE_NAME
    """
    with get_db_session() as session:
        result = session.execute(text(sql), {"schema": schema})
        return [row[0] for row in result.fetchall()]


def get_table_columns(table_name: str, schema: str = "dbo") -> list[dict]:
    """
    Return column metadata for a given table.
    Used to build the initial semantic layer embeddings.
    """
    sql = """
        SELECT
            c.COLUMN_NAME,
            c.DATA_TYPE,
            c.IS_NULLABLE,
            c.CHARACTER_MAXIMUM_LENGTH,
            c.NUMERIC_PRECISION,
            ep.value AS column_description
        FROM INFORMATION_SCHEMA.COLUMNS c
        LEFT JOIN sys.extended_properties ep
            ON ep.major_id = OBJECT_ID(:full_table)
            AND ep.minor_id = c.ORDINAL_POSITION
            AND ep.name = 'MS_Description'
        WHERE c.TABLE_NAME = :table_name
          AND c.TABLE_SCHEMA = :schema
        ORDER BY c.ORDINAL_POSITION
    """
    with get_db_session() as session:
        result = session.execute(
            text(sql),
            {
                "full_table": f"{schema}.{table_name}",
                "table_name": table_name,
                "schema": schema,
            },
        )
        rows = result.fetchall()
        keys = result.keys()
        return [dict(zip(keys, row)) for row in rows]
