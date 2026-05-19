"""Tests for scripts/chat-precompact-hook.py — heartbeat hook for /compact.

Mirrors the loader and isolation patterns of test_chat_drain_inbox.py: the
script lives under scripts/ and is invoked directly by Claude Code's
PreCompact hook. The script is expected to log a single hook_precompact
flow event whose summary records the compaction trigger so the dashboard's
flow panel does not go silent across compaction.
"""
from src.chat_db import ChatDB

from tests._chat_precompact_hook_helpers import _set_stdin, precompact_mod  # noqa: F401


class TestFlowEventEmission:
    """The dashboard's flow panel needs hook_precompact rows so the panel
    keeps animating across compaction. The hook must emit one — and only
    one — event per invocation, with the trigger captured in the summary."""

    def test_emits_with_manual_trigger(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "alpha"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        })
        rc = precompact_mod.main()
        capsys.readouterr()
        assert rc == 0
        events = db.get_flow_events_since(0)
        assert [e["event_type"] for e in events] == ["hook_precompact"]
        assert events[0]["participant"] == "agent-alpha"
        assert "trigger=manual" in events[0]["summary"]

    def test_emits_with_auto_trigger(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "beta"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "auto",
        })
        precompact_mod.main()
        capsys.readouterr()
        events = db.get_flow_events_since(0)
        assert len(events) == 1
        assert "trigger=auto" in events[0]["summary"]

    def test_missing_trigger_records_unknown(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        """A PreCompact payload without a trigger field still emits a
        heartbeat — the dashboard cares about the lifecycle pulse, not
        the specific trigger. Summary records trigger=unknown so the
        gap is visible if Claude Code drops the field."""
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "gamma"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        _set_stdin(monkeypatch, {"hook_event_name": "PreCompact"})
        precompact_mod.main()
        capsys.readouterr()
        events = db.get_flow_events_since(0)
        assert len(events) == 1
        assert "trigger=unknown" in events[0]["summary"]
