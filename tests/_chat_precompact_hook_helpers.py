"""Shared fixtures and helpers for the chat-precompact-hook test suite.

Underscore prefix prevents pytest collection; the test modules import
``precompact_mod`` and ``_set_stdin`` from here.
"""
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

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
