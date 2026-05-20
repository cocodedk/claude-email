"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import os
import sys

import pytest

from src.chat_db import ChatDB
from tests._chat_drain_inbox_helpers import drain_mod  # noqa: F401


class TestPidReclaim:
    """Every drain invocation re-registers the caller's real Claude PID.

    Heals two failure modes that otherwise leave the dashboard blank:
      - chat-register-self.py didn't run for this session — older
        configs had a ``startup|resume`` matcher on SessionStart that
        silently skipped ``compact`` / ``continue`` session sources
        (matcher is empty now, but stale rows from pre-fix sessions
        still exist on disk).
      - chat-register-self.py fell back to ``os.getpid()`` when the PPID
        walker found no claude ancestor (hook-helper subprocess layout
        varies by how Claude Code spawns hooks), stamping a short-lived
        helper pid that dies the moment the hook exits.

    The invariant is: whenever a hook fires under a live claude session,
    the claude session's pid ends up in the row. Reclaim runs before the
    sibling-ownership gate so the gate sees the fresh pid and drain
    actually delivers.
    """

    @pytest.fixture(autouse=True)
    def _no_stdin(self, monkeypatch):
        """Default UserPromptSubmit (tty stdin) unless overridden."""
        class FakeStdin:
            def isatty(self):
                return True
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", FakeStdin())

    def _prepare(self, drain_mod, tmp_path, monkeypatch, ancestor_pid):
        """Stand up a fresh DB, a project cwd, and a fixed walker result."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "reclaim"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        from src import chat_pid_reclaim
        monkeypatch.setattr(
            chat_pid_reclaim, "find_ancestor_pid_matching",
            lambda _marker: ancestor_pid,
        )
        monkeypatch.setattr(
            chat_pid_reclaim, "find_session_pid_for_cwd",
            lambda cwd, **kw: None,
        )
        return db, project

    def test_noop_when_no_claude_ancestor_visible(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """Ad-hoc CLI / missing /proc: walker returns None, row stays put."""
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, None)
        db.register_agent("agent-reclaim", str(project), pid=999_999)
        rc = drain_mod.main()
        assert rc == 0
        assert db.get_agent("agent-reclaim")["pid"] == 999_999

    def test_noop_when_agent_row_missing(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """Registration is chat-register-self.py's job; drain must not
        conjure a row on its own — otherwise one-off manual drains would
        create bogus entries for directories that were never meant to be
        agents."""
        db, _ = self._prepare(drain_mod, tmp_path, monkeypatch, os.getpid())
        rc = drain_mod.main()
        assert rc == 0
        assert db.get_agent("agent-reclaim") is None

    def test_noop_when_stored_pid_already_matches_ancestor(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """Happy-path idempotence: don't churn status / last_seen_at on
        every turn when the row is already correct."""
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        db.register_agent("agent-reclaim", str(project), pid=me)
        # Capture register_agent calls via a class-level spy.
        calls: list[tuple] = []
        orig = drain_mod.ChatDB.register_agent

        def spy(self, name, cwd, pid=None):
            calls.append((name, cwd, pid))
            return orig(self, name, cwd, pid=pid)

        monkeypatch.setattr(drain_mod.ChatDB, "register_agent", spy)
        rc = drain_mod.main()
        assert rc == 0
        assert calls == []
        assert db.get_agent("agent-reclaim")["pid"] == me

    def test_rewrites_dead_stored_pid(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """The headline case: stale dead pid left by a prior session, no
        live sibling — reclaim heals the row."""
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        dead_pid = 99_999_999  # far above Linux's 22-bit default PID space
        db.register_agent("agent-reclaim", str(project), pid=dead_pid)
        rc = drain_mod.main()
        assert rc == 0
        assert db.get_agent("agent-reclaim")["pid"] == me

    def test_rewrites_null_stored_pid(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """MCP chat_register writes pid=NULL; the first drain fixes it."""
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        db.register_agent("agent-reclaim", str(project), pid=None)
        assert db.get_agent("agent-reclaim")["pid"] is None
        rc = drain_mod.main()
        assert rc == 0
        assert db.get_agent("agent-reclaim")["pid"] == me
