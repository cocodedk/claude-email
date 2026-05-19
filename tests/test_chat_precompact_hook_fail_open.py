"""Fail-open tests for scripts/chat-precompact-hook.py.

The hook is best-effort telemetry; broken config, missing DB, or write
failures must never raise or block the session.
"""
import io
import sys

from src.chat_db import ChatDB

from tests._chat_precompact_hook_helpers import _set_stdin, precompact_mod  # noqa: F401


class TestFailOpen:
    """The hook is best-effort telemetry; a broken DB or missing config
    must never raise or block the session — exit code 0 and silence."""

    def test_missing_db_path_exits_clean(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        project = tmp_path / "zeta"
        project.mkdir()
        monkeypatch.chdir(project)
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        })
        rc = precompact_mod.main()
        assert rc == 0
        # Diagnostic to stderr is fine; stdout must stay empty.
        assert capsys.readouterr().out == ""

    def test_db_file_does_not_exist_exits_clean(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "missing.db"))
        project = tmp_path / "eta"
        project.mkdir()
        monkeypatch.chdir(project)
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        })
        rc = precompact_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_telemetry_write_failure_swallowed(
        self, precompact_mod, tmp_path, monkeypatch, capsys, mocker,
    ):
        db_file = tmp_path / "bus.db"
        ChatDB(str(db_file))
        project = tmp_path / "theta"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        mocker.patch(
            "src.chat_db.ChatDB._log_event",
            side_effect=RuntimeError("events table is sulking"),
        )
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        })
        rc = precompact_mod.main()
        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_malformed_stdin_exits_clean(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "iota"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
        rc = precompact_mod.main()
        capsys.readouterr()
        assert rc == 0
        # Malformed payload still produces a heartbeat with trigger=unknown.
        events = db.get_flow_events_since(0)
        assert len(events) == 1
        assert "trigger=unknown" in events[0]["summary"]
