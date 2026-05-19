"""Shared fixture for tests of scripts/chat-drain-inbox.py.

The script lives under scripts/ and is invoked directly by Claude Code
hooks rather than imported by any package — load it via importlib so
each test gets a fresh module-level state.
"""
import importlib.util
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-drain-inbox.py"


@pytest.fixture
def drain_mod(monkeypatch):
    for key in ("CHAT_DB_PATH", "CLAUDE_AGENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("chat_drain_inbox", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
