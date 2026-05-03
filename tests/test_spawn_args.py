"""Tests for src/spawn_args.py — meta-command argument parser for `spawn`."""
import pytest

from src.spawn_args import parse_spawn_args


class TestParseSpawnArgs:
    def test_path_only(self):
        assert parse_spawn_args("/some/path") == ("/some/path", None, "")

    def test_path_and_instruction(self):
        assert parse_spawn_args("/some/path do something now") == (
            "/some/path", None, "do something now",
        )

    def test_path_as_name(self):
        assert parse_spawn_args("/some/path as agent-foo") == (
            "/some/path", "agent-foo", "",
        )

    def test_path_as_name_and_instruction(self):
        assert parse_spawn_args("/some/path as agent-foo do something") == (
            "/some/path", "agent-foo", "do something",
        )

    def test_empty_returns_empty_path(self):
        assert parse_spawn_args("") == ("", None, "")

    def test_only_whitespace_returns_empty_path(self):
        assert parse_spawn_args("   ") == ("", None, "")

    def test_as_keyword_only_recognized_at_position_two(self):
        """'as' as part of an instruction should NOT trigger name parsing."""
        assert parse_spawn_args("/path do as previously") == (
            "/path", None, "do as previously",
        )

    def test_as_without_following_token_raises(self):
        """`spawn /path as` is a typo (no name after `as`) — fail fast."""
        with pytest.raises(ValueError) as excinfo:
            parse_spawn_args("/path as")
        assert "missing agent name after 'as'" in str(excinfo.value)
