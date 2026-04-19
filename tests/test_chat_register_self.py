"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
import importlib.util
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
        assert "registration failed" in err

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
