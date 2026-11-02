# Analytics AI Backend

An AI-powered natural language to SQL analytics engine, inspired by Querio.
Ask business questions in plain English. Get SQL-powered answers instantly.

## Architecture

```
User Query → Intent Analyzer → Context Retriever → SQL Generator
           → Validator → SQL Server Executor → Response Formatter
```

With clarification loop and self-correction when needed.

## Quick Start

### 1. Install dependencies
```bash
pip install fastapi uvicorn[standard] pydantic pydantic-settings \
  langgraph langchain langchain-openai openai qdrant-client \
  sqlglot pyodbc sqlalchemy redis pyyaml python-dotenv tenacity \
  "httpx>=0.28" "vanna==2.0.2" pandas numpy
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your SQL Server credentials and OpenAI API key
```

### 3. Run with Docker (recommended)
```bash
cd docker
docker-compose up -d
```

### 4. Run locally (dev)
```bash
cd analytics-backend
python -m app.main
# OR
uvicorn app.main:app --reload --port 8000
```

### 5. Open API docs
```
http://localhost:8000/docs
```

## API Usage

### Submit a query
```bash
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the total revenue for last quarter?", "session_id": "sess-001"}'
```

### Handle clarification (if returned)
```bash
# If response has status: "clarification_needed":
curl -X POST http://localhost:8000/query/clarify \
  -H "Content-Type: application/json" \
  -d '{"session_id": "sess-001", "answer": "Net Revenue (after returns)"}'
```

## Project Structure

```
analytics-backend/
├── app/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings from .env
│   ├── api/routes/
│   │   ├── query.py               # POST /query, POST /query/clarify
│   │   └── health.py              # GET /health, GET /health/ready
│   ├── agent/
│   │   ├── graph.py               # LangGraph pipeline definition
│   │   ├── state.py               # QueryAgentState TypedDict
│   │   ├── nodes/                 # 8 pipeline nodes
│   │   └── prompts/               # LLM prompt templates
│   ├── semantic/
│   │   ├── loader.py              # YAML file loader + cache
│   │   └── retriever.py          # Qdrant vector search
│   ├── vanna_ai/
│   │   ├── engine.py              # Vanna instance (project LLM + memory/Qdrant store)
│   │   ├── training.py            # Trains Vanna from semantic/ + 👍 feedback learning
│   │   ├── reviewer.py            # Checks SQL answers the question (parallel to execution)
│   │   └── analyst.py             # AI analysis report + follow-up questions
│   ├── analytics/
│   │   └── engine.py              # Exact stats: KPIs, trends, YoY, drivers, anomalies, forecast
│   ├── db/
│   │   └── sqlserver.py           # SQL Server connection pool
│   ├── cache/
│   │   └── redis_client.py        # Redis cache wrapper
│   └── utils/
│       └── sql_safety.py          # sqlglot AST validator
├── semantic/                      # YAML business definitions (Git-versioned)
│   ├── metrics/                   # Revenue, customers, etc.
│   ├── tables/                    # Table + column descriptions
│   ├── joins/                     # Approved JOIN paths
│   ├── synonyms/                  # Business vocabulary
│   ├── documentation/             # Plain-English business rules (*.md) for Vanna
│   └── few_shots/                 # Verified Q→SQL examples (+ learned_examples.yaml)
├── tests/
├── docker/
└── .env.example
```

## Semantic Layer

The `semantic/` directory is the **business brain** of the system.
Edit YAML files to define:
- What "Revenue" means (`metrics/revenue.yaml`)
- Table and column descriptions (`tables/orders.yaml`)
- Pre-approved JOIN paths (`joins/approved_joins.yaml`)
- Business vocabulary (`synonyms/vocabulary.yaml`)
- Verified Q→SQL examples (`few_shots/examples.yaml`)

## Vanna AI: SQL Verification & Analytics

[Vanna](https://vanna.ai) is trained at startup on the same `semantic/` files the SQL
generator uses (tables → DDL, joins, metrics, synonyms, `documentation/*.md`,
few-shot examples). It uses the same LLM provider as the rest of the app.

1. **SQL review**: after the safety validator, Vanna checks that the SQL actually
   answers the question (right tables, joins, filters, no double counting). It runs
   **in parallel with the database query**, so it only adds time when it is slower
   than the query. A confident "incorrect" verdict sends its feedback to the SQL
   generator for a corrected attempt; otherwise the verdict is shown next to the SQL.
2. **Analysis**: `POST /query/insights` is called by the UI *after* the table is shown.
   `app/analytics/engine.py` computes exact numbers (KPIs, trends, period-over-period,
   YoY/YTD, category drivers of a change, anomalies, Holt forecast, patient metrics),
   and Vanna writes a clinic-focused report and follow-up questions from them.
   Results are cached.
3. **Learning**: 👍 on a result calls `POST /query/feedback`, which teaches Vanna the
   question→SQL pair and saves it to `semantic/few_shots/learned_examples.yaml`, which
   the main SQL generator also reads.

After editing `semantic/` files, call `POST /vanna/train` (no restart needed).
`GET /vanna/training-data` shows what Vanna knows.

| Variable | Default | Purpose |
|---|---|---|
| `VANNA_ENABLED` | `true` | Turn the whole Vanna layer on/off |
| `VANNA_MODEL` | `LLM_SQL_MODEL` | Model for SQL review |
| `VANNA_FAST_MODEL` | `LLM_INTENT_MODEL` | Model for analysis + follow-ups |
| `VANNA_VECTOR_STORE` | `memory` | `memory` (instant) or `qdrant` (persistent, for large schemas) |
| `VANNA_REVIEW_ENABLED` | `true` | Review SQL before showing results |
| `VANNA_REVIEW_TIMEOUT_SECONDS` | `8` | Results are shown unverified after this |
| `VANNA_REVIEW_MIN_CONFIDENCE` | `0.75` | Confidence needed for a rejection to trigger a retry |
| `VANNA_ANALYSIS_SAMPLE_ROWS` | `15` | Raw rows sent to the LLM (`0` = aggregates only) |
| `INLINE_INSIGHTS` | `false` | `true` = old in-request narrative (slower) |

## Running Tests

```bash
python -m pytest tests/ -v
```

## Environment Variables

| Variable | Description | Required |
|---------|-------------|----------|
| `OPENAI_API_KEY` | OpenAI API key | ✅ |
| `SQLSERVER_SERVER` | SQL Server hostname | ✅ |
| `SQLSERVER_DATABASE` | Database name | ✅ |
| `SQLSERVER_USER` | Read-only DB user | ✅ |
| `SQLSERVER_PASSWORD` | DB password | ✅ |
| `QDRANT_URL` | Qdrant vector DB URL | Optional (dev fallback) |
| `REDIS_URL` | Redis cache URL | Optional (dev fallback) |
