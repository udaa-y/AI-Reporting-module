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
  sqlglot pyodbc sqlalchemy redis pyyaml python-dotenv tenacity
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
│   └── few_shots/                 # Verified Q→SQL examples
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
