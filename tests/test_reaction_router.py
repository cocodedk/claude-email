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


# ── handle_reaction ───────────────────────────────────────────────────────────

def _make_db(original_type: str | None, original_id: int = 1):
    """Return a mock ChatDB with get_message pre-configured."""
    db = MagicMock()
    if original_type is None:
        db.get_message.return_value = None
    else:
        db.get_message.return_value = {"id": original_id, "type": original_type}
    db.insert_message.return_value = {"id": 99, "type": "reply"}
    return db


class TestHandleReactionAsk:
    """When the original message is type='ask', positive → 'yes', negative → 'no'."""

    def test_positive_reaction_on_ask_inserts_yes(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        ack, tag = handle_reaction(db, agent_name="agent-foo", original_message_id=1, reaction="like")
        db.insert_message.assert_called_once_with(
            "user", "agent-foo", "yes", "reply", in_reply_to=1,
        )
        assert "like" in ack
        assert "noted" in ack.lower()

    def test_heart_on_ask_inserts_yes(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        ack, _ = handle_reaction(db, agent_name="agent-foo", original_message_id=1, reaction="heart")
        db.insert_message.assert_called_once_with(
            "user", "agent-foo", "yes", "reply", in_reply_to=1,
        )

    def test_thumbsup_on_ask_inserts_yes(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        handle_reaction(db, agent_name="agent-foo", original_message_id=1, reaction="thumbsup")
        args = db.insert_message.call_args
        assert args[0][2] == "yes"

    def test_celebrate_on_ask_inserts_yes(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        handle_reaction(db, agent_name="agent-foo", original_message_id=1, reaction="celebrate")
        assert db.insert_message.call_args[0][2] == "yes"

    def test_thumbsdown_on_ask_inserts_no(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        ack, tag = handle_reaction(db, agent_name="agent-foo", original_message_id=1, reaction="thumbsdown")
        db.insert_message.assert_called_once_with(
            "user", "agent-foo", "no", "reply", in_reply_to=1,
        )
        assert "thumbsdown" in ack
        assert "noted" in ack.lower()

    def test_sad_on_ask_inserts_no(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        handle_reaction(db, agent_name="agent-foo", original_message_id=1, reaction="sad")
        assert db.insert_message.call_args[0][2] == "no"

    def test_ask_ack_contains_reaction_name(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        ack, tag = handle_reaction(db, agent_name="agent-x", original_message_id=1, reaction="like")
        assert "like" in ack

    def test_ask_tag_is_reaction(self):
        from src.reaction_router import handle_reaction
        db = _make_db("ask")
        _, tag = handle_reaction(db, agent_name="agent-x", original_message_id=1, reaction="like")
        assert tag == "Reaction"


class TestHandleReactionNonAsk:
    """Non-ask originals → lightweight ack insert, audit-log only."""

    def test_notify_original_inserts_bracket_reaction(self):
        from src.reaction_router import handle_reaction
        db = _make_db("notify")
        ack, tag = handle_reaction(db, agent_name="agent-foo", original_message_id=5, reaction="like")
        db.insert_message.assert_called_once_with(
            "user", "agent-foo", "[like]", "reply", in_reply_to=5,
        )

    def test_result_original_inserts_bracket_reaction(self):
        from src.reaction_router import handle_reaction
        db = _make_db("result")
        handle_reaction(db, agent_name="agent-foo", original_message_id=3, reaction="heart")
        db.insert_message.assert_called_once_with(
            "user", "agent-foo", "[heart]", "reply", in_reply_to=3,
        )

    def test_none_original_inserts_bracket_reaction(self):
        """Missing original (None from get_message) treated as non-ask."""
        from src.reaction_router import handle_reaction
        db = _make_db(None)
        handle_reaction(db, agent_name="agent-foo", original_message_id=999, reaction="thumbsup")
        db.insert_message.assert_called_once_with(
            "user", "agent-foo", "[thumbsup]", "reply", in_reply_to=999,
        )

    def test_non_ask_ack_text(self):
        from src.reaction_router import handle_reaction
        db = _make_db("notify")
        ack, tag = handle_reaction(db, agent_name="agent-foo", original_message_id=5, reaction="like")
        assert "like" in ack
        assert "noted" in ack.lower()
        assert "no work queued" in ack.lower()

    def test_non_ask_tag_is_reaction(self):
        from src.reaction_router import handle_reaction
        db = _make_db("notify")
        _, tag = handle_reaction(db, agent_name="agent-foo", original_message_id=5, reaction="like")
        assert tag == "Reaction"
