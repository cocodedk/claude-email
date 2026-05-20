"""Tests for scripts/chat-drain-inbox.py — hook that drains agent inbox.

Mirrors the loader pattern of test_chat_register_self.py because the script
lives under scripts/ and is invoked directly by Claude Code hooks.
"""
import io
import json
import sys

import pytest

from tests._chat_drain_inbox_helpers import _REPO_ROOT, drain_mod  # noqa: F401


class TestReadHookEvent:
    def test_isatty_defaults_to_user_prompt(self, drain_mod, monkeypatch):
        class FakeStdin:
            def isatty(self):
                return True
        monkeypatch.setattr(sys, "stdin", FakeStdin())
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_empty_stdin_defaults(self, drain_mod, monkeypatch):
        buf = io.StringIO("")
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_valid_json_uses_event_name(self, drain_mod, monkeypatch):
        buf = io.StringIO(json.dumps({"hook_event_name": "SessionStart"}))
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "SessionStart"

    def test_malformed_json_defaults(self, drain_mod, monkeypatch):
        buf = io.StringIO("{not json")
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_json_without_event_name_defaults(self, drain_mod, monkeypatch):
        buf = io.StringIO("{}")
        monkeypatch.setattr(sys, "stdin", buf)
        assert drain_mod._read_hook_event() == "UserPromptSubmit"

    def test_read_hook_payload_swallows_stdin_errors(
        self, drain_mod, monkeypatch,
    ):
        """Broken stdin (OSError) must not crash — return {}."""
        class _Broken:
            def isatty(self):
                raise OSError("stdin gone")
            def read(self):
                return ""
        monkeypatch.setattr(sys, "stdin", _Broken())
        assert drain_mod._read_hook_payload() == {}



class TestFormatContext:
    def test_single_message(self, drain_mod):
        ctx = drain_mod._format_context("agent-foo", [
            {"id": 1, "from_name": "user", "created_at": "2026-04-19T08:00:00+00:00", "body": "hello"},
        ])
        assert "INBOX" in ctx
        assert "do NOT call" in ctx
        assert "[msg #1]" in ctx
        assert "from=user" in ctx
        assert "hello" in ctx
        assert 'agent-foo' in ctx

    def test_multiple_messages(self, drain_mod):
        msgs = [
            {"id": 1, "from_name": "user", "created_at": "t1", "body": "a"},
            {"id": 2, "from_name": "agent-bar", "created_at": "t2", "body": "b"},
        ]
        ctx = drain_mod._format_context("agent-foo", msgs)
        assert "[msg #1]" in ctx and "[msg #2]" in ctx
        assert "from=user" in ctx and "from=agent-bar" in ctx
