"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
import importlib.util
import sys

from tests._chat_register_self_helpers import _SCRIPT_PATH, reg_mod  # noqa: F401


class TestImportTimeDotenv:
    def test_import_does_not_crash_when_dotenv_missing(self, monkeypatch):
        """If python-dotenv is not installed the script should still import."""
        # Remove dotenv from sys.modules and mask it
        real_dotenv = sys.modules.pop("dotenv", None)
        monkeypatch.setitem(sys.modules, "dotenv", None)
        try:
            spec = importlib.util.spec_from_file_location("chat_register_self_nodotenv", _SCRIPT_PATH)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            assert hasattr(mod, "main")
        finally:
            if real_dotenv is not None:
                sys.modules["dotenv"] = real_dotenv
            else:
                sys.modules.pop("dotenv", None)
