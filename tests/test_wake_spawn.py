"""Tests for wake_spawn argv builder + subprocess runner."""
from src.wake_spawn import build_wake_cmd


def test_build_wake_cmd_first_session():
    cmd = build_wake_cmd("claude", "uuid-1", is_resume=False, prompt="drain")
    assert cmd == ["claude", "--print", "--session-id", "uuid-1", "drain"]


def test_build_wake_cmd_resume():
    cmd = build_wake_cmd("claude", "uuid-1", is_resume=True, prompt="drain")
    assert cmd == ["claude", "--print", "--resume", "uuid-1", "drain"]


def test_build_wake_cmd_custom_binary():
    cmd = build_wake_cmd("/opt/bin/claude", "uuid-9", is_resume=False, prompt="x")
    assert cmd[0] == "/opt/bin/claude"
