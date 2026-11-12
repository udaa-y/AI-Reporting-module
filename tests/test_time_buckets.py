"""
tests/test_time_buckets.py
───────────────────────────
Chart time grouping: bucket boundaries, labels, drill path and limits.
"""

import datetime as dt

from app.dashboard.time_buckets import (
    DRILL_TO, MAX_BUCKETS, auto_granularity, bucket_start, buckets, count_buckets,
)

D = dt.date


def test_weeks_start_on_monday_across_year_end():
    b = buckets(D(2025, 12, 30), D(2026, 1, 12), "week")
    assert [x["key"] for x in b] == ["2025-12-29", "2026-01-05", "2026-01-12"]
    assert b[0]["label"] == "29 Dec–4 Jan"
    # Clipped to the selected range and flagged as partial
    assert (b[0]["start"], b[0]["partial"]) == ("2025-12-30", True)
    assert (b[-1]["end"], b[-1]["partial"]) == ("2026-01-12", True)


def test_months_include_empty_buckets_and_leap_february():
    b = buckets(D(2024, 1, 15), D(2024, 3, 10), "month")
    assert [x["label"] for x in b] == ["Jan 2024", "Feb 2024", "Mar 2024"]
    assert b[1]["end"] == "2024-02-29" and not b[1]["partial"]


def test_bucket_start_matches_sql_grouping():
    d = D(2026, 11, 5)   # a Thursday
    assert bucket_start(d, "week") == D(2026, 11, 2)
    assert bucket_start(d, "month") == D(2026, 11, 1)
    assert bucket_start(d, "year") == D(2026, 1, 1)


def test_drill_path_and_auto_granularity():
    assert DRILL_TO == {"year": "month", "month": "week", "week": "day", "day": None}
    assert auto_granularity(D(2026, 10, 6), D(2026, 11, 4)) == "day"
    assert auto_granularity(D(2026, 1, 1), D(2026, 4, 30)) == "week"
    assert auto_granularity(D(2025, 11, 5), D(2026, 11, 4)) == "month"
    assert auto_granularity(D(2020, 1, 1), D(2026, 11, 4)) == "year"


def test_bucket_limit():
    assert count_buckets(D(2025, 1, 1), D(2025, 12, 31), "day") == 365
    assert count_buckets(D(2020, 1, 1), D(2026, 1, 1), "day") > MAX_BUCKETS
