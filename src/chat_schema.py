"""SQLite schema for claude-chat.db.

Kept separate from chat_db.py so new tables (tasks, future ones) don't
push chat_db.py over the 200-line cap.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    name TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    pid INTEGER,
    registered_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_name TEXT NOT NULL,
    to_name TEXT NOT NULL,
    body TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    email_message_id TEXT,
    in_reply_to INTEGER REFERENCES messages(id),
    created_at TEXT NOT NULL,
    content_type TEXT,
    task_id INTEGER
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    participant TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_path TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    pid INTEGER,
    branch_name TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_text TEXT,
    output_text TEXT,
    retry_of INTEGER,
    plan_first INTEGER NOT NULL DEFAULT 0,
    origin_content_type TEXT,
    origin_message_id TEXT,
    origin_subject TEXT,
    last_sent_status TEXT
);
CREATE INDEX IF NOT EXISTS tasks_project_status_idx ON tasks(project_path, status);

CREATE TABLE IF NOT EXISTS wake_sessions (
    agent_name TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    last_turn_at TEXT NOT NULL
);
"""


# Idempotent migrations for existing DBs that predate new columns.
MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN branch_name TEXT",
    "ALTER TABLE tasks ADD COLUMN output_text TEXT",
    "ALTER TABLE tasks ADD COLUMN retry_of INTEGER",
    "ALTER TABLE tasks ADD COLUMN plan_first INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN origin_content_type TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_message_id TEXT",
    "ALTER TABLE tasks ADD COLUMN origin_subject TEXT",
    "ALTER TABLE tasks ADD COLUMN last_sent_status TEXT",
    "ALTER TABLE messages ADD COLUMN content_type TEXT",
    "ALTER TABLE messages ADD COLUMN task_id INTEGER",
]
