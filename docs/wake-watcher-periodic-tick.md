# Wake watcher: per-agent periodic tick (post-demo task)

**Status:** IMPLEMENTED 2026-06-10 (deploy = service restart at a calm window,
then `scripts/set-agent-tick.py GPT 300`) · **Created:** 2026-06-10
**Owner note:** written by agent-max during the SwanReady demo crunch.

## Problem (observed 2026-06-10)

Agents without a live interactive session (e.g. `GPT`, `pid=None`) are driven
solely by the wake watcher, which spawns a `claude --print` turn **only when a
pending bus message exists** (`process_agent` early-returns on empty inbox).

Consequences observed in production:

- An agent consumes its assignment message, starts working, and the turn ends
  (time-bounded). With the inbox now empty, **nothing ever wakes it again** —
  it stalls silently with uncommitted work (`GPT` sat on 12→33 dirty files,
  zero commits, until a coordinator manually messaged it).
- After idle-expiry the agent is disconnected and re-registered; the next wake
  runs `resume=False` with **zero context**.
- Workaround in use: a coordinator agent (agent-max) polls the worktree from
  its own session-cron and sends self-contained "CONTINUE" briefs — each
  message forcing one wake turn. Works, but couples liveness to a human-run
  coordinator session.

## Fix (~30 lines)

Add an optional per-agent periodic tick so the watcher wakes an agent on a
schedule even with an empty inbox.

1. **Schema:** `agents` table gains `tick_secs INTEGER NULL` (NULL = current
   behavior, no tick). Migration in `src/chat_db.py` bootstrap.
2. **Loop:** in `run_wake_watcher`, alongside the pending-message scan, select
   agents where `tick_secs IS NOT NULL` and
   `now - last_wake_at >= tick_secs` (track `last_wake_at` — reuse
   `wake_sessions.last_turn_at`).
3. **Turn:** call `process_agent` with a standard tick prompt instead of the
   drain prompt, e.g.:
   > `[watcher tick] No new messages. If you have an open task with
   > uncommitted work, continue it now; commit a coherent green slice before
   > expanding. If you are idle, reply the single word "quiet".`
   Bypass the `pre_ids` empty-inbox early-return for tick turns.
4. **Guards:** respect `_has_live_owner` (live sessions keep self-ticking),
   `_FailureTracker`, `rate_limit_secs`, and the per-agent lock — all already
   exist; tick turns go through the same `process_agent` path.
5. **Ops:** set via a tiny helper script or SQL
   (`UPDATE agents SET tick_secs=300 WHERE name='GPT'`). Document in README.

## Acceptance

- Agent with `tick_secs=300` and an empty inbox gets a wake turn ≤ ~5 min
  after its last turn; with `tick_secs NULL` behavior is unchanged.
- Tick turns don't fire while a turn is in flight (lock) and back off on
  repeated failures (existing tracker).
- Smoke: extend `scripts/test-wake-smoke.sh` — register dummy agent with a
  tick, post NO message, assert a wake turn happened within the window.

## Cost note

Each tick is a paid `claude --print` turn. Default to NULL; enable only for
actively-assigned worker agents, and clear (`tick_secs=NULL`) when a task
lane closes.
