# e2e: concurrent and duplicated real delivery

`tests/e2e/test_concurrency.py` — 13 tests, all `e2e`-marked, all against the
real stack: GreenMail in docker, real SMTP and IMAP sockets, the real `gpg`
binary, real `main.py` processes, the real SQLite bus. The only stand-in is the
`claude` CLI, a third-party program *outside* the product, reached by a real
fork/exec from the real `src/executor.py`.

## What it asserts

> Two commands delivered simultaneously; the same nonce delivered twice in
> parallel; the same message delivered twice by the mail server (real IMAP
> re-delivery). Exactly-one-execution semantics hold under all three.

Three concurrency shapes, one property:

| Shape | Delivery | Expected effect |
|---|---|---|
| Two distinct commands, simultaneous | two independent SMTP sessions, one barrier | both run, exactly once each |
| Same credential twice, in parallel | one signature, two different `Message-ID`s | exactly one run |
| Same message twice, in parallel | byte-identical, one `Message-ID`, two copies really in the mailbox | exactly one run |

Everything is counted as an **effect** — lines in an append-only ledger the CLI
stand-in writes, `[Running]` / `[Result]` mails threaded on the inbound
`Message-ID`, rows on the bus — never as a log line saying "duplicate refused".

## Why the deliveries have to be genuinely concurrent

`EmailPoller.fetch_unseen` holds two barriers, and only one of them is
reachable sequentially:

* the persisted `STATE_FILE` set, which catches a duplicate arriving in a
  *later* poll cycle — that is what `docs/e2e-replay.md` covers;
* the in-memory `batch` set, consulted in the same two `if` statements, which
  is the only thing between a duplicate and a second execution when both copies
  are fetched in the **same** cycle.

A sequential test can never reach the second one: by the time the duplicate is
sent, the first copy has been marked processed and the persisted store answers
first. So this file *forces* the batch.

## Forcing the batch, and proving it was forced

A blocking command is delivered first; the CLI stand-in sleeps for
`BLOCK_SECONDS` (12s, against a `CLAUDE_TIMEOUT` of 30s) inside it, writing a
`start` record with a wall-clock stamp before it sleeps and an `end` record
after. While the poller is stuck in that `execute_command` — it is single
threaded and cannot issue another `SEARCH` — six messages are pushed onto the
wire from six threads released by one `threading.Barrier`. Each thread opens and
authenticates its own SMTP session *before* the barrier, so the only thing
inside the timed window is `sendmail`.

The batch is then asserted rather than assumed:

* every send *started* before any send *finished* (`Delivery` records);
* every message was already in the polled mailbox before the blocker's `end`
  stamp, confirmed over IMAP with `SEARCH HEADER Message-ID`, which reads no
  body and sets no flags. `FETCH (RFC822)` would set `\Seen` and the poller
  searches `UNSEEN`, so a mid-flight fetch would silently eat the messages
  under test; the full-inbox snapshot is taken only once the run is over.

If that window assertion ever fails, the run is reported as proving nothing
about the in-batch barrier — it does not quietly fall back to the persisted
store and pass.

## Two credential regimes, because the guards do not cover the same routes

`fetch_unseen` checks the `Message-ID` first (`src/poller.py:157`) and the
content-bound replay key second (`src/poller.py:167`). For a **signed**
byte-identical duplicate the two are fully redundant — the copies share both a
`Message-ID` and a signature, so either guard alone would refuse the second,
and the one that actually fires is the `Message-ID`. Where they stop
overlapping is across route classes:

* only the **replay key** catches a captured signed payload re-sent under a
  *fresh* `Message-ID` — the case `docs/e2e-replay.md` is about, and the case
  this file delivers twice in parallel;
* only the **`Message-ID`** guard stands on the unsigned **shared-secret**
  routes, where `replay_key` returns `""` — there is no credential bound to
  content, and `fetch_unseen` treats an empty key as "no opinion".

Each single-mutation pin therefore lives with the shape it is the only guard
for, and the byte-identical signed duplicate is pinned by neither alone: it
fails only if both in-batch guards are dropped. Said plainly in the test's own
docstring rather than dressed up as a precedence the code does not have.

So the file boots a second real `main.py` with `GPG_FINGERPRINT=""` (a supported deployment: `main.py`
requires one of `GPG_FINGERPRINT` or `SHARED_SECRET`, and `is_authorized`
returns on the GPG branch whenever a fingerprint is set), polling the
`bystander` mailbox so it cannot race the session poller, and re-delivers one
unsigned command twice in parallel into one batch. `replay_key` is asked
directly about those bytes and must answer `""`, so the test cannot silently
stop exercising the branch it names.

## Mutation evidence

Each duplicate-suppression test was checked against a deliberately broken
`src/poller.py`, reverted before committing:

| Mutation | Result |
|---|---|
| drop `key in batch` | `test_the_same_credential_delivered_twice_in_parallel_ran_once`, `test_the_batch_produced_no_execution_beyond_the_expected_ones`, `test_the_bus_recorded_each_accepted_turn_exactly_once` fail — two executions, two bus rows |
| drop `msg_id in batch` | `test_an_unsigned_command_redelivered_in_parallel_ran_once` and `test_the_bearer_batch_ran_nothing_else` fail — the unsigned command runs twice. The signed cases still pass, which is the honest result: for signed mail the two guards are redundant, so the replay key still refuses those duplicates |
| widen the barrier to one message per sender per batch | the distinct-command controls (`alpha` / `beta`, and `other` on the bearer route) never complete; the module fails on the 180s reply timeout |

## Controls

* **Positive control.** A differently-signed tracer is sent after the parallel
  batch and its full round trip is awaited before any snapshot, so "nothing
  happened" can never be "not processed yet".
* **Independent oracle.** Both parallel copies of the replayed credential are
  verified out-of-band by a separate, real `gpg --verify`, so a refusal cannot
  be mistaken for a signature failure.
* **Over-suppression controls.** In both regimes a distinct command shares the
  sender, the mailbox, the credential and the poll batch with the duplicates
  and must still run.

## Residuals

* The window is timing-based, not synchronised: a machine slow enough to spend
  more than 12s delivering six local SMTP messages would fail the window
  assertion rather than pass for the wrong reason.
* This file says nothing about *two pollers on one mailbox*. Nothing in the
  product runs that configuration, and the in-batch set is per-process; a
  shared-mailbox deployment would need a shared claim, which does not exist
  today.
* Delivery remains **at most once** overall — see `docs/e2e-failure-injection.md`.
  Exactly-one-execution here means "never twice", with the drop case documented
  there.
