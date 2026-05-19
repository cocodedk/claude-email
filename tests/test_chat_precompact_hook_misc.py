"""Misc tests for scripts/chat-precompact-hook.py.

Covers flow-event-type registration, stdin edge cases in
``_read_hook_payload``, dotenv-less import resilience, and ChatDB
constructor-failure handling.
"""
import importlib.util
import io
import sys
from pathlib import Path

from tests._chat_precompact_hook_helpers import _set_stdin, precompact_mod  # noqa: F401

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-precompact-hook.py"


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
