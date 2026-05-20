"""Tests for scripts/chat-stop-hook.py — subagent skip + stdin edge cases."""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def stop_mod(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "bus.db"))
    spec = importlib.util.spec_from_file_location(
        "chat_stop_hook",
        Path(__file__).resolve().parent.parent / "scripts" / "chat-stop-hook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestStopHookSkip:
    def test_skips_when_agent_id_in_payload(self, stop_mod, monkeypatch, tmp_path):
        payload = json.dumps({"agent_id": "sub-123", "background_tasks": [{"id": 1}]})
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
        assert stop_mod.main() == 0

    def test_succeeds_with_tty_stdin(self, stop_mod, monkeypatch):
        class FakeTty:
            def isatty(self): return True
            def read(self): return ""
        monkeypatch.setattr(sys, "stdin", FakeTty())
        assert stop_mod.main() == 0

    def test_succeeds_with_empty_stdin(self, stop_mod, monkeypatch):
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(""))
        assert stop_mod.main() == 0
