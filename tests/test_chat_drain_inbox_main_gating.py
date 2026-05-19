"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import io
import json
import os
import sys

import pytest

from src.chat_db import ChatDB
from tests._chat_drain_inbox_helpers import drain_mod  # noqa: F401


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

    def test_empty_inbox_no_stdout(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""

    def test_skips_drain_when_subagent_indicated_by_agent_id(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """SessionStart/UserPromptSubmit hook input includes agent_id only
        inside a subagent — if present, drain must skip."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "sub"
        project.mkdir()
        db.insert_message("user", "agent-sub", "hello", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        buf = io.StringIO(json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "agent_id": "sub-123",
        }))
        monkeypatch.setattr(sys, "stdin", buf)
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""
        assert len(db.get_pending_messages_for("agent-sub")) == 1

    def test_skips_drain_when_another_live_pid_owns_name(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """A sub-agent or sibling session with the same caller name must
        not steal messages from the registered master process."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        master_pid = os.getpid()
        db.register_agent("agent-alpha", str(project), pid=master_pid)
        db.insert_message("user", "agent-alpha", "hi there", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        # Pretend to be a subagent with a different pid than the master
        monkeypatch.setattr(drain_mod.os, "getpid", lambda: master_pid + 1)
        rc = drain_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""
        assert len(db.get_pending_messages_for("agent-alpha")) == 1

    def test_drains_when_we_own_the_name(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """If the registered pid matches ours, we are the master — drain."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        db.register_agent("agent-alpha", str(project), pid=os.getpid())
        db.insert_message("user", "agent-alpha", "hi there", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "hi there" in out

    def test_drains_when_stored_pid_is_our_ancestor(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """Spawned-agent case: agents.pid stores the long-lived Claude
        session PID (written by src/spawner.py), and this hook is its
        descendant. Must drain — an earlier version skipped when
        os.getpid() != stored_pid, breaking hook-based delivery for
        every spawned agent (caught by codex review)."""
        import src.process_liveness as pl
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "spawned"
        project.mkdir()
        fake_hook_pid = 100
        claude_session_pid = 555
        db.register_agent("agent-spawned", str(project), pid=claude_session_pid)
        db.insert_message("user", "agent-spawned", "welcome back", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        chain = {fake_hook_pid: 200, 200: claude_session_pid, claude_session_pid: 1}
        monkeypatch.setattr(pl, "_get_ppid", lambda pid: chain.get(pid))
        monkeypatch.setattr(pl.os, "getpid", lambda: fake_hook_pid)
        # Stored PID is "alive" — this is the real Claude session.
        monkeypatch.setattr(drain_mod, "is_alive", lambda pid: True)
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr().out
        assert "welcome back" in out

    def test_drains_pending_and_emits_json(self, drain_mod, tmp_path, monkeypatch, capsys):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        db.insert_message("user", "agent-alpha", "hi there", "command")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        rc = drain_mod.main()
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        assert "hi there" in ctx
        assert "agent-alpha" in ctx
        # Message is now marked delivered in the DB
        remaining = db.get_pending_messages_for("agent-alpha")
        assert remaining == []
