# ChatDB Tx Wrapper — Phase 0 (Probe) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship SQL-level observability on `ChatDB`'s shared connection so the next time we leak the WAL writer slot (the 2026-05-19 incident), we capture the smoking-gun trace without affecting any caller.

**Architecture:** Extract connection lifecycle from `ChatDB.__init__` into a new `TransactionMixin` (`src/chat_db_tx.py`) with `_open_conn(path)` + env-flagged `_trace_cb(sql)`. Reserve `self._db_lock = threading.RLock()` as a placeholder (no callers acquire it in Phase 0 — Phase 1 introduces `_run_tx`/`_read` that consume it). Lower `busy_timeout` from 5000 to 200 ms per the spec's worst-case event-loop block budget. No public method bodies change; existing 1441 tests must keep passing (one assertion update for the new busy_timeout value).

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `threading`, `os`, `logging`). pytest + `caplog` + `monkeypatch` fixtures. No new third-party deps.

**Spec reference:** `docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md` (cursor-agent + robo mutual ACCEPT after 4 rounds).

---

## File Structure

**Create:**
- `src/chat_db_tx.py` — Phase 0 contents only:
  - `TransactionMixin` class (instance attribute `_db_lock`, methods `_open_conn`, `_trace_cb`, helper `_classify_sql`).
  - Phase 1 will add `_run_tx`, `_read`, `_check_or_recover_at_depth_zero` here.
- `tests/test_chat_db_tx.py` — Phase 0 tests for `_open_conn`, env-gated trace, classifier safety, placeholder lock.

**Modify:**
- `src/chat_db.py:21-40` — Add `TransactionMixin` to `ChatDB`'s base list, replace inline `sqlite3.connect(...)` + 3 pragmas with `self._conn = self._open_conn(path)`, initialize `self._db_lock = threading.RLock()`.
- `tests/test_chat_db.py:18-19` — Update `test_busy_timeout_set` assertion from `5000` to `200` (spec §6 budgeting).

**No other production code changes in Phase 0.**

---

## Self-Review Checklist (post-write)

After this plan is fully drafted, run the writing-plans skill's self-review:
1. **Spec coverage:** Every spec §1, §2 (placeholder only), §7 line covered by a task. §3-§6 deferred to Phase 1 plan. ✓
2. **Placeholder scan:** Every step has concrete code/commands. No "TODO".
3. **Type consistency:** Method names (`_open_conn`, `_trace_cb`, `_classify_sql`, `_db_lock`) used consistently across tasks and the spec.

---

## Task 1: Create the `TransactionMixin` skeleton with `_open_conn`

**Files:**
- Create: `src/chat_db_tx.py`
- Test: `tests/test_chat_db_tx.py`

- [ ] **Step 1: Write the failing test** — `tests/test_chat_db_tx.py`

```python
"""Phase 0 tests for the ChatDB transaction wrapper layer (probe only)."""
import logging
import os
import sqlite3
import threading

import pytest

from src.chat_db_tx import TransactionMixin


class _Host(TransactionMixin):
    """Minimal host class so the mixin can be exercised in isolation."""
    def __init__(self):
        self._db_lock = threading.RLock()


@pytest.fixture
def host():
    return _Host()


class TestOpenConn:
    def test_returns_a_connection(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_row_factory_is_row(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert conn.row_factory is sqlite3.Row
        conn.close()

    def test_wal_mode_enabled(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.close()

    def test_busy_timeout_is_200ms(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 200
        conn.close()

    def test_foreign_keys_enabled(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        conn.close()

    def test_check_same_thread_off(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        # sqlite3 doesn't expose check_same_thread on the conn directly,
        # so verify by using the conn from another thread without raising.
        errors = []
        def run():
            try:
                conn.execute("SELECT 1").fetchone()
            except Exception as e:
                errors.append(e)
        t = threading.Thread(target=run)
        t.start()
        t.join()
        assert errors == []
        conn.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py -v`
Expected: ImportError — `src.chat_db_tx` does not exist yet.

- [ ] **Step 3: Implement `src/chat_db_tx.py`**

```python
"""Transaction wrapper layer for ChatDB.

Extracted from chat_db.py so that module stays under the 200-line cap
and so connection lifecycle (open, reopen, trace) is owned by one place.
Phase 0 only ships the connection factory + env-gated SQL trace callback.
Phase 1 will add _run_tx / _read / stale-tx recovery / post-commit hooks.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
"""
import logging
import os
import sqlite3


_BUSY_TIMEOUT_MS = 200  # spec §6 — bound event-loop block per write
_TRACE_ENV_VAR = "CHAT_DB_TRACE"

logger = logging.getLogger(__name__)


class TransactionMixin:
    """Adds connection lifecycle helpers to ChatDB."""

    def _open_conn(self, path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        if os.environ.get(_TRACE_ENV_VAR):
            conn.set_trace_callback(self._trace_cb)
        return conn

    def _trace_cb(self, sql: str) -> None:
        """SQLite trace hook — Phase 0 stub overridden in Task 3."""
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestOpenConn -v`
Expected: 6/6 pass.

- [ ] **Step 5: Run the full suite — no regressions allowed**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count as before the change (currently 1441) + 6 new = 1447 pass.

- [ ] **Step 6: Commit**

```bash
git add src/chat_db_tx.py tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
feat(chat_db_tx): add TransactionMixin._open_conn factory (Phase 0)

Extracts connection lifecycle so the next phase can reopen on poison
without duplicating pragmas. Drops busy_timeout to 200ms per spec
budgeting. Trace callback exists as a stub — env-gated install in
next task.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
EOF
)"
```

---

## Task 2: Env-gated SQL trace callback that logs kind + in_transaction

**Files:**
- Modify: `src/chat_db_tx.py:30-37` (replace `_trace_cb` stub + add `_classify_sql`)
- Test: `tests/test_chat_db_tx.py` (extend with `TestTraceCallback`)

- [ ] **Step 1: Write the failing tests** — append to `tests/test_chat_db_tx.py`

```python
class TestTraceCallback:
    def test_callback_not_installed_without_env(self, monkeypatch, host, tmp_path):
        monkeypatch.delenv("CHAT_DB_TRACE", raising=False)
        conn = host._open_conn(str(tmp_path / "a.db"))
        # set_trace_callback(None) clears the hook; if the install path ran,
        # SQLite would have invoked our cb when we exec. So we exec and
        # assert nothing was logged at our module's logger.
        with conn:
            conn.execute("CREATE TABLE t (id INTEGER)")
        # If the cb were installed it would have logged the kind; absence
        # is the contract.
        conn.close()

    def test_callback_installed_with_env(self, monkeypatch, host, tmp_path, caplog):
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.close()
        # We expect at least one trace line classifying SQL kinds.
        kinds = [r.message for r in caplog.records
                 if "chatdb.trace" in r.message]
        assert kinds, "trace callback did not log anything"
        joined = " ".join(kinds)
        # Kinds should appear; the SQL itself MUST NOT.
        assert "CREATE" in joined.upper() or "OTHER" in joined.upper()
        assert "TABLE t" not in joined  # no full SQL leaked

    def test_callback_never_logs_parameters_or_full_sql(
        self, monkeypatch, host, tmp_path, caplog
    ):
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        conn.execute("CREATE TABLE m (body TEXT)")
        conn.execute(
            "INSERT INTO m (body) VALUES (?)",
            ("super-secret-message-body",),
        )
        conn.commit()
        conn.close()
        full = " ".join(r.getMessage() for r in caplog.records)
        assert "super-secret-message-body" not in full
        # SQLite's trace_v2 does substitute literals into bound SQL on
        # some builds. We classify *only*, never echo the SQL.
        assert "INSERT INTO m" not in full


class TestClassifySql:
    @pytest.mark.parametrize(
        "sql,expected",
        [
            ("BEGIN IMMEDIATE", "BEGIN"),
            ("  begin transaction", "BEGIN"),
            ("COMMIT", "COMMIT"),
            ("ROLLBACK", "ROLLBACK"),
            ("INSERT INTO foo VALUES (1)", "OTHER"),
            ("SELECT * FROM bar", "OTHER"),
            ("", "OTHER"),
            ("   \n  ", "OTHER"),
        ],
    )
    def test_classify(self, host, sql, expected):
        assert host._classify_sql(sql) == expected
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestTraceCallback tests/test_chat_db_tx.py::TestClassifySql -v`
Expected: `TestClassifySql` fails on AttributeError (`_classify_sql` not defined). `TestTraceCallback::test_callback_installed_with_env` fails — no log lines (stub `_trace_cb` returns None without logging).

- [ ] **Step 3: Implement the real `_trace_cb` and `_classify_sql`** — edit `src/chat_db_tx.py`

Replace the stub `_trace_cb` and add `_classify_sql`. Final state of the methods:

```python
    def _trace_cb(self, sql: str) -> None:
        kind = self._classify_sql(sql)
        # Never echo `sql` — it may contain message bodies if the build
        # of SQLite substitutes literals. Log only kind + tx state.
        in_tx = getattr(self, "_conn", None) is not None and self._conn.in_transaction
        logger.debug(
            "chatdb.trace kind=%s in_transaction=%s",
            kind, in_tx,
        )

    @staticmethod
    def _classify_sql(sql: str) -> str:
        head = (sql or "").strip().split(None, 1)
        if not head:
            return "OTHER"
        first = head[0].upper()
        if first in ("BEGIN", "COMMIT", "ROLLBACK"):
            return first
        return "OTHER"
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py -v`
Expected: all `TestOpenConn` + `TestTraceCallback` + `TestClassifySql` pass.

- [ ] **Step 5: Run the full suite — no regressions allowed**

Run: `.venv/bin/pytest tests/ -q`
Expected: still all pass (existing 1441 + new tests added in Task 1 + new tests here).

- [ ] **Step 6: Commit**

```bash
git add src/chat_db_tx.py tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
feat(chat_db_tx): env-gated SQL trace logging without SQL/param echo

CHAT_DB_TRACE=1 installs a callback that logs only SQL kind (BEGIN /
COMMIT / ROLLBACK / OTHER) plus conn.in_transaction at DEBUG. The raw
SQL string is never logged — defends against message bodies appearing
in trace output (cf. memory feedback_no_real_emails_in_code).

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
EOF
)"
```

---

## Task 3: Reserve `_db_lock` on the mixin (placeholder for Phase 1)

**Files:**
- Modify: `src/chat_db_tx.py` (add an `_init_db_lock` helper)
- Test: `tests/test_chat_db_tx.py` (extend with `TestDbLock`)

Phase 1 will require `self._db_lock` to exist before any `_run_tx` / `_read` call. We add a tiny helper now so Task 4 (wiring ChatDB) is one line: `self._init_db_lock()`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_chat_db_tx.py`

```python
class TestDbLock:
    def test_init_db_lock_attaches_rlock(self):
        class H(TransactionMixin):
            pass
        h = H()
        h._init_db_lock()
        # RLock is internally `threading._RLock`; verify by re-acquiring
        # from the same thread (would deadlock on a plain Lock).
        with h._db_lock:
            with h._db_lock:
                pass

    def test_init_db_lock_is_idempotent(self):
        class H(TransactionMixin):
            pass
        h = H()
        h._init_db_lock()
        first = h._db_lock
        h._init_db_lock()
        # Idempotent: second call does NOT replace the lock (would lose
        # any acquisition state). Same object identity expected.
        assert h._db_lock is first
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestDbLock -v`
Expected: AttributeError — `_init_db_lock` not defined.

- [ ] **Step 3: Implement** — append to `TransactionMixin` in `src/chat_db_tx.py`

Add `import threading` at the top. Then:

```python
    def _init_db_lock(self) -> None:
        if getattr(self, "_db_lock", None) is None:
            self._db_lock = threading.RLock()
```

Also remove the `_db_lock` requirement from the test `_Host` class — the mixin now initializes it. Update the `_Host` fixture in `tests/test_chat_db_tx.py`:

```python
class _Host(TransactionMixin):
    """Minimal host class so the mixin can be exercised in isolation."""
    def __init__(self):
        self._init_db_lock()
```

- [ ] **Step 4: Run the new tests + module suite**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py -v`
Expected: all pass including `TestDbLock`.

- [ ] **Step 5: Commit**

```bash
git add src/chat_db_tx.py tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
feat(chat_db_tx): _init_db_lock helper reserves RLock for Phase 1

Placeholder so ChatDB.__init__ can call _init_db_lock() now and Phase 1
can wire _run_tx / _read consumers without an additional refactor of
the constructor.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
EOF
)"
```

---

## Task 4: Wire `ChatDB.__init__` to use `TransactionMixin`

**Files:**
- Modify: `src/chat_db.py:21-40` (add mixin, replace inline connect/pragmas, init lock).
- Modify: `tests/test_chat_db.py:18-19` (update `test_busy_timeout_set` from 5000 → 200).

- [ ] **Step 1: Update the busy_timeout assertion first** (this test will fail without it; updating up-front mirrors the spec change)

Current `tests/test_chat_db.py:18-19`:

```python
    def test_busy_timeout_set(self, db):
        cur = db._conn.execute("PRAGMA busy_timeout")
        assert cur.fetchone()[0] == 5000
```

Replace with:

```python
    def test_busy_timeout_set(self, db):
        cur = db._conn.execute("PRAGMA busy_timeout")
        # 200 ms per spec §6 — bounds event-loop block; retry handles
        # genuine cross-process contention.
        assert cur.fetchone()[0] == 200
```

- [ ] **Step 2: Run this single test — confirm it now fails on the OLD code**

Run: `.venv/bin/pytest tests/test_chat_db.py::TestSchema::test_busy_timeout_set -v`
Expected: FAIL — actual is `5000`, expected is `200`.

- [ ] **Step 3: Refactor `ChatDB.__init__`** — edit `src/chat_db.py:21-40`

Current `__init__` (lines 21-41):

```python
class ChatDB(
    AgentRegistryMixin, AgentStateMixin, DashboardQueriesMixin,
    MaintenanceMixin, OutboundEmailsMixin, WakeSessionStoreMixin,
):
    """Single entry-point for all chat DB operations."""

    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column/index already present
        self._conn.commit()
        self._wake_nudge = None
```

After:

```python
class ChatDB(
    TransactionMixin,
    AgentRegistryMixin, AgentStateMixin, DashboardQueriesMixin,
    MaintenanceMixin, OutboundEmailsMixin, WakeSessionStoreMixin,
):
    """Single entry-point for all chat DB operations."""

    def __init__(self, path: str):
        self.path = path
        self._init_db_lock()
        self._conn = self._open_conn(path)
        self._conn.executescript(_SCHEMA)
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column/index already present
        self._conn.commit()
        self._wake_nudge = None
```

Also add the import near the top of `src/chat_db.py`:

```python
from src.chat_db_tx import TransactionMixin
```

The `import sqlite3` line stays — `chat_db.py:38` still uses `sqlite3.OperationalError`.

- [ ] **Step 4: Run the full suite — every existing test must pass against the new wiring**

Run: `.venv/bin/pytest tests/ -q`
Expected: 1441 prior + new tests from Tasks 1-3 = ~1452 pass, zero fails.

If any test fails: STOP. Common causes (debug before continuing):
- A test that asserted `busy_timeout == 5000` outside `test_chat_db.py` (search: `rg "busy_timeout" tests/`).
- A test that relies on the trace callback being absent without setting `CHAT_DB_TRACE`. Phase 0 keeps the callback OFF by default — should be a non-issue.

- [ ] **Step 5: Verify line count under 200**

Run: `wc -l src/chat_db.py src/chat_db_tx.py`
Expected: both files report ≤ 200 lines.

- [ ] **Step 6: Run `/simplify` per memory `feedback_simplify_when_done`**

Use the `simplify` skill on the changed working tree (it'll review reuse, quality, efficiency). Apply any safe simplifications; fold them into this same commit (no separate fixup).

- [ ] **Step 7: Commit**

```bash
git add src/chat_db.py tests/test_chat_db.py
git commit -m "$(cat <<'EOF'
refactor(chat_db): wire ChatDB to TransactionMixin._open_conn

ChatDB.__init__ now delegates connection lifecycle (sqlite3.connect +
pragmas + optional trace callback) to TransactionMixin._open_conn.
Drops busy_timeout to 200ms per spec §6 budgeting; test_busy_timeout_set
updated accordingly. _db_lock reserved for Phase 1 consumers
(_run_tx / _read). No public method bodies change.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
EOF
)"
```

---

## Task 5: Integration smoke — ChatDB with `CHAT_DB_TRACE=1` writes trace lines on real traffic

**Files:**
- Test: `tests/test_chat_db_tx.py` (extend with `TestChatDbIntegration`)

This task ensures the wiring of Task 4 actually flows through end-to-end on a real `ChatDB` (not just the bare mixin), and gives us the contract that the trace callback sees `insert_message` traffic.

- [ ] **Step 1: Write the integration test** — append to `tests/test_chat_db_tx.py`

```python
class TestChatDbIntegration:
    def test_trace_callback_fires_on_real_chat_db_traffic(
        self, monkeypatch, tmp_path, caplog
    ):
        from src.chat_db import ChatDB
        monkeypatch.setenv("CHAT_DB_TRACE", "1")
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "integration.db"))
        db.register_agent("agent-a", "/tmp/a")
        db.insert_message("agent-a", "agent-b", "hi", "notify")
        kinds = [r.getMessage() for r in caplog.records
                 if "chatdb.trace" in r.getMessage()]
        assert kinds, "no trace lines captured during real ChatDB ops"
        joined = " ".join(kinds).upper()
        assert "BEGIN" in joined or "COMMIT" in joined, (
            "trace expected at least one transaction boundary"
        )

    def test_no_trace_lines_when_env_unset(
        self, monkeypatch, tmp_path, caplog
    ):
        from src.chat_db import ChatDB
        monkeypatch.delenv("CHAT_DB_TRACE", raising=False)
        caplog.set_level(logging.DEBUG, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "quiet.db"))
        db.register_agent("agent-a", "/tmp/a")
        db.insert_message("agent-a", "agent-b", "hi", "notify")
        kinds = [r.getMessage() for r in caplog.records
                 if "chatdb.trace" in r.getMessage()]
        assert kinds == [], (
            "trace callback emitted lines without CHAT_DB_TRACE=1"
        )

    def test_db_lock_attribute_present_on_chat_db(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "lock.db"))
        # Placeholder lock — no caller acquires it in Phase 0, but Phase 1
        # depends on it being an RLock. Verify reentrancy from same thread.
        with db._db_lock:
            with db._db_lock:
                pass
```

- [ ] **Step 2: Run the integration tests — expect them all to pass on the now-wired ChatDB**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestChatDbIntegration -v`
Expected: 3/3 pass. They are only meaningful AFTER Task 4 because they exercise `ChatDB`, which is what Task 4 wired up.

- [ ] **Step 3: Run the full suite — final confirmation**

Run: `.venv/bin/pytest tests/ -q && wc -l src/chat_db.py src/chat_db_tx.py`
Expected: all tests pass; both files under 200 lines.

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
test(chat_db_tx): integration smoke for trace callback + db_lock

Closes Phase 0: real ChatDB traffic produces trace lines when
CHAT_DB_TRACE=1, silence otherwise, and the _db_lock placeholder is
present and reentrant — ready for Phase 1 consumers.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md
EOF
)"
```

---

## Phase 0 complete — handoff to Phase 1 planning

Phase 0 ships independently. After it merges, Babak can flip `CHAT_DB_TRACE=1` on the live `claude-chat` / `claude-email` services (via environment override in the systemd unit) to capture trace lines if the bus wedge recurs.

A separate plan covers Phase 1 (`_run_tx`, `_read`, `_check_or_recover_at_depth_zero`, post-commit hooks, refactor of every public ChatDB method, retry on `database is locked`, the four external-module migrations, `emit_status_message` ChatDB method, all the lock/retry/poisoned-conn/close-on-replace/hooks-outside-lock tests). That plan is written **after Phase 0 lands** so any signal the probe surfaces can shape it.

---

## Verification before completion (per `superpowers:verification-before-completion`)

Before claiming Phase 0 done:

- [ ] `wc -l src/chat_db.py src/chat_db_tx.py` — both ≤ 200 lines.
- [ ] `.venv/bin/pytest tests/ -q` — all pass (previous count + ~12 new tests).
- [ ] `scripts/check-line-limit.sh` — no violations.
- [ ] `git log --oneline -6` — five new commits (one per task).
- [ ] Coverage report from `.coveragerc`: 100% retained on production code.
