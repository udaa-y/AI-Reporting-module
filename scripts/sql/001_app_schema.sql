/* ─────────────────────────────────────────────────────────────────────────────
   001_app_schema.sql — application tables for login, sessions and Ask AI chats

   Run once in SSMS (or sqlcmd) as a user who can create schemas, in the SAME
   database the app reads clinic data from. Safe to re-run: existing tables are
   left untouched.
   ───────────────────────────────────────────────────────────────────────────── */

IF SCHEMA_ID(N'app') IS NULL
    EXEC(N'CREATE SCHEMA app');
GO

IF OBJECT_ID(N'app.EmployeeLogin', N'U') IS NULL
CREATE TABLE app.EmployeeLogin (
    Id            INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_EmployeeLogin PRIMARY KEY,
    EmployeeId    INT            NULL,              -- optional link to dbo.Employee.Id
    Username      NVARCHAR(100)  NOT NULL CONSTRAINT UQ_EmployeeLogin_Username UNIQUE,
    PasswordHash  NVARCHAR(255)  NOT NULL,          -- PBKDF2 hash, never the password
    DisplayName   NVARCHAR(150)  NOT NULL,
    Role          NVARCHAR(20)   NOT NULL CONSTRAINT CK_EmployeeLogin_Role CHECK (Role IN (N'admin', N'staff')),
    IsActive      BIT            NOT NULL CONSTRAINT DF_EmployeeLogin_IsActive DEFAULT (1),
    CreatedAt     DATETIME2(0)   NOT NULL CONSTRAINT DF_EmployeeLogin_CreatedAt DEFAULT (SYSUTCDATETIME()),
    LastLoginAt   DATETIME2(0)   NULL
);
GO

IF OBJECT_ID(N'app.UserSession', N'U') IS NULL
CREATE TABLE app.UserSession (
    TokenHash   CHAR(64)      NOT NULL CONSTRAINT PK_UserSession PRIMARY KEY,   -- SHA-256 of the cookie
    LoginId     INT           NOT NULL CONSTRAINT FK_UserSession_Login REFERENCES app.EmployeeLogin(Id),
    CreatedAt   DATETIME2(0)  NOT NULL,
    LastSeenAt  DATETIME2(0)  NOT NULL,
    ExpiresAt   DATETIME2(0)  NOT NULL,
    RevokedAt   DATETIME2(0)  NULL
);
CREATE INDEX IX_UserSession_LoginId ON app.UserSession (LoginId);
GO

IF OBJECT_ID(N'app.ChatConversation', N'U') IS NULL
CREATE TABLE app.ChatConversation (
    Id         INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ChatConversation PRIMARY KEY,
    LoginId    INT            NOT NULL CONSTRAINT FK_ChatConversation_Login REFERENCES app.EmployeeLogin(Id),
    Title      NVARCHAR(200)  NOT NULL,
    Scope      NVARCHAR(50)   NOT NULL,
    CreatedAt  DATETIME2(0)   NOT NULL,
    UpdatedAt  DATETIME2(0)   NOT NULL
);
CREATE INDEX IX_ChatConversation_Login_Updated ON app.ChatConversation (LoginId, UpdatedAt DESC);
GO

IF OBJECT_ID(N'app.ChatMessage', N'U') IS NULL
CREATE TABLE app.ChatMessage (
    Id              BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ChatMessage PRIMARY KEY,
    ConversationId  INT            NOT NULL CONSTRAINT FK_ChatMessage_Conversation
                                   REFERENCES app.ChatConversation(Id) ON DELETE CASCADE,
    Role            NVARCHAR(20)   NOT NULL,         -- 'user' | 'assistant'
    Content         NVARCHAR(MAX)  NOT NULL,
    Payload         NVARCHAR(MAX)  NULL,             -- JSON: SQL, result rows, review, insights
    CreatedAt       DATETIME2(0)   NOT NULL
);
CREATE INDEX IX_ChatMessage_Conversation ON app.ChatMessage (ConversationId, Id);
GO



CREATE TABLE app.AiQuestionUsage (
    Id        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AiQuestionUsage PRIMARY KEY,
    LoginId   INT            NOT NULL CONSTRAINT FK_AiQuestionUsage_Login REFERENCES app.EmployeeLogin(Id),
    Question  NVARCHAR(MAX)  NOT NULL,
    AskedAt   DATETIME2(0)   NOT NULL
);
CREATE INDEX IX_AiQuestionUsage_Login_AskedAt ON app.AiQuestionUsage (LoginId, AskedAt);

/* ── Permissions ─────────────────────────────────────────────────────────────
   Replace [your_app_user] with the SQL login/user in your .env (SQLSERVER_USER).
   The app writes ONLY to the app schema; clinic data in dbo stays read-only.
   ─────────────────────────────────────────────────────────────────────────── */
-- GRANT SELECT ON SCHEMA::dbo TO [your_app_user];
-- GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::app TO [your_app_user];
