"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import io
import json

from src.chat_db import ChatDB
from tests._chat_drain_inbox_helpers import drain_mod  # noqa: F401


class TestFlowEventEmission:
    """The dashboard's flow panel depends on hook_drain_stop /
    hook_drain_session being written to the events table whenever the
    drain actually delivers something. Quiet turns must NOT emit an
    event — we don't want to animate nothing."""

    def _run_with_stdin(self, drain_mod, stdin_payload: dict):
        import sys as _sys
        buf = io.StringIO(json.dumps(stdin_payload))
        orig = _sys.stdin
        _sys.stdin = buf
        try:
            return drain_mod.main()
        finally:
            _sys.stdin = orig

    def test_stop_drain_emits_hook_drain_stop(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "kappa"
        project.mkdir()
        db.insert_message("peer", "agent-kappa", "fire", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        self._run_with_stdin(drain_mod, {"hook_event_name": "Stop"})
        capsys.readouterr()
        types = [r["event_type"] for r in db.get_flow_events_since(0)]
        assert types == ["hook_drain_stop"]

    def test_session_drain_emits_hook_drain_session(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "lambda"
        project.mkdir()
        db.insert_message("peer", "agent-lambda", "fire", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        self._run_with_stdin(
            drain_mod, {"hook_event_name": "SessionStart"},
        )
        capsys.readouterr()
        types = [r["event_type"] for r in db.get_flow_events_since(0)]
        assert types == ["hook_drain_session"]

    def test_empty_inbox_does_not_emit(
        self, drain_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "mu"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        self._run_with_stdin(drain_mod, {"hook_event_name": "Stop"})
        capsys.readouterr()
        assert db.get_flow_events_since(0) == []

    def test_telemetry_failure_does_not_break_drain(
        self, drain_mod, tmp_path, monkeypatch, capsys, mocker,
    ):
        """Never block the session on telemetry — if _log_event raises,
        the drained payload still lands on stdout."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "nu"
        project.mkdir()
        db.insert_message("peer", "agent-nu", "fire", "notify")
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        mocker.patch(
            "src.chat_db.ChatDB._log_event",
            side_effect=RuntimeError("events table is sulking"),
        )
        self._run_with_stdin(drain_mod, {"hook_event_name": "Stop"})
        payload = json.loads(capsys.readouterr().out)
        assert payload["decision"] == "block"
        assert "fire" in payload["reason"]
