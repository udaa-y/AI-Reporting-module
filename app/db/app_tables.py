"""
app/db/app_tables.py
─────────────────────
The application's OWN tables (logins, sessions, chat history), kept in the
`app` schema of the same SQL Server database. The app's DB user can write
here but stays read-only on the clinic tables in `dbo`.

Create them with scripts/sql/001_app_schema.sql. Defined with SQLAlchemy Core
so queries are generated for SQL Server (and tests can run on SQLite).
"""

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, MetaData,
    String, Table, UnicodeText,
)

APP_SCHEMA = "app"
metadata = MetaData(schema=APP_SCHEMA)

employee_login = Table(
    "EmployeeLogin", metadata,
    Column("Id", Integer, primary_key=True, autoincrement=True),
    Column("EmployeeId", Integer, nullable=True),          # optional link to dbo.Employee.Id
    Column("BranchId", Integer, nullable=True),            # dbo.BranchMaster.BranchId; NULL only for superadmin
    Column("Username", String(100), nullable=False, unique=True),
    Column("PasswordHash", String(255), nullable=False),
    Column("DisplayName", String(150), nullable=False),
    Column("Role", String(20), nullable=False),            # "admin" | "staff"
    Column("IsActive", Boolean, nullable=False, default=True),
    Column("CreatedAt", DateTime, nullable=False),
    Column("LastLoginAt", DateTime, nullable=True),
)

user_session = Table(
    "UserSession", metadata,
    Column("TokenHash", String(64), primary_key=True),     # SHA-256 of the cookie token
    Column("LoginId", Integer, ForeignKey(f"{APP_SCHEMA}.EmployeeLogin.Id"), nullable=False),
    Column("CreatedAt", DateTime, nullable=False),
    Column("LastSeenAt", DateTime, nullable=False),
    Column("ExpiresAt", DateTime, nullable=False),         # absolute limit
    Column("RevokedAt", DateTime, nullable=True),
)

chat_conversation = Table(
    "ChatConversation", metadata,
    Column("Id", Integer, primary_key=True, autoincrement=True),
    Column("LoginId", Integer, ForeignKey(f"{APP_SCHEMA}.EmployeeLogin.Id"), nullable=False),
    Column("Title", String(200), nullable=False),
    Column("Scope", String(50), nullable=False),
    Column("CreatedAt", DateTime, nullable=False),
    Column("UpdatedAt", DateTime, nullable=False),
)

chat_message = Table(
    "ChatMessage", metadata,
    Column("Id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column("ConversationId", Integer, ForeignKey(f"{APP_SCHEMA}.ChatConversation.Id"), nullable=False),
    Column("Role", String(20), nullable=False),            # "user" | "assistant"
    Column("Content", UnicodeText, nullable=False),
    Column("Payload", UnicodeText, nullable=True),         # JSON: SQL, rows, review, insights…
    Column("CreatedAt", DateTime, nullable=False),
)
