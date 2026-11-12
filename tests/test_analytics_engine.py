"""
tests/test_analytics_engine.py
───────────────────────────────
Unit tests for the deterministic analytics engine (no LLM, no database).
"""

import datetime as dt
import random

from app.analytics.engine import analyze, holt_forecast

TODAY = dt.date(2025, 10, 20)


def _visits(months=21, spike_month=8):
    random.seed(7)
    rows = []
    for i in range(1500):
        d = dt.date(2024, 1, 1) + dt.timedelta(days=random.randint(0, months * 30 + 20))
        service = random.choice(["Lab", "Radiology", "Consultation", "Procedure"])
        cost = random.choice([50, 80, 120, 200]) * (2.5 if d.month == spike_month else 1)
        rows.append({
            "VisitDate": d.isoformat(), "ServiceType": service, "Cost": cost,
            "PatientName": f"P{random.randint(1, 400)}",
            "DateOfBirth": (dt.date(1950, 1, 1) + dt.timedelta(days=random.randint(0, 25000))).isoformat(),
        })
    return rows


def test_empty_result():
    assert analyze(["A"], []) == {"facts": [], "highlights": {}, "analysis_types": []}


def test_full_row_level_analysis():
    rows = _visits()
    out = analyze(list(rows[0]), rows, today=TODAY)
    types = set(out["analysis_types"])
    assert {"kpi", "trend", "comparative", "anomaly", "forecast", "diagnostic",
            "performance", "patient"} <= types
    hl = out["highlights"]
    assert hl["trend"]["granularity"] == "month"
    # The running month (2025-10) must not appear as the "latest" period.
    assert hl["trend"]["latest"]["period"] == "2025-09"
    assert any(a["kind"] == "period" and a["label"].endswith("-08") for a in hl["anomalies"])
    assert len(hl["forecast"]["points"]) == 3
    assert hl["patients"]["age_bands_pct"]
    assert sum(b["share"] for b in hl["breakdowns"][0]["top"]) > 99


def test_preaggregated_month_strings_and_partial_month():
    rows = [{"VisitMonth": f"2025-{m:02d}", "Visits": 100 + m * 5 + (60 if m == 6 else 0)}
            for m in range(1, 11)]
    out = analyze(["VisitMonth", "Visits"], rows, today=TODAY)
    trend = out["highlights"]["trend"]
    assert trend["direction"] == "rising"
    assert trend["latest"]["period"] == "2025-09"           # 2025-10 still running
    anomalies = out["highlights"]["anomalies"]
    assert [a["label"] for a in anomalies] == ["2025-06"]   # detrended spike only


def test_year_month_integer_columns_give_yoy_and_ytd():
    rows = [{"Year": 2024 + (m > 12), "Month": (m - 1) % 12 + 1, "TotalCost": 1000 + m * 30}
            for m in range(1, 20)]
    out = analyze(["Year", "Month", "TotalCost"], rows, today=TODAY)
    trend = out["highlights"]["trend"]
    assert "yoy" in trend and "ytd" in trend
    labels = [k["label"] for k in out["highlights"]["kpis"]]
    assert "Total Year" not in labels and "Total TotalCost" in labels


def test_record_outliers_ignore_normal_price_points():
    rows = [{"Patient": f"P{i}", "Cost": c} for i, c in enumerate([50, 80, 120, 200] * 10 + [5000])]
    out = analyze(["Patient", "Cost"], rows, today=TODAY)
    records = [a for a in out["highlights"].get("anomalies", []) if a["kind"] == "record"]
    assert [r["value"] for r in records] == [5000]


def test_holt_forecast_follows_linear_trend():
    fc = holt_forecast([10, 20, 30, 40, 50, 60, 70])
    assert [round(p["forecast"]) for p in fc["points"]] == [80, 90, 100]
    assert holt_forecast([1, 2, 3]) is None
