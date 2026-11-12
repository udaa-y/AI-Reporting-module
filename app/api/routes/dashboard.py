"""
app/api/routes/dashboard.py
────────────────────────────
Home-page KPIs from fixed, hand-written SQL — no LLM involved, so they are
instant, free and always consistent.

  GET /dashboard/kpis?from_date=2025-01-01&to_date=2025-01-31&compare=period&branch_id=3
  GET /dashboard/branches      branches this user may select (dbo.BranchMaster)

Every KPI is returned for the selected range AND a comparison range, so the
UI can show the change (e.g. "+12%"):
  compare=period (default)  the same number of days immediately before
  compare=year              the same dates one year earlier (for MTD/QTD/YTD)

Each table is scanned once for both ranges using conditional aggregation.

Branch: every query filters on its OWN table's BranchId. Non-Super-Admins are
always held to their own branch (app/security/auth.resolve_branch).
"""

import asyncio
import datetime as dt
import logging
import time
from decimal import Decimal
from typing import Any, Dict, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import text

from app.db.sqlserver import get_db_session
from app.security.auth import CurrentUser, require_user, resolve_branch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

MAX_RANGE_DAYS = 3660          # ~10 years
CACHE_TTL_SECONDS = 60         # Switching between presets feels instant

# Half-open ranges: current = [:cur_from, :cur_to), previous = [:prev_from, :prev_to)
CUR = "{col} >= :cur_from AND {col} < :cur_to"
PREV = "{col} >= :prev_from AND {col} < :prev_to"


def _in(range_sql: str, col: str) -> str:
    return range_sql.format(col=col)


VISIT = "a.VisitDate"
IN_EITHER_RANGE = f"(({_in(CUR, VISIT)}) OR ({_in(PREV, VISIT)}))"

# "{branch_X}" placeholders become "AND X.BranchId = :branch_id" for one branch, or "" for all.
BRANCH_ALIASES = ("a", "p", "sp", "mp")

APPOINTMENTS_SQL = f"""
SELECT
    SUM(CASE WHEN {_in(CUR, VISIT)}  THEN 1 ELSE 0 END)              AS appointments_cur,
    SUM(CASE WHEN {_in(PREV, VISIT)} THEN 1 ELSE 0 END)              AS appointments_prev,
    COUNT(DISTINCT CASE WHEN {_in(CUR, VISIT)}  THEN a.PatientId END) AS patients_seen_cur,
    COUNT(DISTINCT CASE WHEN {_in(PREV, VISIT)} THEN a.PatientId END) AS patients_seen_prev,
    COUNT(DISTINCT CASE WHEN {_in(CUR, VISIT)}  THEN a.DoctorId END)  AS active_doctors_cur,
    COUNT(DISTINCT CASE WHEN {_in(PREV, VISIT)} THEN a.DoctorId END)  AS active_doctors_prev,
    SUM(CASE WHEN {_in(CUR, VISIT)}  AND a.ParentAppointment IS NOT NULL THEN 1 ELSE 0 END) AS follow_ups_cur,
    SUM(CASE WHEN {_in(PREV, VISIT)} AND a.ParentAppointment IS NOT NULL THEN 1 ELSE 0 END) AS follow_ups_prev
FROM dbo.Appointment a
WHERE {IN_EITHER_RANGE} {{branch_a}}
"""

PATIENTS_SQL = f"""
SELECT
    SUM(CASE WHEN {_in(CUR, "p.RegisteredOn")}  THEN 1 ELSE 0 END)   AS new_registrations_cur,
    SUM(CASE WHEN {_in(PREV, "p.RegisteredOn")} THEN 1 ELSE 0 END)   AS new_registrations_prev,
    -- Registered patients as of the end of each range (undated rows always count)
    SUM(CASE WHEN p.RegisteredOn IS NULL OR p.RegisteredOn < :cur_to  THEN 1 ELSE 0 END) AS registered_patients_cur,
    SUM(CASE WHEN p.RegisteredOn IS NULL OR p.RegisteredOn < :prev_to THEN 1 ELSE 0 END) AS registered_patients_prev
FROM dbo.Patient p
WHERE 1 = 1 {{branch_p}}
"""

# Assumes ServicePrescription.Cost is the line amount. If Cost is a unit price,
# change both SUMs to ISNULL(sp.Cost, 0) * ISNULL(sp.Quantity, 1).
SERVICES_SQL = f"""
SELECT
    SUM(CASE WHEN {_in(CUR, VISIT)}  THEN ISNULL(sp.Cost, 0) ELSE 0 END) AS service_revenue_cur,
    SUM(CASE WHEN {_in(PREV, VISIT)} THEN ISNULL(sp.Cost, 0) ELSE 0 END) AS service_revenue_prev
FROM dbo.ServicePrescription sp
INNER JOIN dbo.Appointment a ON a.Id = sp.Appointment
WHERE {IN_EITHER_RANGE} {{branch_sp}}
"""

MEDICINES_SQL = f"""
SELECT
    SUM(CASE WHEN {_in(CUR, VISIT)}  THEN 1 ELSE 0 END)              AS medicines_prescribed_cur,
    SUM(CASE WHEN {_in(PREV, VISIT)} THEN 1 ELSE 0 END)              AS medicines_prescribed_prev
FROM dbo.MedicinePrescription mp
INNER JOIN dbo.Appointment a ON a.Id = mp.Appointment
WHERE {IN_EITHER_RANGE} {{branch_mp}}
"""

# (key, label, format, description) — the order here is the order of the cards.
KPI_DEFINITIONS = [
    ("appointments", "Total Appointments", "number", "Visits scheduled in the period"),
    ("patients_seen", "Patients Seen", "number", "Unique patients with a visit"),
    ("new_registrations", "New Registrations", "number", "Patients registered in the period"),
    ("registered_patients", "Total Registered Patients", "number", "All patients registered up to the end date"),
    ("follow_ups", "Follow-up Visits", "number", "Visits linked to an earlier appointment"),
    ("active_doctors", "Active Doctors", "number", "Doctors who saw at least one patient"),
    ("service_revenue", "Service Revenue", "currency", "Billed services and procedures"),
    ("medicines_prescribed", "Medicines Prescribed", "number", "Medicine lines prescribed"),
]

_cache: Dict[Tuple[dt.date, dt.date, str, Optional[int]], Tuple[float, dict]] = {}


def _with_branch(sql: str, branch_id: Optional[int]):
    fill = {f"branch_{a}": (f"AND {a}.BranchId = :branch_id" if branch_id is not None else "")
            for a in BRANCH_ALIASES}
    return text(sql.format(**fill))


def _one_year_earlier(d: dt.date) -> dt.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:              # 29 Feb → 28 Feb
        return d.replace(year=d.year - 1, day=28)


def comparison_range(from_date: dt.date, to_date: dt.date, compare: str) -> Tuple[dt.date, dt.date]:
    """Inclusive (from, to) of the range the selected one is compared with."""
    if compare == "year":
        return _one_year_earlier(from_date), _one_year_earlier(to_date)
    days = (to_date - from_date).days + 1
    return from_date - dt.timedelta(days=days), from_date - dt.timedelta(days=1)


def _number(value: Any) -> Any:
    if value is None:
        return 0
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    return value


def _run(sql, params: dict) -> dict:
    with get_db_session() as session:
        row = session.execute(sql, params).mappings().first()
        return {k: _number(v) for k, v in (row or {}).items()}


@router.get("/kpis")
async def get_kpis(
    from_date: dt.date = Query(..., description="Start date (inclusive), YYYY-MM-DD"),
    to_date: dt.date = Query(..., description="End date (inclusive), YYYY-MM-DD"),
    compare: Literal["period", "year"] = Query("period", description="Comparison range"),
    branch_id: Optional[int] = Query(None, description="Branch (empty = all; Super Admin only)"),
    user: CurrentUser = Depends(require_user),
):
    branch_id = resolve_branch(user, branch_id)
    if from_date > to_date:
        raise HTTPException(status_code=400, detail="from_date must be on or before to_date.")
    days = (to_date - from_date).days + 1
    if days > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"Date range is limited to {MAX_RANGE_DAYS} days.")

    cache_key = (from_date, to_date, compare, branch_id)
    cached = _cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    prev_from, prev_to = comparison_range(from_date, to_date, compare)
    one_day = dt.timedelta(days=1)
    params = {
        "cur_from": from_date, "cur_to": to_date + one_day,
        "prev_from": prev_from, "prev_to": prev_to + one_day,
    }
    if branch_id is not None:
        params["branch_id"] = branch_id

    start = time.perf_counter()
    try:
        # Independent queries run in parallel, each on its own pooled connection.
        results = await asyncio.gather(*(
            run_in_threadpool(_run, _with_branch(sql, branch_id), params)
            for sql in (APPOINTMENTS_SQL, PATIENTS_SQL, SERVICES_SQL, MEDICINES_SQL)
        ))
    except Exception as exc:
        logger.exception("[Dashboard] KPI query failed: %s", exc)
        raise HTTPException(status_code=503, detail="Could not load KPIs from the database.")

    values: Dict[str, Any] = {}
    for r in results:
        values.update(r)

    kpis = []
    for key, label, fmt, description in KPI_DEFINITIONS:
        current, previous = values.get(f"{key}_cur", 0), values.get(f"{key}_prev", 0)
        change_pct = round((current - previous) / previous * 100, 1) if previous else None
        kpis.append({
            "key": key, "label": label, "format": fmt, "description": description,
            "value": current, "previous": previous, "change_pct": change_pct,
        })

    response = {
        "range": {"from": from_date.isoformat(), "to": to_date.isoformat(), "days": days},
        "branch_id": branch_id,
        "previous_range": {"from": prev_from.isoformat(), "to": prev_to.isoformat(), "compare": compare},
        "kpis": kpis,
        "query_time_ms": round((time.perf_counter() - start) * 1000),
    }
    _cache[cache_key] = (time.monotonic(), response)
    if len(_cache) > 200:
        _cache.clear()
    return response


@router.get("/branches")
async def branches(user: CurrentUser = Depends(require_user)):
    """Branches for the top-bar selector: all of them for a Super Admin, else the user's own."""
    sql = """
SELECT BranchId, BranchName, BranchCode, BranchRegion, HQ_Branch
FROM dbo.BranchMaster
{where}
ORDER BY CASE WHEN HQ_Branch = 1 THEN 0 ELSE 1 END, BranchName
"""
    if user.is_superadmin:
        stmt, params = text(sql.format(where="")), {}
    else:
        stmt, params = text(sql.format(where="WHERE BranchId = :branch_id")), {
            "branch_id": resolve_branch(user, None)}

    def fetch():
        with get_db_session() as session:
            return session.execute(stmt, params).mappings().all()

    try:
        rows = await run_in_threadpool(fetch)
    except Exception as exc:
        logger.exception("[Dashboard] Branch list failed: %s", exc)
        raise HTTPException(503, "Could not load branches from the database.")
    return {
        "can_switch": user.is_superadmin,       # Super Admins also get "All branches"
        "branches": [
            {"id": r["BranchId"], "name": r["BranchName"], "code": r["BranchCode"],
             "region": r["BranchRegion"], "is_hq": bool(r["HQ_Branch"])}
            for r in rows
        ],
    }