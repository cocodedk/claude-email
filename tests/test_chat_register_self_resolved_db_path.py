"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
import pytest

from tests._chat_register_self_helpers import _REPO_ROOT, reg_mod  # noqa: F401


class TestResolvedDbPath:
    def test_relative_resolves_against_repo_root(self, reg_mod, monkeypatch):
        monkeypatch.setenv("CHAT_DB_PATH", "claude-chat.db")
        assert reg_mod._resolved_db_path() == _REPO_ROOT / "claude-chat.db"

    def test_absolute_returned_as_is(self, reg_mod, monkeypatch, tmp_path):
        abs_db = tmp_path / "chat.db"
        monkeypatch.setenv("CHAT_DB_PATH", str(abs_db))
        assert reg_mod._resolved_db_path() == abs_db

    def test_missing_env_raises(self, reg_mod, monkeypatch):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_DB_PATH not set"):
            reg_mod._resolved_db_path()
