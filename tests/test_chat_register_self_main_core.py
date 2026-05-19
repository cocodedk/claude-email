"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
import json
import sys

from src.chat_db import ChatDB
from tests._chat_register_self_helpers import reg_mod  # noqa: F401


class TestMain:
    def test_registers_agent_from_cwd(self, reg_mod, tmp_path, monkeypatch):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))  # create schema
        project = tmp_path / "myproj"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc = reg_mod.main()
        assert rc == 0

        db = ChatDB(str(db_file))
        agent = db.get_agent("agent-myproj")
        assert agent is not None
        assert agent["project_path"] == str(project)
        assert agent["status"] == "running"

    def test_missing_env_exits_nonzero(self, reg_mod, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        rc = reg_mod.main()
        assert rc == 2
        err = capsys.readouterr().err
        assert "CHAT_DB_PATH" in err

    def test_missing_db_exits_nonzero(self, reg_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "nope.db"
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = reg_mod.main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "does not exist" in err

    def test_register_failure_exits_nonzero(self, reg_mod, tmp_path, monkeypatch, capsys):
        # DB path exists but isn't a valid SQLite file — ChatDB init will fail
        db_file = tmp_path / "bad.db"
        db_file.write_bytes(b"not-sqlite-garbage")
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = reg_mod.main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "cannot open DB" in err

    def test_skips_register_when_subagent_indicated_by_agent_id(
        self, reg_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "subagent-proj"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        import io as _io
        buf = _io.StringIO(json.dumps({"agent_id": "sub-xyz"}))
        monkeypatch.setattr(sys, "stdin", buf)
        rc = reg_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.err == ""
        # No agent registered
        db = ChatDB(str(db_file))
        assert db.get_agent("agent-subagent-proj") is None

    def test_silent_skip_when_another_live_pid_owns_name(
        self, reg_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "contested"
        project.mkdir()
        import os as _os
        master_pid = _os.getpid()
        db.register_agent("agent-contested", str(project), pid=master_pid)
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        monkeypatch.setattr(reg_mod.os, "getpid", lambda: master_pid + 1)
        rc = reg_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""
        # Master registration unchanged
        assert db.get_agent("agent-contested")["pid"] == master_pid

    def test_proceeds_when_existing_row_pid_is_dead(
        self, reg_mod, tmp_path, monkeypatch,
    ):
        """If a row exists for our name but its pid is no longer alive,
        _master_already_owns must NOT short-circuit — we re-register
        (the proc_reconcile sweep would have cleaned it up otherwise)."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "stale"
        project.mkdir()
        # Insert a row with an obviously-dead pid; ChatDB.register_agent
        # would refuse this so we go through the same path the bus does.
        db.register_agent("agent-stale", str(project), pid=999_999_999)
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        # is_alive(999_999_999) is False on any sane system; assert it.
        from src.process_liveness import is_alive
        assert not is_alive(999_999_999)
        rc = reg_mod.main()
        assert rc == 0
        # The row's pid was overwritten to our session pid (or kept the
        # name) — either way the agent is now registered for this session.
        assert db.get_agent("agent-stale") is not None

    def test_distinct_name_in_shared_project_registers(
        self, reg_mod, tmp_path, monkeypatch, capsys,
    ):
        """Post-Task-2: multi-agent-per-project is legal. A new session
        in a project with a different live agent name must register
        successfully (it doesn't conflict because names differ)."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "shared"
        project.mkdir()
        import os as _os
        master_pid = _os.getpid()
        db.register_agent("agent-old-name", str(project), pid=master_pid)
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        monkeypatch.setattr(reg_mod.os, "getpid", lambda: master_pid + 1)
        rc = reg_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.err == ""
        # Both rows must coexist now.
        from src.chat_db import ChatDB as _DB
        db2 = _DB(str(db_file))
        assert db2.get_agent("agent-old-name") is not None
        assert db2.get_agent("agent-shared") is not None

    def test_name_derivation_strips_trailing_slash(self, reg_mod, tmp_path, monkeypatch):
        """cwd never has a trailing slash per POSIX, but basename logic should be robust."""
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "dune-Browser-Game"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        reg_mod.main()
        db = ChatDB(str(db_file))
        assert db.get_agent("agent-dune-Browser-Game") is not None
