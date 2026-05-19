"""Schema migration tests for router-side email-thread context.

New additive columns:
- messages.in_reply_to_eid TEXT
- outbound_emails.body TEXT
- outbound_emails.in_reply_to_eid TEXT

Both fresh-DB and pre-existing-DB upgrade paths must end up with the
columns present and default NULL.
"""
import sqlite3

from src.chat_db import ChatDB


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


class TestFreshDB:
    def test_messages_has_in_reply_to_eid(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "a.db"))
        assert "in_reply_to_eid" in _columns(cdb._conn, "messages")

    def test_outbound_has_body_and_in_reply_to_eid(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "b.db"))
        cols = _columns(cdb._conn, "outbound_emails")
        assert "body" in cols
        assert "in_reply_to_eid" in cols

    def test_in_reply_to_eid_indexes_exist(self, tmp_path):
        cdb = ChatDB(str(tmp_path / "c.db"))
        rows = cdb._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "messages_in_reply_to_eid_idx" in names
        assert "outbound_emails_in_reply_to_eid_idx" in names


class TestUpgradeExistingDB:
    def _make_old_db(self, path):
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_name TEXT NOT NULL,
                to_name TEXT NOT NULL,
                body TEXT NOT NULL,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                email_message_id TEXT,
                in_reply_to INTEGER,
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
        cdb = ChatDB(path)
        assert "in_reply_to_eid" in _columns(cdb._conn, "messages")
        cols = _columns(cdb._conn, "outbound_emails")
        assert "body" in cols
        assert "in_reply_to_eid" in cols

    def test_migrations_are_idempotent(self, tmp_path):
        path = str(tmp_path / "old2.db")
        self._make_old_db(path)
        ChatDB(path)
        ChatDB(path)
