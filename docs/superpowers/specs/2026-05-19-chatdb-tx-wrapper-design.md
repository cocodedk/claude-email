# ChatDB transaction wrapper — design spec

Date: 2026-05-19
Author: Babak (with robo + cursor-agent consult)
Status: Mutual-acceptance ACHIEVED (robo + cursor-agent, after round 4). Awaiting Babak's review gate before transitioning to writing-plans.

## Problem

On 2026-05-19, the bus wedged for ~1 hour. Two compounding bugs:

1. **Writer leak**: `claude-email` (PID 3277) held SQLite's `WAL_WRITE_LOCK` (shm byte 120) continuously. Proven from `/proc/locks`. Frozen WAL (2.9 MB, mtime 08:42 → 09:42) corroborates a single connection holding the writer slot.
2. **No recovery**: After we killed the holder, a sidecar Python process could write and `PRAGMA wal_checkpoint(TRUNCATE)` cleanly — but `claude-chat`'s own shared `sqlite3` connection stayed poisoned and kept returning `database is locked` until a service restart, severing every MCP session.

Both root in the same shape: `ChatDB.__init__` (`src/chat_db.py:29`) opens **one** long-lived `sqlite3.connect(path, check_same_thread=False)` per process, shared across threads/coroutines without a Python-level mutex, and individual public methods own their own `commit`/`rollback`. There is no boundary that guarantees "one logical operation = one transaction" or recovers from a poisoned in-transaction state.

## Constraints

- Single SQLite file (not negotiable in this iteration).
- 200-line cap per source file. Mixins already split.
- 1212 tests, 100% prod-code coverage. Each new/changed line must keep the report at 100%.
- TDD: failing test before each implementation step.
- Both processes (`claude-chat`, `claude-email`) use the same `ChatDB` type.
- `claude-chat` is asyncio (Starlette + MCP SSE); `claude-email` is a sync poll loop.

## Decision

**Option B with cursor-agent's twist**: centralize transaction ownership inside `ChatDB`. Reject A (MCP-tool decorator) because today's multi-commit methods (`insert_message`, `emit_status`, `register_agent`) can duplicate or silently drop on naive whole-method retry. Reject C (connection-per-call) as too much churn for this iteration; revisit if/when we still see correctness gaps after this lands.

### Components

**1. `_open_conn(path)` — connection factory**

Private helper used by both `__init__` and reopen-on-poison. Single source of truth for connection state:

- `sqlite3.connect(path, check_same_thread=False)`
- `row_factory = sqlite3.Row`
- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=200` (lowered from 5000 — see §6 budgeting)
- `PRAGMA foreign_keys=ON`
- If `os.environ.get("CHAT_DB_TRACE")` is truthy: `conn.set_trace_callback(self._trace_cb)`

Migrations and schema bootstrap stay in `__init__`; they don't need to re-run on reopen because the file is the same and migrations are idempotent.

**2. Process-local serializer**

A `threading.RLock` on each `ChatDB` instance (`self._db_lock`), guarding **every** access to `self._conn` (reads and writes). Reentrant so a public method that calls another public method (e.g. `register_agent` → `recover_failed_messages_for` → `_log_event`) does not self-deadlock.

Tradeoff (explicit): an RLock held during a SQLite call can block the Starlette event loop for the duration of that call. We bound the worst case with two levers — `busy_timeout=200ms` so a contested `BEGIN IMMEDIATE` cannot pin the loop for the full 5 seconds, and a hard rule: **no `await`, network call, subprocess spawn, or email send may run while the RLock is held.** Long-running side effects move to post-commit hooks (see §4).

We do NOT use `asyncio.Lock` — `ChatDB` is sync and is shared by sync callers (`claude-email`). One primitive, both worlds.

**3. `_run_tx(fn, *args, **kwargs)` — callable-form transaction wrapper**

Rejected the context-manager (`with self._tx(): ...`) form: a context manager cannot re-execute the yielded block, which means in-wrapper retry is impossible. Replaced with a callable form.

`fn` is a bound method or callable that takes no `conn` argument — it uses `self._conn` directly, which is already protected by the held RLock.

Behaviour on the outermost frame for this thread (`tx_depth == 0` before entry):

1. Acquire `self._db_lock` (RLock).
2. **Poison check** via shared `_check_or_recover_at_depth_zero()` (also used by `_read` — see §4): if `self._conn.in_transaction` is True at depth 0, log `lock_event kind=stale_tx`, attempt best-effort `rollback()`. If rollback raises, **close the old connection best-effort** and swap to a fresh one via `_open_conn(self.path)`; record `connection_replaced=True`. Close failures are logged separately at WARNING but do not propagate — the old fd will be cleaned by GC even if `close()` itself raises.
3. `BEGIN IMMEDIATE`.
4. `tx_depth = 1`.
5. Call `fn(*args, **kwargs)`. Collect any deferred side-effects appended to `self._after_commit`, a single instance attribute (not `threading.local()`) — safe because the held RLock guarantees only one outermost `_run_tx` is active at a time per ChatDB.
6. On clean return: `COMMIT`. Snapshot `hooks = self._after_commit[:]`, clear the list, set `tx_depth = 0`.
7. **Release the RLock**, then fire `hooks` in order outside the lock and outside any transaction context. Hook exceptions are logged (`chatdb.after_commit_hook_failed`) and otherwise swallowed — a failing hook must not undo a committed write. Return `fn`'s return value.
8. On `sqlite3.OperationalError("database is locked")`: best-effort `rollback()` (with the close-and-reopen escape from step 2), clear `_after_commit`, retry the call once. If the retry also raises, re-raise the underlying error to the caller.
9. On any other exception: `rollback()` (with the close-and-reopen escape), clear `_after_commit`, set `tx_depth = 0`, release the RLock, then re-raise outside the lock.

Nested entry (`tx_depth > 0`):

- Joins the outer transaction. No BEGIN, no COMMIT, no retry, no after-commit firing. Callable runs inline. The outer frame still owns commit and post-commit semantics.

**4. `_read(fn, *args, **kwargs)` — read helper**

Read-only counterpart. Acquires the RLock. On outermost entry (`tx_depth == 0`) it runs the same `_check_or_recover_at_depth_zero()` as `_run_tx` — a poisoned connection must not silently serve reads while preserving the wedged in-transaction state until some later write happens. Nested reads (`tx_depth > 0`, i.e. inside a write transaction) skip the poison check and run inline. After `fn` returns, releases the lock and returns the value. No transaction management beyond the shared entry guard. Every read path that today calls `self._conn.execute(...)` directly migrates to `_read(self._impl_method)` or its caller-side equivalent.

**4a. `_check_or_recover_at_depth_zero()` — shared entry guard**

Private helper invoked by both `_run_tx` and `_read` on outermost entry. If `self._conn.in_transaction` is True, log `lock_event kind=stale_tx`, attempt `rollback()`, on rollback failure close-and-reopen via `_open_conn`. After the guard runs, the connection is guaranteed to be in a clean (no-tx) state.

**5. Post-commit hooks**

Side effects that must NOT fire on rollback (e.g. `self._nudge_wake()` in `insert_message`) append to `self._after_commit: list[Callable[[], None]]` during the wrapped body — a single instance attribute, serialized by the RLock (no `threading.local()` needed; only one outermost write is active at a time per ChatDB). Hooks are **synchronous** (no `async def`, no awaitables). `_run_tx` fires them in order **after** a successful outer commit *and after releasing the RLock*. Cleared on rollback or retry. Hook exceptions are logged at WARNING (`chatdb.after_commit_hook_failed`) and otherwise swallowed — a failing hook must not undo a committed write.

**6. busy_timeout budgeting**

`PRAGMA busy_timeout=200` (down from 5000). Rationale: a contested writer normally completes in <10ms; 200ms covers SSD jitter without pinning the event loop. Genuine cross-process contention surfaces as `OperationalError("database is locked")`, which `_run_tx` retries once. Net worst-case event-loop block per logical write: ~400ms (initial 200ms BEGIN wait + retry's 200ms). If we later observe excessive retry storms, the next step is `loop.run_in_executor` for DB writes — out of scope for this spec.

**7. Leak probe — env-flagged trace callback**

`self._trace_cb(sql)`: when `CHAT_DB_TRACE=1`, log at DEBUG `thread id, conn.in_transaction, tx_depth, SQL kind (BEGIN/COMMIT/ROLLBACK/other)`. SQL parameters are NEVER logged — they can contain message bodies (cf. memory `feedback_no_real_emails_in_code`). Install via `_open_conn`.

Always-on (no env flag) WARNING events fired from `_run_tx`:

```
chatdb.lock_event method=<fn.__qualname__> kind=<locked|stale_tx|rollback_failed|retry_failed>
  conn.in_transaction=<bool> tx_depth=<int> thread=<id> connection_replaced=<bool>
```

Smoking-gun signatures:
- `kind=stale_tx` — outer entry found the conn already in-transaction. The original 2026-05-19 incident pattern.
- `kind=retry_failed` — second attempt also raised. Cross-process pressure or worse.
- `kind=rollback_failed connection_replaced=True` — connection was unrecoverable; we swapped.

Plain `kind=locked conn.in_transaction=False` followed by a successful retry is normal cross-process contention; not a leak.

### Complete writer inventory (in scope of refactor)

All call-sites currently touching `self._conn` for any reason — reads, writes, and ad-hoc commits.

`src/chat_db.py`:
- `insert_message` (write+event+nudge — post-commit)
- `get_pending_messages_for` (read)
- `claim_pending_messages_for` (write+return rows)
- `get_distinct_pending_recipients` (read)
- `mark_message_delivered` (write)
- `mark_message_failed` (write)
- `recover_failed_messages_for` (write)
- `set_email_message_id` (write)
- `find_message_by_email_id` (read)
- `get_message` (read)
- `get_last_email_message_id_for_agent` (read)
- `get_reply_to_message` (read)
- `_log_event` (write — always called inside another method, so nested)

`src/agent_registry.py`: `register_agent`, `find_live_owner`, `get_agent`, `list_agents`, `find_live_agent_for_project`, `agent_status_for_project`, `update_agent_status`, `update_agent_pid`, `reap_dead_agents`, `_disconnect`, `touch_agent`.

`src/agent_state.py`: `agent_state` reader.

`src/dashboard_queries.py`: read-only; all migrate to `_read`.

`src/db_maintenance.py`: `cleanup_old` (3 DELETEs + commit).

`src/outbound_emails_store.py`: `record_outbound_email`, `get_outbound_email`.

`src/wake_session_store.py`: `get_wake_session` (read), `upsert_wake_session` (write), `delete_wake_session` (write).

**External modules touching `db._conn` directly** (all violate the "every `_conn` access goes through the wrapper" rule and must migrate):

- `src/status_envelope.py` — `emit_status` (update+insert), `clear_status_dedup`, `clear_status_dedup_for_project`. Refactor: introduce three ChatDB methods (`emit_status_message`, `clear_status_dedup`, `clear_status_dedup_for_project`) that own the dedup-update + insert pattern as a single `_run_tx`. `src/status_envelope.py` becomes a thin builder of the envelope body that delegates persistence to ChatDB. This also closes the latent silent-drop race documented in `src/status_envelope.py:71-79` (dedup mark committed before insert).
- `src/chat_relay.py` — two direct reads (`chat_db._conn.execute(...)` at lines 53 and 58). Refactor: add the corresponding read methods on ChatDB (e.g. `should_relay_message`, `lookup_relay_target`) and route through `_read`.
- `src/origin_envelope.py` — one direct read at line 23. Refactor: add a ChatDB read method (e.g. `lookup_origin`) and route through `_read`.
- `src/relay_routing.py` — three direct reads at lines 19, 40, 62. Refactor: add ChatDB read methods named after each routing decision and route through `_read`.

**Out of scope for this spec** (same pattern, separate follow-up):

- `src/task_queue.py` (`TaskQueue` class) — opens its own `sqlite3.Connection` to the same file. Has the same fragility. A follow-up spec applies an analogous `_run_tx`/`_read`/RLock pattern. The trace callback in this spec sees only `ChatDB` traffic; TaskQueue traffic remains untraced until the follow-up.

## Rollout

**Phase 0 — Probe only.** Add `_db_lock` (unused placeholder), `_open_conn`, env-flagged `_trace_cb` (installed on the connection via `_open_conn`). Do NOT add `_run_tx`/`_read` yet, do NOT refactor methods, do NOT emit `lock_event` yet (its emission site is `_run_tx`, which lives in Phase 1). Every existing `_conn` access still works as today. The probe shows up on real traffic via the SQL trace callback only when `CHAT_DB_TRACE=1`. Shippable independently — gives us SQL-level observability without touching any caller.

**Phase 1 — Refactor + retry.** Introduce `_run_tx`, `_read`, `_check_or_recover_at_depth_zero`, and the `lock_event` WARNING emitter. Refactor every method in the inventory above so reads go through `_read` and writes go through `_run_tx(self._impl_xxx)` with `_impl_xxx` private. Move `_nudge_wake` to `_after_commit`. Introduce the three ChatDB methods replacing `src/status_envelope.py` direct-`_conn` writes; update the envelope module to call them. Retry is automatically active once `_run_tx` lands — no separate flag.

**Semantic change introduced by Phase 1**: `_log_event` and other "always-called-from-another-method" writes today each issue their own `commit()`. After refactor they participate in the outer transaction and roll back atomically if the surrounding operation fails. This means an event that previously persisted independently (e.g. a log line for an insert that errored on a follow-up step) will no longer be visible after a failure. We accept this — atomic event logging matches the surrounding write is the desirable invariant, and no current call-site relies on the orphan-event behaviour.

**Test fixture for retry path**: a helper that opens its own sidecar `sqlite3` connection, runs `BEGIN IMMEDIATE` against the test DB, and releases on signal. Tests that exercise retry configure the wrapped ChatDB's connection with `busy_timeout=50ms` so the held lock causes a real `OperationalError`, then release the sidecar between attempt 1 and attempt 2 so retry succeeds. Without lowering the test-side `busy_timeout`, the held lock would just block then succeed — retry would never fire.

## Testing strategy

- **Phase 0 tests**: assert (a) trace callback fires only when `CHAT_DB_TRACE=1`, (b) trace callback never logs parameters. (The `stale_tx` smoking-gun warning is emitted from `_run_tx`/`_read`, which don't exist until Phase 1 — its test moves to Phase 1 below. Phase 0 ships observability only.)
- **Phase 1 lock tests**: assert (a) the RLock serializes concurrent `_run_tx` callers from different threads, (b) `_read` and `_run_tx` mutually exclude, (c) nested `_run_tx` does not deadlock and the outer commit governs.
- **Phase 1 retry tests** (using the sidecar fixture + reduced `busy_timeout`): for each refactored writer, assert the method completes cleanly when the first attempt hits a held lock and the second attempt succeeds — and that DB state matches the single-success case (no duplicate messages/events, no half-applied status mark, post-commit hooks fire once).
- **Phase 1 poisoned-conn test**: simulate `stale_tx` (start a transaction via raw `db._conn.execute("BEGIN")` then call a `_run_tx` method) and assert recovery succeeds, with `lock_event stale_tx` logged once. Repeat for `_read` to verify the shared `_check_or_recover_at_depth_zero` guard covers both code paths.
- **Phase 1 close-on-replace test**: monkeypatch `_conn.close` to record calls; trigger the rollback-failed path; assert the old conn's `close()` was attempted exactly once and `lock_event connection_replaced=True` logged.
- **Phase 1 hooks-outside-lock test**: append a hook that calls another `_run_tx` method against the same ChatDB; assert the inner call acquires the RLock fresh (depth 0, not nested) and that the inner write commits independently.
- **Phase 1 reopen test**: monkeypatch `_conn.rollback` to raise; assert the connection is replaced via `_open_conn`, the wrapped method succeeds, `connection_replaced=True` logged.
- **Phase 1 after-commit test**: assert `_after_commit` callbacks fire after a successful commit, and do NOT fire when the wrapped body raises.
- **All 1212 existing tests** must continue to pass without modification. Any required test fixture change (e.g., teardown to ensure no leftover transactions between tests) is itself a finding worth flagging in the PR.

## Risks / non-goals

- **Not switching to connection-per-call.** Option C remains the structural fix. Revisit only if Phase 1 still leaks lock state in production.
- **TaskQueue is out of scope.** It can independently leak the writer slot. The trace probe will not see TaskQueue traffic. Plan a follow-up spec.
- **Holding RLock across awaitables is banned by convention, not enforced.** If we observe a violation, add a runtime sentinel (e.g. an asyncio context-var check inside `_run_tx`).
- **Cross-process WAL contention** is not eliminated — only made recoverable on our side. The retry budget caps per-call latency.
- **`CHAT_DB_TRACE` is debug-only.** Never enable in shared environments without verifying log destinations are private. Document in operations notes.
- **Test runtime impact**: lock + retry tests add wall-clock to a small set of Phase 1 tests. Acceptable as long as full suite stays under 60s on developer machines.

## Out of scope

- Replacing SQLite with another store.
- Per-tool retry decorators in `chat/tools.py` (rejected option A).
- Refactoring `claude-email`'s `main.py` poll loop. Phase 1 changes flow through automatically because both processes share `ChatDB`.
- `TaskQueue` refactor (follow-up spec).
