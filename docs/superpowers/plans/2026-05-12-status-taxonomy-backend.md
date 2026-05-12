# Status Taxonomy Backend (Envelope v: 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the backend side of the two-axis status taxonomy proposed by `agent-Claude-Email-App` in `Claude-Email-App/docs/superpowers/plans/2026-05-12-status-taxonomy-proposal.md`. Ship `task_state` (waiting | working | completed | error | null) and a new `agent_status` vocabulary (online | stale | offline) on the JSON envelope `list_projects` response, gated by envelope `v >= 2`.

**Architecture:**

- Bump module-level envelope version: `src/json_envelope.py:V = 2`.
- Server negotiates per-response: the response envelope's `v` is `min(client_v, server_V)`. Clients on `v: 1` keep the legacy `agent_status` vocabulary (`connected | disconnected | absent`) and never see `task_state`.
- Two new pure helpers, side-by-side with their legacy counterparts (no renames, no breaks):
  - `src/agent_registry.py::agent_state_for_project()` → `online | stale | offline`.
  - `src/task_state.py::task_state_for_project()` (new module) → `waiting | working | completed | error | None`.
- `chat/project_tools.py::list_projects_tool()` gains an `envelope_version: int = 1` param. Default keeps current callers' behavior intact; the JSON dispatcher passes the client's `v`.
- `src/json_kinds.py` `_handle_list_projects` reads `env.v` from the inbound envelope and threads it into `list_projects_tool`.
- New env var `TASK_STATE_FADE_SEC` (default `30`) controls how long terminal `completed` / `error` stays visible after `completed_at` before flipping to `null`.
- README + website footnotes updated where current status vocab is named.

**Tech Stack:** Python 3.12, SQLite, pytest, the existing JSON-envelope protocol. No new third-party deps.

**Out of scope:**
- Dashboard adoption of the new vocabulary (deferred — separate branch; dashboard keeps its existing 3-state filter for now).
- Animated transitions / fade UX on the app side (peer's responsibility).
- Per-agent multi-task visibility (peer's proposal explicitly defers it).
- Persisting derived `task_state` to the DB — it's pure derivation from the existing `tasks` + `agents` tables.

**Answers to the peer's three open questions** (canonical, also sent on the bus):

| Q | Answer |
|---|--------|
| Q1: thresholds | Heartbeat window = `DEFAULT_AGENT_FRESHNESS_SEC = 300` (5 min, existing). Ghost threshold = `DEFAULT_AGENT_STALE_SECS = 1800` (30 min, existing). pid set + `is_alive(pid)=True` → forces `online` regardless of timestamp. pid set + `is_alive(pid)=False` → `offline` immediately. |
| Q2: completed fade | 30 s default. Configurable via `TASK_STATE_FADE_SEC`. Applies to both `completed` and `error`. |
| Q3: error as 4th state | Yes. Enum becomes `waiting | working | completed | error | null`. Justification: errors deserve distinct UI treatment without the app having to parse `last_task_status` separately. `cancelled` tasks map to `completed` (terminal, no UI alarm). |

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/json_envelope.py` | Envelope protocol module | `V = 1` → `V = 2`. Add `negotiate_v(client_v: int) -> int` helper. |
| `src/agent_registry.py` | Agent presence | New `agent_state_for_project()` returning `online | stale | offline`. Legacy `agent_status_for_project()` unchanged. |
| `src/task_state.py` | NEW — task-state derivation | New module with `task_state_for_project(queue, path, fade_secs)` returning `waiting | working | completed | error | None`. Owns the env-var read. |
| `chat/project_tools.py` | `list_projects` tool body | Add `envelope_version` param; switch vocab + emit `task_state` when `>= 2`. |
| `src/json_kinds.py` | Kind dispatcher | `_handle_list_projects` threads `env.v` through to `list_projects_tool`. |
| `.env.example` | Config doc | Document `TASK_STATE_FADE_SEC`. |
| `README.md` | Project doc | Update the "Agent status tracking (running, idle, disconnected, deregistered)" bullet to mention the new two-axis model; cite envelope `v >= 2` gating. |
| `website/index.html`, `website/fa/index.html` | Marketing copy | No section adds; only update the one place that names old vocab, if any. Verify with grep. |
| `tests/test_agent_state.py` | NEW | Cover all 6 branches of `agent_state_for_project`. |
| `tests/test_task_state.py` | NEW | Cover all 5 branches + fade window of `task_state_for_project`. |
| `tests/test_envelope_negotiate.py` | NEW | Cover `negotiate_v` + that `V = 2` is the new ceiling. |
| `tests/test_list_projects_envelope_v2.py` | NEW | End-to-end shape of `list_projects_tool` for `envelope_version=1` (legacy) and `envelope_version=2` (new). |

200-line cap applies to all `src/`, `chat/` files I touch — none of the changes should push any file over. Verify with `scripts/check-line-limit.sh` at the end.

---

## Task 1: Bump envelope version + add `negotiate_v`

**Files:**
- Modify: `src/json_envelope.py`
- Create: `tests/test_envelope_negotiate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_envelope_negotiate.py`:

```python
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
    # Defensive: a missing / zero / negative v on the inbound envelope
    # gets pinned at the floor — the legacy clients we ship with were
    # always v: 1 since the protocol began.
    assert negotiate_v(0) == 1
    assert negotiate_v(-5) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_envelope_negotiate.py -v`
Expected: FAIL — `negotiate_v` doesn't exist, `V` is still `1`.

- [ ] **Step 3: Bump V and add `negotiate_v`**

Edit `src/json_envelope.py`. Find:

```python
V = 1
CONTENT_TYPE = "application/json"
```

Replace with:

```python
V = 2
CONTENT_TYPE = "application/json"


def negotiate_v(client_v: int) -> int:
    """Return the envelope version to use in the response.

    Caps at the server's ``V``; floors at 1 so a malformed or missing
    inbound ``v`` falls back to legacy shape rather than crashing or
    silently advertising support the server can't honor.
    """
    if client_v < 1:
        return 1
    return min(client_v, V)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_envelope_negotiate.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite to confirm nothing downstream broke**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS, count = current + 4.

- [ ] **Step 6: `/simplify` the working tree, then commit**

Per the durable rule, simplify before commit. Inline review (small diff): `negotiate_v` is straightforward; check the floor=1 branch isn't redundant with the cap branch.

```bash
git add src/json_envelope.py tests/test_envelope_negotiate.py
git commit -m "feat(envelope): bump V to 2 and add negotiate_v helper"
```

---

## Task 2: New `agent_state_for_project` helper

**Files:**
- Modify: `src/agent_registry.py` (add helper, keep legacy `agent_status_for_project` intact)
- Create: `tests/test_agent_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_state.py`:

```python
"""3-state liveness vocabulary for envelope v: 2 list_projects."""
from datetime import datetime, timedelta, timezone

import pytest

from src.agent_registry import (
    DEFAULT_AGENT_FRESHNESS_SEC,
    DEFAULT_AGENT_STALE_SECS,
)
from src.chat_db import ChatDB


def _ts(seconds_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "t.db"))


def _row(db, name, path, *, pid, status, seen):
    db._conn.execute(
        "INSERT INTO agents (name, project_path, pid, status, "
        "last_seen_at, registered_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, path, pid, status, seen, seen),
    )
    db._conn.commit()


def test_no_row_returns_offline(db):
    assert db.agent_state_for_project("/x") == "offline"


def test_pid_alive_forces_online_even_when_seen_old(db):
    import os
    _row(db, "a", "/x", pid=os.getpid(), status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))
    assert db.agent_state_for_project("/x") == "online"


def test_pid_dead_forces_offline(db):
    # PID 999999 should not exist (very high, well above kernel-allocated)
    _row(db, "a", "/x", pid=999999, status="running", seen=_ts(10))
    assert db.agent_state_for_project("/x") == "offline"


def test_no_pid_recent_seen_is_online(db):
    _row(db, "a", "/x", pid=None, status="running", seen=_ts(60))
    assert db.agent_state_for_project("/x") == "online"


def test_no_pid_mid_window_is_stale(db):
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_FRESHNESS_SEC + 60))
    assert db.agent_state_for_project("/x") == "stale"


def test_no_pid_beyond_ghost_is_offline(db):
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))
    assert db.agent_state_for_project("/x") == "offline"


def test_deregistered_status_is_offline(db):
    _row(db, "a", "/x", pid=None, status="deregistered", seen=_ts(10))
    assert db.agent_state_for_project("/x") == "offline"


def test_multiple_rows_best_wins(db):
    # If any row is online, the project is online.
    _row(db, "a", "/x", pid=None, status="running",
         seen=_ts(DEFAULT_AGENT_STALE_SECS + 60))  # would be offline alone
    _row(db, "b", "/x", pid=None, status="running",
         seen=_ts(30))                              # fresh → online
    assert db.agent_state_for_project("/x") == "online"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_agent_state.py -v`
Expected: FAIL — `agent_state_for_project` doesn't exist on `ChatDB`.

- [ ] **Step 3: Implement the helper**

Edit `src/agent_registry.py`. Add after `agent_status_for_project` (around line 164):

```python
    def agent_state_for_project(
        self, project_path: str,
        freshness_sec: int = DEFAULT_AGENT_FRESHNESS_SEC,
        stale_sec: int = DEFAULT_AGENT_STALE_SECS,
    ) -> str:
        """3-state liveness for envelope v: 2 list_projects responses.

        Returns ``online | stale | offline``. Per-row precedence:

        - pid set + is_alive(pid)=True  → online (overrides timestamp,
          covers PreCompact gaps and bus-restart pauses).
        - pid set + is_alive(pid)=False → offline (authoritative death).
        - pid NULL + last_seen_at within ``freshness_sec`` → online.
        - pid NULL + last_seen_at within ``stale_sec`` → stale.
        - otherwise (no rows, deregistered, beyond ghost) → offline.

        Best-of-rows: if any registered agent for this project is
        online, the project is online; else if any is stale, the
        project is stale; else offline.
        """
        from src.process_liveness import is_alive
        rows = self._conn.execute(
            "SELECT pid, status, last_seen_at FROM agents "
            "WHERE project_path=?",
            (project_path,),
        ).fetchall()
        if not rows:
            return "offline"
        fresh_cutoff = _cutoff(freshness_sec)
        stale_cutoff = _cutoff(stale_sec)
        best = "offline"
        for row in rows:
            verdict = _row_state(row, fresh_cutoff, stale_cutoff, is_alive)
            if verdict == "online":
                return "online"   # short-circuit; cannot beat online
            if verdict == "stale":
                best = "stale"
        return best
```

Then add the row-classifier as a module-level private helper (just above the `ChatDB` class so it can be unit-tested independently and stays under the 200-line cap):

```python
def _row_state(row, fresh_cutoff: str, stale_cutoff: str, is_alive) -> str:
    pid = row["pid"]
    status = row["status"]
    seen = row["last_seen_at"] or ""
    if status in ("deregistered", "disconnected"):
        return "offline"
    if pid is not None:
        return "online" if is_alive(pid) else "offline"
    if seen >= fresh_cutoff:
        return "online"
    if seen >= stale_cutoff:
        return "stale"
    return "offline"
```

`is_alive` is injected as a parameter (rather than imported at module top) to keep the import local and to allow tests to stub it later if needed without monkeypatching.

- [ ] **Step 4: Run agent-state tests**

Run: `.venv/bin/pytest tests/test_agent_state.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the full suite — legacy `agent_status_for_project` must be unaffected**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS, count = previous + 8. No regressions in existing agent_registry tests.

- [ ] **Step 6: Confirm `src/agent_registry.py` stays under 200 lines**

Run: `wc -l src/agent_registry.py`
Expected: under 200. If over (the row-classifier helper plus the new method might push it past), extract the new method + helper into `src/agent_state.py` and re-import. Specifically:

```bash
[ $(wc -l < src/agent_registry.py) -gt 200 ] && echo "split needed"
```

If split needed, create `src/agent_state.py` with the helper + `_row_state`, and in `agent_registry.py` add `from src.agent_state import agent_state_for_project as _agent_state_for_project_fn` and either expose as a method via a one-line wrapper, or just leave `ChatDB.agent_state_for_project` calling the module function. Re-run tests.

- [ ] **Step 7: `/simplify` the working tree, then commit**

Inline review: check that `_row_state` doesn't duplicate logic now in `get_agents_summary` (the dashboard's two-signal check). It does — both consult `pid`/`is_alive`/`last_seen_at`. Acceptable for now since the two callsites disagree on threshold (5min vs 30min); revisit only if a third callsite appears.

```bash
git add src/agent_registry.py tests/test_agent_state.py
git commit -m "feat(agent-registry): add agent_state_for_project (online|stale|offline)"
```

---

## Task 3: New `task_state_for_project` helper

**Files:**
- Create: `src/task_state.py`
- Create: `tests/test_task_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_task_state.py`:

```python
"""Task-state vocabulary for envelope v: 2 list_projects."""
from datetime import datetime, timedelta, timezone

import pytest

from src.task_queue import TaskQueue
from src.task_state import (
    DEFAULT_TASK_STATE_FADE_SEC,
    task_state_for_project,
)


def _ts(seconds_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    ).isoformat()


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(str(tmp_path / "t.db"))


def test_no_tasks_returns_none(queue):
    assert task_state_for_project(queue, "/x") is None


def test_running_task_is_working(queue):
    queue.enqueue("/x", "do thing", priority=0)
    queue.claim_next()
    assert task_state_for_project(queue, "/x") == "working"


def test_pending_only_is_waiting(queue):
    queue.enqueue("/x", "do thing", priority=0)
    assert task_state_for_project(queue, "/x") == "waiting"


def test_done_within_fade_is_completed(queue):
    queue.enqueue("/x", "do thing", priority=0)
    t = queue.claim_next()
    queue.mark_done(t["id"])
    assert task_state_for_project(queue, "/x") == "completed"


def test_done_beyond_fade_is_none(queue):
    # Use fade=0 so any non-zero age flips to None.
    queue.enqueue("/x", "do thing", priority=0)
    t = queue.claim_next()
    queue.mark_done(t["id"])
    assert task_state_for_project(queue, "/x", fade_secs=0) is None


def test_failed_within_fade_is_error(queue):
    queue.enqueue("/x", "do thing", priority=0)
    t = queue.claim_next()
    queue.mark_failed(t["id"], "boom")
    assert task_state_for_project(queue, "/x") == "error"


def test_cancelled_maps_to_completed(queue):
    queue.enqueue("/x", "do thing", priority=0)
    queue.cancel_pending("/x")
    assert task_state_for_project(queue, "/x") == "completed"


def test_running_beats_pending(queue):
    queue.enqueue("/x", "first", priority=0)
    queue.enqueue("/x", "second", priority=0)
    queue.claim_next()
    assert task_state_for_project(queue, "/x") == "working"


def test_default_fade_is_30_seconds():
    assert DEFAULT_TASK_STATE_FADE_SEC == 30
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_task_state.py -v`
Expected: FAIL — `src.task_state` module doesn't exist.

- [ ] **Step 3: Implement the module**

Create `src/task_state.py`:

```python
"""Task-state derivation for envelope v: 2 list_projects responses.

Maps the tasks table's storage vocabulary
(``pending | running | done | failed | cancelled``) to the wire
vocabulary the app and dashboard render:

  waiting | working | completed | error | None

Precedence inside a project:

  running task       → working
  pending tasks      → waiting
  latest terminal task within fade window
      status=done       → completed
      status=cancelled  → completed
      status=failed     → error
  no recent task     → None
"""
import os
from datetime import datetime, timedelta, timezone

DEFAULT_TASK_STATE_FADE_SEC = 30


def _fade_secs() -> int:
    raw = os.environ.get("TASK_STATE_FADE_SEC")
    if raw is None:
        return DEFAULT_TASK_STATE_FADE_SEC
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_TASK_STATE_FADE_SEC


def task_state_for_project(
    queue, project_path: str, *, fade_secs: int | None = None,
) -> str | None:
    """See module docstring. ``fade_secs=None`` reads
    ``TASK_STATE_FADE_SEC`` env var, defaulting to 30."""
    if queue.get_running(project_path):
        return "working"
    if queue.list_pending(project_path):
        return "waiting"
    latest = queue.latest_task(project_path)
    if not latest:
        return None
    if fade_secs is None:
        fade_secs = _fade_secs()
    completed_at = latest.get("completed_at")
    if not completed_at:
        return None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=fade_secs)
    ).isoformat()
    if completed_at < cutoff:
        return None
    status = latest.get("status")
    if status == "failed":
        return "error"
    if status in ("done", "cancelled"):
        return "completed"
    return None
```

- [ ] **Step 4: Run task-state tests**

Run: `.venv/bin/pytest tests/test_task_state.py -v`
Expected: PASS (9 tests). If `TaskQueue.latest_task` returns `None` keys without `completed_at`, adjust the test fixtures accordingly — verify by reading `src/task_queue.py::latest_task`.

- [ ] **Step 5: Confirm new file is under the line limit**

Run: `wc -l src/task_state.py`
Expected: well under 200 (this module is ~50 lines).

- [ ] **Step 6: Full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS, count = previous + 9.

- [ ] **Step 7: `/simplify`, then commit**

Inline review: `_fade_secs` env-var read pattern is a one-time cost per call, but `task_state_for_project` is called once per project per `list_projects` poll. Not a hot path. Accept.

```bash
git add src/task_state.py tests/test_task_state.py
git commit -m "feat(task-state): derive waiting|working|completed|error from tasks table"
```

---

## Task 4: Extend `list_projects_tool` with envelope-version gating

**Files:**
- Modify: `chat/project_tools.py`
- Create: `tests/test_list_projects_envelope_v2.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_list_projects_envelope_v2.py`:

```python
"""Wire shape contract for list_projects across envelope versions."""
import os

import pytest

from chat.project_tools import list_projects_tool
from src.chat_db import ChatDB
from src.task_queue import TaskQueue


@pytest.fixture
def tmp_setup(tmp_path, monkeypatch):
    base = tmp_path / "projects"
    base.mkdir()
    (base / "proj-a").mkdir()
    (base / "proj-a" / ".git").mkdir()
    queue = TaskQueue(str(tmp_path / "queue.db"))
    chat_db = ChatDB(str(tmp_path / "chat.db"))
    monkeypatch.delenv("TASK_STATE_FADE_SEC", raising=False)
    return base, queue, chat_db


def test_v1_returns_legacy_agent_status_vocab(tmp_setup):
    base, queue, chat_db = tmp_setup
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
        envelope_version=1,
    )
    row = out["projects"][0]
    assert row["agent_status"] in ("connected", "disconnected", "absent")
    assert "task_state" not in row


def test_v2_returns_new_vocab_and_task_state(tmp_setup):
    base, queue, chat_db = tmp_setup
    queue.enqueue(str(base / "proj-a"), "do x", priority=0)
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
        envelope_version=2,
    )
    row = out["projects"][0]
    assert row["agent_status"] in ("online", "stale", "offline")
    assert row["task_state"] == "waiting"


def test_v2_no_agent_no_task_is_offline_and_null(tmp_setup):
    base, queue, chat_db = tmp_setup
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
        envelope_version=2,
    )
    row = out["projects"][0]
    assert row["agent_status"] == "offline"
    assert row["task_state"] is None


def test_default_envelope_version_is_1(tmp_setup):
    """Existing callers that don't pass the new param keep legacy shape."""
    base, queue, chat_db = tmp_setup
    out = list_projects_tool(
        queue, allowed_base=str(base), chat_db=chat_db,
    )
    row = out["projects"][0]
    assert "task_state" not in row
    assert row["agent_status"] in ("connected", "disconnected", "absent")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_list_projects_envelope_v2.py -v`
Expected: FAIL — `envelope_version` param doesn't exist.

- [ ] **Step 3: Modify `list_projects_tool`**

Edit `chat/project_tools.py`. Find the current signature and body of `list_projects_tool` (around line 158). Replace with:

```python
def list_projects_tool(
    queue: TaskQueue, *, allowed_base: str, chat_db=None,
    envelope_version: int = 1,
) -> dict:
    """Discover git repos under ``allowed_base`` + merge with task state.

    Project = a top-level directory containing a ``.git/`` entry. Hidden
    directories and plain files are skipped. Sorted by name so the row
    order is stable across polls.

    When ``chat_db`` is provided, each row carries an ``agent_status``
    field reflecting bus presence:

    - ``envelope_version <= 1``: legacy 3-state
      (``connected | disconnected | absent``). No ``task_state`` field.
    - ``envelope_version >= 2``: new 3-state
      (``online | stale | offline``) plus a ``task_state`` field
      (``waiting | working | completed | error | null``).

    Older callers that don't pass ``chat_db`` get ``agent_status``
    as ``"absent"`` (v: 1) or ``"offline"`` (v: 2). The field is
    always present.
    """
    if not allowed_base:
        return {"projects": []}
    base = Path(allowed_base).resolve()
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return {"projects": []}
    use_v2 = envelope_version >= 2
    rows = []
    for entry in entries:
        if entry.startswith("."):
            continue
        path = base / entry
        if not path.is_dir() or not (path / ".git").exists():
            continue
        resolved = str(path)  # ``base`` is already resolve()d
        running = queue.get_running(resolved)
        row = {
            "name": entry,
            "path": resolved,
            "running_task_id": running["id"] if running else None,
            "queue_depth": len(queue.list_pending(resolved)),
            "last_activity_at": _last_activity(queue.latest_task(resolved)),
            "agent_status": _agent_status(chat_db, resolved, use_v2),
        }
        if use_v2:
            row["task_state"] = task_state_for_project(queue, resolved)
        rows.append(row)
    return {"projects": rows}


def _agent_status(chat_db, resolved: str, use_v2: bool) -> str:
    if chat_db is None:
        return "offline" if use_v2 else "absent"
    if use_v2:
        return chat_db.agent_state_for_project(resolved)
    return chat_db.agent_status_for_project(resolved)
```

Also add the new import at the top of `chat/project_tools.py`:

```python
from src.task_state import task_state_for_project
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest tests/test_list_projects_envelope_v2.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run the full suite — existing list_projects tests must keep passing**

Run: `.venv/bin/pytest tests/ -q`
Expected: PASS, total = previous + 4. No regressions in existing `test_list_projects*` tests because the default `envelope_version=1` keeps the legacy contract.

- [ ] **Step 6: Confirm `chat/project_tools.py` stays under 200 lines**

Run: `wc -l chat/project_tools.py`
Expected: under 200. If over, extract the new `_agent_status` helper into a small `chat/project_status.py` module.

- [ ] **Step 7: `/simplify`, commit**

Inline review: `_agent_status` collapses the "which vocab + which source" decision into a single function. Avoids duplicating the chat_db None-check across two branches. Accept.

```bash
git add chat/project_tools.py tests/test_list_projects_envelope_v2.py
git commit -m "feat(list-projects): emit v2 vocab + task_state when envelope_version >= 2"
```

---

## Task 5: Thread `env.v` through the JSON dispatcher

**Files:**
- Modify: `src/json_kinds.py` (or wherever `_handle_list_projects` lives)
- Modify or add: existing dispatcher test for `kind=list_projects`

- [ ] **Step 1: Locate the handler**

Run: `grep -n list_projects src/json_kinds.py`
Expected: a `_handle_list_projects` function (or `list_projects` branch in a switch). Read it.

- [ ] **Step 2: Write the failing test**

Look at an existing `tests/test_json_kinds*.py` for the convention. Add a test (in the same style) that constructs a `kind=list_projects` envelope with `v: 2` and asserts the response body contains `task_state` and `agent_status` from the v2 vocabulary.

If no convenient existing test fixture covers this kind, add a new file `tests/test_json_kinds_list_projects_v2.py` and bootstrap it from how `tests/test_json_kinds.py` instantiates handlers.

(Concrete code: read the existing `_handle_list_projects` implementation first — its signature determines exactly how to call it. The minimum new behavior is: pass `env.v` into `list_projects_tool` as `envelope_version`.)

- [ ] **Step 3: Wire `env.v` through**

In `_handle_list_projects`, find the call site for `list_projects_tool(...)`. Add `envelope_version=env.v` to the kwargs.

- [ ] **Step 4: Build response envelope at negotiated `v`**

Inside the same handler, when building the response envelope, replace any hard-coded `v=V` or `v=1` with `v=negotiate_v(env.v)` (imported from `src.json_envelope`). The response's `v` field now matches what the client actually gets.

- [ ] **Step 5: Run the new test**

Run: `.venv/bin/pytest tests/test_json_kinds_list_projects_v2.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite + line-limit**

Run: `.venv/bin/pytest tests/ -q && scripts/check-line-limit.sh`
Expected: PASS, line-limit clean.

- [ ] **Step 7: `/simplify`, commit**

```bash
git add src/json_kinds.py tests/test_json_kinds_list_projects_v2.py
git commit -m "feat(json-kinds): negotiate envelope v and pass it to list_projects"
```

---

## Task 6: Document `TASK_STATE_FADE_SEC` in `.env.example`

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Read the existing `.env.example` to see where similar tunables live**

Run: `grep -n -A1 -E 'POLL_INTERVAL|CHAT_PORT' .env.example`

- [ ] **Step 2: Insert documentation block**

Edit `.env.example`. After the polling/CLI block (where similar timing knobs already live), add:

```
# Task-state fade window (seconds) for envelope v: 2 list_projects.
# After this many seconds past completed_at, terminal task_state
# (completed | error) flips back to null on the wire. Default: 30.
TASK_STATE_FADE_SEC=30
```

- [ ] **Step 3: Confirm no test depends on the new key being unset**

Run: `grep -rn TASK_STATE_FADE_SEC tests/`
Expected: only the `tests/test_task_state.py` reference exists.

- [ ] **Step 4: Commit**

```bash
git add .env.example
git commit -m "docs(env): document TASK_STATE_FADE_SEC for list_projects v:2 fade window"
```

(No /simplify needed; pure docs touch.)

---

## Task 7: Docs follow code — README + website

**Files:**
- Modify: `README.md` (the "Agent status tracking" bullet near L79)
- Modify (if matched): `website/index.html` and `website/fa/index.html`

- [ ] **Step 1: Find references to the old vocabulary**

Run: `grep -nE 'connected, disconnected|disconnected, deregistered|status tracking' README.md website/index.html website/fa/index.html`

- [ ] **Step 2: Update README bullet (L79)**

Current text reads:

```
- Agent status tracking (running, idle, disconnected, deregistered)
```

Replace with:

```
- Agent status tracked along two axes for envelope `v >= 2` consumers:
  - **Process-state** (`online` / `stale` / `offline`) — derived from `last_seen_at` heartbeat (5-min window), `is_alive(pid)` when set, and a 30-min ghost threshold.
  - **Task-state** (`waiting` / `working` / `completed` / `error` / `null`) — derived from the tasks table with a configurable fade window (`TASK_STATE_FADE_SEC`, default 30 s).
- Envelope `v: 1` clients keep the legacy 3-state vocabulary (`connected` / `disconnected` / `absent`) and never see `task_state`.
- Agent PIDs recorded in the database
```

(Removes the old "Agent PIDs recorded" bullet duplication that would otherwise sit immediately after — confirm by reading L80-81 before editing.)

- [ ] **Step 3: Website lockstep, only if grep found references**

If `grep` showed marketing copy mentioning the old vocab, update EN + FA in lockstep with a one-line equivalent. If nothing matched, skip.

- [ ] **Step 4: Commit**

```bash
git add README.md website/
git commit -m "docs: describe two-axis status taxonomy (envelope v:2) in README"
```

(/simplify — inline review only; this is prose.)

---

## Task 8: Verify, push, open PR cross-linked with peer #57

**Files:** none modified.

- [ ] **Step 1: Final full verification**

Run all three independent checks in parallel via Bash:

```bash
scripts/check-line-limit.sh
.venv/bin/pytest tests/ -q
git diff master --stat
```

Expected:
- Line-limit: exit 0, no output.
- pytest: PASS, count = baseline + (4 envelope + 8 agent_state + 9 task_state + 4 list_projects + 1-or-more dispatcher) = baseline + ~26.
- diff stat: only the files this plan touches.

- [ ] **Step 2: Push**

```bash
git push -u origin feat/status-taxonomy-backend-v2
```

- [ ] **Step 3: Open PR**

```bash
gh pr create --title "feat: two-axis status taxonomy backend (envelope v:2)" --body "$(cat <<'EOF'
## Summary
Backend half of the status-taxonomy work coordinated with `agent-Claude-Email-App`. Ships envelope `v: 2` for the `list_projects` response:

- **Process-state** axis: new helper `ChatDB.agent_state_for_project()` returning `online | stale | offline` (mirrors the dashboard's heartbeat + ghost-threshold semantics).
- **Task-state** axis: new module `src/task_state.py` deriving `waiting | working | completed | error | null` from the tasks table, with configurable fade (`TASK_STATE_FADE_SEC`, default 30 s).
- **Envelope negotiation**: `src/json_envelope.py::V` bumped to 2; new `negotiate_v()` picks per-response version from `min(client_v, V)`.
- **`list_projects_tool`** gains `envelope_version: int = 1`; v:1 callers get the unchanged legacy contract, v:2 callers get the new shape.

## Paired with
- **Android client UI half:** https://github.com/cocodedk/Claude-Email-App/pull/57 (proposal commit `52015ac`). Their UI parser is encoding the same vocabulary; ship together.

## Test plan
- [x] `scripts/check-line-limit.sh` exits 0
- [x] `.venv/bin/pytest tests/ -q` → baseline + ~26 new tests
- [x] Existing `list_projects` tests unchanged (legacy contract preserved via default `envelope_version=1`)
- [ ] Manual: send a `kind=list_projects` envelope with `v: 2` from the test sender; confirm response carries the new vocab + `task_state`.
- [ ] Manual: send the same envelope with `v: 1`; confirm legacy vocab still works and `task_state` is absent.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Cross-link from peer's PR**

Bus-ping `agent-Claude-Email-App` with this PR's URL so they can add it to their `## Paired with` section.

- [ ] **Step 5: Mark plan executed**

Move this plan file from `docs/superpowers/plans/` to `docs/superpowers/plans/done/` (create the `done/` dir if it doesn't exist) — or leave it in place if there's no done-archival convention in this repo.

Run `ls docs/superpowers/plans/` to see if a `done/` subdir already exists. If not, leave the file in place.

---

## Self-review notes

- **Spec coverage:** Every Q from the peer's proposal is answered (Q1/Q2/Q3 above), every wire-field change is implemented (Tasks 2/3/4/5), back-compat is preserved (Task 4 default param + Task 5 dispatcher negotiation), and docs follow code (Tasks 6/7).
- **No placeholders:** all code blocks are concrete and ready to apply. The only Step that pre-reads existing code before writing (Task 5 dispatcher wiring) does so deliberately because the handler's exact signature isn't shown anywhere in this plan — but the wiring instructions are explicit ("add `envelope_version=env.v`", "replace v=V with v=negotiate_v(env.v)").
- **Type consistency:** `agent_status` field name is reused across both vocabularies (the value vocabulary changes, the key name doesn't). `task_state` is a brand-new field with consistent naming across the helper, the wire, and the tests.
- **Deferrals tracked:** dashboard adoption of new vocab is explicitly out of scope; PR #56's "Out of scope" bullet about this work is *not* fully cleared until the dashboard branch lands. Both PRs (#56 + #57 + this one) can ship independently.
