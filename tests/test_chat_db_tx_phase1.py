"""Tests for the ChatDB transaction wrapper layer."""


class TestPhase1Integration:
    def test_concurrent_writers_serialise(self, tmp_path):
        """Two threads hammering insert_message must observe N inserts,
        not lost writes — the RLock serialises them."""
        from src.chat_db import ChatDB
        import threading as _t
        db = ChatDB(str(tmp_path / "a.db"))
        db.register_agent("agent-a", "/p1")
        db.register_agent("agent-b", "/p2")
        N = 20
        def hammer(name):
            for i in range(N):
                db.insert_message(name, "user", f"msg-{i}", "notify")
        threads = [
            _t.Thread(target=hammer, args=("agent-a",)),
            _t.Thread(target=hammer, args=("agent-b",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        count = db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE type='notify'",
        ).fetchone()[0]
        assert count == 2 * N

    def test_real_method_retries_after_sidecar_release(self, tmp_path):
        from src.chat_db import ChatDB
        from tests._tx_fixtures import sidecar_writer_lock, narrow_busy_timeout
        import threading as _t
        import time as _time
        path = str(tmp_path / "a.db")
        db = ChatDB(path)
        db.register_agent("agent-a", "/p1")
        narrow_busy_timeout(db, 50)
        with sidecar_writer_lock(path) as release:
            result_holder = {}
            def run():
                try:
                    row = db.insert_message("agent-a", "user", "hi", "notify")
                    result_holder["row"] = row
                except Exception as exc:
                    result_holder["err"] = exc
            t = _t.Thread(target=run)
            t.start()
            _time.sleep(0.2)
            release.set()
            t.join(timeout=10)
        assert "row" in result_holder
        assert result_holder["row"]["body"] == "hi"

    def test_stale_tx_on_register_agent_recovers(self, tmp_path, caplog):
        """Simulate the 2026-05-19 wedge: a stale in_transaction state
        on the shared connection. register_agent's entry must clear it
        via _check_or_recover_at_depth_zero rather than failing."""
        import logging as _logging
        from src.chat_db import ChatDB
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("BEGIN")
        db._conn.execute("CREATE TABLE _filler (id INTEGER)")
        assert db._conn.in_transaction is True
        agent = db.register_agent("agent-a", "/p1")
        assert agent["name"] == "agent-a"
        warnings = [r for r in caplog.records
                    if "kind=stale_tx" in r.getMessage()]
        assert warnings

    def test_insert_message_nudge_wake_fires_after_commit(self, tmp_path):
        """_nudge_wake moves to _after_commit so it fires AFTER the
        transaction commits and the RLock releases — never on rollback,
        never before the row is durable."""
        from src.chat_db import ChatDB
        import threading as _t
        db = ChatDB(str(tmp_path / "a.db"))
        evt = _t.Event()
        db.set_wake_nudge(evt)
        db.register_agent("agent-a", "/p1")
        db.insert_message("agent-a", "user", "hi", "notify")
        assert evt.is_set()
