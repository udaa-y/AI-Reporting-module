"""
app/analytics/engine.py
────────────────────────
Deterministic analytics over a query result — computed in Python (pandas /
numpy) so every number the AI narrative quotes is exact. The LLM only
interprets these findings; it never does arithmetic.

Analyses (each runs only when the result's shape supports it):
  KPI            totals, averages, medians, per-patient averages
  Trend          period series (day/week/month), slope, growth, best/worst period
  Comparative    latest vs previous period, year-over-year, year-to-date
  Performance    category ranking, share %, Pareto concentration, low performers
  Diagnostic     which categories drove the latest period-over-period change
  Anomaly        robust z-score (median/MAD) outliers in the series and records
  Forecast       Holt linear exponential smoothing, next 3 periods + 95% band
  Patient        visits per patient, repeat-visit rate, age bands from DOB
  Correlation    relationship between two numeric measures

Column roles come from app/utils/data_profile.classify_columns so the
profile and these analyses always agree on what is a measure, date, etc.
"""

import datetime as dt
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.utils.data_profile import _entity_column, classify_columns

_MONEY_RE = re.compile(r"cost|amount|price|total|fee|charge|revenue|sales|paid|balance", re.I)
_DOB_RE = re.compile(r"birth|dob", re.I)
_YEAR_RE = re.compile(r"^(year|yr)$|year$", re.I)
_MONTH_RE = re.compile(r"^(month|mon|mm)$|month$", re.I)
_PERIOD_RE = re.compile(r"^\d{4}-\d{2}$")

# Period buckets keyed by period START (weeks run Monday-Sunday).
_FREQ = {"month": "MS", "week": "W-MON", "day": "D"}
_RESAMPLE_KW = {"month": {}, "week": {"label": "left", "closed": "left"}, "day": {}}

ROBUST_Z = 3.5      # Iglewicz–Hoaglin threshold for outliers
MIN_SERIES = 4      # periods needed for a trend
MIN_FORECAST = 6    # periods needed for a forecast


def _fmt(n: float) -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "n/a"
    return f"{n:,.0f}" if abs(n - round(n)) < 1e-9 else f"{n:,.2f}"


def _pct(new: float, old: float) -> Optional[float]:
    return None if not old else (new - old) / abs(old) * 100


def _pct_str(p: Optional[float]) -> str:
    return "n/a" if p is None else f"{p:+.1f}%"


def _robust_z(values: np.ndarray) -> np.ndarray:
    med = np.median(values)
    mad = np.median(np.abs(values - med))
    if mad == 0:
        return np.zeros_like(values, dtype=float)
    return 0.6745 * (values - med) / mad


# ── Column roles ──────────────────────────────────────────────────────────────

def _roles(columns: List[str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    kinds = classify_columns(columns, rows)
    dates = [d for d in kinds["dates"] if not _DOB_RE.search(d)]
    dobs = [d for d in kinds["dates"] if _DOB_RE.search(d)]
    year_col = next((c for c in columns if _YEAR_RE.search(c)), None)
    month_col = next((c for c in columns if _MONTH_RE.search(c)), None)
    # "YYYY-MM" strings (e.g. FORMAT(VisitDate, 'yyyy-MM') AS VisitMonth) are a monthly period.
    period_col = next(
        (c for c in columns
         if rows and all(isinstance(r.get(c), str) and _PERIOD_RE.match(r[c])
                         for r in rows if r.get(c) is not None)
         and any(r.get(c) is not None for r in rows)),
        None,
    )
    measures = sorted(
        (m for m in kinds["measures"] if m not in (year_col, month_col)),
        key=lambda m: 0 if _MONEY_RE.search(m) else 1,
    )
    entity = _entity_column(columns, rows)
    cats = [
        c for c in kinds["cats"]
        if c not in (year_col, month_col, period_col, entity) and c.lower() not in ("day", "week", "quarter")
    ]
    return {
        "measures": measures, "dates": dates, "dobs": dobs, "cats": cats,
        "entity": entity, "year": year_col, "month": month_col, "period": period_col,
    }


# ── Time series ───────────────────────────────────────────────────────────────

def _build_series(df: pd.DataFrame, roles: dict, measure: Optional[str]
                  ) -> Optional[Tuple[pd.Series, str, str]]:
    """Return (series indexed by period start, granularity, source column)."""
    value_col = measure or "__records"

    if roles["dates"]:
        dcol = roles["dates"][0]
        d = df.assign(__t=pd.to_datetime(df[dcol], errors="coerce")).dropna(subset=["__t"])
        if d.empty:
            return None
        span = (d["__t"].max() - d["__t"].min()).days
        gran = "month" if span > 120 else "week" if span > 21 else "day"
        series = d.set_index("__t")[value_col].resample(_FREQ[gran], **_RESAMPLE_KW[gran]).sum()
        return series, gran, dcol

    if roles["period"]:
        t = pd.to_datetime(df[roles["period"]] + "-01", errors="coerce")
        ok = t.notna()
        if ok.sum() < 2:
            return None
        series = df.loc[ok, value_col].groupby(t[ok].values).sum().sort_index()
        series.index = pd.DatetimeIndex(series.index)
        return series.asfreq("MS", fill_value=0), "month", roles["period"]

    if roles["year"] and roles["month"]:
        y = pd.to_numeric(df[roles["year"]], errors="coerce")
        m = pd.to_numeric(df[roles["month"]], errors="coerce")
        ok = y.notna() & m.notna() & m.between(1, 12)
        if ok.sum() < 2:
            return None
        idx = pd.to_datetime(
            {"year": y[ok].astype(int), "month": m[ok].astype(int), "day": 1}
        )
        series = df.loc[ok, value_col].groupby(idx.values).sum().sort_index()
        series.index = pd.DatetimeIndex(series.index)
        series = series.asfreq("MS", fill_value=0)
        return series, "month", f"{roles['year']}/{roles['month']}"

    if roles["year"]:
        y = pd.to_numeric(df[roles["year"]], errors="coerce")
        ok = y.notna()
        if ok.sum() < 2:
            return None
        series = df.loc[ok, value_col].groupby(y[ok].astype(int)).sum().sort_index()
        series.index = pd.to_datetime([f"{int(v)}-01-01" for v in series.index])
        return series, "year", roles["year"]
    return None


def _label(ts: pd.Timestamp, gran: str) -> str:
    return {"month": ts.strftime("%Y-%m"), "year": ts.strftime("%Y"),
            "week": "week of " + ts.strftime("%Y-%m-%d")}.get(gran, ts.strftime("%Y-%m-%d"))


def _drop_partial_period(series: pd.Series, gran: str, last_date: Optional[pd.Timestamp],
                         today: dt.date) -> Tuple[pd.Series, Optional[str]]:
    """Exclude the current, still-running period so it doesn't look like a collapse."""
    if gran not in ("month", "week", "year") or len(series) < 2:
        return series, None
    start = series.index[-1]
    end = {"month": start + pd.offsets.MonthEnd(0), "week": start + pd.Timedelta(days=6),
           "year": start + pd.offsets.YearEnd(0)}[gran]
    running = start.date() <= today <= end.date()
    # Raw rows: partial when data stops before the period ends. Pre-aggregated
    # rows (no row dates): partial when the period is the one we are in today.
    if running and (last_date is None or last_date.normalize() < end.normalize()):
        return series.iloc[:-1], _label(start, gran)
    return series, None


def holt_forecast(values: np.ndarray, horizon: int = 3) -> Optional[dict]:
    """Holt's linear exponential smoothing with a small grid search on alpha/beta."""
    y = np.asarray(values, dtype=float)
    if len(y) < MIN_FORECAST:
        return None
    best = None
    for alpha in (0.2, 0.4, 0.6, 0.8):
        for beta in (0.05, 0.1, 0.2, 0.4):
            level, trend = y[0], y[1] - y[0]
            sse, resid = 0.0, []
            for t in range(1, len(y)):
                pred = level + trend
                err = y[t] - pred
                resid.append(err)
                sse += err * err
                new_level = alpha * y[t] + (1 - alpha) * (level + trend)
                trend = beta * (new_level - level) + (1 - beta) * trend
                level = new_level
            if best is None or sse < best[0]:
                best = (sse, alpha, beta, level, trend, np.std(resid, ddof=1))
    _, alpha, beta, level, trend, sigma = best
    points = []
    for h in range(1, horizon + 1):
        mid = level + h * trend
        band = 1.96 * sigma * math.sqrt(h)
        points.append({"step": h, "forecast": max(mid, 0.0),
                       "low": max(mid - band, 0.0), "high": max(mid + band, 0.0)})
    mape = None
    nz = y[1:] != 0
    if nz.any():
        fitted_err = np.abs(np.asarray(_holt_residuals(y, alpha, beta)))[nz]
        mape = float(np.mean(fitted_err / np.abs(y[1:][nz])) * 100)
    return {"method": "Holt linear exponential smoothing", "alpha": alpha, "beta": beta,
            "points": points, "mape": mape}


def _holt_residuals(y: np.ndarray, alpha: float, beta: float) -> List[float]:
    level, trend, out = y[0], y[1] - y[0], []
    for t in range(1, len(y)):
        out.append(y[t] - (level + trend))
        new_level = alpha * y[t] + (1 - alpha) * (level + trend)
        trend = beta * (new_level - level) + (1 - beta) * trend
        level = new_level
    return out


# ── Main entry point ──────────────────────────────────────────────────────────

def analyze(columns: List[str], rows: List[Dict[str, Any]],
            today: Optional[dt.date] = None) -> Dict[str, Any]:
    """
    Run every applicable analysis.

    Returns {"facts": [str], "highlights": {...}, "analysis_types": [str]}:
      facts       compact text lines for the LLM prompt
      highlights  structured numbers for the UI
    """
    today = today or dt.date.today()
    out: Dict[str, Any] = {"facts": [], "highlights": {}, "analysis_types": []}
    if not rows:
        return out

    df = pd.DataFrame(rows, columns=columns)
    roles = _roles(columns, rows)
    measures = roles["measures"]
    for m in measures:
        df[m] = pd.to_numeric(df[m], errors="coerce")
    df["__records"] = 1
    primary = measures[0] if measures else None
    primary_label = primary or "record count"
    facts, hl = out["facts"], out["highlights"]

    def add_type(name: str):
        if name not in out["analysis_types"]:
            out["analysis_types"].append(name)

    # ── KPI ──────────────────────────────────────────────────────────────────
    kpis = [{"label": "Records", "value": len(df)}]
    n_entities = None
    if roles["entity"]:
        n_entities = int(df[roles["entity"]].nunique())
        kpis.append({"label": f"Distinct {roles['entity']}", "value": n_entities})
    for m in measures[:3]:
        s = df[m].dropna()
        if s.empty:
            continue
        kpis.append({"label": f"Total {m}", "value": float(s.sum())})
        kpis.append({"label": f"Average {m}", "value": round(float(s.mean()), 2)})
        line = (f"KPI {m}: total={_fmt(s.sum())}, mean={_fmt(s.mean())}, "
                f"median={_fmt(s.median())}, min={_fmt(s.min())}, max={_fmt(s.max())}")
        if n_entities:
            line += f", per {roles['entity']}={_fmt(s.sum() / n_entities)}"
        facts.append(line)
    hl["kpis"] = kpis
    add_type("descriptive")
    if measures:
        add_type("kpi")

    # ── Time series: trend / comparison / anomaly / forecast / drivers ───────
    built = _build_series(df, roles, primary)
    if built is not None:
        series, gran, source = built
        last_date = None
        if roles["dates"]:
            last_date = pd.to_datetime(df[roles["dates"][0]], errors="coerce").max()
        series, partial = _drop_partial_period(series, gran, last_date, today)
        if partial:
            facts.append(f"Note: {partial} is still in progress, so it is excluded from trend, "
                         f"comparison and forecast calculations.")
        if len(series) >= 2:
            _series_analyses(series, gran, source, primary_label, facts, hl, add_type)
            if roles["cats"] and len(series) >= 2:
                _drivers(df, roles, primary, series, gran, primary_label, facts, hl, add_type)

    # ── Performance / breakdowns ─────────────────────────────────────────────
    breakdowns = []
    for c in roles["cats"][:3]:
        value_col = primary or "__records"
        g = df.groupby(df[c].fillna("(blank)").astype(str))[value_col].sum().sort_values(ascending=False)
        if len(g) < 2:
            continue
        total = g.sum() or 1
        shares = (g / total * 100).round(1)
        cum = shares.cumsum()
        pareto_n = int((cum < 80).sum()) + 1
        top = [{"name": k, "value": float(v), "share": float(shares[k])} for k, v in g.head(5).items()]
        bottom = [{"name": k, "value": float(v), "share": float(shares[k])}
                  for k, v in g.tail(3).items()] if len(g) > 5 else []
        breakdowns.append({"column": c, "groups": int(len(g)), "top": top, "bottom": bottom,
                           "pareto_groups_for_80pct": pareto_n})
        line = (f"By {c} ({len(g)} groups, {primary_label}): " +
                "; ".join(f"{t['name']}={_fmt(t['value'])} ({t['share']}%)" for t in top))
        line += f". {pareto_n} of {len(g)} groups make up 80% of the total."
        if bottom:
            line += " Lowest: " + "; ".join(f"{b['name']}={_fmt(b['value'])} ({b['share']}%)" for b in bottom)
        facts.append(line)
    if breakdowns:
        hl["breakdowns"] = breakdowns
        add_type("performance")
        add_type("comparative")

    # ── Record-level outliers ────────────────────────────────────────────────
    if primary and df[primary].notna().sum() >= 8:
        vals = df[primary].astype(float)
        z = pd.Series(_robust_z(vals.fillna(vals.median()).to_numpy()), index=df.index)
        # Also require the value to sit outside the extreme fence (Q1/Q3 -/+ 3*IQR):
        # with a few fixed price points the MAD alone over-flags ordinary values.
        q1, q3 = vals.quantile(0.25), vals.quantile(0.75)
        fence = (vals < q1 - 3 * (q3 - q1)) | (vals > q3 + 3 * (q3 - q1))
        idx = z[(z.abs() > ROBUST_Z) & fence].abs().sort_values(ascending=False).index[:5]
        if len(idx):
            label_cols = [c for c in (roles["entity"], *roles["cats"][:2], *roles["dates"][:1]) if c]
            recs = []
            for i in idx:
                label = ", ".join(f"{c}={df.at[i, c]}" for c in label_cols) or f"row {i + 1}"
                recs.append({"label": label, "value": float(df.at[i, primary]),
                             "robust_z": round(float(z[i]), 1)})
            hl.setdefault("anomalies", []).extend(
                {"kind": "record", **r} for r in recs
            )
            facts.append(
                f"Unusual records by {primary} (median={_fmt(vals.median())}): " +
                "; ".join(f"{r['label']} -> {_fmt(r['value'])} (robust z {r['robust_z']})" for r in recs)
            )
            add_type("anomaly")

    # ── Patient analysis ─────────────────────────────────────────────────────
    _patient_analysis(df, roles, primary, today, facts, hl, add_type)

    # ── Correlation between measures ─────────────────────────────────────────
    if len(measures) >= 2 and len(df) >= 8:
        a, b = measures[0], measures[1]
        pair = df[[a, b]].dropna()
        if len(pair) >= 8 and pair[a].std() > 0 and pair[b].std() > 0:
            r = float(pair[a].corr(pair[b]))
            strength = "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.4 else "weak"
            facts.append(f"Correlation {a} vs {b}: r={r:.2f} ({strength}; correlation, not causation)")
            hl["correlation"] = {"a": a, "b": b, "r": round(r, 2)}

    return out


def _series_analyses(series: pd.Series, gran: str, source: str, label: str,
                     facts: list, hl: dict, add_type) -> None:
    values = series.to_numpy(dtype=float)
    labels = [_label(ts, gran) for ts in series.index]
    n = len(values)
    trend: Dict[str, Any] = {"granularity": gran, "source": source,
                             "points": [{"period": l, "value": float(v)} for l, v in zip(labels, values)]}

    # Latest vs previous
    last, prev = values[-1], values[-2]
    trend["latest"] = {"period": labels[-1], "value": float(last),
                       "previous_period": labels[-2], "previous": float(prev),
                       "change": float(last - prev), "change_pct": _pct(last, prev)}
    facts.append(f"Latest {gran} {labels[-1]}: {label}={_fmt(last)} vs {labels[-2]}={_fmt(prev)} "
                 f"({_fmt(last - prev)}, {_pct_str(_pct(last, prev))})")
    add_type("comparative")

    if n >= MIN_SERIES:
        x = np.arange(n)
        slope = float(np.polyfit(x, values, 1)[0])
        mean = values.mean() or 1
        direction = "rising" if slope / abs(mean) > 0.02 else "falling" if slope / abs(mean) < -0.02 else "flat"
        best, worst = int(values.argmax()), int(values.argmin())
        trend.update(slope_per_period=slope, direction=direction,
                     overall_change_pct=_pct(values[-1], values[0]),
                     best={"period": labels[best], "value": float(values[best])},
                     worst={"period": labels[worst], "value": float(values[worst])})
        facts.append(
            f"Trend of {label} by {gran} ({labels[0]} to {labels[-1]}, {n} periods): {direction}, "
            f"about {_fmt(slope)} per {gran}; first->last {_pct_str(_pct(values[-1], values[0]))}; "
            f"best {labels[best]}={_fmt(values[best])}, worst {labels[worst]}={_fmt(values[worst])}"
        )
        facts.append("Series: " + "; ".join(f"{l}={_fmt(v)}" for l, v in list(zip(labels, values))[-18:]))
        add_type("trend")

    # Year-over-year and year-to-date (monthly data)
    if gran == "month" and n >= 13:
        idx = series.index
        yoy_ts = idx[-1] - pd.DateOffset(years=1)
        if yoy_ts in series.index:
            ly = float(series[yoy_ts])
            trend["yoy"] = {"period": labels[-1], "value": float(last), "last_year": ly,
                            "change_pct": _pct(last, ly)}
            facts.append(f"Year-over-year: {labels[-1]}={_fmt(last)} vs {_label(yoy_ts, gran)}={_fmt(ly)} "
                         f"({_pct_str(_pct(last, ly))})")
        year, month = idx[-1].year, idx[-1].month
        ytd = series[(idx.year == year) & (idx.month <= month)].sum()
        ytd_ly = series[(idx.year == year - 1) & (idx.month <= month)].sum()
        if ytd_ly:
            trend["ytd"] = {"year": year, "value": float(ytd), "last_year": float(ytd_ly),
                            "change_pct": _pct(ytd, ytd_ly)}
            facts.append(f"Year-to-date {year} (Jan-{idx[-1].strftime('%b')}): {_fmt(ytd)} vs "
                         f"{year - 1} same months {_fmt(ytd_ly)} ({_pct_str(_pct(ytd, ytd_ly))})")
    hl["trend"] = trend

    # Anomalous periods — measured against the trend line, so steady growth isn't flagged
    if n >= 6:
        x = np.arange(n)
        z = _robust_z(values - np.polyval(np.polyfit(x, values, 1), x))
        flagged = [(labels[i], float(values[i]), round(float(z[i]), 1))
                   for i in range(n) if abs(z[i]) > ROBUST_Z]
        if flagged:
            hl.setdefault("anomalies", []).extend(
                {"kind": "period", "label": l, "value": v, "robust_z": zz} for l, v, zz in flagged
            )
            facts.append(f"Unusual {gran}s vs trend (median {_fmt(float(np.median(values)))}): " +
                         "; ".join(f"{l}={_fmt(v)} ({'spike' if zz > 0 else 'drop'}, robust z {zz})"
                                   for l, v, zz in flagged))
            add_type("anomaly")

    # Forecast
    fc = holt_forecast(values)
    if fc:
        step = {"month": pd.DateOffset(months=1), "week": pd.Timedelta(weeks=1),
                "day": pd.Timedelta(days=1), "year": pd.DateOffset(years=1)}[gran]
        for p in fc["points"]:
            p["period"] = _label(series.index[-1] + step * p["step"], gran)
        hl["forecast"] = fc
        accuracy = f", in-sample error ~{fc['mape']:.0f}%" if fc["mape"] is not None else ""
        facts.append(
            f"Forecast ({fc['method']}{accuracy}): " +
            "; ".join(f"{p['period']}={_fmt(p['forecast'])} (95% range {_fmt(p['low'])}-{_fmt(p['high'])})"
                      for p in fc["points"]) +
            ". Statistical projection of the past trend only — it does not know about planned changes."
        )
        add_type("forecast")


def _drivers(df: pd.DataFrame, roles: dict, primary: Optional[str], series: pd.Series,
             gran: str, label: str, facts: list, hl: dict, add_type) -> None:
    """Diagnostic: which category values explain the latest period-over-period change."""
    if not roles["dates"] or gran not in ("month", "week", "day"):
        return
    dcol, value_col = roles["dates"][0], primary or "__records"
    t = pd.to_datetime(df[dcol], errors="coerce")
    period = t.dt.to_period({"month": "M", "week": "W-SUN", "day": "D"}[gran]).dt.start_time
    cur, prev = series.index[-1], series.index[-2]
    total_change = float(series.iloc[-1] - series.iloc[-2])
    if total_change == 0:
        return
    results = []
    for c in roles["cats"][:2]:
        key = df[c].fillna("(blank)").astype(str)
        g_cur = df[period == cur].groupby(key[period == cur])[value_col].sum()
        g_prev = df[period == prev].groupby(key[period == prev])[value_col].sum()
        delta = g_cur.sub(g_prev, fill_value=0).sort_values(key=lambda s: -s.abs())
        for name, d in delta.head(3).items():
            if d != 0:
                results.append({"column": c, "name": name, "change": float(d),
                                "share_of_change": round(float(d) / total_change * 100, 1)})
    if results:
        hl["drivers"] = {"from": _label(prev, gran), "to": _label(cur, gran),
                         "total_change": total_change, "contributors": results}
        facts.append(
            f"Drivers of the {_label(prev, gran)} -> {_label(cur, gran)} change in {label} "
            f"({_fmt(total_change)} total): " +
            "; ".join(f"{r['column']}={r['name']}: {_fmt(r['change'])} ({r['share_of_change']}% of change)"
                      for r in results)
        )
        add_type("diagnostic")


def _patient_analysis(df: pd.DataFrame, roles: dict, primary: Optional[str], today: dt.date,
                      facts: list, hl: dict, add_type) -> None:
    patient: Dict[str, Any] = {}
    ent = roles["entity"]
    if ent and re.search("patient", ent, re.I) and df[ent].nunique() < len(df):
        per = df.groupby(df[ent].astype(str)).size()
        repeat = float((per > 1).mean() * 100)
        patient.update(avg_records_per_patient=round(float(per.mean()), 2),
                       repeat_rate_pct=round(repeat, 1), max_records=int(per.max()))
        facts.append(f"Patients: {len(per)} distinct; {per.mean():.2f} records per patient on average; "
                     f"{repeat:.1f}% have more than one record (repeat/returning); max {int(per.max())}")
        if primary:
            spend = df.groupby(df[ent].astype(str))[primary].sum().sort_values(ascending=False)
            top_share = float(spend.head(max(1, len(spend) // 10)).sum() / (spend.sum() or 1) * 100)
            patient["top10pct_share_of_" + primary] = round(top_share, 1)
            facts.append(f"Top 10% of patients account for {top_share:.1f}% of {primary}")
    if roles["dobs"]:
        dob = pd.to_datetime(df[roles["dobs"][0]], errors="coerce").dropna()
        if not dob.empty:
            ref = pd.Timestamp(today)
            ages = ((ref - dob).dt.days / 365.25).astype(int)
            bands = pd.cut(ages, [-1, 12, 17, 29, 44, 59, 74, 200],
                           labels=["0-12", "13-17", "18-29", "30-44", "45-59", "60-74", "75+"])
            dist = (bands.value_counts(normalize=True).sort_index() * 100).round(1)
            patient["age_bands_pct"] = {str(k): float(v) for k, v in dist.items() if v > 0}
            patient["median_age"] = float(ages.median())
            facts.append(f"Age (from {roles['dobs'][0]}): median {ages.median():.0f}; bands " +
                         ", ".join(f"{k}: {v}%" for k, v in dist.items() if v > 0))
    if patient:
        hl["patients"] = patient
        add_type("patient")
