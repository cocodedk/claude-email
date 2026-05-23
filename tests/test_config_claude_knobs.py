"""Tests for the new claude_* config knobs added in 2026-05 hardening."""
import pytest


@pytest.fixture
def required_env(monkeypatch):
    base = {
        "IMAP_HOST": "x", "IMAP_PORT": "993",
        "SMTP_HOST": "x", "SMTP_PORT": "465",
        "EMAIL_ADDRESS": "a@b", "EMAIL_PASSWORD": "p",
        "AUTHORIZED_SENDER": "u@b", "GPG_FINGERPRINT": "F",
        "POLL_INTERVAL": "30", "CLAUDE_TIMEOUT": "60",
        "CLAUDE_BIN": "claude", "CLAUDE_CWD": "/tmp",
        "STATE_FILE": "/tmp/s.json", "EMAIL_DOMAIN": "b",
        "CHAT_DB_PATH": "/tmp/c.db", "CHAT_URL": "http://x",
        "SERVICE_NAME_EMAIL": "claude-email",
        "SERVICE_NAME_CHAT": "claude-chat",
    }
    for k, v in base.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT", raising=False)
    monkeypatch.delenv("CLAUDE_EMAIL_MCP_NONBLOCKING", raising=False)
    return monkeypatch


def test_exclude_dynamic_prompt_default_true(required_env):
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_exclude_dynamic_prompt"] is True


def test_exclude_dynamic_prompt_off(required_env):
    required_env.setenv("CLAUDE_EMAIL_EXCLUDE_DYNAMIC_PROMPT", "0")
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_exclude_dynamic_prompt"] is False


def test_mcp_nonblocking_default_false(required_env):
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_mcp_nonblocking"] is False


def test_mcp_nonblocking_on(required_env):
    required_env.setenv("CLAUDE_EMAIL_MCP_NONBLOCKING", "1")
    from src.config import build_config
    cfg = build_config()
    assert cfg["claude_mcp_nonblocking"] is True
