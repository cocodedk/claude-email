"""Tests for src/universes.py — per-sender universe config."""
from src.universes import Universe, build_universes


def _base_env(**extra):
    env = {
        "AUTHORIZED_SENDER": "user@example.com",
        "CLAUDE_CWD": "/home/u/projects",
        "CHAT_DB_PATH": "claude-chat.db",
        "CHAT_URL": "http://127.0.0.1:8420/sse",
        "SERVICE_NAME_CHAT": "claude-chat.service",
    }
    env.update(extra)
    return env


class TestBuildUniverses:
    def test_primary_only_when_test_sender_absent(self):
        result = build_universes(_base_env())
        assert len(result) == 1
        assert result[0].sender == "user@example.com"
        assert result[0].is_test is False

    def test_empty_test_sender_is_noop(self):
        result = build_universes(_base_env(TEST_SENDER=""))
        assert len(result) == 1

    def test_whitespace_test_sender_is_noop(self):
        result = build_universes(_base_env(TEST_SENDER="   "))
        assert len(result) == 1

    def test_adds_test_universe(self):
        result = build_universes(_base_env(
            TEST_SENDER="test@cocode.dk",
            TEST_CLAUDE_CWD="/home/u/projects-test",
            TEST_CHAT_DB_PATH="t.db",
            TEST_CHAT_URL="http://127.0.0.1:8421/sse",
            TEST_SERVICE_NAME_CHAT="claude-chat-test.service",
        ))
        assert len(result) == 2
        test = result[1]
        assert test.sender == "test@cocode.dk"
        assert test.allowed_base == "/home/u/projects-test"
        assert test.chat_db_path == "t.db"
        assert test.chat_url == "http://127.0.0.1:8421/sse"
        assert test.service_name_chat == "claude-chat-test.service"
        assert test.is_test is True

    def test_test_universe_defaults(self):
        """When only TEST_SENDER is set, defaults fill the rest."""
        result = build_universes(_base_env(TEST_SENDER="test@cocode.dk"))
        assert len(result) == 2
        test = result[1]
        assert test.chat_db_path == "claude-chat-test.db"
        assert test.chat_url == "http://127.0.0.1:8421/sse"
        assert test.service_name_chat == "claude-chat-test.service"
        # Falls back to prod CLAUDE_CWD (caller should override!) — a loud
        # default, not a silent one; flagged in docs as must-override.
        assert test.allowed_base == "/home/u/projects"

    def test_primary_uses_prod_mcp_config(self):
        result = build_universes(_base_env())
        assert result[0].mcp_config.endswith(".mcp.json")
        assert not result[0].mcp_config.endswith(".mcp-test.json")

    def test_test_universe_uses_test_mcp_config(self):
        result = build_universes(_base_env(TEST_SENDER="t@c.dk"))
        assert result[1].mcp_config.endswith(".mcp-test.json")
