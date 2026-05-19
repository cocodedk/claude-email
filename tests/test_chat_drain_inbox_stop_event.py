"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import io
import json

from src.chat_db import ChatDB
from tests._chat_drain_inbox_helpers import drain_mod  # noqa: F401


class TestStopEvent:
    """Stop hook must emit {decision:block, reason:...} so pending peer
    messages are reinjected before the session idles. Responsiveness is
    the whole point — stop_hook_active is ignored because
    mark_message_delivered is the real loop guard.
    """

    def _run_with_stdin(self, drain_mod, stdin_payload: dict, capsys):
        buf = io.StringIO(json.dumps(stdin_payload))
        import sys as _sys
        # monkeypatch via direct attribute set inside this helper
        orig = _sys.stdin
        _sys.stdin = buf
        try:
            rc = drain_mod.main()
        finally:
            _sys.stdin = orig
        return rc, capsys.readouterr()

    def test_stop_with_pending_emits_decision_block(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "gamma"
        project.mkdir()
        db.insert_message("agent-peer", "agent-gamma", "peer ping", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc, out = self._run_with_stdin(
            drain_mod, {"hook_event_name": "Stop"}, capsys,
        )
        assert rc == 0
        payload = json.loads(out.out)
        assert payload["decision"] == "block"
        assert "peer ping" in payload["reason"]
        assert "agent-peer" in payload["reason"]
        # And messages are marked delivered
        assert db.get_pending_messages_for("agent-gamma") == []

    def test_stop_empty_inbox_is_silent(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "delta"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc, out = self._run_with_stdin(
            drain_mod, {"hook_event_name": "Stop"}, capsys,
        )
        assert rc == 0
        assert out.out == ""

    def test_stop_blocks_even_when_stop_hook_active(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        # Design decision: peer responsiveness matters more than the
        # default stop_hook_active guard. The loop terminates naturally
        # once mark_message_delivered drains the inbox, so we re-block
        # to keep the agent conversant.
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "epsilon"
        project.mkdir()
        db.insert_message("agent-peer", "agent-epsilon", "still here", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))

        rc, out = self._run_with_stdin(
            drain_mod,
            {"hook_event_name": "Stop", "stop_hook_active": True},
            capsys,
        )
        assert rc == 0
        payload = json.loads(out.out)
        assert payload["decision"] == "block"
        assert "still here" in payload["reason"]
