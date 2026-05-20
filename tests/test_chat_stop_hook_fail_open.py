"""Tests for scripts/chat-stop-hook.py — fail-open on DB errors."""
import importlib
import io
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def stop_mod(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "chat_stop_hook",
        Path(__file__).resolve().parent.parent / "scripts" / "chat-stop-hook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestStopHookFailOpen:
    def test_exit_0_when_chat_db_path_not_set(self, stop_mod, monkeypatch, tmp_path):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"background_tasks": [{"id": 1}], "session_crons": []}
        )))
        assert stop_mod.main() == 0

    def test_exit_0_when_db_file_missing(self, stop_mod, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "nonexistent.db"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"background_tasks": [{"id": 1}], "session_crons": []}
        )))
        assert stop_mod.main() == 0
