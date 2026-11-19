"""
app/assistant/library.py
─────────────────────────
Verified query library — answers common questions from saved SQL templates
WITHOUT calling the LLM (no tokens, sub-second answers).

Templates live in semantic/few_shots/*.yaml (examples.yaml = curated,
learned_examples.yaml = added automatically from 👍 feedback):

  - id: appointments_per_doctor
    scope: appointments                 # or a list of section keys
    questions:                          # any number of phrasings
      - "appointments per doctor {period}"
      - "which doctors saw the most patients {period}"
    sql: |
      SELECT TOP {top_n} e.Name AS Doctor, COUNT(*) AS Appointments
      FROM Appointment a JOIN Employee e ON e.Id = a.DoctorId
      WHERE a.VisitDate >= {date_from} AND a.VisitDate < {date_to}
      GROUP BY e.Name ORDER BY Appointments DESC
    defaults: {period: "this month", top_n: 10}
    chart: {type: horizontal_bar, x: Doctor, y: [Appointments]}
    branch_compare: false               # true = groups by branch itself (Super Admin, all branches)

SQL slots:  {date_from} {date_to} (exclusive) {date_last} (inclusive)
            {prev_from} {prev_to} {prev_last}  (the period just before)   {top_n}

Matching (pure Python): the question's period ("last month", "Q1 2026",
"between 1 Jan and 15 Mar"…) and top-N are extracted, words are normalised
(synonyms, plurals, word order, filler words), and a template is used only
when EVERY meaningful word of the question is covered by one of its phrasings.
"Cancelled appointments per doctor" therefore never runs the plain
"appointments per doctor" template — it goes to the LLM instead.
"""

import calendar
import datetime as dt
import glob
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

AUTO_QUESTION_COVERAGE = 1.0    # every meaningful word of the question must be understood
AUTO_PHRASING_COVERAGE = 0.75   # …and the phrasing must not be much more specific
SUGGEST_SCORE = 0.5             # "Did you mean…?" threshold

# ── Periods ───────────────────────────────────────────────────────────────────

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_LEAD = r"(?:\b(?:in|for|during|over|of|from|on|within)\s+)?(?:\bthe\s+)?"
_UNITS = {"day": "day", "days": "day", "week": "week", "weeks": "week", "month": "month",
          "months": "month", "quarter": "quarter", "quarters": "quarter", "year": "year", "years": "year"}
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
             "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30}


@dataclass(frozen=True)
class Period:
    key: str                      # canonical identity, e.g. "this:month", "2026-03", "range:..."
    label: str                    # human text, e.g. "this month", "March 2026"
    start: dt.date
    end: dt.date                  # exclusive
    unit: Optional[str] = None    # calendar unit, used to find the previous period

    def previous(self) -> Tuple[dt.date, dt.date]:
        if self.unit in ("month", "quarter", "year"):
            months = {"month": 1, "quarter": 3, "year": 12}[self.unit]
            return _add_months(self.start, -months), self.start
        if self.unit == "week":
            return self.start - dt.timedelta(days=7), self.start
        days = (self.end - self.start).days
        return self.start - dt.timedelta(days=days), self.start


def _add_months(d: dt.date, n: int) -> dt.date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def _month_period(year: int, month: int) -> Period:
    start = dt.date(year, month, 1)
    return Period(f"{year}-{month:02d}", f"{calendar.month_name[month]} {year}", start,
                  _add_months(start, 1), "month")


def _date(day: str, month: str, year: Optional[str], today: dt.date) -> dt.date:
    return dt.date(int(year) if year else today.year, _MONTHS[month.lower()[:3]], int(day))


def _calendar(unit: str, which: str, today: dt.date) -> Period:
    if unit == "week":
        start = today - dt.timedelta(days=today.weekday())
        start = start - dt.timedelta(days=7) if which == "last" else start
        end = start + dt.timedelta(days=7)
    elif unit == "month":
        start = today.replace(day=1)
        start = _add_months(start, -1) if which == "last" else start
        end = _add_months(start, 1)
    elif unit == "quarter":
        start = dt.date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
        start = _add_months(start, -3) if which == "last" else start
        end = _add_months(start, 3)
    else:
        start = dt.date(today.year - (1 if which == "last" else 0), 1, 1)
        end = dt.date(start.year + 1, 1, 1)
    return Period(f"{which}:{unit}", f"{which} {unit}", start, end, unit)


_PERIOD_PATTERNS: List[Tuple[re.Pattern, Any]] = []


def _pattern(regex: str):
    def register(fn):
        _PERIOD_PATTERNS.append((re.compile(_LEAD + regex, re.I), fn))
        return fn
    return register


@_pattern(r"\b(?:from|between)\s+(\d{4}-\d{2}-\d{2})\s+(?:to|and|until|till|-)\s+(\d{4}-\d{2}-\d{2})\b")
def _iso_range(m, today):
    a, b = dt.date.fromisoformat(m[1]), dt.date.fromisoformat(m[2])
    return Period(f"range:{a}:{b}", f"{a:%d %b %Y} – {b:%d %b %Y}", a, b + dt.timedelta(days=1))


@_pattern(r"\b(?:from|between)\s+(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"\.?(?:\s+(\d{4}))?\s+(?:to|and|until|till|-)\s+(\d{1,2})(?:st|nd|rd|th)?\s+" + _MON + r"\.?(?:\s+(\d{4}))?\b")
def _dm_range(m, today):
    year_b = m[6] or m[3]
    a = _date(m[1], m[2], m[3] or year_b, today)
    b = _date(m[4], m[5], year_b, today)
    return Period(f"range:{a}:{b}", f"{a:%d %b %Y} – {b:%d %b %Y}", a, b + dt.timedelta(days=1))


@_pattern(r"\bsince\s+(\d{4}-\d{2}-\d{2})\b")
def _since_iso(m, today):
    a = dt.date.fromisoformat(m[1])
    return Period(f"since:{a}", f"since {a:%d %b %Y}", a, today + dt.timedelta(days=1))


@_pattern(r"\bsince\s+" + _MON + r"(?:\s+(\d{4}))?\b")
def _since_month(m, today):
    year = int(m[2]) if m[2] else today.year
    a = dt.date(year, _MONTHS[m[1].lower()[:3]], 1)
    if a > today and not m[2]:
        a = a.replace(year=year - 1)
    return Period(f"since:{a}", f"since {a:%B %Y}", a, today + dt.timedelta(days=1))


@_pattern(r"\b(?:the\s+)?(?:last|past|previous|prior)\s+(\d{1,3}|" + "|".join(_WORD_NUM) + r")\s+(days?|weeks?|months?|years?)\b")
def _rolling(m, today):
    n = int(m[1]) if m[1].isdigit() else _WORD_NUM[m[1].lower()]
    unit = _UNITS[m[2].lower()]
    end = today + dt.timedelta(days=1)
    if unit == "day":
        start = end - dt.timedelta(days=n)
    elif unit == "week":
        start = end - dt.timedelta(days=7 * n)
    else:
        start = _add_months(end, -n * (12 if unit == "year" else 1))
    return Period(f"rolling:{n}:{unit}", f"last {n} {unit}s", start, end)


@_pattern(r"\b(this|current|last|previous|past|prior)\s+(week|month|quarter|year)\b")
def _relative(m, today):
    which = "this" if m[1].lower() in ("this", "current") else "last"
    return _calendar(m[2].lower(), which, today)


@_pattern(r"\b(year|month|quarter)[\s-]+to[\s-]+date\b|\b(ytd|mtd|qtd)\b")
def _to_date(m, today):
    unit = (m[1] or {"ytd": "year", "mtd": "month", "qtd": "quarter"}[m[2].lower()]).lower()
    p = _calendar(unit, "this", today)
    return Period(f"todate:{unit}", f"{unit} to date", p.start, today + dt.timedelta(days=1))


@_pattern(r"\b(today|yesterday)\b")
def _day(m, today):
    d = today if m[1].lower() == "today" else today - dt.timedelta(days=1)
    return Period(f"day:{m[1].lower()}", m[1].lower(), d, d + dt.timedelta(days=1), "day")


@_pattern(r"\bq([1-4])(?:\s*(\d{4}))?\b")
def _quarter(m, today):
    year = int(m[2]) if m[2] else today.year
    start = dt.date(year, (int(m[1]) - 1) * 3 + 1, 1)
    return Period(f"{year}-q{m[1]}", f"Q{m[1]} {year}", start, _add_months(start, 3), "quarter")


@_pattern(_MON + r"\.?\s+(\d{4})\b")
def _month_year(m, today):
    return _month_period(int(m[2]), _MONTHS[m[1].lower()[:3]])


@_pattern(r"\b(?:in|for|during)\s+" + _MON + r"\b(?!\s*\d)")
def _month_only(m, today):
    month = _MONTHS[m[1].lower()[:3]]
    year = today.year if month <= today.month else today.year - 1
    return _month_period(year, month)


@_pattern(r"\b(20\d{2})\b")
def _year(m, today):
    y = int(m[1])
    return Period(f"{y}", str(y), dt.date(y, 1, 1), dt.date(y + 1, 1, 1), "year")


def parse_period(text: str, today: Optional[dt.date] = None) -> Optional[Tuple[Period, Tuple[int, int]]]:
    """The first time period mentioned in the text, and where it is."""
    today = today or dt.date.today()
    best = None
    for regex, build in _PERIOD_PATTERNS:          # earlier patterns are more specific
        m = regex.search(text)
        if m and (best is None or m.start() < best[1][0]):
            try:
                best = (build(m, today), m.span())
            except (ValueError, KeyError):
                continue
    return best


_TOP_RE = re.compile(r"\b(?:top|bottom|first)\s+(\d{1,3})\b|\b(\d{1,3})\s+(?=(?:most|busiest|best|highest|largest|biggest|lowest|least|fewest|worst)\b)", re.I)


def parse_top_n(text: str) -> Optional[Tuple[int, Tuple[int, int]]]:
    """'top 5', '3 busiest' … → (5, span of the number)."""
    m = _TOP_RE.search(text)
    if not m:
        return None
    group = 1 if m[1] else 2
    return max(1, min(int(m[group]), 100)), m.span(group)


# ── Wording ───────────────────────────────────────────────────────────────────

_SYNONYMS: Dict[str, str] = {}
for canonical, words in {
    "appointment": "appointment appointments visit visits consultation consultations appt appts booking bookings booked encounter encounters",
    "doctor": "doctor doctors physician physicians dr doc docs consultant consultants clinician clinicians practitioner practitioners provider providers",
    "patient": "patient patients client clients",
    "department": "department departments dept depts speciality specialities specialty specialties",
    "branch": "branch branches clinics location locations site sites centre centres center centers",
    "most": "most highest top busiest maximum max largest biggest best leading",
    "least": "least lowest fewest minimum min smallest bottom worst quietest",
    "cancel": "cancel cancelled canceled cancellation cancellations cancelling",
    "noshow": "noshow no-show no-shows missed absent",
    "followup": "followup followups follow-up follow-ups revisit revisits repeat return returning",
    "new": "new first-time",
    "registration": "registration registrations registered register signup signups enrolled enrolment enrollment",
    "month": "monthly month-wise monthwise month",
    "week": "weekly week-wise weekwise week",
    "day": "daily day-wise daywise day",
    "year": "yearly annual annually year-wise year",
    "compare": "compare compared comparing comparison vs versus against",
    "trend": "trend trends over-time",
    "change": "change changes growth grew increase increased decrease decreased difference",
    "gender": "gender genders sex",
    "nationality": "nationality nationalities country countries",
    "age": "age ages age-group age-groups",
    "status": "status statuses state",
    "revenue": "revenue revenues income earnings sales billing billed",
    "service": "service services procedure procedures test tests",
    "medicine": "medicine medicines medication medications drug drugs prescription prescriptions",
    "complaint": "complaint complaints symptom symptoms",
    "diagnosis": "diagnosis diagnoses diagnosed diagnose diagnostic diagnostics dx condition conditions illness illnesses disease diseases icd",
    "average": "average avg mean",
    "previous": "previous prior preceding earlier",
}.items():
    for w in words.split():
        _SYNONYMS[w] = canonical

_STOP = set("""
a an the of in on at by per for to from with within and or is are was were be been being am do does did done
have has had show me list give get find tell display please can could would should will i we our us my your you
what which who whom whose how many much each every all there their this that these those it its number count
counts total totals amount see saw seen sees treated attended handled made happened so far overall currently
data report reports wise than more fewer less just only also about across between during over into some any
when where why breakdown broken split down up out view chart graph table per-month period periods
""".split())

# Generic words count half when judging how specific a phrasing is.
_LOW_WEIGHT = {"appointment", "patient"}


def _stem(w: str) -> str:
    if w in _SYNONYMS:
        return _SYNONYMS[w]
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("s", "")):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            base = w[: len(w) - len(suffix)] + repl
            return _SYNONYMS.get(base, base)
    return w


def terms(text: str) -> List[str]:
    """Meaningful, normalised words of a question (slots like {period} removed)."""
    text = re.sub(r"\{[a-z_]+\}", " ", text.lower())
    text = text.replace("no show", "noshow").replace("follow up", "followup").replace("over time", "over-time")
    out = []
    for w in re.findall(r"[a-z][a-z\-]*", text):
        if w in _STOP:
            continue
        t = _stem(w)
        if t and t not in _STOP and t not in out:
            out.append(t)
    return out


_SECOND_PERIOD = re.compile(
    r"\b(?:the\s+)?(?:last|previous|prior|preceding|same)\s+(?:week|month|quarter|year|period)(?:\s+last\s+year)?\b", re.I)


_OWN_BRANCH = re.compile(r"\b(?:in|at|for|of)?\s*(?:my|our|this)\s+(?:branch|clinic)\b", re.I)


def _coverage(q: List[str], p: List[str]) -> Tuple[float, float]:
    qs, ps = set(q), set(p)
    if not qs or not ps:
        return 0.0, 0.0
    w = lambda t: 0.5 if t in _LOW_WEIGHT else 1.0          # noqa: E731
    both = sum(w(t) for t in qs & ps)
    return both / sum(w(t) for t in qs), both / sum(w(t) for t in ps)


# ── Templates ─────────────────────────────────────────────────────────────────

@dataclass
class Template:
    id: str
    scopes: List[str]
    questions: List[str]
    sql: str
    defaults: Dict[str, Any] = field(default_factory=dict)
    chart: Any = None
    branch_compare: bool = False
    fixed: Dict[str, Any] = field(default_factory=dict)   # learned: period/top_n baked into the SQL
    source: str = "examples"

    @property
    def slots(self) -> set:
        return set(re.findall(r"\{([a-z_]+)\}", self.sql))

    @property
    def has_period(self) -> bool:
        return bool(self.slots & {"date_from", "date_to", "date_last", "prev_from", "prev_to", "prev_last"})

    def to_yaml(self) -> dict:
        data = {"id": self.id, "scope": self.scopes[0] if len(self.scopes) == 1 else self.scopes,
                "questions": self.questions, "sql": self.sql}
        for key in ("defaults", "chart", "fixed"):
            if getattr(self, key):
                data[key] = getattr(self, key)
        if self.branch_compare:
            data["branch_compare"] = True
        return data


@dataclass
class Match:
    template: Template
    phrasing: str
    params: Dict[str, Any]          # period (Period), top_n (int)
    auto: bool
    score: float

    def question_text(self) -> str:
        return render_question(self.phrasing, self.params)


def render_question(phrasing: str, params: Dict[str, Any]) -> str:
    text = phrasing
    if "period" in params:
        text = text.replace("{period}", params["period"].label)
    if "top_n" in params:
        text = text.replace("{top_n}", str(params["top_n"]))
    text = re.sub(r"\s*\{[a-z_]+\}", "", text)
    return text[:1].upper() + text[1:]


def _sql_date(d: dt.date) -> str:
    return f"'{d.isoformat()}'"


def render_sql(template: Template, params: Dict[str, Any]) -> str:
    values: Dict[str, str] = {}
    period: Optional[Period] = params.get("period")
    if period:
        prev_from, prev_to = period.previous()
        values.update(
            date_from=_sql_date(period.start), date_to=_sql_date(period.end),
            date_last=_sql_date(period.end - dt.timedelta(days=1)),
            prev_from=_sql_date(prev_from), prev_to=_sql_date(prev_to),
            prev_last=_sql_date(prev_to - dt.timedelta(days=1)),
        )
    if "top_n" in params:
        values["top_n"] = str(int(params["top_n"]))
    missing = template.slots - set(values)
    if missing:
        raise ValueError(f"Template {template.id} needs values for: {', '.join(sorted(missing))}")
    return re.sub(r"\{([a-z_]+)\}", lambda m: values[m[1]], template.sql).strip()


def _normalise_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


# ── Library ───────────────────────────────────────────────────────────────────

class Library:
    def __init__(self, few_shots_dir: str, learned_path: str):
        self.dir = few_shots_dir
        self.learned_path = learned_path
        self._templates: Optional[List[Template]] = None
        self._lock = threading.Lock()

    # Loading ------------------------------------------------------------------
    def templates(self) -> List[Template]:
        if self._templates is None:
            with self._lock:
                if self._templates is None:
                    self._templates = self._load()
        return self._templates

    def reload(self) -> None:
        with self._lock:
            self._templates = None

    def _load(self) -> List[Template]:
        out: List[Template] = []
        files = sorted(glob.glob(os.path.join(self.dir, "*.yaml")))
        learned = os.path.abspath(self.learned_path)
        if os.path.isfile(learned) and learned not in map(os.path.abspath, files):
            files.append(learned)
        for path in files:
            source = "learned" if os.path.abspath(path) == learned else "examples"
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            except Exception as exc:
                logger.warning("[Library] Could not read %s: %s", path, exc)
                continue
            for i, item in enumerate((data.get("templates") or []) + (data.get("examples") or [])):
                t = self._parse(item, f"{os.path.basename(path)}#{i}", source)
                if t:
                    out.append(t)
        logger.info("[Library] %d query templates loaded.", len(out))
        return out

    def _parse(self, item: dict, fallback_id: str, source: str) -> Optional[Template]:
        if not isinstance(item, dict) or not item.get("sql"):
            return None
        if "questions" not in item and item.get("question"):
            # v1 example ({question, sql}) — upgrade to a template on the fly.
            return templatize(item["question"], item["sql"], item.get("scope") or "*", source=source)
        scopes = item.get("scope") or "*"
        return Template(
            id=str(item.get("id") or fallback_id),
            scopes=scopes if isinstance(scopes, list) else [scopes],
            questions=[str(q) for q in item.get("questions") or []],
            sql=str(item["sql"]).strip(),
            defaults=item.get("defaults") or {},
            chart=item.get("chart"),
            branch_compare=bool(item.get("branch_compare")),
            fixed=item.get("fixed") or {},
            source=source,
        )

    # Matching -----------------------------------------------------------------
    def match(self, question: str, scope: str, can_compare_branches: bool,
              today: Optional[dt.date] = None) -> Tuple[Optional[Match], List[Match]]:
        """(confident match or None, 'did you mean' suggestions)."""
        today = today or dt.date.today()
        found = parse_period(question, today)
        top = parse_top_n(question)
        text = question
        for span in sorted([s for s in (found and found[1], top and top[1]) if s], reverse=True):
            text = text[: span[0]] + " " + text[span[1]:]
        # "…this month vs last month": the second period just means "the previous period".
        text = _SECOND_PERIOD.sub(" previous ", text)
        # "in my branch" adds nothing: answers are always limited to the user's branch.
        text = _OWN_BRANCH.sub(" ", text)
        q_terms = terms(text)
        if not q_terms:
            return None, []

        candidates: List[Match] = []
        for t in self.templates():
            if "*" not in t.scopes and scope not in t.scopes:
                continue
            if t.branch_compare and not can_compare_branches:
                continue
            params, compatible = self._params(t, found, top, today)
            if params is None:
                continue
            for phrasing in t.questions:
                q_cov, p_cov = _coverage(q_terms, terms(phrasing))
                score = (q_cov + p_cov) / 2
                if score < SUGGEST_SCORE:
                    continue
                auto = compatible and q_cov >= AUTO_QUESTION_COVERAGE and p_cov >= AUTO_PHRASING_COVERAGE
                candidates.append(Match(t, phrasing, params, auto, score))

        candidates.sort(key=lambda m: (m.auto, m.score), reverse=True)
        best = candidates[0] if candidates and candidates[0].auto else None
        seen, suggestions = set(), []
        for m in candidates:
            if m.template.id in seen or (best and m.template.id == best.template.id):
                continue
            seen.add(m.template.id)
            suggestions.append(m)
        return best, suggestions[:3]

    def _params(self, t: Template, found, top, today) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Slot values for a template, and whether the question fits it exactly."""
        params: Dict[str, Any] = {}
        compatible = True
        if t.has_period:
            if found:
                params["period"] = found[0]
            else:
                default = parse_period(str(t.defaults.get("period", "this month")), today)
                params["period"] = default[0] if default else _calendar("month", "this", today)
        elif found:
            # The SQL cannot change period: only an exact fixed-period match fits.
            compatible = t.fixed.get("period") == found[0].key
            params["period"] = found[0]
        elif t.fixed.get("period"):
            compatible = False
        if "top_n" in t.slots:
            params["top_n"] = top[0] if top else int(t.defaults.get("top_n", 10))
        elif top:
            compatible = compatible and t.fixed.get("top_n") == top[0]
        return params, compatible

    def related(self, question: str, scope: str, can_compare_branches: bool, exclude: Optional[str],
                period: Optional[Period] = None, limit: int = 3) -> List[str]:
        """Other library questions for this section — free follow-ups (no LLM)."""
        q_terms = terms(question)
        scored = []
        for t in self.templates():
            if t.id == exclude or not t.questions or ("*" not in t.scopes and scope not in t.scopes):
                continue
            if t.branch_compare and not can_compare_branches:
                continue
            if t.fixed.get("period") or t.source == "learned":
                continue
            phrasing = t.questions[0]
            overlap = len(set(q_terms) & set(terms(phrasing)))
            params = {}
            if "{period}" in phrasing:
                default = parse_period(str(t.defaults.get("period", "this month")))
                default = default[0] if default else None
                # Keep the asked period unless it is too short for this question
                # (e.g. "appointments by month" needs more than one month).
                fits = period and (not default or (period.end - period.start) >= (default.end - default.start))
                if fits or default:
                    params["period"] = period if fits else default
            if "{top_n}" in phrasing:
                params["top_n"] = int(t.defaults.get("top_n", 10))
            scored.append((overlap, render_question(phrasing, params)))
        scored.sort(key=lambda x: -x[0])
        return [q for _, q in scored[:limit]]

    def get(self, template_id: str) -> Optional[Template]:
        return next((t for t in self.templates() if t.id == template_id), None)

    # Few-shot examples for the LLM / Vanna ------------------------------------
    def as_examples(self, today: Optional[dt.date] = None) -> List[dict]:
        out = []
        for t in self.templates():
            if not t.questions:
                continue
            params, _ = self._params(t, None, None, today or dt.date.today())
            if t.fixed.get("top_n"):
                params.pop("top_n", None)
            try:
                out.append({"question": render_question(t.questions[0], params),
                            "sql": render_sql(t, params), "tables_used": []})
            except ValueError:
                continue
        return out

    # Learning from 👍 ---------------------------------------------------------
    def learn(self, question: str, sql: str, scope: str, branch_compare: bool = False) -> str:
        """Save a confirmed question→SQL pair as a template (or a new phrasing of one)."""
        new = templatize(question, sql, scope, source="learned")
        new.branch_compare = branch_compare
        with self._lock:
            templates = self._templates if self._templates is not None else self._load()
            same = next((t for t in templates
                         if _normalise_sql(t.sql) == _normalise_sql(new.sql)
                         and t.fixed == new.fixed and set(t.scopes) & set(new.scopes)), None)
            if same:
                if new.questions[0] not in same.questions:
                    same.questions.append(new.questions[0])
                target = same
            else:
                templates.append(new)
                target = new
            self._templates = templates
            self._save_learned(templates)
        return target.id

    def _save_learned(self, templates: List[Template]) -> None:
        learned = [t.to_yaml() for t in templates if t.source == "learned"]
        try:
            os.makedirs(os.path.dirname(self.learned_path) or ".", exist_ok=True)
            with open(self.learned_path, "w", encoding="utf-8") as f:
                f.write("# Added automatically from 👍 feedback in Ask AI. Safe to edit or delete entries.\n")
                yaml.safe_dump({"version": "v2", "templates": learned}, f, sort_keys=False,
                               allow_unicode=True, width=1000)
        except OSError as exc:
            logger.warning("[Library] Could not save %s: %s", self.learned_path, exc)


_DATE_LIT = re.compile(r"'(\d{4}-\d{2}-\d{2})(?:[ T]00:00(?::00(?:\.0+)?)?)?'")


def templatize(question: str, sql: str, scope: str, source: str = "learned",
               today: Optional[dt.date] = None) -> Template:
    """
    Turn a concrete question + SQL into a reusable template, without an LLM:
    the dates of the question's period and its top-N in the SQL become slots.
    If that is not possible exactly, the values are kept fixed instead.
    """
    today = today or dt.date.today()
    found = parse_period(question, today)
    top = parse_top_n(question)
    phrasing, tsql, fixed = question.strip(), sql.strip(), {}
    replacements = []

    if found:
        period = found[0]
        targets = {period.start.isoformat(): "{date_from}", period.end.isoformat(): "{date_to}",
                   (period.end - dt.timedelta(days=1)).isoformat(): "{date_last}"}
        replaced = _DATE_LIT.sub(lambda m: targets.get(m[1], m[0]), tsql)
        if replaced != tsql:
            tsql = replaced
            replacements.append((found[1], "{period}"))
        else:
            fixed["period"] = period.key
    if top:
        n, span = top
        pattern = re.compile(rf"\bTOP(\s*\(\s*|\s+){n}(\s*\))?(?=\W)", re.I)   # TOP 5 / TOP (5)
        if pattern.search(tsql):
            tsql = pattern.sub(lambda m: f"TOP{m[1]}{{top_n}}{m[2] or ''}", tsql)
            replacements.append((span, "{top_n}"))
        else:
            fixed["top_n"] = n
    for (start, end), slot in sorted(replacements, reverse=True):
        phrasing = phrasing[:start] + slot + phrasing[end:]
    phrasing = re.sub(r"\s+", " ", phrasing).strip(" ?.!")

    t = Template(id="learned_" + hashlib.sha1(_normalise_sql(tsql).encode()).hexdigest()[:10],
                 scopes=[scope], questions=[phrasing], sql=tsql, fixed=fixed, source=source)
    # Safety check: re-filling the slots must give back exactly the original SQL.
    params = {}
    if found:
        params["period"] = found[0]
    if top:
        params["top_n"] = top[0]
    try:
        ok = _normalise_sql(render_sql(t, params)) == _normalise_sql(sql)
    except ValueError:
        ok = False
    if not ok:
        t.sql, t.questions = sql.strip(), [question.strip(" ?.!")]
        t.fixed = {k: v for k, v in {"period": found and found[0].key, "top_n": top and top[0]}.items() if v}
    return t


_library: Optional[Library] = None


def get_library() -> Library:
    global _library
    if _library is None:
        _library = Library(os.path.join(settings.semantic_yaml_dir, "few_shots"),
                           settings.vanna_learned_examples_path)
    return _library
