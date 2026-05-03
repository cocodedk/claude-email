"""Tests for src/agent_name.py — central validator for bus identities."""
from src.agent_name import validated_agent_name


class TestValidatedAgentName:
    def test_valid_passes_through(self):
        assert validated_agent_name("agent-foo", "agent-fallback") == "agent-foo"

    def test_valid_with_hyphens_and_underscores(self):
        assert validated_agent_name("agent-foo_bar-baz", "agent-fb") == "agent-foo_bar-baz"

    def test_none_returns_fallback(self):
        assert validated_agent_name(None, "agent-fb") == "agent-fb"

    def test_empty_string_returns_fallback(self):
        assert validated_agent_name("", "agent-fb") == "agent-fb"

    def test_missing_prefix_falls_back_with_warning(self, capsys):
        assert validated_agent_name("foo", "agent-fb") == "agent-fb"
        assert "rejecting invalid name 'foo'" in capsys.readouterr().err

    def test_uppercase_falls_back(self, capsys):
        assert validated_agent_name("agent-FOO", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_starts_with_hyphen_after_prefix_falls_back(self, capsys):
        assert validated_agent_name("agent--foo", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_too_long_falls_back(self, capsys):
        long = "agent-" + "a" * 63  # 6 + 63 = 69 chars > 64 max
        assert validated_agent_name(long, "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_max_length_passes(self):
        # agent- (6) + alphanumeric start (1) + 57 of [a-z0-9_-] = 64
        name = "agent-" + "a" + "b" * 57
        assert len(name) == 64
        assert validated_agent_name(name, "agent-fb") == name

    def test_disallowed_char_falls_back(self, capsys):
        assert validated_agent_name("agent-foo bar", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err

    def test_minimum_length_passes(self):
        assert validated_agent_name("agent-a", "agent-fb") == "agent-a"

    def test_digit_first_char_passes(self):
        assert validated_agent_name("agent-7omid", "agent-fb") == "agent-7omid"

    def test_trailing_newline_falls_back(self, capsys):
        assert validated_agent_name("agent-foo\n", "agent-fb") == "agent-fb"
        assert "rejecting invalid name" in capsys.readouterr().err
