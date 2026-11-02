"""
app/utils/data_profile.py
─────────────────────────
Builds a compact, column-aware analytical profile of a query result.

It works from the result's own column names: it detects measures (Cost,
Quantity, ...), identifiers, categories (ServiceType, Facility, Gender ...),
people/entity columns (PatientName ...) and date columns, then pre-computes
accurate totals, averages, per-entity averages, top groups with share %, and
monthly / period-over-period trends over ALL rows. This costs zero LLM tokens
and gives the LLM exact numbers to reason with.
"""

import re
from collections import OrderedDict, defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_TIME_PARTS = {"year", "month", "day", "quarter", "week"}
_ID_HINTS = ("id", "code", "number", "no", "mrn", "tps", "phone", "mobile", "zip", "pin")
_MONEY_RE = re.compile(r"cost|amount|price|total|fee|charge|revenue|sales|paid|balance", re.I)


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return None


def _fmt(n: float) -> str:
    return f"{n:,.0f}" if abs(n - round(n)) < 1e-9 else f"{n:,.2f}"


def _is_id_like(name: str) -> bool:
    n = re.sub(r"[^a-z0-9]", "", name.lower())
    if n in _TIME_PARTS:
        return False
    return any(n.endswith(h) or n.startswith(h) for h in _ID_HINTS) and not any(
        k in n for k in ("cost", "amount", "price", "total", "fee", "charge", "qty", "quantity")
    )


def _is_date_str(v: Any) -> bool:
    return isinstance(v, str) and bool(_DATE_RE.match(v))


def classify_columns(columns: List[str], rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    n = len(rows)
    measures, ids, dates, cats, others = [], [], [], [], []
    for c in columns:
        vals = [r.get(c) for r in rows if r.get(c) is not None]
        if not vals:
            others.append(c)
            continue
        numeric = sum(1 for v in vals if _num(v) is not None)
        dated = sum(1 for v in vals if _is_date_str(v))
        if dated >= max(1, len(vals) * 0.8):
            dates.append(c)
        elif numeric >= max(1, len(vals) * 0.8):
            if c.lower() in _TIME_PARTS:
                cats.append(c)
            elif _is_id_like(c):
                ids.append(c)
            else:
                measures.append(c)
        else:
            distinct = len({str(v) for v in vals})
            if _is_id_like(c) and distinct > max(3, n * 0.5):
                ids.append(c)
            elif distinct <= max(12, int(n * 0.5)) and distinct >= 1:
                cats.append(c)
            else:
                others.append(c)
    return {"measures": measures, "ids": ids, "dates": dates, "cats": cats, "others": others}


def _entity_column(columns: List[str], rows: List[Dict[str, Any]]) -> Optional[str]:
    """Pick the column that best identifies the 'person/customer' (e.g. PatientName)."""
    cands = [
        c for c in columns
        if re.search(r"(patient|customer|client|employee|doctor|member|staff)", c, re.I)
        and re.search(r"(name|id)$", c, re.I)
    ]
    cands.sort(key=lambda c: (0 if re.search("name", c, re.I) else 1))
    for c in cands:
        if len({str(r.get(c)) for r in rows if r.get(c) is not None}) >= 1:
            return c
    return None


def profile_data(columns: List[str], rows: List[Dict[str, Any]], max_groups: int = 5) -> str:
    """Return a compact text profile of the full result set."""
    if not rows:
        return "No data."

    kinds = classify_columns(columns, rows)
    measures, dates, cats = kinds["measures"], kinds["dates"], kinds["cats"]
    lines: List[str] = [f"Records: {len(rows)}"]

    entity = _entity_column(columns, rows)
    n_entities = None
    if entity:
        n_entities = len({str(r.get(entity)) for r in rows if r.get(entity) is not None})
        lines.append(f"Distinct {entity}: {n_entities}")

    # ── Measures: totals / averages / extremes ──────────────────────────────
    ident_cols = [
        c for c in columns
        if c not in measures and c not in dates and c.lower() not in _TIME_PARTS
    ]
    ordered_measures = sorted(measures, key=lambda x: 0 if _MONEY_RE.search(x) else 1)
    for m in ordered_measures:
        vals = [(r, _num(r.get(m))) for r in rows]
        vals = [(r, v) for r, v in vals if v is not None]
        if not vals:
            continue
        total = sum(v for _, v in vals)
        hi = max(vals, key=lambda x: x[1])
        lo = min(vals, key=lambda x: x[1])
        tag = entity or (ident_cols[0] if ident_cols else None)
        hi_l = f" ({hi[0].get(tag)})" if tag and hi[0].get(tag) is not None else ""
        lo_l = f" ({lo[0].get(tag)})" if tag and lo[0].get(tag) is not None else ""
        line = (
            f"{m}: total={_fmt(total)}, average per record={_fmt(total / len(vals))}, "
            f"highest={_fmt(hi[1])}{hi_l}, lowest={_fmt(lo[1])}{lo_l}"
        )
        if n_entities:
            line += f", average per {entity}={_fmt(total / n_entities)}"
        lines.append(line)

    # ── Breakdown by category columns ──────────────────────────────────────
    primary = ordered_measures[:2]
    shown = 0
    for c in cats:
        if shown >= 4:
            break
        if c == entity or c.lower() in _TIME_PARTS or _is_id_like(c):
            continue
        groups: Dict[str, Dict[str, float]] = OrderedDict()
        for r in rows:
            k = str(r.get(c)) if r.get(c) is not None else "(blank)"
            g = groups.setdefault(k, defaultdict(float))
            g["_count"] += 1
            for m in primary:
                v = _num(r.get(m))
                if v is not None:
                    g[m] += v
        if len(groups) < 2:
            continue
        shown += 1
        sort_key = primary[0] if primary else "_count"
        ranked = sorted(groups.items(), key=lambda kv: kv[1].get(sort_key, 0), reverse=True)
        total_for_share = sum(g.get(sort_key, 0) for _, g in ranked) or 1
        parts = []
        for k, g in ranked[:max_groups]:
            seg = f"{k}: {int(g['_count'])} rec"
            for m in primary:
                seg += f", {m}={_fmt(g.get(m, 0))}"
            seg += f" ({g.get(sort_key, 0) / total_for_share * 100:.0f}% of {sort_key if primary else 'records'})"
            parts.append(seg)
        more = f" (+{len(ranked) - max_groups} more)" if len(ranked) > max_groups else ""
        lines.append(f"By {c} [{len(groups)} groups]: " + "; ".join(parts) + more)

    # ── Dates: range + monthly trend ───────────────────────────────────────
    for d in dates:
        ds = sorted(str(r.get(d))[:10] for r in rows if _is_date_str(r.get(d)))
        if not ds:
            continue
        lines.append(f"{d}: from {ds[0]} to {ds[-1]}")
        if re.search(r"birth|dob", d, re.I):
            continue
        months: Dict[str, Dict[str, float]] = OrderedDict()
        for r in sorted((r for r in rows if _is_date_str(r.get(d))), key=lambda r: str(r.get(d))):
            mk = str(r.get(d))[:7]
            g = months.setdefault(mk, defaultdict(float))
            g["_count"] += 1
            for m in primary:
                v = _num(r.get(m))
                if v is not None:
                    g[m] += v
        if len(months) >= 2:
            seq = []
            for mk, g in months.items():
                seg = f"{mk}: {int(g['_count'])} rec"
                for m in primary:
                    seg += f", {m}={_fmt(g.get(m, 0))}"
                seq.append(seg)
            lines.append(f"Monthly trend by {d}: " + "; ".join(seq[:24]))

    # ── Small ordered series (e.g. Year/Month/Total): step changes ─────────
    label_cols = [c for c in columns if c not in measures]
    if 2 < len(rows) <= 24 and measures and label_cols and not dates and not entity:
        for m in ordered_measures[:2]:
            pairs = [("/".join(str(r.get(c)) for c in label_cols), _num(r.get(m))) for r in rows]
            pairs = [(l, v) for l, v in pairs if v is not None]
            if len(pairs) < 3:
                continue
            steps = []
            for (pl, pv), (cl, cv) in zip(pairs, pairs[1:]):
                pct = f"{(cv - pv) / pv * 100:+.1f}%" if pv else "n/a"
                steps.append(f"{pl}->{cl}: {_fmt(cv - pv)} ({pct})")
            first, last = pairs[0], pairs[-1]
            chg = f"{(last[1] - first[1]) / first[1] * 100:+.1f}%" if first[1] else "n/a"
            lines.append(
                f"{m} period-over-period: " + "; ".join(steps) +
                f". Overall {first[0]} -> {last[0]}: {chg}"
            )

    return "\n".join(lines)


def format_rows(columns: List[str], rows: List[Dict[str, Any]], max_rows: int = 40) -> str:
    """Compact pipe table; keeps head+tail when truncated."""
    if not rows:
        return "No results returned."
    note = ""
    if len(rows) > max_rows:
        half = max_rows // 2
        sample = rows[:half] + rows[-half:]
        note = (
            f"\n[NOTE: showing first {half} and last {half} of {len(rows)} rows; "
            f"the profile covers all rows.]"
        )
    else:
        sample = rows
    header = " | ".join(str(c) for c in columns)
    body = "\n".join(" | ".join(str(r.get(c, "")) for c in columns) for r in sample)
    return f"{header}\n{body}{note}"
