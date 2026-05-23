"""Tests for inject_codex_config — Codex chat bus bootstrap."""
import json
import os
import stat

import pytest


class TestInjectCodexConfig:
    def test_writes_config_toml(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-backend")
        toml = (tmp_path / ".codex" / "config.toml").read_text()
        assert 'url = "http://127.0.0.1:8420/mcp/"' in toml
        assert 'hooks = ".codex/hooks.json"' in toml
        assert "[features]" in toml

    def test_writes_hooks_json(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-backend")
        data = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
        assert "SessionStart" in data["hooks"]
        assert "UserPromptSubmit" in data["hooks"]
        assert "Stop" in data["hooks"]
        cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "chat-agent-advisor-hook.sh" in cmd
        assert "SessionStart" in cmd

    def test_writes_wrapper_script_with_agent_name(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-front")
        script = (tmp_path / ".codex" / "scripts" / "chat-agent-advisor-hook.sh").read_text()
        assert 'agent_name="${CODEX_CHAT_AGENT_NAME:-agent-codex-front}"' in script

    def test_wrapper_script_has_chat_root(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-x")
        script = (tmp_path / ".codex" / "scripts" / "chat-agent-advisor-hook.sh").read_text()
        assert "chat-register-self.py" in script
        assert "chat-drain-inbox.py" in script
        assert "CLAUDE_PROCESS_MARKER" in script

    def test_wrapper_script_is_executable(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-x")
        path = tmp_path / ".codex" / "scripts" / "chat-agent-advisor-hook.sh"
        assert os.access(str(path), os.X_OK)

    def test_writes_agent_name_file(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-back")
        content = (tmp_path / ".codex" / "agent-name").read_text()
        assert content.strip() == "agent-codex-back"

    def test_derives_mcp_url_from_sse_url(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://10.0.0.5:9999/sse", "agent-codex-x")
        toml = (tmp_path / ".codex" / "config.toml").read_text()
        assert 'url = "http://10.0.0.5:9999/mcp/"' in toml

    def test_is_idempotent(self, tmp_path):
        from src.codex_bootstrap import inject_codex_config
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-x")
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-x")
        toml = (tmp_path / ".codex" / "config.toml").read_text()
        assert toml.count("[mcp_servers.claude-chat]") == 1

    def test_io_error_is_logged_not_raised(self, tmp_path, mocker):
        from src.codex_bootstrap import inject_codex_config
        mocker.patch("builtins.open", side_effect=PermissionError("denied"))
        mocker.patch("os.makedirs")
        inject_codex_config(str(tmp_path), "http://127.0.0.1:8420/sse", "agent-codex-x")
