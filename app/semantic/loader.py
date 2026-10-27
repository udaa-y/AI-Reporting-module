"""
app/semantic/loader.py
────────────────────────
YAML Semantic Layer Loader

Reads and parses all YAML business definition files from the
/semantic directory tree. Results are cached in memory after the
first load to avoid repeated disk I/O on every query.

Directory structure expected:
  semantic/
    metrics/     → business metric definitions (revenue.yaml, etc.)
    tables/      → table + column descriptions (orders.yaml, etc.)
    joins/       → approved join paths (approved_joins.yaml)
    synonyms/    → vocabulary mappings (vocabulary.yaml)
    few_shots/   → verified Q→SQL examples (examples.yaml)
"""

import glob
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List

import yaml

from app.agent.state import FewShotExample, RetrievedTable, SemanticDefinition

logger = logging.getLogger(__name__)


class SemanticLoader:
    """
    Loads and caches YAML semantic definitions from the filesystem.
    Thread-safe for read access after initial load.
    """

    def __init__(self, base_dir: str):
        self.base_dir = os.path.abspath(base_dir)
        self._tables: List[RetrievedTable] | None = None
        self._metrics: List[SemanticDefinition] | None = None
        self._joins: List[Dict] | None = None
        self._few_shots: List[FewShotExample] | None = None
        logger.info("[SemanticLoader] Base dir: %s", self.base_dir)

    # ── Included Tables (Whitelist) ───────────────────────────────────────────

    def get_included_table_metadata(self) -> dict[str, dict]:
        """
        Return a dict mapping lowercase table name -> metadata dict from included_tables.yaml.
        Supports both:
          - tables: [TableA, TableB]
          - tables:
              - name: TableA
                description: "TableA description..."
        """
        meta = {}
        config_path = os.path.join(self.base_dir, "included_tables.yaml")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        table_list = data.get("tables")
                        if isinstance(table_list, list):
                            for t in table_list:
                                if isinstance(t, str) and t.strip():
                                    clean_name = t.strip()
                                    meta[clean_name.lower()] = {"name": clean_name, "description": ""}
                                elif isinstance(t, dict) and t.get("name"):
                                    clean_name = str(t["name"]).strip()
                                    meta[clean_name.lower()] = {
                                        "name": clean_name,
                                        "description": str(t.get("description", "")).strip(),
                                    }
            except Exception as exc:
                logger.warning("[SemanticLoader] Error reading %s: %s", config_path, exc)
        return meta

    def get_included_tables(self) -> set[str] | None:
        """
        Return the set of lowercase table names explicitly whitelisted to be learned.
        Sources:
          1. semantic/included_tables.yaml (tables: [...])
          2. INCLUDED_TABLES env var / setting (comma-separated)
        Returns None if no filter is configured (meaning: allow all available tables).
        """
        meta = self.get_included_table_metadata()
        included = set(meta.keys())

        # 2. From settings / .env
        try:
            from app.config import get_settings
            env_tables = get_settings().included_tables
            if env_tables:
                clean_env = env_tables.split("#")[0].strip()
                if clean_env:
                    for t in clean_env.split(","):
                        clean = t.strip().lower()
                        if clean:
                            included.add(clean)
        except Exception:
            pass

        return included if included else None

    # ── Tables ────────────────────────────────────────────────────────────────

    def load_all_tables(self) -> List[RetrievedTable]:
        if self._tables is None:
            raw_tables = self._load_yaml_dir("tables")
            table_meta = self.get_included_table_metadata()

            # Normalize table_name field and enrich descriptions
            tables = []
            for t in raw_tables:
                if not isinstance(t, dict):
                    continue
                name = t.get("table") or t.get("table_name") or ""
                t["table_name"] = name
                name_lower = name.lower()

                # Merge custom table description from included_tables.yaml if available
                if name_lower in table_meta and table_meta[name_lower].get("description"):
                    custom_desc = table_meta[name_lower]["description"]
                    existing_desc = t.get("description", "")
                    if custom_desc not in existing_desc:
                        t["description"] = f"{custom_desc}. {existing_desc}".strip()

                tables.append(t)

            # Apply whitelist filter if configured
            whitelist = self.get_included_tables()
            if whitelist is not None:
                tables = [
                    t for t in tables
                    if t.get("table_name", "").lower() in whitelist
                ]
                logger.info(
                    "[SemanticLoader] Filtered tables by inclusion list (%d allowed): %s",
                    len(tables), [t.get("table_name") for t in tables]
                )

            self._tables = tables
            logger.info("[SemanticLoader] Loaded %d table definitions.", len(self._tables))
        return self._tables

    # ── Metrics ───────────────────────────────────────────────────────────────

    def load_all_metrics(self) -> List[SemanticDefinition]:
        if self._metrics is None:
            raw = self._load_yaml_dir("metrics")
            metrics = [self._parse_metric(m) for m in raw]

            # Apply whitelist filter on metric dependencies if configured
            whitelist = self.get_included_tables()
            if whitelist is not None:
                filtered_metrics = []
                for m in metrics:
                    deps = [d.lower() for d in m.get("table_dependencies", [])]
                    # Keep if metric has no specific dependencies or all dependencies are in whitelist
                    if not deps or all(d in whitelist for d in deps):
                        filtered_metrics.append(m)
                metrics = filtered_metrics

            self._metrics = metrics
            logger.info("[SemanticLoader] Loaded %d metric definitions.", len(self._metrics))
        return self._metrics

    def _parse_metric(self, raw: Dict) -> SemanticDefinition:
        return {
            "name": raw.get("metric", raw.get("name", "Unknown")),
            "aliases": raw.get("aliases", []),
            "definition": raw.get("definition", ""),
            "calculation": raw.get("calculation", {}).get("expression")
                if isinstance(raw.get("calculation"), dict)
                else raw.get("calculation"),
            "table_dependencies": raw.get("table_dependencies", []),
            "filters": raw.get("calculation", {}).get("filters", [])
                if isinstance(raw.get("calculation"), dict)
                else [],
        }

    # ── Joins ─────────────────────────────────────────────────────────────────

    def load_approved_joins(self) -> List[Dict]:
        if self._joins is None:
            raw = self._load_yaml_dir("joins")
            # Flatten — join files may contain a list under 'approved_joins' key
            joins = []
            for item in raw:
                if "approved_joins" in item:
                    joins_list = item.get("approved_joins")
                    if isinstance(joins_list, list):
                        joins.extend(joins_list)
                elif isinstance(item, dict):
                    joins.append(item)

            # Apply whitelist filter to joins if configured
            whitelist = self.get_included_tables()
            if whitelist is not None:
                joins = [
                    j for j in joins
                    if j.get("from_table", "").lower() in whitelist
                    and j.get("to_table", "").lower() in whitelist
                ]

            self._joins = joins
            logger.info("[SemanticLoader] Loaded %d approved join paths.", len(self._joins))
        return self._joins

    # ── Few-Shot Examples ─────────────────────────────────────────────────────

    def load_few_shots(self, query: str = "", top_k: int = 3) -> List[FewShotExample]:
        """
        Load all verified Q→SQL examples. If a query is provided,
        return those whose keywords overlap most with the query.
        """
        if self._few_shots is None:
            raw = self._load_yaml_dir("few_shots")
            self._few_shots = []
            for item in raw:
                examples = item.get("examples")
                if isinstance(examples, list):
                    for ex in examples:
                        if isinstance(ex, dict) and ex.get("question"):
                            self._few_shots.append({
                                "question": ex.get("question", ""),
                                "sql": ex.get("sql", ""),
                                "tables_used": ex.get("tables_used", []),
                            })
                elif isinstance(item, dict) and item.get("question"):
                    self._few_shots.append({
                        "question": item.get("question", ""),
                        "sql": item.get("sql", ""),
                        "tables_used": item.get("tables_used", []),
                    })
            logger.info("[SemanticLoader] Loaded %d few-shot examples.", len(self._few_shots))

        if not query or not self._few_shots:
            return self._few_shots[:top_k]

        # Simple keyword overlap scoring
        query_words = set(query.lower().split())
        scored = []
        for ex in self._few_shots:
            ex_words = set(ex["question"].lower().split())
            score = len(query_words & ex_words)
            scored.append((score, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:top_k]]

    # ── Synonyms ──────────────────────────────────────────────────────────────

    def load_synonyms(self) -> Dict[str, str]:
        """Return {synonym: canonical_term} mapping."""
        raw = self._load_yaml_dir("synonyms")
        result = {}
        for item in raw:
            mappings = item.get("mappings", item)
            if isinstance(mappings, dict):
                result.update(mappings)
        return result

    # ── Internal YAML loader ──────────────────────────────────────────────────

    def _load_yaml_dir(self, subdir: str) -> List[Dict[str, Any]]:
        """Load all .yaml files from a subdirectory. Returns list of parsed dicts."""
        path = os.path.join(self.base_dir, subdir, "*.yaml")
        files = glob.glob(path)

        if not files:
            logger.debug("[SemanticLoader] No YAML files in %s", path)
            return []

        results = []
        for filepath in sorted(files):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data:
                        results.append(data)
            except Exception as exc:
                logger.warning("[SemanticLoader] Failed to load %s: %s", filepath, exc)

        return results
