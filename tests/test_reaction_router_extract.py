"""Tests for src/reaction_router.py — Outlook reaction notification filter."""
import pytest
from unittest.mock import MagicMock


# ── extract_reaction ──────────────────────────────────────────────────────────

class TestExtractReaction:
    """extract_reaction(body) returns the lowercase tag or None."""

    def test_like_reaction_outlook_shape(self):
        from src.reaction_router import extract_reaction
        body = "[like]  Babak Bandpey reacted to your message:"
        assert extract_reaction(body) == "like"

    def test_heart_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[heart]  Someone reacted to your post:"
        assert extract_reaction(body) == "heart"

    def test_thumbsdown_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[thumbsdown] X reacted to your email:"
        assert extract_reaction(body) == "thumbsdown"

    def test_thumbsup_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[thumbsup] Alice reacted to your message:"
        assert extract_reaction(body) == "thumbsup"

    def test_celebrate_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[celebrate]  Bob reacted to your post:"
        assert extract_reaction(body) == "celebrate"

    def test_surprised_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[surprised] Carol reacted to your message:"
        assert extract_reaction(body) == "surprised"

    def test_laugh_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[laugh] Dan reacted to your email:"
        assert extract_reaction(body) == "laugh"

    def test_sad_reaction(self):
        from src.reaction_router import extract_reaction
        body = "[sad] Eve reacted to your message:"
        assert extract_reaction(body) == "sad"

    def test_case_insensitive_tag(self):
        from src.reaction_router import extract_reaction
        body = "[LIKE]  Babak reacted to your message:"
        assert extract_reaction(body) == "like"

    def test_mixed_case_tag(self):
        from src.reaction_router import extract_reaction
        body = "[Heart]  Someone reacted to your post:"
        assert extract_reaction(body) == "heart"

    def test_whitespace_tolerant_no_leading_space(self):
        from src.reaction_router import extract_reaction
        body = "[like] Someone reacted to your message:"
        assert extract_reaction(body) == "like"

    def test_leading_whitespace_tolerated(self):
        from src.reaction_router import extract_reaction
        body = "   [like]  Someone reacted to your message:"
        assert extract_reaction(body) == "like"

    def test_multiline_body_matches(self):
        from src.reaction_router import extract_reaction
        body = "[like]  Babak reacted to your message:\n\nOriginal: do the thing"
        assert extract_reaction(body) == "like"

    # Non-reaction bodies must return None

    def test_normal_reply_returns_none(self):
        from src.reaction_router import extract_reaction
        assert extract_reaction("normal reply") is None

    def test_empty_string_returns_none(self):
        from src.reaction_router import extract_reaction
        assert extract_reaction("") is None

    def test_none_input_returns_none(self):
        from src.reaction_router import extract_reaction
        assert extract_reaction(None) is None

    def test_inline_bracket_not_matched(self):
        """[like] mid-sentence must NOT match — tag must be at body start."""
        from src.reaction_router import extract_reaction
        assert extract_reaction("hi [like] is a band") is None

    def test_unknown_tag_returns_none(self):
        from src.reaction_router import extract_reaction
        body = "[clap] Someone reacted to your message:"
        assert extract_reaction(body) is None

    def test_missing_reacted_phrase_returns_none(self):
        from src.reaction_router import extract_reaction
        body = "[like] this is just a command"
        assert extract_reaction(body) is None
