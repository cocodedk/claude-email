"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import json
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

    def test_env_var_set_force_reclaims_over_live_sibling(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """Env-var-bound resumed session must overtake a stale sibling-pid row
        (otherwise mail rots — the silent-drain-skip incident)."""
        import subprocess
        sibling = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
        )
        try:
            assert sibling.pid != os.getpid()
            me = os.getpid()
            db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
            db.register_agent(
                "agent-reclaim", str(project), pid=sibling.pid,
            )
            db.insert_message(
                "peer", "agent-reclaim", "deliver-to-resumed", "notify",
            )
            monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-reclaim")
            rc = drain_mod.main()
            assert rc == 0
            # Force-reclaim updated the row to the current claude pid.
            assert db.get_agent("agent-reclaim")["pid"] == me
            # Sibling-gate now sees self → drain proceeds → message delivered.
            payload = json.loads(capsys.readouterr().out)
            assert (
                "deliver-to-resumed"
                in payload["hookSpecificOutput"]["additionalContext"]
            )
            assert db.get_pending_messages_for("agent-reclaim") == []
        finally:
            sibling.kill()
            sibling.wait()

    def test_invalid_env_var_does_not_force_reclaim(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """A typo'd env var must not grant cross-name authority over a sibling."""
        import subprocess
        sibling = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
        )
        try:
            me = os.getpid()
            db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
            db.register_agent(
                "agent-reclaim", str(project), pid=sibling.pid,
            )
            db.insert_message(
                "peer", "agent-reclaim", "for-sibling", "notify",
            )
            monkeypatch.setenv("CLAUDE_AGENT_NAME", "Not Valid")
            rc = drain_mod.main()
            assert rc == 0
            # Sibling still owns the row — invalid env var did not force.
            assert db.get_agent("agent-reclaim")["pid"] == sibling.pid
            assert len(db.get_pending_messages_for("agent-reclaim")) == 1
        finally:
            sibling.kill()
            sibling.wait()

    def test_force_reclaim_uses_update_agent_pid_when_register_blocked(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """Force path goes through update_agent_pid, bypassing the AgentNameTaken
        guard that would re-fire if we routed via register_agent."""
        from src.chat_errors import AgentNameTaken
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        db.register_agent("agent-reclaim", str(project), pid=99_999_999)
        db.insert_message("peer", "agent-reclaim", "force-pid", "notify")
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-reclaim")

        def always_taken(self, name, cwd, pid=None):
            raise AgentNameTaken(name, 12345)

        update_pid_calls: list[tuple[str, int]] = []
        orig_update_pid = drain_mod.ChatDB.update_agent_pid

        def spy_update_pid(self, name, pid):
            update_pid_calls.append((name, pid))
            return orig_update_pid(self, name, pid)

        monkeypatch.setattr(
            drain_mod.ChatDB, "register_agent", always_taken,
        )
        monkeypatch.setattr(
            drain_mod.ChatDB, "update_agent_pid", spy_update_pid,
        )
        rc = drain_mod.main()
        assert rc == 0
        assert update_pid_calls == [("agent-reclaim", me)]
        # And the row reflects the rewrite.
        assert db.get_agent("agent-reclaim")["pid"] == me
