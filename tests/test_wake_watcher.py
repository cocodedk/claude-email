"""Tests for wake_watcher helpers and main loop."""
import pytest

from src.wake_watcher import _AgentLocks, _FailureTracker, _SessionCache


@pytest.mark.asyncio
async def test_agent_locks_acquire_release():
    locks = _AgentLocks()
    assert await locks.try_acquire("agent-foo") is True
    locks.release("agent-foo")


@pytest.mark.asyncio
async def test_agent_locks_rejects_concurrent_same_agent():
    locks = _AgentLocks()
    assert await locks.try_acquire("agent-foo") is True
    assert await locks.try_acquire("agent-foo") is False
    locks.release("agent-foo")
    assert await locks.try_acquire("agent-foo") is True
    locks.release("agent-foo")


@pytest.mark.asyncio
async def test_agent_locks_independent_per_agent():
    locks = _AgentLocks()
    assert await locks.try_acquire("agent-a") is True
    assert await locks.try_acquire("agent-b") is True
    locks.release("agent-a")
    locks.release("agent-b")


def test_session_cache_miss():
    cache = _SessionCache(idle_secs=900, clock=lambda: 1000.0)
    assert cache.get("agent-foo") is None


def test_session_cache_hit_within_ttl():
    cache = _SessionCache(idle_secs=900, clock=lambda: 1000.0)
    cache.set("agent-foo", "uuid-1")
    assert cache.get("agent-foo") == "uuid-1"


def test_session_cache_expired_drops_entry():
    t = [1000.0]
    cache = _SessionCache(idle_secs=60, clock=lambda: t[0])
    cache.set("agent-foo", "uuid-1")
    t[0] = 1000.0 + 61
    assert cache.get("agent-foo") is None
    cache.set("agent-foo", "uuid-2")
    assert cache.get("agent-foo") == "uuid-2"


def test_failure_tracker_starts_at_zero():
    ft = _FailureTracker(max_failures=3, rate_limit_secs=3600, clock=lambda: 0.0)
    assert ft.count("agent-foo") == 0


def test_failure_tracker_increment_and_reset():
    ft = _FailureTracker(max_failures=3, rate_limit_secs=3600, clock=lambda: 0.0)
    ft.record_failure("agent-foo")
    ft.record_failure("agent-foo")
    assert ft.count("agent-foo") == 2
    ft.record_success("agent-foo")
    assert ft.count("agent-foo") == 0


def test_failure_tracker_should_escalate():
    ft = _FailureTracker(max_failures=3, rate_limit_secs=3600, clock=lambda: 0.0)
    assert ft.should_escalate("agent-foo") is False
    ft.record_failure("agent-foo")
    ft.record_failure("agent-foo")
    assert ft.should_escalate("agent-foo") is False
    ft.record_failure("agent-foo")
    assert ft.should_escalate("agent-foo") is True


def test_failure_tracker_rate_limits_notifications():
    t = [0.0]
    ft = _FailureTracker(max_failures=1, rate_limit_secs=60, clock=lambda: t[0])
    ft.record_failure("agent-foo")
    assert ft.can_notify("agent-foo") is True
    ft.mark_notified("agent-foo")
    assert ft.can_notify("agent-foo") is False
    t[0] = 61
    assert ft.can_notify("agent-foo") is True
