"""Shared helpers for test_reaction_router_* modules."""
from unittest.mock import MagicMock


def _make_db(original_type: str | None, original_id: int = 1):
    """Return a mock ChatDB with get_message pre-configured."""
    db = MagicMock()
    if original_type is None:
        db.get_message.return_value = None
    else:
        db.get_message.return_value = {"id": original_id, "type": original_type}
    db.insert_message.return_value = {"id": 99, "type": "reply"}
    return db
