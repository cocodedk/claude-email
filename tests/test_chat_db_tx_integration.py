"""Tests for the ChatDB transaction wrapper layer."""
import logging


class TestChatDbIntegration:
    def test_trace_callback_fires_on_real_chat_db_traffic(
        self, monkeypatch, tmp_path, caplog
    ):
        from src.chat_db import ChatDB
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "integration.db"))
        db.register_agent("agent-a", "/tmp/a")
        db.insert_message("agent-a", "agent-b", "hi", "notify")
        kinds = [r.getMessage() for r in caplog.records
                 if "chatdb.trace" in r.getMessage()]
        assert kinds, "no trace lines captured during real ChatDB ops"
        joined = " ".join(kinds).upper()
        assert "BEGIN" in joined or "COMMIT" in joined, (
            "trace expected at least one transaction boundary"
        )

    def test_no_trace_lines_when_env_unset(
        self, monkeypatch, tmp_path, caplog
    ):
        from src.chat_db import ChatDB
        monkeypatch.delenv("CHAT_DB_TRACE", raising=False)
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "quiet.db"))
        db.register_agent("agent-a", "/tmp/a")
        db.insert_message("agent-a", "agent-b", "hi", "notify")
        kinds = [r.getMessage() for r in caplog.records
                 if "chatdb.trace" in r.getMessage()]
        assert kinds == [], (
            "trace callback emitted lines without CHAT_DB_TRACE=1"
        )

    def test_db_lock_attribute_present_on_chat_db(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "lock.db"))
        # ChatDB exposes _db_lock as a reentrant lock — re-acquire from
        # the same thread (a plain Lock would deadlock).
        with db._db_lock:
            with db._db_lock:
                pass
