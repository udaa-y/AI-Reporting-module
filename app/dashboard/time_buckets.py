"""
app/dashboard/time_buckets.py
──────────────────────────────
Time granularities for charts: how dates are grouped (SQL + Python), labelled,
and which level a click drills down to.

  Year → Month → Week (Monday–Sunday) → Day → individual appointments
"""

import datetime as dt
from typing import Dict, List, Optional

MAX_BUCKETS = 400       # More points than this is unreadable — pick a coarser level

# Monday-based week start that works whatever SQL Server's DATEFIRST setting is.
_WEEK_START_SQL = "DATEADD(day, -((DATEPART(weekday, {d}) + @@DATEFIRST - 2) % 7), {d})"

BUCKET_SQL: Dict[str, str] = {
    "day": "{d}",
    "week": _WEEK_START_SQL,
    "month": "DATEFROMPARTS(YEAR({d}), MONTH({d}), 1)",
    "year": "DATEFROMPARTS(YEAR({d}), 1, 1)",
}

DRILL_TO: Dict[str, Optional[str]] = {"year": "month", "month": "week", "week": "day", "day": None}

GRANULARITIES = ("day", "week", "month", "year")


def bucket_sql(granularity: str, date_expr: str) -> str:
    return BUCKET_SQL[granularity].format(d=date_expr)


def bucket_start(d: dt.date, granularity: str) -> dt.date:
    if granularity == "day":
        return d
    if granularity == "week":
        return d - dt.timedelta(days=d.weekday())
    if granularity == "month":
        return d.replace(day=1)
    return d.replace(month=1, day=1)


def _next_start(start: dt.date, granularity: str) -> dt.date:
    if granularity == "day":
        return start + dt.timedelta(days=1)
    if granularity == "week":
        return start + dt.timedelta(days=7)
    if granularity == "month":
        return (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start.replace(year=start.year + 1)


def count_buckets(from_date: dt.date, to_date: dt.date, granularity: str) -> int:
    n, start = 0, bucket_start(from_date, granularity)
    while start <= to_date and n <= MAX_BUCKETS:
        n += 1
        start = _next_start(start, granularity)
    return n


def auto_granularity(from_date: dt.date, to_date: dt.date) -> str:
    days = (to_date - from_date).days + 1
    if days <= 45:
        return "day"
    if days <= 7 * 26:
        return "week"
    if days <= 366 * 3:
        return "month"
    return "year"


def _label(start: dt.date, end: dt.date, granularity: str) -> str:
    if granularity == "day":
        return f"{start:%a} {start.day} {start:%b}"
    if granularity == "week":
        if start.month == end.month:
            return f"{start.day}–{end.day} {end:%b}"
        return f"{start.day} {start:%b}–{end.day} {end:%b}"
    if granularity == "month":
        return f"{start:%b %Y}"
    return f"{start:%Y}"


def buckets(from_date: dt.date, to_date: dt.date, granularity: str) -> List[dict]:
    """Every bucket in the range (empty ones included), clipped to the range."""
    out, start = [], bucket_start(from_date, granularity)
    while start <= to_date:
        nxt = _next_start(start, granularity)
        lo, hi = max(start, from_date), min(nxt - dt.timedelta(days=1), to_date)
        out.append({
            "key": start.isoformat(),               # matches the SQL bucket value
            "start": lo.isoformat(),
            "end": hi.isoformat(),
            "label": _label(start, nxt - dt.timedelta(days=1), granularity),
            "partial": lo != start or hi != nxt - dt.timedelta(days=1),
        })
        start = nxt
    return out
