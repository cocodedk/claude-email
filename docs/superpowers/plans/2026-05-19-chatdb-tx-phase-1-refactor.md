# ChatDB Tx Wrapper — Phase 1 (Refactor + Retry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralise transaction ownership inside `ChatDB` via `_run_tx` / `_read` callable wrappers, serialise all `_conn` access with `threading.RLock`, detect and recover from stale transactions, retry once on `database is locked`, and migrate the four external modules that currently reach into `db._conn` directly. End state: the bus self-heals from the 2026-05-19 wedge class of bug.

**Architecture:** Extend `src/chat_db_tx.py` with `_run_tx(fn, *args)` and `_read(fn, *args)` callable wrappers that own the RLock + transaction lifecycle. Every public ChatDB write method becomes a thin wrapper that calls `self._run_tx(self._impl_xxx)` with the body extracted into a private `_impl_xxx`. Every read method becomes `self._read(self._impl_xxx)`. Side effects that must not fire on rollback (e.g. `_nudge_wake`) move to a `self._after_commit` post-commit hook list flushed after the outer commit releases the lock. Four external modules (`status_envelope`, `chat_relay`, `origin_envelope`, `relay_routing`) get new ChatDB methods to replace their direct `_conn.execute(...)` accesses.

**Tech Stack:** Python 3.12 stdlib (`sqlite3`, `threading`, `logging`). pytest + `caplog` + `monkeypatch` fixtures. The Phase 0 `open_conn` factory and `_init_db_lock` placeholder already shipped on master. No new third-party deps.

**Spec reference:** `docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md` (cursor-agent + robo mutual ACCEPT after 4 rounds, Babak signed off, Phase 0 merged as `cb2c216`).

**Branch (suggested):** `chatdb-tx-phase-1-refactor`, branched from master after Phase 0 merge.

---

## File Structure

**Modify:**
- `src/chat_db_tx.py` (currently 81 lines) — grow to ~150–165 lines. Adds `_check_or_recover_at_depth_zero`, `_run_tx`, `_read`, the post-commit hook plumbing, the `lock_event` WARNING emitter, and a `_close_and_reopen` helper. Stays under the 200-line cap.
- `src/chat_db.py` — refactor every writer to `_run_tx(self._impl_xxx)` + every reader to `_read(self._impl_xxx)`. Add three new methods that replace `status_envelope`'s direct-`_conn` writes, plus read methods for chat_relay / origin_envelope / relay_routing.
- `src/agent_registry.py` — refactor every public writer/reader.
- `src/agent_state.py`, `src/dashboard_queries.py`, `src/db_maintenance.py`, `src/outbound_emails_store.py`, `src/wake_session_store.py` — refactor.
- `src/status_envelope.py` — delegate to new ChatDB methods.
- `src/chat_relay.py`, `src/origin_envelope.py`, `src/relay_routing.py` — delegate to new ChatDB read methods.

**Create:**
- `tests/_tx_fixtures.py` — shared pytest helpers: `sidecar_writer_lock(path)` (holds `BEGIN IMMEDIATE` on a sidecar connection, releases on signal) and `narrow_busy_timeout(db, ms=50)` (lowers the wrapped ChatDB's `busy_timeout` so the held lock surfaces as `OperationalError` before the sidecar releases).

**Tests touched:** every `tests/test_*.py` that exercises ChatDB or one of the mixins. The refactor changes internal method shape but NOT the public API; existing tests should pass without modification unless they relied on `_nudge_wake`'s pre-commit timing (none currently do).

---

## Refactor Pattern (canonical — applied throughout Tasks 5–7)

Every public ChatDB method moves to this shape. **Writers:**

Before:
```python
def public_writer(self, arg1, arg2):
    self._conn.execute("INSERT INTO foo (a, b) VALUES (?, ?)", (arg1, arg2))
    self._conn.commit()
    self._log_event(...)
    self._nudge_wake()  # if applicable
```

After:
```python
def public_writer(self, arg1, arg2):
    return self._run_tx(self._impl_public_writer, arg1, arg2)

def _impl_public_writer(self, arg1, arg2):
    self._conn.execute("INSERT INTO foo (a, b) VALUES (?, ?)", (arg1, arg2))
    self._impl_log_event(...)  # nested — no commit
    self._after_commit.append(self._nudge_wake)  # post-commit hook
```

**Readers:**

Before:
```python
def public_reader(self, arg):
    row = self._conn.execute("SELECT * FROM foo WHERE id=?", (arg,)).fetchone()
    return dict(row) if row else None
```

After:
```python
def public_reader(self, arg):
    return self._read(self._impl_public_reader, arg)

def _impl_public_reader(self, arg):
    row = self._conn.execute("SELECT * FROM foo WHERE id=?", (arg,)).fetchone()
    return dict(row) if row else None
```

**Rules:**
- The `_impl_X` body never calls `.commit()` or `.rollback()` — that's `_run_tx`'s job on the outermost frame.
- The `_impl_X` body never calls `_run_tx` on another method's public wrapper — call the nested `_impl_X` directly to participate in the outer transaction.
- Side effects that must not fire on rollback go into `self._after_commit` (a list of zero-arg callables).
- Methods that today return values still return them — `_run_tx` and `_read` propagate the return.

---

## Task 1: Shared poison-recovery entry guard + `lock_event` emitter

**Files:**
- Modify: `src/chat_db_tx.py`
- Test: `tests/test_chat_db_tx.py` (extend with `TestPoisonRecovery`)

Phase 1's two callable wrappers (`_run_tx`, `_read`) both need the same depth-0 check: if the connection arrives with `in_transaction=True` despite our wrapper not having started one, that's the smoking-gun signature of a leaked implicit transaction. We log it, attempt rollback, and on rollback failure close-and-reopen the connection. Centralised so the two wrappers stay simple.

- [ ] **Step 1: Write the failing test** — append to `tests/test_chat_db_tx.py`

```python
class TestPoisonRecovery:
    def test_clean_conn_no_op(self, host, tmp_path):
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn
        host._check_or_recover_at_depth_zero("test_method")
        # No exception; connection unchanged.
        assert host._conn is conn
        conn.close()

    def test_stale_tx_rolled_back(self, host, tmp_path, caplog):
        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("BEGIN")
        conn.execute("INSERT INTO t VALUES (1)")
        assert conn.in_transaction is True
        host._check_or_recover_at_depth_zero("test_method")
        assert conn.in_transaction is False
        warnings = [r for r in caplog.records
                    if "chatdb.lock_event" in r.getMessage()
                    and "kind=stale_tx" in r.getMessage()
                    and "test_method" in r.getMessage()]
        assert warnings, "expected one stale_tx warning"
        conn.close()

    def test_rollback_failure_swaps_connection(
        self, host, tmp_path, monkeypatch, caplog,
    ):
        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        conn = host._open_conn(str(tmp_path / "a.db"))
        host._conn = conn
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("BEGIN")
        original_rollback = conn.rollback
        monkeypatch.setattr(
            conn, "rollback",
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("forced")),
        )
        host._check_or_recover_at_depth_zero("test_method")
        # Connection was replaced — host._conn is a new object.
        assert host._conn is not conn
        # Old conn is closed (best-effort).
        replaced_warnings = [r for r in caplog.records
                             if "kind=rollback_failed" in r.getMessage()]
        assert replaced_warnings
        host._conn.close()
```

- [ ] **Step 2: Run the new tests — they should fail**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestPoisonRecovery -v`
Expected: AttributeError — `_check_or_recover_at_depth_zero` not defined.

- [ ] **Step 3: Implement** — add to `src/chat_db_tx.py` inside `TransactionMixin`

```python
    def _check_or_recover_at_depth_zero(self, method_name: str) -> None:
        """Entry guard for outermost _run_tx / _read frames.

        If self._conn is None or not in a transaction, no-op. Otherwise
        we found a leaked implicit tx — log the smoking-gun event, try
        rollback, and on rollback failure swap to a fresh connection.
        """
        if self._conn is None or not self._conn.in_transaction:
            return
        self._lock_event(method_name, "stale_tx", connection_replaced=False)
        try:
            self._conn.rollback()
        except sqlite3.Error:
            self._lock_event(method_name, "rollback_failed", connection_replaced=True)
            self._close_and_reopen()

    def _lock_event(
        self, method_name: str, kind: str, *, connection_replaced: bool = False,
    ) -> None:
        in_tx = self._conn is not None and self._conn.in_transaction
        logger.warning(
            "chatdb.lock_event method=%s kind=%s conn.in_transaction=%s "
            "connection_replaced=%s",
            method_name, kind, in_tx, connection_replaced,
        )

    def _close_and_reopen(self) -> None:
        """Best-effort close of self._conn, then open a fresh one."""
        old = self._conn
        try:
            old.close()
        except sqlite3.Error:
            pass  # GC will reclaim the fd
        self._conn = open_conn(self.path, self._trace_cb)
```

Also add `self.path: str` to the mixin's class-level annotations (needed by `_close_and_reopen`):

```python
    _conn: sqlite3.Connection | None = None
    _db_lock: threading.RLock | None = None
    path: str = ""  # Set by host __init__; needed for _close_and_reopen.
```

- [ ] **Step 4: Run the new tests — they should pass**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestPoisonRecovery -v`
Expected: 3/3 pass.

- [ ] **Step 5: Run the full suite — no regressions**

Run: `.venv/bin/pytest tests/ -q`
Expected: all prior tests pass + 3 new = +3 over the post-Phase-0 baseline.

- [ ] **Step 6: Commit**

```bash
git add src/chat_db_tx.py tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
feat(chat_db_tx): _check_or_recover_at_depth_zero + lock_event emitter

Shared entry guard for the upcoming _run_tx / _read wrappers. Detects
a connection that arrives at depth 0 with in_transaction=True (the
smoking-gun signature for a leaked implicit transaction), logs the
event at WARNING, attempts rollback, and on rollback failure swaps to
a fresh connection via open_conn.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_run_tx` callable wrapper with retry

**Files:**
- Create: `tests/_tx_fixtures.py`
- Modify: `src/chat_db_tx.py`
- Test: `tests/test_chat_db_tx.py` (extend with `TestRunTx`)

`_run_tx(fn, *args, **kwargs)` acquires the RLock, runs the depth-0 entry guard, opens `BEGIN IMMEDIATE`, calls `fn`, commits on clean return, fires post-commit hooks outside the lock, retries once on `database is locked`, rolls back on any other exception. Nested entry (depth > 0) joins the outer transaction — no BEGIN, no COMMIT, no retry.

- [ ] **Step 1: Create the shared test fixture** — `tests/_tx_fixtures.py`

```python
"""Shared pytest helpers for the Phase 1 tx-wrapper tests."""
import sqlite3
import threading
from contextlib import contextmanager


@contextmanager
def sidecar_writer_lock(path: str):
    """Hold a write transaction on `path` via a sidecar sqlite3 connection.

    Yields a threading.Event the caller can set() to signal the sidecar
    to commit and release. Used to deterministically provoke
    `OperationalError("database is locked")` against another connection
    whose busy_timeout is shorter than the test's wait.
    """
    release = threading.Event()
    started = threading.Event()

    def _hold():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA busy_timeout=30000")
        # Use a side table so we don't interfere with the schema.
        conn.execute("CREATE TABLE IF NOT EXISTS _sidecar_lock (id INTEGER)")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO _sidecar_lock (id) VALUES (1)")
        started.set()
        release.wait(timeout=10)
        conn.commit()
        conn.close()

    t = threading.Thread(target=_hold, daemon=True)
    t.start()
    started.wait(timeout=5)
    try:
        yield release
    finally:
        release.set()
        t.join(timeout=5)


def narrow_busy_timeout(db, ms: int = 50) -> None:
    """Lower the wrapped ChatDB's busy_timeout so a held writer lock
    surfaces as OperationalError before the sidecar releases."""
    db._conn.execute(f"PRAGMA busy_timeout={ms}")
```

- [ ] **Step 2: Write the failing test** — append to `tests/test_chat_db_tx.py`

```python
class TestRunTx:
    def test_clean_commit_returns_value(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        def body():
            db._conn.execute("CREATE TABLE t (id INTEGER)")
            db._conn.execute("INSERT INTO t VALUES (42)")
            return "done"
        result = db._run_tx(body)
        assert result == "done"
        rows = db._conn.execute("SELECT id FROM t").fetchall()
        assert [r[0] for r in rows] == [42]

    def test_exception_rolls_back(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        def body():
            db._conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("boom")
        with pytest.raises(RuntimeError):
            db._run_tx(body)
        rows = db._conn.execute("SELECT id FROM t").fetchall()
        assert rows == []

    def test_retry_succeeds_after_sidecar_releases(self, tmp_path):
        from src.chat_db import ChatDB
        from tests._tx_fixtures import sidecar_writer_lock, narrow_busy_timeout
        path = str(tmp_path / "a.db")
        db = ChatDB(path)
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        narrow_busy_timeout(db, 50)
        attempts = {"n": 0}
        def body():
            attempts["n"] += 1
            db._conn.execute("INSERT INTO t VALUES (1)")
        with sidecar_writer_lock(path) as release:
            # Trigger the first attempt asynchronously so the test can
            # release the sidecar between attempts.
            import threading as _t
            result_holder = {}
            def run():
                try:
                    db._run_tx(body)
                    result_holder["ok"] = True
                except Exception as exc:
                    result_holder["err"] = exc
            t = _t.Thread(target=run)
            t.start()
            # Give the first attempt time to hit busy_timeout.
            import time
            time.sleep(0.2)
            release.set()
            t.join(timeout=10)
        assert result_holder.get("ok"), result_holder
        assert attempts["n"] >= 2  # at least one retry
        assert db._conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1

    def test_nested_run_tx_joins_outer(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        seen_depth = []
        def inner():
            seen_depth.append(db._tx_depth)
            db._conn.execute("INSERT INTO t VALUES (2)")
        def outer():
            seen_depth.append(db._tx_depth)
            db._conn.execute("INSERT INTO t VALUES (1)")
            db._run_tx(inner)  # nested
            db._conn.execute("INSERT INTO t VALUES (3)")
        db._run_tx(outer)
        rows = [r[0] for r in db._conn.execute("SELECT id FROM t ORDER BY id")]
        assert rows == [1, 2, 3]
        assert seen_depth == [1, 2]  # outer at 1, inner at 2

    def test_post_commit_hooks_fire_outside_lock(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        order = []
        def hook():
            # If we're still under the RLock with tx_depth > 0, this would
            # be observable; by contract, hooks fire AFTER lock release.
            order.append(("hook", db._tx_depth))
        def body():
            db._after_commit.append(hook)
            order.append(("body", db._tx_depth))
        db._run_tx(body)
        assert order == [("body", 1), ("hook", 0)]

    def test_post_commit_hooks_skipped_on_rollback(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        fired = []
        def body():
            db._after_commit.append(lambda: fired.append(True))
            raise RuntimeError("rollback me")
        with pytest.raises(RuntimeError):
            db._run_tx(body)
        assert fired == []
```

- [ ] **Step 3: Run the new tests — they should fail**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestRunTx -v`
Expected: AttributeError — `_run_tx`, `_tx_depth`, or `_after_commit` not defined.

- [ ] **Step 4: Implement `_run_tx` + supporting state** — add to `TransactionMixin` in `src/chat_db_tx.py`

Add the class-level state:

```python
    _conn: sqlite3.Connection | None = None
    _db_lock: threading.RLock | None = None
    path: str = ""
    _tx_depth: int = 0  # 0 = outermost; >0 = nested
    # After-commit hook list; per instance, serialised by _db_lock so a
    # plain list is safe (only one outermost _run_tx active at a time).
    _after_commit: list = None  # type: ignore[assignment]
```

In `_init_db_lock`, also initialise the hook list:

```python
    def _init_db_lock(self) -> None:
        """Attach an RLock + post-commit list if not already set."""
        if self._db_lock is None:
            self._db_lock = threading.RLock()
        if self._after_commit is None:
            self._after_commit = []
```

Add `_run_tx`:

```python
    def _run_tx(self, fn, *args, **kwargs):
        """Run `fn(*args, **kwargs)` inside a serialised write transaction.

        Outermost frame: acquires self._db_lock, runs the poison-recovery
        entry guard, begins an immediate transaction, calls fn, commits,
        releases the lock, then fires post-commit hooks. Retries once on
        `database is locked` (clears hooks before the retry).

        Nested frame: joins the outer transaction — no BEGIN/COMMIT/retry.
        Hooks appended by nested fn are owned by the outer frame.
        """
        method_name = getattr(fn, "__qualname__", repr(fn))
        if self._tx_depth > 0:
            return fn(*args, **kwargs)
        return self._run_tx_outer(method_name, fn, args, kwargs)

    def _run_tx_outer(self, method_name, fn, args, kwargs):
        with self._db_lock:
            self._check_or_recover_at_depth_zero(method_name)
            for attempt in (1, 2):
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._tx_depth = 1
                    try:
                        result = fn(*args, **kwargs)
                        self._conn.commit()
                    except BaseException:
                        self._safe_rollback(method_name)
                        self._after_commit.clear()
                        self._tx_depth = 0
                        raise
                    hooks = self._after_commit[:]
                    self._after_commit.clear()
                    self._tx_depth = 0
                    break
                except sqlite3.OperationalError as exc:
                    if "database is locked" not in str(exc):
                        raise
                    if attempt == 2:
                        self._lock_event(method_name, "retry_failed")
                        raise
                    self._lock_event(method_name, "locked")
                    self._safe_rollback(method_name)
                    self._after_commit.clear()
                    self._tx_depth = 0
                    # Loop again for the retry.
            # Hooks fire AFTER lock release.
        for hook in hooks:
            try:
                hook()
            except Exception:
                logger.warning(
                    "chatdb.after_commit_hook_failed method=%s hook=%s",
                    method_name, getattr(hook, "__qualname__", repr(hook)),
                    exc_info=True,
                )
        return result

    def _safe_rollback(self, method_name: str) -> None:
        try:
            self._conn.rollback()
        except sqlite3.Error:
            self._lock_event(method_name, "rollback_failed", connection_replaced=True)
            self._close_and_reopen()
```

- [ ] **Step 5: Run the new tests — they should pass**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestRunTx -v`
Expected: 6/6 pass.

- [ ] **Step 6: Run the full suite — no regressions**

Run: `.venv/bin/pytest tests/ -q`
Expected: previous count + 6 new.

- [ ] **Step 7: Commit**

```bash
git add tests/_tx_fixtures.py src/chat_db_tx.py tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
feat(chat_db_tx): _run_tx callable wrapper with retry + post-commit hooks

Adds the core Phase 1 transaction wrapper. RLock-guarded, BEGIN IMMEDIATE,
single retry on `database is locked`, post-commit hooks fired outside
the lock, rollback on any exception, close-and-reopen on rollback
failure. Nested calls join the outer transaction.

Test fixture (tests/_tx_fixtures.py) provides a sidecar BEGIN IMMEDIATE
helper + a narrow_busy_timeout shortcut so retry paths can be tested
deterministically.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_read` locked read helper

**Files:**
- Modify: `src/chat_db_tx.py`
- Test: `tests/test_chat_db_tx.py` (extend with `TestRead`)

`_read(fn, *args, **kwargs)` is the read counterpart to `_run_tx`. It acquires the same RLock and runs the same depth-0 poison check, but does NOT open a transaction. Nested reads inside an active write transaction skip the poison check (the active write provides serialisation).

- [ ] **Step 1: Write the failing test** — append to `tests/test_chat_db_tx.py`

```python
class TestRead:
    def test_returns_value(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        db._conn.execute("INSERT INTO t VALUES (7)")
        def body():
            return db._conn.execute("SELECT id FROM t").fetchone()[0]
        assert db._read(body) == 7

    def test_nested_read_inside_run_tx(self, tmp_path):
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        captured = []
        def outer():
            db._conn.execute("INSERT INTO t VALUES (1)")
            captured.append(
                db._read(lambda: db._conn.execute(
                    "SELECT id FROM t").fetchone()[0]),
            )
        db._run_tx(outer)
        assert captured == [1]

    def test_read_runs_poison_check_at_depth_zero(self, tmp_path, caplog):
        import logging as _logging
        from src.chat_db import ChatDB
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("CREATE TABLE t (id INTEGER)")
        db._conn.execute("BEGIN")
        db._conn.execute("INSERT INTO t VALUES (1)")
        assert db._conn.in_transaction is True
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db._read(lambda: None)
        assert db._conn.in_transaction is False
        warnings = [r for r in caplog.records
                    if "kind=stale_tx" in r.getMessage()]
        assert warnings
```

- [ ] **Step 2: Run — should fail**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestRead -v`
Expected: AttributeError on `_read`.

- [ ] **Step 3: Implement `_read`** — add to `TransactionMixin`

```python
    def _read(self, fn, *args, **kwargs):
        """Run `fn` under the RLock without opening a transaction.

        Outermost frame runs the poison check; nested frames (inside an
        active write transaction) skip it because the write provides
        serialisation."""
        method_name = getattr(fn, "__qualname__", repr(fn))
        with self._db_lock:
            if self._tx_depth == 0:
                self._check_or_recover_at_depth_zero(method_name)
            return fn(*args, **kwargs)
```

- [ ] **Step 4: Run — should pass**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestRead -v`
Expected: 3/3 pass.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: previous + 3 new.

- [ ] **Step 6: Commit**

```bash
git add src/chat_db_tx.py tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
feat(chat_db_tx): _read locked read helper with poison recovery

Read counterpart to _run_tx. Acquires the RLock + runs the depth-0
poison check on outermost entry; nested reads inside a write tx skip
the check because the surrounding write already serialises access.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Refactor `ChatDB` writers + readers to use `_run_tx` / `_read`

**Files:**
- Modify: `src/chat_db.py`
- Tests: existing `tests/test_chat_db.py` should pass without modification; add a small `TestNudgeWakeIsPostCommit` if the wake-nudge timing is observable.

Apply the canonical pattern (§ Refactor Pattern above) to every method in `src/chat_db.py`. Each method becomes a thin wrapper around `self._run_tx(self._impl_X)` or `self._read(self._impl_X)`. `_log_event` is special: it stays a method on the class but its body becomes nest-safe (no `commit()` call) — the new `_impl_log_event` is what `_run_tx` users call.

- [ ] **Step 1: Identify the methods to refactor**

In `src/chat_db.py`:
- Writers: `insert_message`, `claim_pending_messages_for`, `mark_message_delivered`, `mark_message_failed`, `recover_failed_messages_for`, `set_email_message_id`, `_log_event`.
- Readers: `get_pending_messages_for`, `get_distinct_pending_recipients`, `find_message_by_email_id`, `get_message`, `get_last_email_message_id_for_agent`, `get_reply_to_message`.

- [ ] **Step 2: Refactor `insert_message`** (worked example — moves `_nudge_wake` to post-commit)

Before (current `src/chat_db.py:53-72`):
```python
def insert_message(
    self, from_name: str, to_name: str, body: str,
    msg_type: str, in_reply_to: int | None = None,
    content_type: str = "", task_id: int | None = None,
) -> dict:
    now = _now()
    cur = self._conn.execute(
        """INSERT INTO messages (from_name, to_name, body, type, status,
                                 in_reply_to, created_at, content_type, task_id)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (from_name, to_name, body, msg_type, in_reply_to, now,
         content_type or None, task_id),
    )
    self._conn.commit()
    self._log_event(from_name, "message", f"{msg_type} from {from_name} to {to_name}")
    row = self._conn.execute(
        "SELECT * FROM messages WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    self._nudge_wake()
    return dict(row)
```

After:
```python
def insert_message(
    self, from_name: str, to_name: str, body: str,
    msg_type: str, in_reply_to: int | None = None,
    content_type: str = "", task_id: int | None = None,
) -> dict:
    return self._run_tx(
        self._impl_insert_message,
        from_name, to_name, body, msg_type, in_reply_to,
        content_type, task_id,
    )

def _impl_insert_message(
    self, from_name, to_name, body, msg_type, in_reply_to,
    content_type, task_id,
) -> dict:
    now = _now()
    cur = self._conn.execute(
        """INSERT INTO messages (from_name, to_name, body, type, status,
                                 in_reply_to, created_at, content_type, task_id)
           VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (from_name, to_name, body, msg_type, in_reply_to, now,
         content_type or None, task_id),
    )
    self._impl_log_event(
        from_name, "message", f"{msg_type} from {from_name} to {to_name}",
    )
    row = self._conn.execute(
        "SELECT * FROM messages WHERE id=?", (cur.lastrowid,)
    ).fetchone()
    self._after_commit.append(self._nudge_wake)
    return dict(row)
```

- [ ] **Step 3: Refactor `_log_event` into nest-safe shape**

Before:
```python
def _log_event(self, participant: str, event_type: str, summary: str) -> None:
    self._conn.execute(
        "INSERT INTO events (event_type, participant, summary, created_at) VALUES (?, ?, ?, ?)",
        (event_type, participant, summary, _now()),
    )
    self._conn.commit()
```

After:
```python
def _log_event(self, participant: str, event_type: str, summary: str) -> None:
    """Public entry — used by callers that aren't already inside a tx.
    Inside _run_tx bodies, call self._impl_log_event directly to nest."""
    self._run_tx(self._impl_log_event, participant, event_type, summary)

def _impl_log_event(self, participant, event_type, summary) -> None:
    self._conn.execute(
        "INSERT INTO events (event_type, participant, summary, created_at) "
        "VALUES (?, ?, ?, ?)",
        (event_type, participant, summary, _now()),
    )
```

- [ ] **Step 4: Refactor the remaining writers**

For each of `claim_pending_messages_for`, `mark_message_delivered`, `mark_message_failed`, `recover_failed_messages_for`, `set_email_message_id`:

1. Rename the existing body to `_impl_<name>`.
2. Remove the inline `self._conn.commit()` call (the wrapper owns commit).
3. Add the thin wrapper:

```python
def <name>(self, *args):
    return self._run_tx(self._impl_<name>, *args)
```

Example for `mark_message_delivered`:

```python
def mark_message_delivered(self, msg_id: int) -> None:
    self._run_tx(self._impl_mark_message_delivered, msg_id)

def _impl_mark_message_delivered(self, msg_id: int) -> None:
    self._conn.execute(
        "UPDATE messages SET status='delivered' WHERE id=?", (msg_id,),
    )
```

- [ ] **Step 5: Refactor the readers**

For each of `get_pending_messages_for`, `get_distinct_pending_recipients`, `find_message_by_email_id`, `get_message`, `get_last_email_message_id_for_agent`, `get_reply_to_message`:

```python
def <name>(self, *args):
    return self._read(self._impl_<name>, *args)

def _impl_<name>(self, *args):
    # original body — unchanged
```

- [ ] **Step 6: Verify file length** — `src/chat_db.py` was 170 lines pre-refactor. Doubling the public-method count (wrapper + `_impl_`) adds roughly +60 lines, putting it near 230. **If the file exceeds 200 lines, extract `_impl_*` methods into a new mixin file** (e.g. `src/chat_db_impls.py`) that ChatDB inherits from. Surface this in the commit body.

Run: `wc -l src/chat_db.py`
If > 200: extract before committing.

- [ ] **Step 7: Run the full suite — every existing ChatDB test must pass**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count as before this task. The refactor must not change observable behaviour — `_nudge_wake` now fires after commit (was after commit anyway since `_log_event` committed first), and tests don't observe the intermediate state.

- [ ] **Step 8: Commit**

```bash
git add src/chat_db.py src/chat_db_impls.py tests/test_chat_db.py
git commit -m "$(cat <<'EOF'
refactor(chat_db): route every writer through _run_tx, every reader through _read

Each public method becomes a thin wrapper that calls
_run_tx(self._impl_X) or _read(self._impl_X). _log_event splits into
a top-level entry that opens its own tx + an _impl_log_event that
nests safely inside another _run_tx. insert_message moves
_nudge_wake into the _after_commit hook list so it fires only on
successful commit.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Refactor `AgentRegistryMixin`

**Files:**
- Modify: `src/agent_registry.py`

Apply the canonical pattern to every method. `register_agent`'s manual `BEGIN IMMEDIATE` + try/rollback flow is the most complex; it folds cleanly into `_run_tx` because the body has exactly the one-transaction shape `_run_tx` enforces.

- [ ] **Step 1: Refactor `register_agent`**

Before (current `src/agent_registry.py:28-84`):
```python
def register_agent(self, name, project_path, pid=None) -> dict:
    now = _now()
    insert_sql = (...)
    insert_args = (...)
    if pid is not None:
        try:
            self._conn.rollback()
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass
        try:
            existing = self.get_agent(name)
            if (existing and existing["pid"] is not None
                and existing["pid"] != pid
                and is_alive(existing["pid"])):
                raise AgentNameTaken(name, existing["pid"])
            self._conn.execute(insert_sql, insert_args)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
    else:
        self._conn.execute(insert_sql, insert_args)
        self._conn.commit()
    self._log_event(name, "register", f"Agent {name} registered")
    recovered = self.recover_failed_messages_for(name)
    if recovered > 0:
        self._log_event(
            name, "messages_recovered",
            f"Recovered {recovered} failed messages on re-register",
        )
    return self.get_agent(name)
```

After:
```python
def register_agent(self, name, project_path, pid=None) -> dict:
    return self._run_tx(
        self._impl_register_agent, name, project_path, pid,
    )

def _impl_register_agent(self, name, project_path, pid) -> dict:
    now = _now()
    insert_sql = (
        "INSERT INTO agents (name, project_path, status, pid, "
        "registered_at, last_seen_at) "
        "VALUES (?, ?, 'running', ?, ?, ?) "
        "ON CONFLICT(name) DO UPDATE SET "
        "  project_path=excluded.project_path, "
        "  status='running', "
        "  pid=COALESCE(excluded.pid, agents.pid), "
        "  last_seen_at=excluded.last_seen_at"
    )
    insert_args = (name, project_path, pid, now, now)
    if pid is not None:
        existing = self._impl_get_agent(name)
        if (existing and existing["pid"] is not None
            and existing["pid"] != pid
            and is_alive(existing["pid"])):
            raise AgentNameTaken(name, existing["pid"])
    self._conn.execute(insert_sql, insert_args)
    self._impl_log_event(name, "register", f"Agent {name} registered")
    recovered = self._impl_recover_failed_messages_for(name)
    if recovered > 0:
        self._impl_log_event(
            name, "messages_recovered",
            f"Recovered {recovered} failed messages on re-register",
        )
    return self._impl_get_agent(name)
```

The manual `BEGIN IMMEDIATE` + nested try/rollback collapses to a single `_run_tx` call: if any sub-step raises, `_run_tx`'s exception handler rolls back the entire transaction.

- [ ] **Step 2: Refactor the remaining writers**

`update_agent_status`, `update_agent_pid`, `touch_agent`, `_disconnect`, `reap_dead_agents` — same pattern: split into `<name>` (thin wrapper) + `_impl_<name>` (body without `.commit()`).

`reap_dead_agents` is internally chatty (loops over rows, calls `_disconnect` which calls `update_agent_status` + `_log_event`). After refactor, the outer `_run_tx` for `reap_dead_agents` wraps ALL of these; the nested calls go to the `_impl_` variants:

```python
def reap_dead_agents(self, no_pid_idle_secs=DEFAULT_AGENT_FRESHNESS_SEC) -> list[str]:
    return self._run_tx(self._impl_reap_dead_agents, no_pid_idle_secs)

def _impl_reap_dead_agents(self, no_pid_idle_secs) -> list[str]:
    reaped: list[str] = []
    for row in self._conn.execute(
        "SELECT name, pid FROM agents WHERE pid IS NOT NULL AND status='running'"
    ).fetchall():
        if not is_alive(row["pid"]):
            self._impl_disconnect(row["name"], f"PID {row['pid']} no longer running")
            reaped.append(row["name"])
    for row in self._conn.execute(
        "SELECT name FROM agents "
        "WHERE pid IS NULL AND status='running' AND last_seen_at < ?",
        (_cutoff(no_pid_idle_secs),),
    ).fetchall():
        self._impl_disconnect(row["name"], "no PID, idle past freshness window")
        reaped.append(row["name"])
    return reaped

def _impl_disconnect(self, name, why) -> None:
    self._impl_update_agent_status(name, "disconnected")
    self._impl_log_event(name, "disconnect", f"Agent {name} {why}")
```

- [ ] **Step 3: Refactor the readers**

`find_live_owner`, `get_agent`, `list_agents`, `find_live_agent_for_project`, `agent_status_for_project` — wrap with `_read(self._impl_<name>)`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count, all pass.

- [ ] **Step 5: Verify line count**

Run: `wc -l src/agent_registry.py`
Was 199 (right at the cap). If over 200 after the refactor: split into `agent_registry_impls.py` along the same line as Task 4 if needed.

- [ ] **Step 6: Commit**

```bash
git add src/agent_registry.py
git commit -m "$(cat <<'EOF'
refactor(agent_registry): route writers/readers through _run_tx/_read

register_agent's manual BEGIN IMMEDIATE + try/rollback chain folds
into _run_tx. reap_dead_agents + _disconnect now use _impl_ variants
so the whole sweep runs as one atomic transaction. Every public
reader migrates to _read.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Refactor remaining mixin files

**Files:**
- Modify: `src/agent_state.py`, `src/dashboard_queries.py`, `src/db_maintenance.py`, `src/outbound_emails_store.py`, `src/wake_session_store.py`

Each file is small (5–35 lines of method bodies). Apply the canonical pattern uniformly.

- [ ] **Step 1: `src/wake_session_store.py`**

```python
class WakeSessionStoreMixin:
    def get_wake_session(self, agent_name: str) -> dict | None:
        return self._read(self._impl_get_wake_session, agent_name)

    def _impl_get_wake_session(self, agent_name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM wake_sessions WHERE agent_name=?", (agent_name,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_wake_session(self, agent_name: str, session_id: str) -> None:
        self._run_tx(self._impl_upsert_wake_session, agent_name, session_id)

    def _impl_upsert_wake_session(self, agent_name, session_id) -> None:
        self._conn.execute(
            """INSERT INTO wake_sessions (agent_name, session_id, last_turn_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent_name) DO UPDATE SET
                 session_id=excluded.session_id,
                 last_turn_at=excluded.last_turn_at""",
            (agent_name, session_id, _now()),
        )

    def delete_wake_session(self, agent_name: str) -> None:
        self._run_tx(self._impl_delete_wake_session, agent_name)

    def _impl_delete_wake_session(self, agent_name: str) -> None:
        self._conn.execute(
            "DELETE FROM wake_sessions WHERE agent_name=?", (agent_name,),
        )
```

- [ ] **Step 2: `src/db_maintenance.py`**

```python
class MaintenanceMixin:
    def cleanup_old(self, days: int = 30) -> dict:
        return self._run_tx(self._impl_cleanup_old, days)

    def _impl_cleanup_old(self, days: int) -> dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        m = self._conn.execute(
            "DELETE FROM messages WHERE status IN ('delivered','failed') "
            "AND created_at < ?",
            (cutoff,),
        ).rowcount
        e = self._conn.execute(
            "DELETE FROM events WHERE created_at < ?", (cutoff,)
        ).rowcount
        o = self._conn.execute(
            "DELETE FROM outbound_emails WHERE sent_at < ?", (cutoff,)
        ).rowcount
        return {"messages": m, "events": e, "outbound_emails": o}
```

- [ ] **Step 3: `src/outbound_emails_store.py`**

```python
class OutboundEmailsMixin:
    def record_outbound_email(
        self, email_message_id: str, *, kind: str, sender_agent: str = "",
        task_id: int | None = None,
    ) -> None:
        if not email_message_id:
            raise ValueError("email_message_id must not be empty")
        self._run_tx(
            self._impl_record_outbound_email,
            email_message_id, kind, sender_agent, task_id,
        )

    def _impl_record_outbound_email(
        self, email_message_id, kind, sender_agent, task_id,
    ) -> None:
        self._conn.execute(
            "INSERT INTO outbound_emails "
            "(email_message_id, sent_at, kind, sender_agent, task_id) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(email_message_id) DO UPDATE SET "
            "task_id = COALESCE(outbound_emails.task_id, excluded.task_id)",
            (email_message_id, _now(), kind, sender_agent or None, task_id),
        )

    def find_outbound_email(self, email_message_id: str) -> dict | None:
        return self._read(self._impl_find_outbound_email, email_message_id)

    def _impl_find_outbound_email(self, email_message_id) -> dict | None:
        if not email_message_id:
            return None
        row = self._conn.execute(
            "SELECT * FROM outbound_emails WHERE email_message_id=?",
            (email_message_id,),
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: `src/agent_state.py` and `src/dashboard_queries.py`**

Both are read-only. Wrap every method with `_read(self._impl_<name>)`.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count, all pass.

- [ ] **Step 6: Commit**

```bash
git add src/agent_state.py src/dashboard_queries.py src/db_maintenance.py src/outbound_emails_store.py src/wake_session_store.py
git commit -m "$(cat <<'EOF'
refactor(mixins): route every remaining ChatDB mixin through _run_tx/_read

agent_state, dashboard_queries, db_maintenance, outbound_emails_store,
wake_session_store — each public method becomes a thin wrapper around
_run_tx or _read. _impl_ private methods hold the original body
(minus the inline commit() calls).

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Migrate `src/status_envelope.py` to ChatDB methods

**Files:**
- Modify: `src/chat_db.py` (add three new methods)
- Modify: `src/status_envelope.py` (delegate)
- Tests: existing `tests/test_status_envelope.py` should pass without modification.

`src/status_envelope.py` currently uses `db._conn.execute(...)` + `db._conn.commit()` in three places. Move the persistence into ChatDB so the wrapper owns the transaction.

- [ ] **Step 1: Add `ChatDB.emit_status_message`**

```python
def emit_status_message(
    self, *, task_id: int, status: str, agent_name: str,
    body: str, content_type: str,
) -> dict | None:
    """Atomic dedup-mark + insert_message for status envelope traffic.

    Returns the inserted message row, or None if dedup skipped the send
    (current last_sent_status already equals `status` for this task).
    """
    return self._run_tx(
        self._impl_emit_status_message,
        task_id, status, agent_name, body, content_type,
    )

def _impl_emit_status_message(
    self, task_id, status, agent_name, body, content_type,
) -> dict | None:
    row = self._conn.execute(
        "SELECT last_sent_status FROM tasks WHERE id=?", (task_id,),
    ).fetchone()
    if row is None or row["last_sent_status"] == status:
        return None
    self._conn.execute(
        "UPDATE tasks SET last_sent_status=? WHERE id=?", (status, task_id),
    )
    cur = self._conn.execute(
        """INSERT INTO messages (from_name, to_name, body, type, status,
                                 created_at, content_type, task_id)
           VALUES (?, 'user', ?, 'notify', 'pending', ?, ?, ?)""",
        (agent_name, body, _now(), content_type or None, task_id),
    )
    self._impl_log_event(
        agent_name, "status_emit", f"emit_status({task_id}, {status})",
    )
    inserted = self._conn.execute(
        "SELECT * FROM messages WHERE id=?", (cur.lastrowid,),
    ).fetchone()
    self._after_commit.append(self._nudge_wake)
    return dict(inserted)
```

- [ ] **Step 2: Add `ChatDB.clear_status_dedup` and `clear_status_dedup_for_project`**

```python
def clear_status_dedup(self, task_id: int) -> None:
    self._run_tx(self._impl_clear_status_dedup, task_id)

def _impl_clear_status_dedup(self, task_id: int) -> None:
    self._conn.execute(
        "UPDATE tasks SET last_sent_status=NULL "
        "WHERE id=? AND last_sent_status IS NOT NULL",
        (task_id,),
    )

def clear_status_dedup_for_project(self, project_path: str) -> None:
    self._run_tx(self._impl_clear_status_dedup_for_project, project_path)

def _impl_clear_status_dedup_for_project(self, project_path: str) -> None:
    self._conn.execute(
        "UPDATE tasks SET last_sent_status=NULL "
        "WHERE project_path=? AND status IN ('pending','running') "
        "AND last_sent_status IS NOT NULL",
        (project_path,),
    )
```

- [ ] **Step 3: Update `src/status_envelope.py` to call the new methods**

Replace the three direct-`_conn` blocks with calls to `db.emit_status_message(...)`, `db.clear_status_dedup(...)`, `db.clear_status_dedup_for_project(...)`. Drop the `# noqa: SLF001` markers and the inline SQL — the envelope module becomes a thin builder of body + content_type that hands off to ChatDB.

The exact rewrite preserves the existing function signatures (`emit_status`, `clear_status_dedup`, `clear_status_dedup_for_project`) so callers don't change.

Example for `emit_status` (the most complex one):

```python
def emit_status(
    db, *, task_id: int, status: str, agent_name: str, project_path: str,
) -> None:
    """Insert a status message + advance last_sent_status atomically.

    Skips when status is unchanged (deduped). See ChatDB.emit_status_message
    for the atomic persistence."""
    body, content_type = build_status_body(db, task_id, status)
    row = db.emit_status_message(
        task_id=task_id, status=status, agent_name=agent_name,
        body=body, content_type=content_type,
    )
    if row is not None:
        logger.info(
            "Emitted status %s for task %d to %s",
            status, task_id, agent_name,
        )
```

(The exact body of `build_status_body` already exists in the envelope module — leave it alone.)

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count, all pass.

- [ ] **Step 5: Verify the latent silent-drop bug is closed**

Add a regression test in `tests/test_status_envelope.py`:

```python
def test_emit_status_atomic_on_insert_failure(tmp_path, monkeypatch):
    """If insert_message raises, last_sent_status must NOT advance —
    rollback closes the previous-design silent-drop window."""
    from src.chat_db import ChatDB
    from src.status_envelope import emit_status
    db = ChatDB(str(tmp_path / "a.db"))
    db._conn.execute(
        "INSERT INTO tasks (id, project_path, body, created_at, last_sent_status) "
        "VALUES (1, '/p', 'b', '2026-05-19T00:00:00+00:00', NULL)"
    )
    db._conn.commit()
    real_execute = db._conn.execute
    def fail_on_insert(sql, *args, **kwargs):
        if sql.strip().upper().startswith("INSERT INTO MESSAGES"):
            raise RuntimeError("forced insert failure")
        return real_execute(sql, *args, **kwargs)
    monkeypatch.setattr(db._conn, "execute", fail_on_insert)
    with pytest.raises(RuntimeError):
        emit_status(
            db, task_id=1, status="waiting-on-peer",
            agent_name="agent-a", project_path="/p",
        )
    # last_sent_status was never advanced.
    monkeypatch.undo()
    row = db._conn.execute(
        "SELECT last_sent_status FROM tasks WHERE id=1"
    ).fetchone()
    assert row["last_sent_status"] is None
```

- [ ] **Step 6: Commit**

```bash
git add src/chat_db.py src/status_envelope.py tests/test_status_envelope.py
git commit -m "$(cat <<'EOF'
refactor(status_envelope): persist via ChatDB.emit_status_message + cousins

The previous design committed the dedup UPDATE before insert_message,
opening a silent-drop window if the insert raised: last_sent_status
would advance but no message would land, so the next call would skip
the user-facing notify entirely. Moving both writes inside a single
_run_tx closes the race.

Three new ChatDB methods (emit_status_message, clear_status_dedup,
clear_status_dedup_for_project) take ownership; src/status_envelope.py
becomes a thin envelope builder.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Migrate `chat_relay.py` / `origin_envelope.py` / `relay_routing.py` to ChatDB readers

**Files:**
- Modify: `src/chat_db.py` (add new read methods)
- Modify: `src/chat_relay.py` (2 sites)
- Modify: `src/origin_envelope.py` (1 site)
- Modify: `src/relay_routing.py` (3 sites)

Each external read becomes a new ChatDB read method going through `_read`. Naming convention: name each method by what it returns (`lookup_origin_envelope_v(...)` rather than `_conn_query_X`).

- [ ] **Step 1: Audit the call-sites**

Run: `grep -n "_conn\.execute" src/chat_relay.py src/origin_envelope.py src/relay_routing.py`

Expected sites:
- `src/chat_relay.py:53`, `src/chat_relay.py:58` — relay-routing reads.
- `src/origin_envelope.py:23` — origin envelope version lookup.
- `src/relay_routing.py:19`, `src/relay_routing.py:40`, `src/relay_routing.py:62` — routing decision reads.

- [ ] **Step 2: For each call-site, read the existing SQL and add a corresponding ChatDB method**

Each method follows the same shape:

```python
def lookup_<purpose>(self, <args>) -> <returntype>:
    return self._read(self._impl_lookup_<purpose>, <args>)

def _impl_lookup_<purpose>(self, <args>) -> <returntype>:
    row = self._conn.execute("<exact SQL from current site>", (<args>,)).fetchone()
    return dict(row) if row else None
```

Concrete names to use (pick based on the SQL each site runs):
- `src/chat_relay.py` → `chatdb.lookup_relay_target(in_reply_to)` (returns `dict | None`) and `chatdb.lookup_relay_origin(email_message_id)` (returns `dict | None`).
- `src/origin_envelope.py` → `chatdb.lookup_origin_envelope_version(task_id)` (returns `int | None`).
- `src/relay_routing.py` → `chatdb.lookup_routing_target(...)` × 3 (one per call-site; name each by what the SQL is filtering on).

Each method ships with a unit test in `tests/test_chat_db.py` (or a new `tests/test_chat_db_read_helpers.py` if existing test file is at the line cap) that asserts the read returns the same shape the inline SQL produced.

- [ ] **Step 3: Update each call-site to delegate**

Replace each `chat_db._conn.execute(...)` block with the new ChatDB method call. Drop the `# noqa: SLF001` markers.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest tests/ -q`
Expected: same count, all pass.

- [ ] **Step 5: Grep for residual direct-`_conn` access**

Run: `grep -rn "_conn\.execute" src/ | grep -v "src/chat_db\.py\|src/chat_db_tx\.py\|src/agent_registry\.py\|src/agent_state\.py\|src/dashboard_queries\.py\|src/db_maintenance\.py\|src/outbound_emails_store\.py\|src/wake_session_store\.py"`

Expected: only `src/task_queue.py` remains (explicitly out-of-scope per spec).

- [ ] **Step 6: Commit**

```bash
git add src/chat_db.py src/chat_relay.py src/origin_envelope.py src/relay_routing.py tests/
git commit -m "$(cat <<'EOF'
refactor: route relay/envelope/routing reads through ChatDB._read

Adds lookup_* read methods to ChatDB and migrates the four external
modules that previously reached into db._conn directly. Every read in
src/ (except TaskQueue, out of scope per spec) now goes through the
RLock-guarded wrapper.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Integration smokes + regression tests

**Files:**
- Test: `tests/test_chat_db_tx.py` (extend with `TestPhase1Integration`)

End-to-end verification of the Phase 1 wrappers acting through real ChatDB methods after all refactors land.

- [ ] **Step 1: Add integration tests** — append to `tests/test_chat_db_tx.py`

```python
class TestPhase1Integration:
    def test_concurrent_writers_serialise(self, tmp_path):
        """Two threads hammering insert_message must observe N inserts,
        not lost writes — the RLock serialises them."""
        from src.chat_db import ChatDB
        import threading as _t
        db = ChatDB(str(tmp_path / "a.db"))
        db.register_agent("agent-a", "/p1")
        db.register_agent("agent-b", "/p2")
        N = 20
        def hammer(name):
            for i in range(N):
                db.insert_message(name, "user", f"msg-{i}", "notify")
        threads = [
            _t.Thread(target=hammer, args=("agent-a",)),
            _t.Thread(target=hammer, args=("agent-b",)),
        ]
        for t in threads: t.start()
        for t in threads: t.join(timeout=10)
        count = db._conn.execute(
            "SELECT COUNT(*) FROM messages WHERE type='notify'",
        ).fetchone()[0]
        assert count == 2 * N

    def test_real_method_retries_after_sidecar_release(self, tmp_path):
        from src.chat_db import ChatDB
        from tests._tx_fixtures import sidecar_writer_lock, narrow_busy_timeout
        import threading as _t
        import time as _time
        path = str(tmp_path / "a.db")
        db = ChatDB(path)
        db.register_agent("agent-a", "/p1")
        narrow_busy_timeout(db, 50)
        with sidecar_writer_lock(path) as release:
            result_holder = {}
            def run():
                try:
                    row = db.insert_message("agent-a", "user", "hi", "notify")
                    result_holder["row"] = row
                except Exception as exc:
                    result_holder["err"] = exc
            t = _t.Thread(target=run)
            t.start()
            _time.sleep(0.2)
            release.set()
            t.join(timeout=10)
        assert "row" in result_holder
        assert result_holder["row"]["body"] == "hi"

    def test_stale_tx_on_register_agent_recovers(self, tmp_path, caplog):
        """Simulate the 2026-05-19 wedge: a stale in_transaction state
        on the shared connection. register_agent's entry must clear it
        via _check_or_recover_at_depth_zero rather than failing."""
        import logging as _logging
        from src.chat_db import ChatDB
        caplog.set_level(_logging.WARNING, logger="src.chat_db_tx")
        db = ChatDB(str(tmp_path / "a.db"))
        db._conn.execute("BEGIN")
        db._conn.execute("CREATE TABLE _filler (id INTEGER)")
        assert db._conn.in_transaction is True
        agent = db.register_agent("agent-a", "/p1")
        assert agent["name"] == "agent-a"
        warnings = [r for r in caplog.records
                    if "kind=stale_tx" in r.getMessage()]
        assert warnings

    def test_insert_message_nudge_wake_fires_after_commit(self, tmp_path):
        """_nudge_wake moves to _after_commit so it fires AFTER the
        transaction commits and the RLock releases — never on rollback,
        never before the row is durable."""
        from src.chat_db import ChatDB
        import threading as _t
        db = ChatDB(str(tmp_path / "a.db"))
        evt = _t.Event()
        db.set_wake_nudge(evt)
        db.register_agent("agent-a", "/p1")
        db.insert_message("agent-a", "user", "hi", "notify")
        assert evt.is_set()
```

- [ ] **Step 2: Run the integration tests**

Run: `.venv/bin/pytest tests/test_chat_db_tx.py::TestPhase1Integration -v`
Expected: 4/4 pass.

- [ ] **Step 3: Run the full suite + line-limit gate + grep for stragglers**

```bash
.venv/bin/pytest tests/ -q
scripts/check-line-limit.sh
grep -rn "_conn\.execute" src/ | grep -v "src/chat_db\.py\|src/chat_db_tx\.py\|src/agent_registry\.py\|src/agent_state\.py\|src/dashboard_queries\.py\|src/db_maintenance\.py\|src/outbound_emails_store\.py\|src/wake_session_store\.py\|src/task_queue\.py"
```

Expected:
- All tests pass.
- Line-limit script exits 0.
- The grep returns NOTHING (all external modules migrated).

- [ ] **Step 4: Commit**

```bash
git add tests/test_chat_db_tx.py
git commit -m "$(cat <<'EOF'
test(chat_db_tx): Phase 1 integration smokes

Closes Phase 1: concurrent writers serialise correctly, retry-on-locked
works against a real ChatDB method, stale_tx recovery handles the
2026-05-19 wedge pattern, _nudge_wake fires after commit.

Spec: docs/superpowers/specs/2026-05-19-chatdb-tx-wrapper-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Phase 1 complete — handoff

After Task 9 lands:

- **The bus self-heals**: a leaked implicit transaction on the shared connection clears at the next public-method entry via `_check_or_recover_at_depth_zero`. Cross-process WAL contention surfaces as a 200 ms wait → `OperationalError` → automatic retry; the retry budget is bounded.
- **Trace probe still installs** behind `CHAT_DB_TRACE=1` from Phase 0 — handy if a new kind of leak shows up.
- **TaskQueue is still out of scope** by design; spec carries the follow-up.

### Deploy

1. Merge `chatdb-tx-phase-1-refactor` into master.
2. Restart claude-email (`systemctl --user restart claude-email.service`).
3. Restart claude-chat (`systemctl --user restart claude-chat.service`) — severs peer MCP sessions per CLAUDE.md; peers reconnect on next request.

### Verification before completion (per `superpowers:verification-before-completion`)

- [ ] `wc -l src/chat_db.py src/chat_db_tx.py src/agent_registry.py` — all ≤ 200 (or extracted into helper modules if not).
- [ ] `.venv/bin/pytest tests/ -q` — all pass.
- [ ] `scripts/check-line-limit.sh` — no violations.
- [ ] `grep -rn "_conn\.execute" src/` — only `src/task_queue.py` and the `_impl_*` bodies remain.
- [ ] 100% production-code coverage retained.
