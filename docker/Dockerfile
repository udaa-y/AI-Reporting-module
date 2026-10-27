FROM python:3.11-slim

WORKDIR /app

# Install ODBC Driver 17 for SQL Server
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg2 \
    unixodbc \
    unixodbc-dev \
    && curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list \
       > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] pydantic pydantic-settings \
    langgraph langchain langchain-openai langchain-anthropic openai \
    qdrant-client sqlglot pyodbc sqlalchemy asyncpg alembic \
    redis pyyaml python-dotenv httpx tenacity

COPY app/ ./app/
COPY semantic/ ./semantic/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
