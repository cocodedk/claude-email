"""Tests for scripts/chat-rejoin-as.py — the in-session name-claim helper.

Loaded by path because the script lives under scripts/ and is invoked
as a CLI from the /chat-rejoin-as slash command, not imported by any
package.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from src.chat_db import ChatDB


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-rejoin-as.py"


@pytest.fixture
def mod(monkeypatch):
    """Reimport the script per test for fresh module-level state."""
    for key in ("CHAT_DB_PATH", "CLAUDE_AGENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("chat_rejoin_as", _SCRIPT_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "bus.db"
    ChatDB(str(p))  # create schema
    return p


class TestArgValidation:
    def test_no_arg_returns_2(self, mod, capsys):
        assert mod.main(["chat-rejoin-as.py"]) == 2
        assert "Usage:" in capsys.readouterr().err

    def test_invalid_name_returns_2(self, mod, monkeypatch, db_path, capsys):
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        assert mod.main(["chat-rejoin-as.py", "Not Valid"]) == 2
        err = capsys.readouterr().err
        assert "invalid agent name" in err
        assert "'Not Valid'" in err

    def test_uppercase_rejected(self, mod, monkeypatch, db_path, capsys):
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        assert mod.main(["chat-rejoin-as.py", "agent-FOO"]) == 2
        assert "invalid agent name" in capsys.readouterr().err


class TestDbResolution:
    def test_missing_chat_db_path_returns_2(self, mod, monkeypatch, capsys):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        assert mod.main(["chat-rejoin-as.py", "agent-foo"]) == 2
        assert "CHAT_DB_PATH not set" in capsys.readouterr().err

    def test_missing_db_file_returns_1(self, mod, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "missing.db"))
        assert mod.main(["chat-rejoin-as.py", "agent-foo"]) == 1
        assert "does not exist" in capsys.readouterr().err

    def test_relative_path_resolves_against_repo_root(
        self, mod, monkeypatch,
    ):
        monkeypatch.setenv("CHAT_DB_PATH", "claude-chat.db")
        assert mod._resolved_db_path() == _REPO_ROOT / "claude-chat.db"

    def test_absolute_path_returned_as_is(self, mod, monkeypatch, tmp_path):
        abs_db = tmp_path / "x.db"
        monkeypatch.setenv("CHAT_DB_PATH", str(abs_db))
        assert mod._resolved_db_path() == abs_db


class TestRegistration:
    def test_writes_row_with_durable_pid(
        self, mod, monkeypatch, db_path, tmp_path,
    ):
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.chdir(tmp_path)
        # Force durable PID to a known live one (this test process).
        monkeypatch.setattr(mod, "_durable_session_pid", lambda: os.getpid())

        rc = mod.main(["chat-rejoin-as.py", "agent-supervisor"])
        assert rc == 0
        row = ChatDB(str(db_path)).get_agent("agent-supervisor")
        assert row is not None
        assert row["pid"] == os.getpid()
        assert row["project_path"] == str(tmp_path)

    def test_collision_with_live_pid_returns_1(
        self, mod, monkeypatch, db_path, tmp_path, capsys,
    ):
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.chdir(tmp_path)
        # Pre-occupy the slot with the test process pid.
        ChatDB(str(db_path)).register_agent(
            "agent-taken", str(tmp_path), pid=os.getpid(),
        )
        # New caller has a different PID (any unused integer would work).
        monkeypatch.setattr(mod, "_durable_session_pid", lambda: os.getpid() + 10_000_000)

        rc = mod.main(["chat-rejoin-as.py", "agent-taken"])
        assert rc == 1
        assert "already held by live pid" in capsys.readouterr().err

    def test_durable_pid_falls_back_to_os_getpid(self, mod, monkeypatch):
        """When no Claude ancestor is visible, _durable_session_pid uses os.getpid()."""
        monkeypatch.setattr(mod, "find_ancestor_pid_matching", lambda *_: None)
        assert mod._durable_session_pid() == os.getpid()


class TestDbInitFailure:
    def test_corrupt_db_file_returns_1(
        self, mod, monkeypatch, tmp_path, capsys,
    ):
        """If the DB file exists but isn't valid SQLite, ChatDB() raises."""
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"not-sqlite-garbage")
        monkeypatch.setenv("CHAT_DB_PATH", str(bad))
        monkeypatch.chdir(tmp_path)
        rc = mod.main(["chat-rejoin-as.py", "agent-foo"])
        assert rc == 1
        assert "cannot open DB" in capsys.readouterr().err


class TestImportTimeDotenv:
    def test_import_does_not_crash_when_dotenv_missing(self, monkeypatch):
        """The script's optional dotenv import must degrade gracefully."""
        real = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location(
                "chat_rejoin_as_nodotenv", _SCRIPT_PATH,
            )
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            assert hasattr(m, "main")
        finally:
            if real is not None:
                sys.modules["dotenv"] = real
            else:
                sys.modules.pop("dotenv", None)


class TestSuccessOutput:
    def test_success_line_lists_name_pid_cwd(
        self, mod, monkeypatch, db_path, tmp_path, capsys,
    ):
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(mod, "_durable_session_pid", lambda: os.getpid())

        rc = mod.main(["chat-rejoin-as.py", "agent-x"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "agent-x" in out
        assert str(os.getpid()) in out
        assert str(tmp_path) in out
