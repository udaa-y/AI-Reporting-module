"""
app/semantic/retriever.py
──────────────────────────
Semantic Context Retriever

Wraps Qdrant vector search to retrieve the most relevant tables,
metrics, and few-shot examples for a given user query.

Architecture:
  1. Embed the user query using OpenAI text-embedding-3-small
  2. Search Qdrant for top-K similar schema/metric chunks
  3. Filter and assemble context for the LLM prompt

Falls back gracefully to YAML-only mode if Qdrant is unavailable.
"""

import logging
from typing import Any, Dict, List, Optional

from openai import OpenAI

from app.agent.state import FewShotExample, RetrievedTable, SemanticDefinition
from app.config import get_settings
from app.semantic.loader import SemanticLoader

logger = logging.getLogger(__name__)
settings = get_settings()


class SemanticRetriever:
    """
    Handles all semantic context retrieval for the query pipeline.

    Initialization is lazy — Qdrant client is only created if available.
    Falls back to YAML-based keyword matching in dev/offline mode.
    """

    def __init__(self):
        self._loader = SemanticLoader(settings.semantic_yaml_dir)
        self._openai_client = None
        self._qdrant = self._init_qdrant()

    @property
    def openai_client(self) -> OpenAI:
        if self._openai_client is None:
            kwargs = {"api_key": settings.openai_api_key or "sk-dummy"}
            if settings.clean_openai_base_url:
                kwargs["base_url"] = settings.clean_openai_base_url
            self._openai_client = OpenAI(**kwargs)
        return self._openai_client

    def _init_qdrant(self):
        """Attempt to initialize Qdrant client. Return None if unavailable."""
        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(
                url=settings.qdrant_url,
                api_key=settings.qdrant_api_key or None,
                timeout=5,
            )
            # Quick connectivity check
            client.get_collections()
            logger.info("[Retriever] Qdrant connected at %s", settings.qdrant_url)
            return client
        except Exception as exc:
            logger.warning(
                "[Retriever] Qdrant not available (%s). "
                "Falling back to YAML-only retrieval.", exc
            )
            return None

    def _embed(self, text: str) -> List[float]:
        """Embed a text string using OpenAI embeddings."""
        response = self.openai_client.embeddings.create(
            model=settings.embedding_model,
            input=text,
        )
        return response.data[0].embedding

    # ── Table Retrieval ───────────────────────────────────────────────────────

    def retrieve_tables(self, query: str) -> List[RetrievedTable]:
        """
        Return the most relevant tables for the query.
        Caps at settings.max_tables_in_context tables.
        """
        all_tables = self._loader.load_all_tables()

        if self._qdrant:
            return self._qdrant_retrieve_tables(query, all_tables)

        # Fallback: relevance ranking based on query keyword matching
        q_lower = query.lower()
        scored_tables = []
        for table in all_tables:
            tname = table.get("table_name", "").lower()
            desc = table.get("description", "").lower()
            score = 0
            for word in q_lower.replace("_", " ").split():
                if len(word) > 2:
                    if word in tname:
                        score += 4
                    if word in desc:
                        score += 2
            for col in table.get("columns", []):
                cname = (col.get("name") or col.get("COLUMN_NAME") or "").lower()
                if cname and len(cname) > 2 and cname in q_lower:
                    score += 1
            scored_tables.append((score, table))

        scored_tables.sort(key=lambda x: x[0], reverse=True)
        top_positive = [t for s, t in scored_tables if s > 0]
        if top_positive:
            logger.debug(
                "[Retriever] Fallback keyword match: returning %d tables (%s)",
                len(top_positive[: settings.max_tables_in_context]),
                [t.get("table_name") for t in top_positive[: settings.max_tables_in_context]],
            )
            return top_positive[: settings.max_tables_in_context]

        logger.debug("[Retriever] Fallback: returning all %d tables.", len(all_tables))
        return all_tables[: settings.max_tables_in_context]

    def _qdrant_retrieve_tables(
        self, query: str, all_tables: List[RetrievedTable]
    ) -> List[RetrievedTable]:
        """Vector-search Qdrant for relevant table chunks."""
        try:
            query_vector = self._embed(query)
            hits = self._qdrant.search(
                collection_name=settings.qdrant_collection_schema,
                query_vector=query_vector,
                limit=settings.top_k_retrieval,
                with_payload=True,
            )
            # Map hit payloads back to RetrievedTable objects
            table_names_scored: Dict[str, float] = {}
            for hit in hits:
                tname = hit.payload.get("table_name", "")
                if tname and hit.score > table_names_scored.get(tname, 0):
                    table_names_scored[tname] = hit.score

            # Sort by score, take top K
            top_names = sorted(
                table_names_scored, key=table_names_scored.get, reverse=True
            )[: settings.top_k_after_rerank]

            # Retrieve full table objects from loader (preserves column details)
            name_to_table = {t["table_name"].lower(): t for t in all_tables}
            result = []
            for name in top_names:
                table = name_to_table.get(name.lower())
                if table:
                    table["relevance_score"] = table_names_scored[name]
                    result.append(table)

            return result

        except Exception as exc:
            logger.warning("[Retriever] Qdrant table search failed: %s", exc)
            return all_tables[: settings.max_tables_in_context]

    # ── Metric Retrieval ──────────────────────────────────────────────────────

    def retrieve_metrics(self, query: str) -> List[SemanticDefinition]:
        """Return business metric definitions relevant to the query."""
        all_metrics = self._loader.load_all_metrics()

        if not all_metrics:
            return []

        if self._qdrant:
            try:
                return self._qdrant_retrieve_metrics(query, all_metrics)
            except Exception as exc:
                logger.warning("[Retriever] Qdrant metric search failed: %s", exc)

        # Fallback: keyword match on metric names and aliases
        query_lower = query.lower()
        matched = []
        for metric in all_metrics:
            terms = [metric["name"].lower()] + [
                a.lower() for a in metric.get("aliases", [])
            ]
            if any(term in query_lower for term in terms):
                matched.append(metric)

        return matched or all_metrics  # Return all if no keyword match

    def _qdrant_retrieve_metrics(
        self, query: str, all_metrics: List[SemanticDefinition]
    ) -> List[SemanticDefinition]:
        query_vector = self._embed(query)
        hits = self._qdrant.search(
            collection_name=settings.qdrant_collection_metrics,
            query_vector=query_vector,
            limit=5,
            with_payload=True,
        )
        metric_names = {h.payload.get("name", "").lower() for h in hits}
        return [m for m in all_metrics if m["name"].lower() in metric_names]

    # ── Approved Joins ────────────────────────────────────────────────────────

    def get_approved_joins(
        self, retrieved_tables: List[RetrievedTable]
    ) -> List[Dict[str, str]]:
        """
        Return approved join paths that involve the retrieved tables.
        Filters the full join list to only paths between present tables.
        """
        all_joins = self._loader.load_approved_joins()
        table_names = {t["table_name"].lower() for t in retrieved_tables}

        relevant = []
        for join in all_joins:
            from_t = join.get("from_table", "").lower()
            to_t = join.get("to_table", "").lower()
            if from_t in table_names and to_t in table_names:
                relevant.append(join)

        return relevant

    # ── Few-Shot Examples ─────────────────────────────────────────────────────

    def retrieve_few_shots(self, query: str) -> List[FewShotExample]:
        """Return up to 3 verified Q→SQL pairs relevant to the query."""
        return self._loader.load_few_shots(query, top_k=3)


_retriever: Optional[SemanticRetriever] = None


def get_retriever() -> SemanticRetriever:
    """Singleton accessor for the SemanticRetriever."""
    global _retriever
    if _retriever is None:
        _retriever = SemanticRetriever()
    return _retriever
