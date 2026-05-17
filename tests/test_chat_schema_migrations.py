"""Schema migration tests for the dirty-block / branch-reuse fix.

Both columns must be present after ChatDB() construction — fresh-DB
path (SCHEMA) and pre-existing-DB path (MIGRATIONS). NULL is the
default so existing rows stay safety-gated."""
import sqlite3

from src.chat_db import ChatDB


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class TestFreshDB:
    def test_tasks_has_mutates_repo(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "a.db"))
        assert "mutates_repo" in _columns(cdb._conn, "tasks")

    def test_outbound_emails_has_task_id(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "b.db"))
        assert "task_id" in _columns(cdb._conn, "outbound_emails")

    def test_mutates_repo_defaults_to_null(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "c.db"))
        cdb._conn.execute(
            "INSERT INTO tasks (project_path, body, created_at) "
            "VALUES ('/p', 'x', '2026-05-17T00:00:00+00:00')"
        )
        cdb._conn.commit()
        row = cdb._conn.execute("SELECT mutates_repo FROM tasks LIMIT 1").fetchone()
        assert row["mutates_repo"] is None

    def test_outbound_task_id_index_exists(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "d.db"))
        rows = cdb._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='outbound_emails'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "outbound_emails_task_id_idx" in names


class TestUpgradeExistingDB:
    """Simulate a deployed DB that pre-dates the new columns."""

    def _make_old_db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_path TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                priority INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE outbound_emails (
                email_message_id TEXT PRIMARY KEY,
                sent_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                sender_agent TEXT
            );
        """)
        conn.commit()
        conn.close()

    def test_migrations_add_new_columns(self, tmp_path):
        path = str(tmp_path / "old.db")
        self._make_old_db(path)
        cdb = ChatDB(path)  # triggers migrations
        assert "mutates_repo" in _columns(cdb._conn, "tasks")
        assert "task_id" in _columns(cdb._conn, "outbound_emails")

    def test_migrations_are_idempotent(self, tmp_path):
        path = str(tmp_path / "old2.db")
        self._make_old_db(path)
        ChatDB(path)  # first migrate
        ChatDB(path)  # second open — must not raise
