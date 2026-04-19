"""Tests for src/universes.py — per-sender universe config."""
from src.universes import Universe, build_universes


def _base_env(**extra):
    env = {
        "AUTHORIZED_SENDER": "user@example.com",
        "CLAUDE_CWD": "/home/u/projects",
        "CHAT_DB_PATH": "claude-chat.db",
        "CHAT_URL": "http://127.0.0.1:8420/sse",
        "SERVICE_NAME_CHAT": "claude-chat.service",
        "SHARED_SECRET": "prod-secret",
        "GPG_FINGERPRINT": "",
    }
    env.update(extra)
    return env


class TestBuildUniverses:
    def test_primary_only_when_no_test_env(self):
        result = build_universes(_base_env(), test_env=None)
        assert len(result) == 1
        assert result[0].sender == "user@example.com"
        assert result[0].is_test is False

    def test_empty_test_env_is_noop(self):
        result = build_universes(_base_env(), test_env={})
        assert len(result) == 1

    def test_test_env_without_sender_is_noop(self):
        result = build_universes(_base_env(), test_env={"CHAT_PORT": "8421"})
        assert len(result) == 1

    def test_test_env_with_whitespace_sender_is_noop(self):
        result = build_universes(_base_env(), test_env={"SENDER": "   "})
        assert len(result) == 1

    def test_test_env_adds_second_universe(self):
        result = build_universes(_base_env(), test_env={
            "SENDER": "test@cocode.dk",
            "CLAUDE_CWD": "/home/u/projects-test",
            "CHAT_DB_PATH": "claude-chat-test.db",
            "CHAT_URL": "http://127.0.0.1:8421/sse",
            "SERVICE_NAME_CHAT": "claude-chat-test.service",
            "SHARED_SECRET": "test-secret",
        })
        assert len(result) == 2
        test = result[1]
        assert test.sender == "test@cocode.dk"
        assert test.allowed_base == "/home/u/projects-test"
        assert test.shared_secret == "test-secret"
        assert test.is_test is True

    def test_primary_secret_isolated_from_test(self):
        """A compromised .env.test with SHARED_SECRET must NOT change the
        primary universe's secret. The two auth gates stay separate."""
        result = build_universes(
            _base_env(), test_env={"SENDER": "t@x", "SHARED_SECRET": "leaked"},
        )
        assert result[0].shared_secret == "prod-secret"  # unchanged
        assert result[1].shared_secret == "leaked"

    def test_test_universe_falls_back_to_prod_cwd_if_missing(self):
        """Loud-but-working default: if .env.test forgets CLAUDE_CWD, inherit
        the primary's so we don't crash, but operator must fix .env.test."""
        result = build_universes(_base_env(), test_env={"SENDER": "t@x"})
        assert result[1].allowed_base == "/home/u/projects"

    def test_auth_prefix_property(self):
        u = Universe(
            sender="x", allowed_base="/", chat_db_path="x", chat_url="",
            mcp_config="", service_name_chat="", shared_secret="s3cret",
        )
        assert u.auth_prefix == "AUTH:s3cret"

    def test_primary_uses_prod_mcp_config(self):
        result = build_universes(_base_env())
        assert result[0].mcp_config.endswith(".mcp.json")
        assert not result[0].mcp_config.endswith(".mcp-test.json")

    def test_test_universe_uses_test_mcp_config(self):
        result = build_universes(_base_env(), test_env={"SENDER": "t@x"})
        assert result[1].mcp_config.endswith(".mcp-test.json")
