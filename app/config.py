"""
app/config.py
─────────────
Centralised settings loaded from environment variables / .env file.
All configuration lives here — no magic strings scattered across modules.
"""

from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ──────────────────────────────────────────────────────
    app_env: str = "development"
    app_name: str = "Analytics AI Backend"
    debug: bool = True
    log_level: str = "INFO"

    # ── OpenAI / Compatible LLM ──────────────────────────────────
    openai_api_key: str = ""
    openai_base_url: str = ""          # Optional custom base URL (e.g. for OpenRouter, Groq, Ollama)
    llm_sql_model: str = "gpt-4o"
    llm_intent_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0

    @property
    def clean_openai_base_url(self) -> str | None:
        """Sanitizes the base URL by stripping trailing endpoints (/chat/completions) and whitespace."""
        if not self.openai_base_url:
            return None
        url = self.openai_base_url.strip()
        for suffix in ["/chat/completions", "/chat/completions/", "/embeddings", "/embeddings/"]:
            if url.endswith(suffix):
                url = url[:-len(suffix)]
        url = url.rstrip("/")
        return url or None

    # ── Anthropic (optional) ─────────────────────────────────────
    anthropic_api_key: str = ""

    # ── SQL Server ───────────────────────────────────────────────
    sqlserver_server: str = "localhost"
    sqlserver_port: int = 1433
    sqlserver_database: str = "your_db"
    sqlserver_user: str = "sa"
    sqlserver_password: str = ""
    sqlserver_driver: str = "ODBC Driver 17 for SQL Server"
    sqlserver_pool_size: int = 20
    sqlserver_max_overflow: int = 10
    sqlserver_query_timeout: int = 30

    # ── PostgreSQL (internal metadata) ───────────────────────────
    postgres_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/analytics_meta"

    # ── Redis ────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 300

    # ── Qdrant ───────────────────────────────────────────────────
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection_schema: str = "schema_embeddings"
    qdrant_collection_metrics: str = "metric_definitions"

    # ── Embeddings ───────────────────────────────────────────────
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # ── Semantic Layer ───────────────────────────────────────────
    semantic_yaml_dir: str = "./semantic"
    included_tables: str = ""         # Comma-separated list of tables to restrict to (e.g. "Table1,Table2")
    max_tables_in_context: int = 12
    top_k_retrieval: int = 30
    top_k_after_rerank: int = 8

    # ── Agent ────────────────────────────────────────────────────
    max_sql_retries: int = 3
    max_result_rows: int = 1000
    clarification_timeout_seconds: int = 120

    @property
    def sqlserver_connection_string(self) -> str:
        """Build the pyodbc connection string for SQLAlchemy."""
        import urllib.parse

        # If a named instance is used (e.g. SERVER\INSTANCE), normalize slashes
        server = self.sqlserver_server.replace("\\\\", "\\")

        # For named instances, do NOT append ,port unless no instance was specified
        if "\\" not in server and self.sqlserver_port:
            server_part = f"{server},{self.sqlserver_port}"
        else:
            server_part = server

        params = urllib.parse.quote_plus(
            f"DRIVER={{{self.sqlserver_driver}}};"
            f"SERVER={server_part};"
            f"DATABASE={self.sqlserver_database};"
            f"UID={self.sqlserver_user};"
            f"PWD={self.sqlserver_password};"
            "TrustServerCertificate=yes;"
            "Encrypt=no;"
        )
        return f"mssql+pyodbc:///?odbc_connect={params}"


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance — instantiated once per process."""
    return Settings()
