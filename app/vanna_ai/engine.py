"""
app/vanna_ai/engine.py
───────────────────────
Vanna AI instance wired to the project's own LLM provider.

Vanna (https://vanna.ai) keeps a "trained" knowledge store of DDL,
documentation and verified question→SQL pairs, and retrieves the relevant
pieces for each question (RAG). We use its classic API (vanna.legacy) with:

  • Chat:   the same OpenAI-compatible endpoint/model as the rest of the app
  • Store:  "memory" — in-process, zero-latency (default; fine for small schemas)
            "qdrant" — persistent Qdrant collections embedded with OPENAI
                       embeddings (EMBEDDING_MODEL), for larger schemas

The instance is trained from the semantic YAML layer at startup
(see app/vanna_ai/training.py).
"""

import logging
import re
import threading
from typing import Dict, List, Optional

from openai import OpenAI
from vanna.legacy.base import VannaBase

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_WORD_RE = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+")


def _tokens(text: str) -> set:
    """Lower-case word tokens, splitting CamelCase / snake_case identifiers."""
    return {t.lower() for t in _WORD_RE.findall(text or "") if len(t) > 2}


# ── LLM (chat) mixin ──────────────────────────────────────────────────────────

class _ProjectChat:
    """Implements Vanna's chat interface on top of the project's OpenAI client."""

    def _init_chat(self):
        kwargs = {"api_key": settings.openai_api_key or "sk-dummy", "timeout": 60}
        if settings.clean_openai_base_url:
            kwargs["base_url"] = settings.clean_openai_base_url
        self.client = OpenAI(**kwargs)
        self.model = settings.vanna_model or settings.llm_sql_model
        self.fast_model = settings.vanna_fast_model or settings.llm_intent_model

    def system_message(self, message: str) -> dict:
        return {"role": "system", "content": message}

    def user_message(self, message: str) -> dict:
        return {"role": "user", "content": message}

    def assistant_message(self, message: str) -> dict:
        return {"role": "assistant", "content": message}

    def submit_prompt(self, prompt, **kwargs) -> str:
        if not prompt:
            raise ValueError("Prompt is empty")
        params = {
            "model": kwargs.get("model") or self.model,
            "messages": prompt,
            "temperature": kwargs.get("temperature", 0.0),
        }
        if kwargs.get("max_tokens"):
            params["max_tokens"] = kwargs["max_tokens"]
        if kwargs.get("json"):
            params["response_format"] = {"type": "json_object"}
        if kwargs.get("timeout"):
            params["timeout"] = kwargs["timeout"]
        response = self.client.chat.completions.create(**params)
        return (response.choices[0].message.content or "").strip()

    def log(self, message: str, title: str = "Info"):
        logger.debug("[Vanna] %s: %s", title, message)


# ── In-memory store ───────────────────────────────────────────────────────────

class InMemoryVectorStore(VannaBase):
    """
    Minimal Vanna store kept in process memory.

    The semantic layer is small, so ranking by token overlap is both instant
    and accurate; everything relevant fits in the prompt. No embedding calls.
    """

    def __init__(self, config=None):
        VannaBase.__init__(self, config=config or {})
        self._lock = threading.Lock()
        self._ddl: Dict[str, str] = {}
        self._docs: Dict[str, str] = {}
        self._sql: Dict[str, dict] = {}
        # Generous: Vanna's prompt builders already cap context by token budget,
        # and with a small schema every relationship should reach the prompt.
        self.n_results = (config or {}).get("n_results", 50)

    def _rank(self, question: str, items: Dict[str, str], n: int) -> List[str]:
        q = _tokens(question)
        scored = sorted(
            items.items(), key=lambda kv: len(q & _tokens(kv[1])), reverse=True
        )
        return [k for k, _ in scored[:n]]

    def generate_embedding(self, data: str, **kwargs) -> List[float]:
        return []

    def add_ddl(self, ddl: str, **kwargs) -> str:
        with self._lock:
            self._ddl[ddl] = ddl
        return f"{abs(hash(ddl))}-ddl"

    def add_documentation(self, documentation: str, **kwargs) -> str:
        with self._lock:
            self._docs[documentation] = documentation
        return f"{abs(hash(documentation))}-doc"

    def add_question_sql(self, question: str, sql: str, **kwargs) -> str:
        key = f"{question}\n{sql}"
        with self._lock:
            self._sql[key] = {"question": question, "sql": sql}
        return f"{abs(hash(key))}-sql"

    def get_related_ddl(self, question: str, **kwargs) -> list:
        return self._rank(question, self._ddl, self.n_results)

    def get_related_documentation(self, question: str, **kwargs) -> list:
        return self._rank(question, self._docs, self.n_results)

    def get_similar_question_sql(self, question: str, **kwargs) -> list:
        texts = {k: v["question"] for k, v in self._sql.items()}
        return [self._sql[k] for k in self._rank(question, texts, 5)]

    def get_training_data(self, **kwargs):
        import pandas as pd
        rows = (
            [{"training_data_type": "ddl", "content": d} for d in self._ddl]
            + [{"training_data_type": "documentation", "content": d} for d in self._docs]
            + [{"training_data_type": "sql", "question": v["question"], "content": v["sql"]}
               for v in self._sql.values()]
        )
        return pd.DataFrame(rows)

    def remove_training_data(self, id: str, **kwargs) -> bool:
        return False

    def reset(self):
        with self._lock:
            self._ddl.clear()
            self._docs.clear()
            self._sql.clear()


class ClinicVannaMemory(_ProjectChat, InMemoryVectorStore):
    def __init__(self, config=None):
        InMemoryVectorStore.__init__(self, config=config)
        self._init_chat()


# ── Qdrant store (optional) ───────────────────────────────────────────────────

def _build_qdrant_vanna():
    from qdrant_client import QdrantClient
    from vanna.legacy.qdrant import Qdrant_VectorStore
    from vanna.legacy.utils import deterministic_uuid

    class ClinicVannaQdrant(_ProjectChat, Qdrant_VectorStore):
        """Qdrant-backed Vanna, embedded with the project's OpenAI embeddings."""

        def __init__(self, config):
            self._init_chat()
            self._embedding_cache: Dict[str, List[float]] = {}
            Qdrant_VectorStore.__init__(self, config=config)

        @property
        def embeddings_dimension(self):
            return settings.embedding_dimensions

        def generate_embedding(self, data: str, **kwargs) -> List[float]:
            # One question is looked up in 3 collections — embed it only once.
            cached = self._embedding_cache.get(data)
            if cached is None:
                resp = self.client.embeddings.create(model=settings.embedding_model, input=data)
                cached = resp.data[0].embedding
                if len(self._embedding_cache) > 256:
                    self._embedding_cache.clear()
                self._embedding_cache[data] = cached
            return cached

        def _exists(self, collection: str, content: str) -> bool:
            return bool(self._client.retrieve(collection, ids=[deterministic_uuid(content)]))

        # Skip re-embedding content that is already stored (fast restarts).
        def add_ddl(self, ddl: str, **kwargs) -> str:
            if self._exists(self.ddl_collection_name, ddl):
                return self._format_point_id(deterministic_uuid(ddl), self.ddl_collection_name)
            return super().add_ddl(ddl, **kwargs)

        def add_documentation(self, documentation: str, **kwargs) -> str:
            if self._exists(self.documentation_collection_name, documentation):
                return self._format_point_id(
                    deterministic_uuid(documentation), self.documentation_collection_name
                )
            return super().add_documentation(documentation, **kwargs)

        def add_question_sql(self, question: str, sql: str, **kwargs) -> str:
            qa = "Question: {0}\n\nSQL: {1}".format(question, sql)
            if self._exists(self.sql_collection_name, qa):
                return self._format_point_id(deterministic_uuid(qa), self.sql_collection_name)
            return super().add_question_sql(question, sql, **kwargs)

        def reset(self):
            for name in (self.ddl_collection_name, self.documentation_collection_name,
                         self.sql_collection_name):
                self.remove_collection(name)

    prefix = settings.vanna_qdrant_prefix
    client = QdrantClient(
        url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=5
    )
    return ClinicVannaQdrant(config={
        "client": client,
        "n_results": 10,
        "ddl_collection_name": f"{prefix}ddl",
        "documentation_collection_name": f"{prefix}documentation",
        "sql_collection_name": f"{prefix}sql",
        "dialect": "T-SQL / Microsoft SQL Server",
    })


# ── Singleton ─────────────────────────────────────────────────────────────────

_vanna = None
_vanna_lock = threading.Lock()


def get_vanna():
    """Return the shared Vanna instance (built and trained on first use)."""
    global _vanna
    if _vanna is None:
        with _vanna_lock:
            if _vanna is None:
                _vanna = _create_vanna()
    return _vanna


def _create_vanna():
    from app.vanna_ai.training import train_from_semantic_layer

    vn = None
    if settings.vanna_vector_store.lower() == "qdrant":
        try:
            vn = _build_qdrant_vanna()
            logger.info("[Vanna] Using Qdrant store at %s", settings.qdrant_url)
        except Exception as exc:
            logger.warning("[Vanna] Qdrant store unavailable (%s); using in-memory store.", exc)
    if vn is None:
        vn = ClinicVannaMemory(config={"dialect": "T-SQL / Microsoft SQL Server"})

    stats = train_from_semantic_layer(vn)
    logger.info("[Vanna] Trained: %s", stats)
    return vn


def retrain() -> dict:
    """Drop and rebuild Vanna's knowledge from the semantic YAML layer."""
    from app.semantic.retriever import get_retriever
    from app.vanna_ai.training import train_from_semantic_layer

    vn = get_vanna()
    with _vanna_lock:
        vn.reset()
        if hasattr(vn, "_setup_collections"):
            vn._setup_collections()
        get_retriever()._loader.reload()
        return train_from_semantic_layer(vn)


def context_for(question: str) -> str:
    """Vanna-retrieved schema + documentation + verified SQL for a question."""
    vn = get_vanna()
    prompt = vn.add_ddl_to_prompt("", vn.get_related_ddl(question), max_tokens=6000)
    prompt = vn.add_documentation_to_prompt(
        prompt, vn.get_related_documentation(question), max_tokens=9000
    )
    prompt = vn.add_sql_to_prompt(
        prompt, vn.get_similar_question_sql(question), max_tokens=11000
    )
    return prompt.strip()


def get_vanna_or_none() -> Optional[object]:
    if not settings.vanna_enabled:
        return None
    try:
        return get_vanna()
    except Exception as exc:
        logger.warning("[Vanna] Unavailable: %s", exc)
        return None
