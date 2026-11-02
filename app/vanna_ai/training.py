"""
app/vanna_ai/training.py
─────────────────────────
Trains Vanna from the semantic YAML layer — the same single source of truth
the SQL generator uses, so the reviewer and the generator never disagree
about what the schema looks like.

Training items produced:
  DDL            CREATE TABLE statements built from semantic/tables/*.yaml
  Documentation  table descriptions (included_tables.yaml), business notes,
                 meaningful column descriptions, approved joins, metrics,
                 synonyms, semantic/documentation/*.md (domain knowledge you
                 write in plain English) and T-SQL dialect rules
  Question→SQL   semantic/few_shots/*.yaml (incl. learned_examples.yaml)
"""

import logging
import os
import re
import threading
from typing import Dict, List

import yaml

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_PLACEHOLDER_NOTES = ("add domain-specific notes",)
_learn_lock = threading.Lock()

DIALECT_DOC = (
    "SQL dialect: Microsoft SQL Server (T-SQL). Use SELECT TOP (N) or "
    "OFFSET ... FETCH NEXT, never LIMIT. Dates: GETDATE(), DATEADD(), DATEDIFF(), "
    "EOMONTH(), YEAR(), MONTH(), FORMAT(). Integer division truncates — CAST to "
    "DECIMAL for averages and percentages. Only SELECT statements are allowed."
)


def _is_meaningful(desc: str, name: str) -> bool:
    d = (desc or "").strip()
    if not d:
        return False
    low = d.lower()
    return not (
        low == f"column {name.lower()}"
        or low.startswith(f"table {name.lower()} in ")
        or any(low.startswith(p) for p in _PLACEHOLDER_NOTES)
    )


def table_to_ddl(table: dict) -> str:
    name = table.get("table_name") or table.get("table", "")
    schema = table.get("schema_name") or "dbo"
    lines = []
    for col in table.get("columns", []):
        cname = col.get("name") or col.get("COLUMN_NAME", "")
        ctype = (col.get("type") or col.get("DATA_TYPE") or "").upper()
        null = " NOT NULL" if str(col.get("IS_NULLABLE", "YES")).upper() == "NO" else ""
        desc = col.get("description") or ""
        comment = f" -- {desc}" if _is_meaningful(desc, cname) else ""
        lines.append(f"    [{cname}] {ctype}{null},{comment}")
    if lines:  # drop the trailing comma of the last column
        lines[-1] = re.sub(r",(?=( --|$))", "", lines[-1], count=1)
    return f"CREATE TABLE [{schema}].[{name}] (\n" + "\n".join(lines) + "\n);"


def table_to_doc(table: dict) -> str:
    name = table.get("table_name") or table.get("table", "")
    parts = []
    desc = table.get("description") or ""
    # The loader prepends the included_tables.yaml description to the placeholder one.
    desc = re.sub(rf"\.?\s*Table {re.escape(name)} in \w+ schema\.?", "", desc).strip()
    if desc:
        parts.append(desc.rstrip(".") + ".")
    notes = table.get("business_notes") or ""
    if _is_meaningful(notes, name):
        parts.append(f"Business rules: {notes}")
    return f"Table {name}: " + " ".join(parts) if parts else ""


def join_to_doc(join: dict) -> str:
    cond = join.get("condition", "")
    # Replace aliases (p.Id) with table names (Patient.Id) so the rule is unambiguous.
    for table, alias in (join.get("aliases") or {}).items():
        cond = re.sub(rf"\b{re.escape(str(alias))}\.", f"{table}.", cond)
    doc = (
        f"Approved relationship: {join.get('from_table')} {join.get('join_type', 'INNER JOIN')} "
        f"{join.get('to_table')} ON {cond}."
    )
    if join.get("notes"):
        doc += f" {join['notes']}"
    return doc


def metric_to_doc(metric: dict) -> str:
    doc = f"Metric '{metric['name']}': {metric.get('definition', '')}"
    if metric.get("aliases"):
        doc += f" Also called: {', '.join(metric['aliases'])}."
    if metric.get("calculation"):
        doc += f" Calculation: {metric['calculation']}."
    if metric.get("filters"):
        doc += f" Required filters: {'; '.join(metric['filters'])}."
    return doc


def build_training_items(loader) -> Dict[str, List]:
    tables = loader.load_all_tables()
    items: Dict[str, List] = {"ddl": [], "documentation": [DIALECT_DOC], "sql": []}

    for t in tables:
        items["ddl"].append(table_to_ddl(t))
        doc = table_to_doc(t)
        if doc:
            items["documentation"].append(doc)

    items["documentation"] += [join_to_doc(j) for j in loader.load_approved_joins()]
    items["documentation"] += [metric_to_doc(m) for m in loader.load_all_metrics()]

    synonyms = loader.load_synonyms()
    if synonyms:
        items["documentation"].append(
            "Vocabulary: " + "; ".join(f"'{k}' means '{v}'" for k, v in synonyms.items())
        )
    items["documentation"] += loader.load_documentation()

    for ex in loader.load_few_shots(top_k=10_000):
        if ex.get("question") and ex.get("sql"):
            items["sql"].append((ex["question"], ex["sql"]))
    return items


def train_from_semantic_layer(vn) -> dict:
    from app.semantic.retriever import get_retriever

    items = build_training_items(get_retriever()._loader)
    # vn.train() just dispatches to these and prints every item to stdout.
    for ddl in items["ddl"]:
        vn.add_ddl(ddl)
    for doc in items["documentation"]:
        vn.add_documentation(doc)
    for question, sql in items["sql"]:
        vn.add_question_sql(question=question, sql=sql)
    return {k: len(v) for k, v in items.items()}


def learn_example(question: str, sql: str) -> None:
    """
    Store a user-approved question→SQL pair.

    It is added to Vanna immediately and appended to the learned-examples YAML,
    which the semantic loader also reads — so the main SQL generator gets it as
    a few-shot example too.
    """
    from app.semantic.retriever import get_retriever
    from app.vanna_ai.engine import get_vanna

    get_vanna().add_question_sql(question=question, sql=sql)

    path = settings.vanna_learned_examples_path
    with _learn_lock:
        try:
            data = {}
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            examples = data.get("examples") or []
            if not any(e.get("question") == question and e.get("sql") == sql for e in examples):
                examples.append({"question": question, "sql": sql, "source": "user_feedback"})
            data = {"version": "v1", "examples": examples}
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
            get_retriever()._loader.reload()
        except OSError as exc:
            # e.g. read-only volume in Docker — Vanna still learned it for this process.
            logger.warning("[Vanna] Could not persist learned example to %s: %s", path, exc)
