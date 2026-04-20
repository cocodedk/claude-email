"""Tests for wake_spawn argv builder + subprocess runner."""
import pytest

from src.wake_spawn import WakeTurnResult, build_wake_cmd, run_wake_turn


def test_build_wake_cmd_first_session():
    cmd = build_wake_cmd("claude", "uuid-1", is_resume=False, prompt="drain")
    assert cmd == ["claude", "--print", "--session-id", "uuid-1", "drain"]


def test_build_wake_cmd_resume():
    cmd = build_wake_cmd("claude", "uuid-1", is_resume=True, prompt="drain")
    assert cmd == ["claude", "--print", "--resume", "uuid-1", "drain"]


def test_build_wake_cmd_custom_binary():
    cmd = build_wake_cmd("/opt/bin/claude", "uuid-9", is_resume=False, prompt="x")
    assert cmd[0] == "/opt/bin/claude"


@pytest.mark.asyncio
async def test_run_wake_turn_success(tmp_path):
    cmd = ["python3", "-c", "import sys; sys.exit(0)"]
    result = await run_wake_turn(cmd, cwd=str(tmp_path), timeout=5)
    assert isinstance(result, WakeTurnResult)
    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.error is None


@pytest.mark.asyncio
async def test_run_wake_turn_nonzero(tmp_path):
    cmd = ["python3", "-c", "import sys; sys.exit(2)"]
    result = await run_wake_turn(cmd, cwd=str(tmp_path), timeout=5)
    assert result.exit_code == 2
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_run_wake_turn_timeout(tmp_path):
    cmd = ["python3", "-c", "import time; time.sleep(10)"]
    result = await run_wake_turn(cmd, cwd=str(tmp_path), timeout=0.3)
    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_run_wake_turn_binary_missing(tmp_path):
    cmd = ["/nonexistent/binary", "arg"]
    result = await run_wake_turn(cmd, cwd=str(tmp_path), timeout=5)
    assert result.exit_code == -1
    assert result.error is not None
    assert result.timed_out is False
