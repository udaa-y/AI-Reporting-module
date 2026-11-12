"""
app/api/routes/appointment_charts.py
─────────────────────────────────────
Appointments chart for the Home page (fixed SQL, no LLM).

  GET  /dashboard/appointments/meta     filters + breakdowns + their options
  POST /dashboard/appointments/series   chart data for one drill level
  POST /dashboard/appointments/list     individual appointments (deepest drill)

Filters and breakdowns come from app/dashboard/dimensions.py and time
grouping from app/dashboard/time_buckets.py — edit those, not this file,
to add or remove parameters.
"""

import asyncio
import datetime as dt
import json
import logging
import time
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text

from app.api.routes.dashboard import comparison_range
from app.dashboard.dimensions import (
    DIMENSIONS, JOINS, LIST_COLUMNS, LIST_JOINS, NULL_LABEL, NULL_TOKEN,
)
from app.dashboard.time_buckets import (
    DRILL_TO, MAX_BUCKETS, auto_granularity, bucket_sql, buckets, count_buckets,
)
from app.db.sqlserver import get_db_session

from app.security.auth import CurrentUser, require_user, resolve_branch

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/appointments", tags=["Dashboard"])

TOP_N_SERIES = 7            # Breakdown shows the 7 largest groups + "Other"
CACHE_TTL_SECONDS = 60
META_TTL_SECONDS = 600
MAX_RANGE_DAYS = 3660

_cache: Dict[str, Tuple[float, Any]] = {}


# ── Request models ────────────────────────────────────────────────────────────

class ChartQuery(BaseModel):
    from_date: dt.date
    to_date: dt.date
    branch_id: Optional[int] = None     # empty = all branches (Super Admin only)
    filters: Dict[str, List[Any]] = Field(default_factory=dict,
                                          description="{dimension_key: [values]}")


class SeriesRequest(ChartQuery):
    granularity: Literal["auto", "day", "week", "month", "year"] = "auto"
    breakdown: Optional[str] = None
    compare: Literal["period", "year", "none"] = "period"


class ListRequest(ChartQuery):
    page: int = Field(1, ge=1)
    page_size: int = Field(25, ge=1, le=200)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cached(key: str, ttl: int):
    hit = _cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl else None


def _store(key: str, value: Any) -> None:
    if len(_cache) > 500:
        _cache.clear()
    _cache[key] = (time.monotonic(), value)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _fetch(sql: str, params: dict, expanding: List[str]) -> List[dict]:
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*(bindparam(name, expanding=True) for name in expanding))
    with get_db_session() as session:
        return [
            {k: _plain(v) for k, v in row.items()}
            for row in session.execute(stmt, params).mappings().all()
        ]


def _validate(q: ChartQuery) -> None:
    if q.from_date > q.to_date:
        raise HTTPException(400, "from_date must be on or before to_date.")
    if (q.to_date - q.from_date).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(400, f"Date range is limited to {MAX_RANGE_DAYS} days.")
    unknown = [k for k in q.filters if k not in DIMENSIONS or not DIMENSIONS[k].filter]
    if unknown:
        raise HTTPException(400, f"Unknown filter(s): {', '.join(unknown)}")


def _where(q: ChartQuery, date_from: dt.date, date_to: dt.date) -> Tuple[str, dict, List[str], set]:
    """WHERE clause for a date range + the selected filters (all values are bound)."""
    clauses = ["a.VisitDate >= :date_from AND a.VisitDate < :date_to"]
    params: Dict[str, Any] = {"date_from": date_from, "date_to": date_to + dt.timedelta(days=1)}

    if q.branch_id is not None:
        clauses.append("a.BranchId = :branch_id")
        params["branch_id"] = q.branch_id

    expanding, joins = [], set()
    for key, values in q.filters.items():
        if not values:
            continue
        dim = DIMENSIONS[key]
        joins.update(dim.joins)
        real = [v for v in values if v != NULL_TOKEN]
        parts = []
        if real:
            name = f"f_{key}"
            params[name] = real
            expanding.append(name)
            parts.append(f"{dim.value_sql} IN :{name}")
        if NULL_TOKEN in values:
            parts.append(f"{dim.value_sql} IS NULL")
        clauses.append("(" + " OR ".join(parts) + ")")
    return " AND ".join(clauses), params, expanding, joins


def _joins_sql(keys) -> str:
    return "\n".join(JOINS[k] for k in JOINS if k in keys)     # stable order


def _series_sql(q: SeriesRequest, granularity: str, date_from: dt.date, date_to: dt.date,
                breakdown: Optional[str]):
    where, params, expanding, joins = _where(q, date_from, date_to)
    bucket = bucket_sql(granularity, "a.VisitDate")
    select_dim, group_dim = "", ""
    if breakdown:
        dim = DIMENSIONS[breakdown]
        joins.update(dim.joins)
        select_dim = f", {dim.value_sql} AS dim_value, MAX({dim.label_expr}) AS dim_label"
        group_dim = f", {dim.value_sql}"
    sql = f"""
SELECT {bucket} AS bucket{select_dim}, COUNT(*) AS n
FROM dbo.Appointment a
{_joins_sql(joins)}
WHERE {where}
GROUP BY {bucket}{group_dim}
"""
    return sql, params, expanding


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/meta")
async def meta(branch_id: Optional[int] = Query(None), user: CurrentUser = Depends(require_user)):
    """Filters and breakdowns available to the UI, with their selectable options."""
    branch_id = resolve_branch(user, branch_id)
    cache_key = f"meta:{branch_id}"
    hit = _cached("meta", META_TTL_SECONDS)
    if hit:
        return hit
    
    where = "WHERE a.BranchId = :branch_id" if branch_id is not None else ""
    params = {"branch_id": branch_id} if branch_id is not None else {}

    async def options(key: str, dim) -> list:
        sql = f"""
SELECT TOP ({dim.max_options}) {dim.value_sql} AS value, MAX({dim.label_expr}) AS label, COUNT(*) AS n
FROM dbo.Appointment a
{_joins_sql(set(dim.joins))}
{where}
GROUP BY {dim.value_sql}
ORDER BY COUNT(*) DESC
"""
        rows = await run_in_threadpool(_fetch, sql, params, [])

        opts = [
            {"value": NULL_TOKEN if r["value"] is None else r["value"],
             "label": NULL_LABEL if r["label"] in (None, "") else str(r["label"]),
             "count": r["n"]}
            for r in rows
        ]
        return sorted(opts, key=lambda o: o["label"].lower())

    filter_keys = [k for k, d in DIMENSIONS.items() if d.filter]
    try:
        option_lists = await asyncio.gather(*(options(k, DIMENSIONS[k]) for k in filter_keys))
    except Exception as exc:
        logger.exception("[Appointments] meta failed: %s", exc)
        raise HTTPException(503, "Could not load filter options from the database.")

    result = {
        "filters": [
            {"key": k, "label": DIMENSIONS[k].label, "options": opts}
            for k, opts in zip(filter_keys, option_lists)
        ],
        "breakdowns": [
            {"key": k, "label": d.label} for k, d in DIMENSIONS.items()
            if d.breakdown and not (d.all_branches_only and branch_id is not None)
        ],
        "granularities": ["year", "month", "week", "day"],
        "max_buckets": MAX_BUCKETS,
    }
    _store(cache_key, result)
    return result


@router.post("/series")
async def series(req: SeriesRequest, user: CurrentUser = Depends(require_user)):
    req.branch_id = resolve_branch(user, req.branch_id)
    _validate(req)
    if req.breakdown and (req.breakdown not in DIMENSIONS or not DIMENSIONS[req.breakdown].breakdown):
        raise HTTPException(400, f"Unknown breakdown: {req.breakdown}")

    granularity = (auto_granularity(req.from_date, req.to_date)
                   if req.granularity == "auto" else req.granularity)
    if count_buckets(req.from_date, req.to_date, granularity) > MAX_BUCKETS:
        raise HTTPException(400, f"Too many {granularity}s in this range to chart. "
                                 "Choose a coarser grouping or a shorter range.")

    cache_key = "series:" + json.dumps(req.model_dump(mode="json"), sort_keys=True, default=str)
    hit = _cached(cache_key, CACHE_TTL_SECONDS)
    if hit:
        return hit

    current_buckets = buckets(req.from_date, req.to_date, granularity)
    prev_range = None
    jobs = [run_in_threadpool(_fetch, *_series_sql(req, granularity, req.from_date,
                                                    req.to_date, req.breakdown))]
    if req.compare != "none":
        prev_from, prev_to = comparison_range(req.from_date, req.to_date, req.compare)
        prev_range = {"from": prev_from.isoformat(), "to": prev_to.isoformat(), "compare": req.compare}
        jobs.append(run_in_threadpool(_fetch, *_series_sql(req, granularity, prev_from, prev_to, None)))

    start = time.perf_counter()
    try:
        results = await asyncio.gather(*jobs)
    except Exception as exc:
        logger.exception("[Appointments] series failed: %s", exc)
        raise HTTPException(503, "Could not load appointment data from the database.")

    index = {b["key"]: i for i, b in enumerate(current_buckets)}
    totals = [0] * len(current_buckets)

    # ── Current period (optionally broken down) ──────────────────────────────
    groups: Dict[Any, Dict[str, Any]] = {}
    for r in results[0]:
        i = index.get(str(r["bucket"])[:10])
        if i is None:
            continue
        totals[i] += r["n"]
        if req.breakdown:
            value = NULL_TOKEN if r["dim_value"] is None else r["dim_value"]
            g = groups.setdefault(value, {
                "key": str(value),
                "name": NULL_LABEL if r["dim_label"] in (None, "") else str(r["dim_label"]),
                "data": [0] * len(current_buckets), "total": 0,
            })
            g["data"][i] += r["n"]
            g["total"] += r["n"]

    if req.breakdown:
        ranked = sorted(groups.values(), key=lambda g: g["total"], reverse=True)
        series_out = [{k: g[k] for k in ("key", "name", "data")} for g in ranked[:TOP_N_SERIES]]
        rest = ranked[TOP_N_SERIES:]
        if rest:
            other = [0] * len(current_buckets)
            for g in rest:
                other = [x + y for x, y in zip(other, g["data"])]
            series_out.append({"key": "__other__", "name": f"Other ({len(rest)})", "data": other})
    else:
        series_out = [{"key": "appointments", "name": "Appointments", "data": totals}]

    # ── Previous period, aligned bucket-by-bucket ────────────────────────────
    previous, previous_total = None, None
    if prev_range:
        prev_buckets = buckets(dt.date.fromisoformat(prev_range["from"]),
                               dt.date.fromisoformat(prev_range["to"]), granularity)
        prev_index = {b["key"]: i for i, b in enumerate(prev_buckets)}
        prev_counts = [0] * len(prev_buckets)
        for r in results[1]:
            i = prev_index.get(str(r["bucket"])[:10])
            if i is not None:
                prev_counts[i] += r["n"]
        previous = [prev_counts[i] if i < len(prev_counts) else None
                    for i in range(len(current_buckets))]
        previous_total = sum(prev_counts)
        for i, b in enumerate(current_buckets):
            if i < len(prev_buckets):
                b["previous_label"] = prev_buckets[i]["label"]

    total = sum(totals)
    days = (req.to_date - req.from_date).days + 1
    peak_i = max(range(len(totals)), key=totals.__getitem__) if totals else None
    response = {
        "granularity": granularity,
        "drill_to": DRILL_TO[granularity],
        "range": {"from": req.from_date.isoformat(), "to": req.to_date.isoformat(), "days": days},
        "previous_range": prev_range,
        "buckets": current_buckets,
        "series": series_out,
        "totals": totals,
        "previous": previous,
        "summary": {
            "total": total,
            "previous_total": previous_total,
            "growth_pct": (round((total - previous_total) / previous_total * 100, 1)
                           if previous_total else None),
            "avg_per_day": round(total / days, 1),
            "avg_per_week": round(total / days * 7, 1),
            "avg_per_month": round(total / days * 30.44, 1),
            "peak": ({"label": current_buckets[peak_i]["label"], "value": totals[peak_i]}
                     if peak_i is not None and total else None),
        },
        "query_time_ms": round((time.perf_counter() - start) * 1000),
    }
    _store(cache_key, response)
    return response


@router.post("/list")

async def appointment_list(req: ListRequest, user: CurrentUser = Depends(require_user)):
    """Individual appointments for the deepest drill level (paged)."""
    req.branch_id = resolve_branch(user, req.branch_id)
    _validate(req)

    where, params, expanding, joins = _where(req, req.from_date, req.to_date)
    joins.update(LIST_JOINS)
    columns = ",\n    ".join(f"{sql} AS {key}" for key, _, sql in LIST_COLUMNS)
    base = f"FROM dbo.Appointment a\n{_joins_sql(joins)}\nWHERE {where}"

    # page / page_size are validated ints (Pydantic, ge=1 / le=200), so they are
    # written into the SQL directly; some ODBC drivers reject bound OFFSET/FETCH.
    offset = int((req.page - 1) * req.page_size)
    limit = int(req.page_size)

    rows_sql = f"""
SELECT
    {columns}
{base}
ORDER BY a.VisitDate DESC, a.Id DESC
OFFSET {offset} ROWS FETCH NEXT {limit} ROWS ONLY
"""
    count_sql = f"SELECT COUNT(*) AS n {base}"

    def run_both():
        return _fetch(rows_sql, params, expanding), _fetch(count_sql, params, expanding)

    try:
        rows, count = await run_in_threadpool(run_both)
    except Exception as exc:
        logger.exception("[Appointments] list failed: %s", exc)
        # Show the database's own message (first line) — no data, just the error text.
        reason = str(getattr(exc, "orig", exc)).splitlines()[0][:300]
        raise HTTPException(503, f"Could not load appointments: {reason}")

    return {
        "columns": [{"key": k, "label": label} for k, label, _ in LIST_COLUMNS],
        "rows": rows,
        "total": count[0]["n"] if count else 0,
        "page": req.page,
        "page_size": req.page_size,
    }
