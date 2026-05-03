"""Tests for src/chat_errors.py — registry layer exception classes.

AgentProjectTaken is deprecated as of 2026-05-03 (no longer raised by
register_agent) but kept as a symbol because three modules still list it
in `except (AgentNameTaken, AgentProjectTaken)` clauses. This test pins
its attribute contract so future cleanup doesn't accidentally break
those catch sites.
"""
from src.chat_errors import AgentNameTaken, AgentProjectTaken


class TestAgentNameTaken:
    def test_attributes_preserved(self):
        exc = AgentNameTaken("agent-foo", 12345)
        assert exc.name == "agent-foo"
        assert exc.owner_pid == 12345
        assert "agent-foo" in str(exc)
        assert "12345" in str(exc)


class TestAgentProjectTaken:
    """Deprecated class — still constructible for backward-compat."""

    def test_attributes_preserved(self):
        exc = AgentProjectTaken("/shared/p", "agent-bar", 67890)
        assert exc.project_path == "/shared/p"
        assert exc.owner_name == "agent-bar"
        assert exc.owner_pid == 67890

    def test_message_includes_all_three_fields(self):
        exc = AgentProjectTaken("/shared/p", "agent-bar", 67890)
        msg = str(exc)
        assert "/shared/p" in msg
        assert "agent-bar" in msg
        assert "67890" in msg
