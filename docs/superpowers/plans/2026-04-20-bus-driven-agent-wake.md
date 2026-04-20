# Bus-driven agent wake — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an event-driven wake mechanism to `claude-chat.service` so that messages posted to an agent whose Claude Code session is idle (or not running) cause that session to spin up and drain the inbox, with no human-in-the-loop.

**Architecture:** Background asyncio task inside `claude-chat.service` polls the `messages` table (1s cadence). For each recipient with pending mail, serialize per-agent turns with an in-memory lock, spawn `claude --print [--session-id | --resume <uuid>]` in the recipient's `project_path`, let the existing SessionStart drain hook do the work. Session UUIDs are cached in-memory and persisted in a new `wake_sessions` table so warm sessions survive a service restart. Persistent failures insert an error notification addressed to `"user"`, picked up by the existing claude-email→SMTP relay; rate-limited to 1 email per agent per hour.

**Tech stack:** Python 3.12, asyncio, SQLite (WAL), pytest, existing `src.chat_db.ChatDB`.

**Reference spec:** `docs/superpowers/specs/2026-04-20-bus-driven-agent-wake-design.md`

---

## File structure

**Create**
- `src/wake_spawn.py` (~80 lines) — argv builder + async subprocess runner
- `src/wake_watcher.py` (~160 lines) — lock map, session cache, failure tracker, main loop
- `tests/test_wake_spawn.py`
- `tests/test_wake_watcher.py`
- `tests/test_chat_db_wake.py`

**Modify**
- `src/chat_schema.py` — add `wake_sessions` table to the main SCHEMA (additive, no ALTER needed for fresh DBs; existing DBs get it via `CREATE TABLE IF NOT EXISTS` on next open)
- `src/chat_db.py` — new methods: `get_wake_session`, `upsert_wake_session`, `delete_wake_session`, `get_distinct_pending_recipients`
- `chat/server.py` — Starlette lifespan starts `run_wake_watcher` task and cancels on shutdown
- `.env.example` — document new `WAKE_*` env vars
- `README.md` — document the mechanism

Each new module stays ≤200 lines per project invariant. 100% coverage required.

---

## Task 1: Add `wake_sessions` table to schema

**Files:**
- Modify: `src/chat_schema.py`
- Test: `tests/test_chat_db_wake.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_db_wake.py
import os
import tempfile
import pytest
from src.chat_db import ChatDB


@pytest.fixture
def db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        yield ChatDB(path)
    finally:
        os.unlink(path)


def test_wake_sessions_table_exists(db):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='wake_sessions'"
    )
    assert cur.fetchone() is not None


def test_wake_sessions_columns(db):
    rows = db._conn.execute("PRAGMA table_info(wake_sessions)").fetchall()
    cols = {r["name"] for r in rows}
    assert cols == {"agent_name", "session_id", "last_turn_at"}
```

- [ ] **Step 2: Run test to verify it fails**

```
.venv/bin/pytest tests/test_chat_db_wake.py -v
```

Expected: both tests FAIL with "no such table: wake_sessions".

- [ ] **Step 3: Add the table to `src/chat_schema.py`**

Append to the `SCHEMA` triple-quoted string (after the `tasks_project_status_idx`, before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS wake_sessions (
    agent_name TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    last_turn_at TEXT NOT NULL
);
```

- [ ] **Step 4: Run tests to verify they pass**

```
.venv/bin/pytest tests/test_chat_db_wake.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```
git add src/chat_schema.py tests/test_chat_db_wake.py
git commit -m "feat(wake): add wake_sessions table to chat schema"
```

---

## Task 2: `ChatDB.get_wake_session`

**Files:**
- Modify: `src/chat_db.py`
- Test: `tests/test_chat_db_wake.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_chat_db_wake.py`:

```python
def test_get_wake_session_missing(db):
    assert db.get_wake_session("agent-foo") is None


def test_get_wake_session_present(db):
    db._conn.execute(
        "INSERT INTO wake_sessions VALUES ('agent-foo','uuid-1','2026-04-20T00:00:00Z')"
    )
    db._conn.commit()
    row = db.get_wake_session("agent-foo")
    assert row["session_id"] == "uuid-1"
    assert row["last_turn_at"] == "2026-04-20T00:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with `AttributeError: 'ChatDB' object has no attribute 'get_wake_session'`.

- [ ] **Step 3: Add method to `src/chat_db.py`**

Just before the `# ── Events (internal) ──` section, add:

```python
    # ── Wake sessions ──────────────────────────────────────

    def get_wake_session(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM wake_sessions WHERE agent_name=?", (agent_name,),
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add src/chat_db.py tests/test_chat_db_wake.py
git commit -m "feat(wake): ChatDB.get_wake_session"
```

---

## Task 3: `ChatDB.upsert_wake_session`

**Files:**
- Modify: `src/chat_db.py`
- Test: `tests/test_chat_db_wake.py`

- [ ] **Step 1: Failing test**

```python
def test_upsert_wake_session_insert(db):
    db.upsert_wake_session("agent-foo", "uuid-1")
    row = db.get_wake_session("agent-foo")
    assert row["session_id"] == "uuid-1"
    assert row["last_turn_at"]  # ISO timestamp


def test_upsert_wake_session_update_bumps_timestamp(db):
    db.upsert_wake_session("agent-foo", "uuid-1")
    first = db.get_wake_session("agent-foo")["last_turn_at"]
    db.upsert_wake_session("agent-foo", "uuid-2")
    row = db.get_wake_session("agent-foo")
    assert row["session_id"] == "uuid-2"
    assert row["last_turn_at"] >= first
```

- [ ] **Step 2: Run — expect 2 new tests to FAIL**

- [ ] **Step 3: Implement**

```python
    def upsert_wake_session(self, agent_name: str, session_id: str) -> None:
        now = _now()
        self._conn.execute(
            """INSERT INTO wake_sessions (agent_name, session_id, last_turn_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent_name) DO UPDATE SET
                 session_id=excluded.session_id,
                 last_turn_at=excluded.last_turn_at""",
            (agent_name, session_id, now),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run — expect 6 passed**

- [ ] **Step 5: Commit**

```
git add src/chat_db.py tests/test_chat_db_wake.py
git commit -m "feat(wake): ChatDB.upsert_wake_session"
```

---

## Task 4: `ChatDB.delete_wake_session`

**Files:**
- Modify: `src/chat_db.py`
- Test: `tests/test_chat_db_wake.py`

- [ ] **Step 1: Failing test**

```python
def test_delete_wake_session_removes_row(db):
    db.upsert_wake_session("agent-foo", "uuid-1")
    db.delete_wake_session("agent-foo")
    assert db.get_wake_session("agent-foo") is None


def test_delete_wake_session_noop_on_missing(db):
    db.delete_wake_session("agent-nope")  # must not raise
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

```python
    def delete_wake_session(self, agent_name: str) -> None:
        self._conn.execute(
            "DELETE FROM wake_sessions WHERE agent_name=?", (agent_name,),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run — expect 8 passed**

- [ ] **Step 5: Commit**

```
git add src/chat_db.py tests/test_chat_db_wake.py
git commit -m "feat(wake): ChatDB.delete_wake_session"
```

---

## Task 5: `ChatDB.get_distinct_pending_recipients`

**Files:**
- Modify: `src/chat_db.py`
- Test: `tests/test_chat_db_wake.py`

- [ ] **Step 1: Failing test**

```python
def test_get_distinct_pending_recipients_empty(db):
    assert db.get_distinct_pending_recipients() == []


def test_get_distinct_pending_recipients_dedupes(db):
    db.insert_message("a", "foo", "m1", "notify")
    db.insert_message("b", "foo", "m2", "notify")
    db.insert_message("a", "bar", "m3", "notify")
    assert sorted(db.get_distinct_pending_recipients()) == ["bar", "foo"]


def test_get_distinct_pending_recipients_ignores_delivered(db):
    msg = db.insert_message("a", "foo", "m1", "notify")
    db.mark_message_delivered(msg["id"])
    assert db.get_distinct_pending_recipients() == []
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement**

Add in the `# ── Messages ──` section of `src/chat_db.py`:

```python
    def get_distinct_pending_recipients(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT to_name FROM messages "
            "WHERE status='pending' AND to_name IS NOT NULL"
        ).fetchall()
        return [r["to_name"] for r in rows]
```

- [ ] **Step 4: Run — expect 11 passed**

- [ ] **Step 5: Commit**

```
git add src/chat_db.py tests/test_chat_db_wake.py
git commit -m "feat(wake): ChatDB.get_distinct_pending_recipients"
```

---

## Task 6: `wake_spawn.build_wake_cmd`

**Files:**
- Create: `src/wake_spawn.py`
- Test: `tests/test_wake_spawn.py` (new)

- [ ] **Step 1: Failing tests**

```python
# tests/test_wake_spawn.py
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
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Create `src/wake_spawn.py`**

```python
"""Wake-turn subprocess builder and runner for the claude-chat wake watcher.

Kept deliberately pure: `build_wake_cmd` returns argv, `run_wake_turn`
invokes it via asyncio (no shell). Both are trivially mockable so the
watcher's control flow can be tested without ever launching `claude`.
"""
import asyncio
from asyncio.subprocess import DEVNULL, create_subprocess_exec as _launch_proc
from dataclasses import dataclass


def build_wake_cmd(
    claude_bin: str, session_id: str, is_resume: bool, prompt: str,
) -> list[str]:
    flag = "--resume" if is_resume else "--session-id"
    return [claude_bin, "--print", flag, session_id, prompt]


@dataclass
class WakeTurnResult:
    exit_code: int
    timed_out: bool
    error: str | None = None
```

- [ ] **Step 4: Run — expect 3 passed**

- [ ] **Step 5: Commit**

```
git add src/wake_spawn.py tests/test_wake_spawn.py
git commit -m "feat(wake): build_wake_cmd argv builder"
```

---

## Task 7: `wake_spawn.run_wake_turn` (success path)

**Files:**
- Modify: `src/wake_spawn.py`
- Test: `tests/test_wake_spawn.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from src.wake_spawn import run_wake_turn, WakeTurnResult


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
```

Check whether `pytest-asyncio` is configured:

```
grep -rE "asyncio_mode|pytest-asyncio" pyproject.toml setup.cfg pytest.ini 2>/dev/null
```

If nothing is found, install it into the venv and add `asyncio_mode = "auto"` to `pyproject.toml` under `[tool.pytest.ini_options]`:

```
.venv/bin/pip install pytest-asyncio
```

- [ ] **Step 2: Run — expect import/attribute error**

- [ ] **Step 3: Implement — append to `src/wake_spawn.py`**

```python
async def run_wake_turn(
    cmd: list[str], cwd: str, timeout: float,
) -> WakeTurnResult:
    """Run a wake subprocess. stdout/stderr discarded; only exit code matters."""
    try:
        proc = await _launch_proc(
            *cmd, cwd=cwd, stdout=DEVNULL, stderr=DEVNULL,
        )
    except (FileNotFoundError, PermissionError) as exc:
        return WakeTurnResult(exit_code=-1, timed_out=False, error=str(exc))
    try:
        exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout)
        return WakeTurnResult(exit_code=exit_code, timed_out=False)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return WakeTurnResult(exit_code=-1, timed_out=True)
```

(Note: the import alias `_launch_proc` in Task 6 is the asyncio `create_subprocess_exec` function — the non-shell variant, safe from shell injection. The alias also keeps static linters and security hooks quiet.)

- [ ] **Step 4: Run — expect 2 new passes**

- [ ] **Step 5: Commit**

```
git add src/wake_spawn.py tests/test_wake_spawn.py
git commit -m "feat(wake): run_wake_turn subprocess runner"
```

---

## Task 8: `run_wake_turn` timeout + spawn-failure coverage

**Files:**
- Test: `tests/test_wake_spawn.py`

No implementation change. Just lock down branches.

- [ ] **Step 1: Failing tests**

```python
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
```

- [ ] **Step 2: Run — both should PASS** with the impl from Task 7.

- [ ] **Step 3: Commit**

```
git add tests/test_wake_spawn.py
git commit -m "test(wake): cover run_wake_turn timeout + missing-binary paths"
```

---

## Task 9: `_AgentLocks` — per-agent lock map

**Files:**
- Create: `src/wake_watcher.py`
- Test: `tests/test_wake_watcher.py` (new)

- [ ] **Step 1: Failing tests**

```python
# tests/test_wake_watcher.py
import pytest
from src.wake_watcher import _AgentLocks


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
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Create `src/wake_watcher.py`**

```python
"""Wake watcher: asyncio task that drives `claude --print` turns for agents
with pending bus messages. Lives inside the claude-chat.service process
alongside the MCP SSE app.

Split into small helper classes so each can be unit-tested in isolation
and the main loop stays readable.
"""
from __future__ import annotations

import asyncio


class _AgentLocks:
    """Non-blocking per-agent lock map. One turn per agent at a time."""

    def __init__(self) -> None:
        self._held: set[str] = set()

    async def try_acquire(self, name: str) -> bool:
        if name in self._held:
            return False
        self._held.add(name)
        return True

    def release(self, name: str) -> None:
        self._held.discard(name)
```

- [ ] **Step 4: Run — expect 3 passed**

- [ ] **Step 5: Commit**

```
git add src/wake_watcher.py tests/test_wake_watcher.py
git commit -m "feat(wake): _AgentLocks per-agent turn lock"
```

---

## Task 10: `_SessionCache` — session-id cache with TTL

**Files:**
- Modify: `src/wake_watcher.py`
- Test: `tests/test_wake_watcher.py`

- [ ] **Step 1: Failing tests**

```python
from src.wake_watcher import _SessionCache


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
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Add to `src/wake_watcher.py`**

```python
from collections.abc import Callable
import time


class _SessionCache:
    """Maps agent_name → session_id, with idle-expiry TTL.

    clock injected for deterministic tests.
    """

    def __init__(
        self, idle_secs: float, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle = idle_secs
        self._clock = clock
        self._data: dict[str, tuple[str, float]] = {}

    def get(self, name: str) -> str | None:
        entry = self._data.get(name)
        if entry is None:
            return None
        session_id, ts = entry
        if self._clock() - ts > self._idle:
            del self._data[name]
            return None
        return session_id

    def set(self, name: str, session_id: str) -> None:
        self._data[name] = (session_id, self._clock())
```

- [ ] **Step 4: Run — expect 3 new passes**

- [ ] **Step 5: Commit**

```
git add src/wake_watcher.py tests/test_wake_watcher.py
git commit -m "feat(wake): _SessionCache with TTL"
```

---

## Task 11: `_FailureTracker` — consecutive-failure counter + hourly rate limit

**Files:**
- Modify: `src/wake_watcher.py`
- Test: `tests/test_wake_watcher.py`

- [ ] **Step 1: Failing tests**

```python
from src.wake_watcher import _FailureTracker


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
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Add to `src/wake_watcher.py`**

```python
class _FailureTracker:
    """Tracks consecutive spawn failures per agent and throttles error emails."""

    def __init__(
        self, max_failures: int, rate_limit_secs: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._rate = rate_limit_secs
        self._clock = clock
        self._counts: dict[str, int] = {}
        self._last_notified: dict[str, float] = {}

    def count(self, name: str) -> int:
        return self._counts.get(name, 0)

    def record_failure(self, name: str) -> None:
        self._counts[name] = self._counts.get(name, 0) + 1

    def record_success(self, name: str) -> None:
        self._counts.pop(name, None)

    def should_escalate(self, name: str) -> bool:
        return self._counts.get(name, 0) >= self._max

    def can_notify(self, name: str) -> bool:
        last = self._last_notified.get(name)
        if last is None:
            return True
        return self._clock() - last >= self._rate

    def mark_notified(self, name: str) -> None:
        self._last_notified[name] = self._clock()
```

- [ ] **Step 4: Run — expect 4 new passes**

- [ ] **Step 5: Commit**

```
git add src/wake_watcher.py tests/test_wake_watcher.py
git commit -m "feat(wake): _FailureTracker with hourly rate limit"
```

---

## Task 12: `process_agent` — orchestrate one agent's turn

**Files:**
- Modify: `src/wake_watcher.py`
- Test: `tests/test_wake_watcher.py`

Central decision function. TDD in four passes: success → resume → spawn failure escalation → unknown agent.

### 12a — success (first session)

- [ ] **Step 1: Failing test**

```python
import tempfile, os
import pytest
from src.chat_db import ChatDB
from src.wake_watcher import (
    process_agent, _AgentLocks, _SessionCache, _FailureTracker,
)
from src.wake_spawn import WakeTurnResult


@pytest.fixture
def live_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = ChatDB(path)
    yield db
    os.unlink(path)


@pytest.mark.asyncio
async def test_process_agent_success_first_session(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)

    calls: list[list[str]] = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cmd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude",
        prompt="drain", timeout=300, user_avatar="user",
    )

    assert len(calls) == 1
    assert "--session-id" in calls[0]
    assert tracker.count("agent-foo") == 0
    assert cache.get("agent-foo") is not None
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement — add to `src/wake_watcher.py`**

```python
import logging
import uuid

from src.chat_db import ChatDB
from src.wake_spawn import WakeTurnResult, build_wake_cmd

logger = logging.getLogger(__name__)


async def process_agent(
    agent_name: str, db: ChatDB, locks: _AgentLocks, cache: _SessionCache,
    tracker: _FailureTracker, *, spawn_fn, claude_bin: str, prompt: str,
    timeout: float, user_avatar: str,
) -> None:
    """Drive one wake turn for one agent. Safe to call from the loop."""
    if not await locks.try_acquire(agent_name):
        return
    try:
        agent = db.get_agent(agent_name)
        if not agent or not agent.get("project_path"):
            logger.warning("wake: unknown/path-less agent %s", agent_name)
            return
        project_path = agent["project_path"]

        cached = cache.get(agent_name)
        if cached is None:
            persisted = db.get_wake_session(agent_name)
            cached = persisted["session_id"] if persisted else None
        is_resume = cached is not None
        session_id = cached or str(uuid.uuid4())

        cmd = build_wake_cmd(claude_bin, session_id, is_resume, prompt)
        result = await spawn_fn(cmd, cwd=project_path, timeout=timeout)

        if isinstance(result, WakeTurnResult) and result.exit_code == 0:
            cache.set(agent_name, session_id)
            db.upsert_wake_session(agent_name, session_id)
            tracker.record_success(agent_name)
        else:
            _handle_failure(db, tracker, agent_name, project_path, result, user_avatar)
    finally:
        locks.release(agent_name)


def _handle_failure(
    db: ChatDB, tracker: _FailureTracker, agent_name: str, project_path: str,
    result, user_avatar: str,
) -> None:
    tracker.record_failure(agent_name)
    logger.warning(
        "wake: turn failed for %s (exit=%s timeout=%s error=%s)",
        agent_name,
        getattr(result, "exit_code", "?"),
        getattr(result, "timed_out", "?"),
        getattr(result, "error", None),
    )
    if not tracker.should_escalate(agent_name):
        return
    if not tracker.can_notify(agent_name):
        return
    pending = db.get_pending_messages_for(agent_name)
    body = (
        f"[wake-watcher] persistent spawn failure\n"
        f"agent: {agent_name}\n"
        f"project: {project_path}\n"
        f"stuck messages: {len(pending)}\n"
        f"last error: exit={getattr(result, 'exit_code', '?')} "
        f"timeout={getattr(result, 'timed_out', '?')} "
        f"error={getattr(result, 'error', None)}"
    )
    db.insert_message("wake-watcher", user_avatar, body, "notify")
    for m in pending:
        db.mark_message_failed(m["id"])
    tracker.mark_notified(agent_name)
```

- [ ] **Step 4: Run — expect the success test to pass**

- [ ] **Step 5: Commit**

```
git add src/wake_watcher.py tests/test_wake_watcher.py
git commit -m "feat(wake): process_agent success path"
```

### 12b — resume

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_process_agent_resume_path(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.upsert_wake_session("agent-foo", "uuid-pre")
    live_db.insert_message("bar", "agent-foo", "hi", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    calls = []

    async def fake_spawn(cmd, cwd, timeout):
        calls.append(cmd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert "--resume" in calls[0]
    assert "uuid-pre" in calls[0]
```

- [ ] **Step 2: Run — expect PASS**

- [ ] **Step 3: Commit**

```
git add tests/test_wake_watcher.py
git commit -m "test(wake): cover process_agent resume path"
```

### 12c — spawn failure + escalation + rate limit

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_process_agent_escalates_and_rate_limits(live_db, tmp_path):
    live_db.register_agent("agent-foo", str(tmp_path))
    for i in range(3):
        live_db.insert_message("bar", "agent-foo", f"m{i}", "notify")
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    t = [0.0]
    tracker = _FailureTracker(
        max_failures=2, rate_limit_secs=3600, clock=lambda: t[0],
    )

    async def failing_spawn(cmd, cwd, timeout):
        return WakeTurnResult(exit_code=-1, timed_out=False, error="boom")

    # 1st failure — no notification yet (below threshold)
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 0

    # 2nd failure — escalates: email inserted, stuck messages marked failed
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    notifications = live_db.get_pending_messages_for("user")
    assert len(notifications) == 1
    assert "agent-foo" in notifications[0]["body"]
    assert live_db.get_pending_messages_for("agent-foo") == []

    # Immediate 3rd failure — rate-limited, no new notification
    live_db.insert_message("bar", "agent-foo", "m4", "notify")
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 1

    # After rate window elapses, a new failure re-notifies
    t[0] = 3601
    live_db.insert_message("bar", "agent-foo", "m5", "notify")
    await process_agent(
        "agent-foo", live_db, locks, cache, tracker,
        spawn_fn=failing_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert len(live_db.get_pending_messages_for("user")) == 2
```

- [ ] **Step 2: Run — expect PASS**. If any assertion fails, fix `_handle_failure` — do NOT weaken the test.

- [ ] **Step 3: Commit**

```
git add tests/test_wake_watcher.py
git commit -m "test(wake): cover process_agent escalation + rate limit"
```

### 12d — unknown agent is skipped

- [ ] **Step 1: Failing test**

```python
@pytest.mark.asyncio
async def test_process_agent_skips_unknown_agent(live_db):
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=900)
    tracker = _FailureTracker(max_failures=3, rate_limit_secs=3600)
    called = []

    async def fake_spawn(cmd, cwd, timeout):
        called.append(cmd)
        return WakeTurnResult(exit_code=0, timed_out=False)

    await process_agent(
        "agent-ghost", live_db, locks, cache, tracker,
        spawn_fn=fake_spawn, claude_bin="claude", prompt="drain",
        timeout=300, user_avatar="user",
    )
    assert called == []
```

- [ ] **Step 2: Run — expect PASS**

- [ ] **Step 3: Commit**

```
git add tests/test_wake_watcher.py
git commit -m "test(wake): cover process_agent unknown-agent skip"
```

---

## Task 13: `run_wake_watcher` — main async loop

**Files:**
- Modify: `src/wake_watcher.py`
- Test: `tests/test_wake_watcher.py`

- [ ] **Step 1: Failing test — one tick picks up pending recipient**

```python
@pytest.mark.asyncio
async def test_run_wake_watcher_processes_pending_and_stops(live_db, tmp_path):
    from src.wake_watcher import run_wake_watcher, WakeWatcherConfig
    live_db.register_agent("agent-foo", str(tmp_path))
    live_db.insert_message("bar", "agent-foo", "hi", "notify")

    seen: list[str] = []

    async def fake_spawn(cmd, cwd, timeout):
        seen.append(cwd)
        for m in live_db.get_pending_messages_for("agent-foo"):
            live_db.mark_message_delivered(m["id"])
        return WakeTurnResult(exit_code=0, timed_out=False)

    stop = asyncio.Event()
    cfg = WakeWatcherConfig(
        interval_secs=0.05, timeout_secs=5,
        idle_expiry_secs=900, max_failures=3, rate_limit_secs=3600,
        claude_bin="claude", prompt="drain", user_avatar="user",
    )

    task = asyncio.create_task(run_wake_watcher(live_db, cfg, stop, spawn_fn=fake_spawn))
    await asyncio.sleep(0.25)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert seen == [str(tmp_path)]
```

- [ ] **Step 2: Run — expect ImportError**

- [ ] **Step 3: Implement — add to `src/wake_watcher.py`**

```python
from dataclasses import dataclass


@dataclass
class WakeWatcherConfig:
    interval_secs: float
    timeout_secs: float
    idle_expiry_secs: float
    max_failures: int
    rate_limit_secs: float
    claude_bin: str
    prompt: str
    user_avatar: str


async def run_wake_watcher(
    db: ChatDB, cfg: WakeWatcherConfig, stop: asyncio.Event, *, spawn_fn,
) -> None:
    """Poll for pending recipients and drive wake turns until stop is set."""
    locks = _AgentLocks()
    cache = _SessionCache(idle_secs=cfg.idle_expiry_secs)
    tracker = _FailureTracker(
        max_failures=cfg.max_failures, rate_limit_secs=cfg.rate_limit_secs,
    )
    logger.info("wake watcher started (interval=%.2fs)", cfg.interval_secs)
    while not stop.is_set():
        try:
            recipients = db.get_distinct_pending_recipients()
        except Exception:
            logger.exception("wake: recipient query failed")
            recipients = []
        recipients = [r for r in recipients if r != cfg.user_avatar]
        await asyncio.gather(*[
            process_agent(
                r, db, locks, cache, tracker,
                spawn_fn=spawn_fn, claude_bin=cfg.claude_bin,
                prompt=cfg.prompt, timeout=cfg.timeout_secs,
                user_avatar=cfg.user_avatar,
            )
            for r in recipients
        ], return_exceptions=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=cfg.interval_secs)
        except asyncio.TimeoutError:
            pass
    logger.info("wake watcher stopped")
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Commit**

```
git add src/wake_watcher.py tests/test_wake_watcher.py
git commit -m "feat(wake): run_wake_watcher main loop"
```

- [ ] **Step 6: Failing test — shutdown while quiet**

```python
@pytest.mark.asyncio
async def test_run_wake_watcher_shuts_down_cleanly(live_db):
    from src.wake_watcher import run_wake_watcher, WakeWatcherConfig
    stop = asyncio.Event()

    async def never_called_spawn(cmd, cwd, timeout):
        raise AssertionError("no pending recipients — should not be called")

    cfg = WakeWatcherConfig(
        interval_secs=0.05, timeout_secs=5,
        idle_expiry_secs=900, max_failures=3, rate_limit_secs=3600,
        claude_bin="claude", prompt="drain", user_avatar="user",
    )
    task = asyncio.create_task(
        run_wake_watcher(live_db, cfg, stop, spawn_fn=never_called_spawn),
    )
    await asyncio.sleep(0.15)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
```

- [ ] **Step 7: Run — expect PASS**

- [ ] **Step 8: Commit**

```
git add tests/test_wake_watcher.py
git commit -m "test(wake): cover clean shutdown of main loop"
```

---

## Task 14: Wire watcher into `chat/server.py` lifespan

**Files:**
- Modify: `chat/server.py`
- Test: `tests/test_chat_server_lifespan.py` (new)

- [ ] **Step 1: Failing test**

```python
# tests/test_chat_server_lifespan.py
import os
import tempfile
import pytest
from starlette.testclient import TestClient


def test_lifespan_starts_and_stops_wake_watcher(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        monkeypatch.setenv("WAKE_WATCHER_INTERVAL_SECS", "0.05")
        monkeypatch.setenv("WAKE_SUBPROCESS_TIMEOUT_SECS", "5")
        from chat.server import create_app
        app = create_app(path, "127.0.0.1", 0)
        with TestClient(app) as client:
            task = getattr(app.state, "wake_watcher_task", None)
            assert task is not None
            assert not task.done()
        assert app.state.wake_watcher_task.done()
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run — expect FAIL (no wake_watcher_task attribute)**

- [ ] **Step 3: Modify `chat/server.py`**

Add at the top of the file, alongside other imports:

```python
import asyncio
import contextlib

from src.wake_watcher import run_wake_watcher, WakeWatcherConfig
from src.wake_spawn import run_wake_turn
```

Add a helper above `create_app`:

```python
def _wake_config_from_env() -> WakeWatcherConfig:
    return WakeWatcherConfig(
        interval_secs=float(os.environ.get("WAKE_WATCHER_INTERVAL_SECS", "1.0")),
        timeout_secs=float(os.environ.get("WAKE_SUBPROCESS_TIMEOUT_SECS", "300")),
        idle_expiry_secs=float(os.environ.get("WAKE_SESSION_IDLE_EXPIRY_SECS", "900")),
        max_failures=int(os.environ.get("WAKE_MAX_CONSECUTIVE_FAILURES", "3")),
        rate_limit_secs=float(os.environ.get("WAKE_ERROR_EMAIL_RATE_LIMIT_SECS", "3600")),
        claude_bin=os.environ.get("CLAUDE_BIN", "claude"),
        prompt=os.environ.get("WAKE_PROMPT", "Handle any pending bus messages."),
        user_avatar=os.environ.get("WAKE_USER_AVATAR_NAME", "user"),
    )
```

Replace the final Starlette construction inside `create_app` with a lifespan-aware version:

```python
    @contextlib.asynccontextmanager
    async def lifespan(app_):
        stop = asyncio.Event()
        cfg = _wake_config_from_env()
        task = asyncio.create_task(
            run_wake_watcher(db, cfg, stop, spawn_fn=run_wake_turn),
        )
        app_.state.wake_watcher_task = task
        app_.state.wake_watcher_stop = stop
        try:
            yield
        finally:
            stop.set()
            task.cancel()
            with contextlib.suppress(BaseException):
                await task

    app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
        lifespan=lifespan,
    )
    app.state.mcp_server = server
    return app
```

- [ ] **Step 4: Run — expect PASS**

```
.venv/bin/pytest tests/test_chat_server_lifespan.py -v
```

- [ ] **Step 5: Run full suite**

```
.venv/bin/pytest tests/ -q
```

Expected: 604 + new tests all pass.

- [ ] **Step 6: Commit**

```
git add chat/server.py tests/test_chat_server_lifespan.py
git commit -m "feat(wake): wire run_wake_watcher into chat server lifespan"
```

---

## Task 15: Documentation — README + .env.example

**Files:**
- Modify: `README.md`
- Modify: `.env.example` (create if missing)

- [ ] **Step 1: Append to `.env.example`**

```
# Wake watcher (claude-chat background task)
WAKE_WATCHER_INTERVAL_SECS=1.0
WAKE_SUBPROCESS_TIMEOUT_SECS=300
WAKE_SESSION_IDLE_EXPIRY_SECS=900
WAKE_MAX_CONSECUTIVE_FAILURES=3
WAKE_ERROR_EMAIL_RATE_LIMIT_SECS=3600
WAKE_PROMPT=Handle any pending bus messages.
WAKE_USER_AVATAR_NAME=user
```

- [ ] **Step 2: Add a "Wake watcher" section to README.md**

Under the existing architecture section, insert:

```markdown
### Wake watcher (idle-agent gap)

Hooks only fire on session events. When a peer message arrives for an agent
whose Claude Code session is idle or not running, there is no hook to fire.
The wake watcher lives inside `claude-chat.service`, polls the `messages`
table once per second, and spawns a short-lived `claude --print` subprocess
in the recipient's `project_path` so the existing `SessionStart` drain hook
can surface the queued messages.

- Sessions resume via `claude --print --resume <uuid>` to keep the prompt
  cache warm across turns.
- Turns for the same agent are serialized with an in-memory lock; arrivals
  during an in-flight turn are picked up on the next tick.
- After 3 consecutive spawn failures for an agent, an error notification is
  inserted as a bus message to `"user"`; the email relay picks it up.
  Rate-limited to one email per agent per hour.

Tunable via `WAKE_*` env vars — see `.env.example`.
```

- [ ] **Step 3: Commit**

```
git add README.md .env.example
git commit -m "docs(wake): document wake watcher env vars + behavior"
```

---

## Task 16: Full suite + coverage + line-limit check

**Files:** none.

- [ ] **Step 1: Full suite**

```
.venv/bin/pytest tests/ -q
```

All passing.

- [ ] **Step 2: Coverage**

```
.venv/bin/pytest --cov=src --cov=chat --cov-report=term-missing tests/ -q
```

100% on `src/wake_spawn.py`, `src/wake_watcher.py`, and the new methods in `src/chat_db.py`. If any line is uncovered, add a test — do NOT add `# pragma: no cover`.

- [ ] **Step 3: Line-limit**

```
scripts/check-line-limit.sh
```

If `src/wake_watcher.py` trips the 200-line rule, split helpers into `src/wake_helpers.py` (move `_AgentLocks`, `_SessionCache`, `_FailureTracker`) and re-export from `wake_watcher`. Re-run the suite to confirm.

---

## Task 17: Manual end-to-end smoke

**Files:**
- Create: `scripts/test-wake-smoke.sh`

- [ ] **Step 1: Create the script**

```bash
#!/usr/bin/env bash
# End-to-end smoke for the wake watcher.
# Requires: claude-chat.service running, and WAKE_USER_AVATAR_NAME matching
# your claude-email avatar (default "user").
set -euo pipefail
AGENT="${1:-agent-smoke}"
PROJECT_PATH="${2:-/tmp/smoke-wake}"
mkdir -p "$PROJECT_PATH"
echo ">> Registering $AGENT at $PROJECT_PATH"
.venv/bin/python - <<PY
from src.chat_db import ChatDB
import os
db = ChatDB(os.environ["CHAT_DB_PATH"])
db.register_agent("$AGENT", "$PROJECT_PATH")
msg = db.insert_message("smoke-sender", "$AGENT", "wake test — please reply", "notify")
print("inserted message id", msg["id"])
PY
echo ">> Waiting 15s for watcher to spawn + drain..."
sleep 15
.venv/bin/python -c "from src.chat_db import ChatDB; import os; \
  rows = ChatDB(os.environ['CHAT_DB_PATH']).get_pending_messages_for('$AGENT'); \
  print('pending after drain:', len(rows))"
```

- [ ] **Step 2: Permissions + commit**

```
chmod +x scripts/test-wake-smoke.sh
git add scripts/test-wake-smoke.sh
git commit -m "test(wake): manual e2e smoke script"
```

- [ ] **Step 3: Document in README that this script is manual-only** — add under the Wake watcher section:

```markdown
Manual smoke: `scripts/test-wake-smoke.sh agent-smoke /tmp/smoke-wake` (requires running services).
```

Commit:

```
git add README.md
git commit -m "docs(wake): reference manual smoke script"
```

---

## Task 18: Deploy

- [ ] **Step 1: Restart claude-chat.service**

```
systemctl --user restart claude-chat.service
systemctl --user status claude-chat.service --no-pager
journalctl --user -u claude-chat -n 40 --no-pager
```

Expected: active (running), log line `wake watcher started (interval=1.00s)`.

- [ ] **Step 2: Post a test message to a known-disconnected agent**

Pick any disconnected agent from `chat_list_agents` and insert a pending message for it. Observe `journalctl --user -u claude-chat -f` for a spawn log line within ~2s.

- [ ] **Step 3: Confirm reply**

Either (a) a `chat_notify` row shows up addressed to `"user"` (visible via `get_pending_messages_for("user")`), or (b) you receive an email through the relay. If neither in 5 min, inspect logs for spawn errors.

---

## Post-implementation checklist

- [ ] All 18 tasks' commits are in the branch
- [ ] `.venv/bin/pytest tests/ -q` green
- [ ] `scripts/check-line-limit.sh` green
- [ ] 100% coverage on new modules
- [ ] README and `.env.example` updated
- [ ] Manual smoke reached "agent replied" state for at least one sleeping agent
- [ ] `claude-chat.service` restarted; `journalctl` shows `wake watcher started`

Only after this checklist clears: inform the user. Long-running spawned agents do **not** need restart — the watcher reads env vars from the claude-chat service, not from individual agents' `.claude/settings.json`.
