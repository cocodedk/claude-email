"""Tests for scripts/chat-precompact-hook.py — heartbeat hook for /compact.

Mirrors the loader and isolation patterns of test_chat_drain_inbox.py: the
script lives under scripts/ and is invoked directly by Claude Code's
PreCompact hook. The script is expected to log a single hook_precompact
flow event whose summary records the compaction trigger so the dashboard's
flow panel does not go silent across compaction.
"""
import importlib.util
import io
import json
import os
import sys
from pathlib import Path

import pytest

from src.chat_db import ChatDB

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-precompact-hook.py"


@pytest.fixture
def precompact_mod(monkeypatch):
    for key in ("CHAT_DB_PATH", "CLAUDE_AGENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location(
        "chat_precompact_hook", _SCRIPT_PATH,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _set_stdin(monkeypatch, payload: dict | None):
    if payload is None:
        class FakeStdin:
            def isatty(self):
                return True
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", FakeStdin())
    else:
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


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


class TestSkipPaths:
    """Sub-agents and live siblings must NOT log events for this session.

    Mirrors the discriminators in chat-drain-inbox.py — the same logic
    applies here because both hooks consume from the same caller identity."""

    def test_skips_when_subagent_id_present(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "delta"
        project.mkdir()
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
            "agent_id": "sub-1",
        })
        rc = precompact_mod.main()
        capsys.readouterr()
        assert rc == 0
        assert db.get_flow_events_since(0) == []

    def test_skips_when_live_sibling_owns_name(
        self, precompact_mod, tmp_path, monkeypatch, capsys,
    ):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "epsilon"
        project.mkdir()
        master_pid = os.getpid()
        db.register_agent("agent-epsilon", str(project), pid=master_pid)
        monkeypatch.chdir(project)
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        # Hook helper pretends to be a sibling — different pid than master.
        monkeypatch.setattr(
            precompact_mod.os, "getpid", lambda: master_pid + 1,
        )
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        })
        rc = precompact_mod.main()
        capsys.readouterr()
        assert rc == 0
        assert db.get_flow_events_since(0) == []


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


class TestFlowEventTypesRegistered:
    """Without this entry, the dashboard's flow query filter ignores the
    new rows even though they exist in the events table."""

    def test_hook_precompact_is_in_flow_event_types(self):
        from src.dashboard_queries import FLOW_EVENT_TYPES
        assert "hook_precompact" in FLOW_EVENT_TYPES


class TestStdinEdgeCases:
    """Defensive paths in _read_hook_payload — the helper must never
    raise; downstream main() decides what a missing payload means."""

    def test_isatty_returns_empty(self, precompact_mod, monkeypatch):
        class FakeStdin:
            def isatty(self):
                return True
        monkeypatch.setattr(sys, "stdin", FakeStdin())
        assert precompact_mod._read_hook_payload() == {}

    def test_empty_stdin_returns_empty(self, precompact_mod, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert precompact_mod._read_hook_payload() == {}

    def test_broken_stdin_returns_empty(self, precompact_mod, monkeypatch):
        class _Broken:
            def isatty(self):
                raise OSError("stdin gone")
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", _Broken())
        assert precompact_mod._read_hook_payload() == {}


class TestImportResilience:
    def test_import_does_not_crash_when_dotenv_missing(self, monkeypatch):
        """Hooks ship as standalone scripts; running them with bare Python
        (no dotenv installed) must not raise at import time."""
        real_dotenv = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location(
                "chat_precompact_hook_nodotenv", _SCRIPT_PATH,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real_dotenv is not None:
                sys.modules["dotenv"] = real_dotenv
            else:
                sys.modules.pop("dotenv", None)


class TestDbOpenFailure:
    def test_chatdb_constructor_failure_swallowed(
        self, precompact_mod, tmp_path, monkeypatch, capsys, mocker,
    ):
        """If ChatDB cannot be opened, the hook still exits 0 — telemetry
        must never block the session."""
        db_file = tmp_path / "bus.db"
        db_file.touch()  # exists, so we proceed past the existence guard
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        project = tmp_path / "kappa"
        project.mkdir()
        monkeypatch.chdir(project)
        mocker.patch.object(
            precompact_mod, "ChatDB",
            side_effect=RuntimeError("locked"),
        )
        _set_stdin(monkeypatch, {
            "hook_event_name": "PreCompact",
            "trigger": "manual",
        })
        rc = precompact_mod.main()
        assert rc == 0
        out = capsys.readouterr()
        assert out.out == ""
        assert "cannot open DB" in out.err
