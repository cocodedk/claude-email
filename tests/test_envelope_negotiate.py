"""Envelope version negotiation contract."""
from src.json_envelope import V, negotiate_v


def test_server_ceiling_is_v2():
    assert V == 2


def test_negotiate_caps_to_server():
    assert negotiate_v(3) == 2
    assert negotiate_v(2) == 2


def test_negotiate_honors_legacy_client():
    assert negotiate_v(1) == 1


def test_negotiate_floor_at_1():
    assert negotiate_v(0) == 1
    assert negotiate_v(-5) == 1
