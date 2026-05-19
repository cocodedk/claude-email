"""Tests for the ChatDB transaction wrapper layer."""


class TestRead:
    def test_returns_value(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        db._conn.execute("INSERT INTO t VALUES (7)")
        db._conn.commit()  # commit so the poison check has nothing to roll back
        def body():
            return db._conn.execute("SELECT id FROM t").fetchone()[0]
        assert db._read(body) == 7

    def test_nested_read_inside_run_tx(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        captured = []
        def outer():
            db._conn.execute("INSERT INTO t VALUES (1)")
            captured.append(
                db._read(lambda: db._conn.execute(
                    "SELECT id FROM t").fetchone()[0]),
            )
        db._run_tx(outer)
        assert captured == [1]

    def test_read_runs_poison_check_at_depth_zero(self, tmp_path, caplog):
        import logging as _logging
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        db._conn.execute("BEGIN")
        db._conn.execute("INSERT INTO t VALUES (1)")
        assert db._conn.in_transaction is True
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db._read(lambda: None)
        assert db._conn.in_transaction is False
        warnings = [r for r in caplog.records
                    if "kind=stale_tx" in r.getMessage()]
        assert warnings
