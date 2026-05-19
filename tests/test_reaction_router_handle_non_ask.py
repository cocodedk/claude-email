"""Tests for src/reaction_router.py — Outlook reaction notification filter."""
import pytest
from unittest.mock import MagicMock

from tests._reaction_router_helpers import _make_db


# ── handle_reaction ───────────────────────────────────────────────────────────


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
