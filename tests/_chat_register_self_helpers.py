"""Shared fixture for tests of scripts/chat-register-self.py.

The script is invoked directly by the SessionStart shell hook, not
imported by any package — load it via importlib so each test gets a
fresh module-level state.
"""
import importlib.util
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "chat-register-self.py"


@pytest.fixture
def reg_mod(monkeypatch):
    """Import the script as a module each test — fresh module-level state."""
    # The script loads .env at import time; strip env vars it might read so
    # tests control them via monkeypatch explicitly.
    for key in ("CHAT_DB_PATH", "CLAUDE_AGENT_NAME"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("chat_register_self", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
