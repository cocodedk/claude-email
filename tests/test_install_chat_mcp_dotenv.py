"""Tests for scripts/install-chat-mcp.py — batch bootstrap of chat MCP into projects.

Script lives under scripts/ so we import it by path (same pattern as the hook scripts).
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "install-chat-mcp.py"


class TestImportTimeDotenv:
    def test_import_survives_missing_dotenv(self, monkeypatch):
        real = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location(
                "install_chat_mcp_nodotenv", _SCRIPT_PATH,
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real is not None:
                sys.modules["dotenv"] = real
            else:
                sys.modules.pop("dotenv", None)
