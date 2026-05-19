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
        return db, project

    def test_does_not_steal_slot_from_live_sibling(
        self, drain_mod, tmp_path, monkeypatch,
    ):
        """If a different live process owns the name (sibling claude),
        reclaim must not overwrite — register_agent raises AgentNameTaken
        and we swallow it. The existing sibling-ownership gate then
        correctly refuses to drain."""
        import subprocess
        sibling = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
        )
        try:
            assert sibling.pid != os.getpid()
            fake_claude_pid = 424_242  # doesn't matter, register_agent
            # looks at sibling's alive pid and raises before it matters
            db, project = self._prepare(
                drain_mod, tmp_path, monkeypatch, fake_claude_pid,
            )
            db.register_agent(
                "agent-reclaim", str(project), pid=sibling.pid,
            )
            db.insert_message(
                "peer", "agent-reclaim", "secret for sibling", "notify",
            )
            rc = drain_mod.main()
            assert rc == 0
            # Sibling still owns the row.
            assert db.get_agent("agent-reclaim")["pid"] == sibling.pid
            # Sibling's inbox untouched — we didn't drain their mail.
            assert len(db.get_pending_messages_for("agent-reclaim")) == 1
        finally:
            sibling.kill()
            sibling.wait()

    def test_reclaim_unblocks_drain_for_stale_row(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """End-to-end: row has dead pid, inbox has a pending message.
        After one drain the pid is live AND the message is delivered —
        proving reclaim runs before the sibling gate."""
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        db.register_agent("agent-reclaim", str(project), pid=99_999_999)
        db.insert_message("peer", "agent-reclaim", "deliver-me", "notify")
        rc = drain_mod.main()
        assert rc == 0
        # pid repaired
        assert db.get_agent("agent-reclaim")["pid"] == me
        # message delivered to stdout as hookSpecificOutput context
        payload = json.loads(capsys.readouterr().out)
        assert "deliver-me" in payload["hookSpecificOutput"]["additionalContext"]
        assert db.get_pending_messages_for("agent-reclaim") == []

    def test_reclaim_swallows_unexpected_exception(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """Never block the session on a broken bus: a RuntimeError from
        register_agent lands on stderr; drain still proceeds."""
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        db.register_agent("agent-reclaim", str(project), pid=99_999_999)
        db.insert_message("peer", "agent-reclaim", "still-deliver", "notify")

        def boom(self, name, cwd, pid=None):
            raise RuntimeError("synthetic DB outage")

        monkeypatch.setattr(drain_mod.ChatDB, "register_agent", boom)
        rc = drain_mod.main()
        assert rc == 0
        captured = capsys.readouterr()
        # Reclaim failure diagnostic on stderr, not stdout.
        assert "pid reclaim failed" in captured.err
        assert "synthetic DB outage" in captured.err
        # Drain still delivered the message despite reclaim failing — the
        # sibling gate sees the old dead pid (not a live sibling), so it
        # falls through and drains.
        payload = json.loads(captured.out)
        assert "still-deliver" in payload["hookSpecificOutput"]["additionalContext"]

    def test_reclaim_runs_before_sibling_gate(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        """Order-of-operations guard: if reclaim ran AFTER the sibling
        gate, a row whose stored pid is a dead short-lived helper NOT in
        our ancestry would still drain (gate: is_alive=False), but a
        later subtle refactor could move the gate first and the dead pid
        would be interpreted as a live owner. Pin the order by asserting
        the row's pid was updated before any message-claim call.

        We detect ordering by monkeypatching claim_pending_messages_for
        to snapshot the pid at the moment it is called."""
        me = os.getpid()
        db, project = self._prepare(drain_mod, tmp_path, monkeypatch, me)
        db.register_agent("agent-reclaim", str(project), pid=99_999_999)
        db.insert_message("peer", "agent-reclaim", "ordering", "notify")
        orig = drain_mod.ChatDB.claim_pending_messages_for
        snapshots: list[int | None] = []

        def spy(self, caller):
            row = self.get_agent(caller)
            snapshots.append(row["pid"] if row else None)
            return orig(self, caller)

        monkeypatch.setattr(
            drain_mod.ChatDB, "claim_pending_messages_for", spy,
        )
        rc = drain_mod.main()
        assert rc == 0
        assert snapshots == [me], (
            f"claim_pending_messages_for saw pid={snapshots} — "
            "reclaim must run before drain"
        )
