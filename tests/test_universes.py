"""Tests for src/universes.py — per-sender universe config."""
import pytest

from src.universes import Universe, _parse_senders, build_universes


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
            "SENDER": "test@example.com",
            "CLAUDE_CWD": "/home/u/projects-test",
            "CHAT_DB_PATH": "claude-chat-test.db",
            "CHAT_URL": "http://127.0.0.1:8421/sse",
            "SERVICE_NAME_CHAT": "claude-chat-test.service",
            "SHARED_SECRET": "test-secret",
        })
        assert len(result) == 2
        test = result[1]
        assert test.sender == "test@example.com"
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


class TestParseSenders:
    """AUTHORIZED_SENDER accepts a comma-separated list. First is canonical,
    the rest become aliases that share the same creds and resource bundle."""

    def test_single_address_returns_empty_aliases(self):
        canon, aliases = _parse_senders("user@example.com")
        assert canon == "user@example.com"
        assert aliases == ()

    def test_multi_address_splits_on_commas(self):
        canon, aliases = _parse_senders("user@example.com,alias@example.com")
        assert canon == "user@example.com"
        assert aliases == ("alias@example.com",)

    def test_whitespace_trimmed(self):
        canon, aliases = _parse_senders(" bb@x , babak@x , admin@x ")
        assert canon == "bb@x"
        assert aliases == ("babak@x", "admin@x")

    def test_empty_entries_dropped(self):
        canon, aliases = _parse_senders("bb@x,,,babak@x,")
        assert canon == "bb@x"
        assert aliases == ("babak@x",)

    def test_empty_or_whitespace_raises(self):
        with pytest.raises(ValueError, match="AUTHORIZED_SENDER"):
            _parse_senders("")
        with pytest.raises(ValueError, match="AUTHORIZED_SENDER"):
            _parse_senders("   ,  ,  ")

    def test_duplicate_raises(self):
        """An address that's both canonical and alias would make routing
        ambiguous and confuse bundle dedupe — reject at parse time."""
        with pytest.raises(ValueError, match="duplicate"):
            _parse_senders("a@x,a@x")

    def test_duplicate_case_insensitive(self):
        with pytest.raises(ValueError, match="duplicate"):
            _parse_senders("A@X,a@x")


class TestUniverseAliases:
    def test_all_senders_includes_aliases(self):
        u = Universe(
            sender="bb@x", aliases=("babak@x", "admin@x"),
            allowed_base="/", chat_db_path="", chat_url="",
            mcp_config="", service_name_chat="",
        )
        assert u.all_senders == ("bb@x", "babak@x", "admin@x")

    def test_all_senders_defaults_to_canonical_only(self):
        u = Universe(
            sender="bb@x", allowed_base="/", chat_db_path="", chat_url="",
            mcp_config="", service_name_chat="",
        )
        assert u.all_senders == ("bb@x",)

    def test_build_universes_splits_comma_separated_primary(self):
        env = {
            "AUTHORIZED_SENDER": "user@example.com, alias@example.com",
            "CLAUDE_CWD": "/home/u",
            "CHAT_DB_PATH": "c.db",
            "CHAT_URL": "http://x",
            "SERVICE_NAME_CHAT": "claude-chat.service",
            "SHARED_SECRET": "s",
        }
        [primary] = build_universes(env)
        assert primary.sender == "user@example.com"
        assert primary.aliases == ("alias@example.com",)
        # Both share the same creds — only the canonical owns the slot.
        assert primary.all_senders == ("user@example.com", "alias@example.com")
        assert primary.shared_secret == "s"

    def test_test_sender_colliding_with_primary_raises(self):
        """Isolation boundary: a .env.test SENDER that's also a primary
        alias would route prod email to the test universe (or vice versa)
        depending on which bundle registered first. Refuse at build time."""
        env = _base_env(AUTHORIZED_SENDER="user@example.com, alias@example.com")
        test_env = {"SENDER": "alias@example.com"}
        with pytest.raises(ValueError, match="duplicates a primary"):
            build_universes(env, test_env=test_env)

    def test_test_sender_collision_case_insensitive(self):
        env = _base_env(AUTHORIZED_SENDER="user@example.com, alias@example.com")
        test_env = {"SENDER": "ALIAS@Example.COM"}
        with pytest.raises(ValueError, match="duplicates a primary"):
            build_universes(env, test_env=test_env)
