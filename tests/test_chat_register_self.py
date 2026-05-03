"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from src.chat_db import ChatDB


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-register-self.py"


@pytest.fixture
def reg_mod(monkeypatch):
    """Import the script as a module each test — fresh module-level state."""
    # The script loads .env at import time; strip env vars it might read so
    # tests control them via monkeypatch explicitly.
    for key in ("CHAT_DB_PATH",):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("chat_register_self", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestResolvedDbPath:
    def test_relative_resolves_against_repo_root(self, reg_mod, monkeypatch):
        monkeypatch.setenv("CHAT_DB_PATH", "claude-chat.db")
        assert reg_mod._resolved_db_path() == _REPO_ROOT / "claude-chat.db"

    def test_absolute_returned_as_is(self, reg_mod, monkeypatch, tmp_path):
        abs_db = tmp_path / "chat.db"
        monkeypatch.setenv("CHAT_DB_PATH", str(abs_db))
        assert reg_mod._resolved_db_path() == abs_db

    def test_missing_env_raises(self, reg_mod, monkeypatch):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_DB_PATH not set"):
            reg_mod._resolved_db_path()


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

    def test_silent_skip_when_different_name_owns_same_project(
        self, reg_mod, tmp_path, monkeypatch, capsys,
    ):
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
        # Should not have registered a second agent for this project
        from src.chat_db import ChatDB as _DB
        db2 = _DB(str(db_file))
        assert db2.get_agent("agent-shared") is None

    def test_read_hook_payload_isatty_returns_empty(self, reg_mod, monkeypatch):
        """A TTY stdin (ad-hoc CLI invocation) means no hook payload —
        return {} rather than blocking on read()."""
        class _Tty:
            def isatty(self):
                return True
            def read(self):
                raise AssertionError("should not read when stdin is a tty")
        monkeypatch.setattr(reg_mod.sys, "stdin", _Tty())
        assert reg_mod._read_hook_payload() == {}

    def test_read_hook_payload_swallows_stdin_errors(self, reg_mod, monkeypatch):
        """Broken stdin (OSError/ValueError) must not crash — return {}."""
        class _Broken:
            def isatty(self):
                raise OSError("stdin gone")
            def read(self):
                return ""
        monkeypatch.setattr(reg_mod.sys, "stdin", _Broken())
        assert reg_mod._read_hook_payload() == {}

    def test_read_hook_payload_empty_returns_empty(self, reg_mod, monkeypatch):
        import io as _io
        monkeypatch.setattr(reg_mod.sys, "stdin", _io.StringIO("   \n"))
        assert reg_mod._read_hook_payload() == {}

    def test_read_hook_payload_invalid_json_returns_empty(
        self, reg_mod, monkeypatch,
    ):
        import io as _io
        monkeypatch.setattr(reg_mod.sys, "stdin", _io.StringIO("{not json"))
        assert reg_mod._read_hook_payload() == {}

    def test_main_swallows_agent_name_taken_race(
        self, reg_mod, tmp_path, monkeypatch,
    ):
        """If another process registered between the pre-check and our INSERT,
        we quietly concede (rc=0) rather than crashing the hook."""
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "proj-race"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        from src.chat_db import AgentNameTaken as _Taken

        class _FakeDB:
            def __init__(self, *_a, **_kw): pass
            def get_agent(self, _): return None
            def find_live_owner(self, *_a, **_kw): return None
            def register_agent(self, *_a, **_kw):
                raise _Taken("agent-proj-race", 12345)

        monkeypatch.setattr(reg_mod, "ChatDB", _FakeDB)
        rc = reg_mod.main()
        assert rc == 0

    def test_main_reports_unexpected_exception(
        self, reg_mod, tmp_path, monkeypatch, capsys,
    ):
        """A generic exception from register_agent must be logged and
        returned as rc=1, so systemd / the hook runner sees a failure."""
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "proj-boom"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        class _FakeDB:
            def __init__(self, *_a, **_kw): pass
            def get_agent(self, _): return None
            def find_live_owner(self, *_a, **_kw): return None
            def register_agent(self, *_a, **_kw):
                raise RuntimeError("synthetic explosion")

        monkeypatch.setattr(reg_mod, "ChatDB", _FakeDB)
        rc = reg_mod.main()
        assert rc == 1
        err = capsys.readouterr().err
        assert "synthetic explosion" in err

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


class _FakeStdin:
    def __init__(self, data: str) -> None:
        self._data = data

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return self._data


class TestEnvAgentName:
    """CLAUDE_AGENT_NAME overrides the cwd-derived default."""

    def test_env_var_overrides_cwd_default(self, reg_mod, monkeypatch, tmp_path):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path))  # initialize schema
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-custom")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        agent = ChatDB(str(db_path)).get_agent("agent-custom")
        assert agent is not None
        assert agent["project_path"] == str(tmp_path)

    def test_invalid_env_falls_back_to_cwd_default(
        self, reg_mod, monkeypatch, tmp_path, capsys,
    ):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path))
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "Not Valid")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        expected_fallback = f"agent-{tmp_path.name}"
        agent = ChatDB(str(db_path)).get_agent(expected_fallback)
        assert agent is not None
        assert "rejecting invalid name 'Not Valid'" in capsys.readouterr().err

    def test_unset_env_uses_cwd_default(self, reg_mod, monkeypatch, tmp_path):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path))
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        expected = f"agent-{tmp_path.name}"
        agent = ChatDB(str(db_path)).get_agent(expected)
        assert agent is not None


class TestImportTimeDotenv:
    def test_import_does_not_crash_when_dotenv_missing(self, monkeypatch):
        """If python-dotenv is not installed the script should still import."""
        # Remove dotenv from sys.modules and mask it
        real_dotenv = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location("chat_register_self_nodotenv", _SCRIPT_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real_dotenv is not None:
                sys.modules["dotenv"] = real_dotenv
            else:
                sys.modules.pop("dotenv", None)
