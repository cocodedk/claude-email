"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import importlib.util
import io
import json
import sys

import pytest

from src.chat_db import ChatDB
from tests._chat_drain_inbox_helpers import _SCRIPT_PATH, drain_mod  # noqa: F401


class TestMain:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        # Standalone (no stdin) — defaults to UserPromptSubmit event
        class FakeStdin:
            def isatty(self):
                return True
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", FakeStdin())

    def test_uses_event_name_from_stdin(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "beta"
        project.mkdir()
        db.insert_message("user", "agent-beta", "ping", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        buf = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        monkeypatch.setattr(sys, "stdin", buf)
        rc = drain_mod.main()
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    def test_missing_env_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        project = tmp_path / "x"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == ""
        assert "CHAT_DB_PATH" in out.err

    def test_missing_db_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        project = tmp_path / "y"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "nope.db"))
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_corrupt_db_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        bad = tmp_path / "corrupt.db"
        bad.write_bytes(b"garbage")
        project = tmp_path / "z"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(bad))
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_caller_derived_from_cwd_basename(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "my-project"
        project.mkdir()
        db.insert_message("user", "agent-my-project", "ok", "reply")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        drain_mod.main()
        payload = json.loads(capsys.readouterr().out)
        assert 'agent-my-project' in payload["hookSpecificOutput"]["additionalContext"]

    def test_env_var_drains_named_agent_inbox(self, drain_mod, tmp_path, monkeypatch, capsys):
        """When CLAUDE_AGENT_NAME is set, drain consumes that agent's
        inbox — not the cwd-default. Without this, two agents in the
        same project silently steal each other's messages."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "shared"
        project.mkdir()
        # One message for the explicit name, one for the cwd-default.
        db.insert_message("user", "agent-supervisor", "for-supervisor", "reply")
        db.insert_message("user", "agent-shared", "for-default", "reply")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-supervisor")

        drain_mod.main()
        ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
        assert "for-supervisor" in ctx
        assert "for-default" not in ctx
        # Default-named inbox is untouched and still pending.
        from src.chat_db import ChatDB as _DB
        remaining = _DB(str(db_file)).get_pending_messages_for("agent-shared")
        assert any(m["body"] == "for-default" for m in remaining)

    def test_invalid_env_var_falls_back_to_cwd_basename(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "fallback"
        project.mkdir()
        db.insert_message("user", "agent-fallback", "ok", "reply")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "Not Valid")

        drain_mod.main()
        out = capsys.readouterr()
        ctx = json.loads(out.out)["hookSpecificOutput"]["additionalContext"]
        assert "agent-fallback" in ctx
        assert "rejecting invalid name 'Not Valid'" in out.err

    def test_import_does_not_crash_when_dotenv_missing(self, monkeypatch):
        real_dotenv = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location("chat_drain_inbox_nodotenv", _SCRIPT_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real_dotenv is not None:
                sys.modules["dotenv"] = real_dotenv
            else:
                sys.modules.pop("dotenv", None)

    def test_query_failure_returns_zero_no_stdout(self, drain_mod, tmp_path, monkeypatch, mocker, capsys):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "q"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        mocker.patch(
            "src.chat_db.ChatDB.claim_pending_messages_for",
            side_effect=RuntimeError("boom"),
        )
        rc = drain_mod.main()
        assert rc == 0
        err = capsys.readouterr().err
        assert "query failed" in err
