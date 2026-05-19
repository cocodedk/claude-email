"""Tests for scripts/chat-register-self.py — the SessionStart pre-registrar.

We load the script by path since it lives under scripts/ and is invoked
directly by the shell hook, not imported by any package.
"""
from src.chat_db import ChatDB
from tests._chat_register_self_helpers import reg_mod  # noqa: F401


class _FakeStdin:
    def __init__(self, data: str) -> None:
        self._data = data

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return self._data


class TestEnvAgentName:
    """CLAUDE_AGENT_NAME overrides the cwd-derived default."""

    def test_env_var_overrides_cwd_default(self, reg_mod, monkeypatch, tmp_path):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path))  # initialize schema
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-custom")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        agent = ChatDB(str(db_path)).get_agent("agent-custom")
        assert agent is not None
        assert agent["project_path"] == str(tmp_path)

    def test_invalid_env_falls_back_to_cwd_default(
        self, reg_mod, monkeypatch, tmp_path, capsys,
    ):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path))
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "Not Valid")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        expected_fallback = f"agent-{tmp_path.name}"
        agent = ChatDB(str(db_path)).get_agent(expected_fallback)
        assert agent is not None
        assert "rejecting invalid name 'Not Valid'" in capsys.readouterr().err

    def test_unset_env_uses_cwd_default(self, reg_mod, monkeypatch, tmp_path):
        db_path = tmp_path / "chat.db"
        ChatDB(str(db_path))
        monkeypatch.setenv("CHAT_DB_PATH", str(db_path))
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(reg_mod.sys, "stdin", _FakeStdin(""))

        rc = reg_mod.main()
        assert rc == 0
        expected = f"agent-{tmp_path.name}"
        agent = ChatDB(str(db_path)).get_agent(expected)
        assert agent is not None
