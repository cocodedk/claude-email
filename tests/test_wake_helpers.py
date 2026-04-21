"""Targeted coverage for wake_helpers._is_session_fresh edge cases."""
from datetime import datetime, timedelta, timezone

from src.wake_helpers import _is_session_fresh


def test_missing_last_turn_at_is_stale():
    assert _is_session_fresh({"session_id": "x"}, idle_secs=60) is False


def test_empty_last_turn_at_is_stale():
    assert _is_session_fresh({"last_turn_at": ""}, idle_secs=60) is False


def test_invalid_iso_is_stale():
    assert _is_session_fresh({"last_turn_at": "not-an-iso"}, idle_secs=60) is False


def test_non_string_last_turn_at_is_stale():
    """None/int/etc. must fall through to the TypeError path, not raise."""
    assert _is_session_fresh({"last_turn_at": 12345}, idle_secs=60) is False


def test_naive_datetime_is_treated_as_utc():
    """ISO without tz should be interpreted as UTC so fresh/expired decision
    is consistent with wallclock UTC comparisons."""
    fresh = datetime.now(timezone.utc) - timedelta(seconds=5)
    naive = fresh.replace(tzinfo=None).isoformat()
    assert _is_session_fresh({"last_turn_at": naive}, idle_secs=60) is True


def test_old_timestamp_is_stale():
    old = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    assert _is_session_fresh({"last_turn_at": old}, idle_secs=60) is False


def test_recent_timestamp_is_fresh():
    recent = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert _is_session_fresh({"last_turn_at": recent}, idle_secs=60) is True
