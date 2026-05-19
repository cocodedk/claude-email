"""Tests for src/reaction_router.py — Outlook reaction notification filter."""
import pytest
from unittest.mock import MagicMock

from tests._reaction_router_helpers import _make_db


# ── handle_reaction ───────────────────────────────────────────────────────────


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
