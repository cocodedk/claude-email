# End-to-end failure injection — what breaks, and what it costs

`tests/e2e/test_failure_injection.py` breaks the system's real dependencies
while a real command is in flight and asserts what actually happens. Nothing is
patched or mocked: the mail server is SIGKILLed as a container, the worker is
SIGKILLed as a process, the IMAP session is severed as a TCP connection, and
the poller is SIGKILLed as a process.

Two of the four outcomes are good news. One is a design trade the project
accepts, and this document is where that trade is written down so the claim is
honest rather than aspirational.

## The delivery guarantee is at-most-once

`src/poller.py` fetches with `UID FETCH … (RFC822)`. RFC 3501 makes a
non-`.PEEK` body fetch implicitly set `\Seen`, and GreenMail implements it —
`test_imap_fetch_sets_seen_server_side` asserts that against the live server
rather than quoting the RFC, because what matters is the behaviour of the
server actually deployed in front of the poller.

`main.run_loop` calls `poller.mark_processed` only *after* dispatch, so the
persisted idempotency store is written late. Put those two together:

| crash lands … | mailbox flag | result |
|---|---|---|
| before the fetch completes | still `UNSEEN` | re-fetched, executed **exactly once** |
| after the fetch, before/during execution | already `\Seen` | **never executed** |

There is no window in which a crash causes a *second* execution. That is the
guarantee: **at most once**. The price is that the second row loses the
command outright.

This is a genuine trade, not an oversight. Switching the poller to
`BODY.PEEK[]` would convert it to at-least-once — the message would survive the
crash and be re-run — and a command re-run after a partial execution is a worse
failure for this system than a command dropped, because the executed side
effects are arbitrary shell work in a real repository. The mutation was tried:
with `BODY.PEEK[]` in place, `test_sigkill_between_accepting_and_executing_loses_the_command`
fails exactly where it should, on the re-execution.

## The loss is traceable, but the user is not told

"Not silently lost" is asserted as three durable artefacts, all checked from
outside the process that produced them:

1. the message is still in the polled mailbox, flagged `\Seen`, with no reply
   threaded to it;
2. its `Message-ID` is absent from `STATE_FILE`, so nothing in the system
   claims it was handled;
3. the execution ledger — an append-only file written by a third-party program
   the real `src/executor.py` forks — has no entry for it.

What the test does **not** claim, and what an operator must not assume, is that
the user was notified. The kill lands while the `[Running]` acknowledgement is
still on the wire, so the sender's mailbox stays empty. Detectability is
server-side only. If a deployment needs the user to learn about a dropped
command, that is new behaviour, not something the current design provides.

## The four injections

### A — `docker compose kill` on the mail server, mid-poll

`test_killing_the_mail_server_mid_poll_does_not_kill_the_poller`.

The JVM is SIGKILLed under a live poller. `main.run_loop` catches the IMAP
failure, logs `IMAP error — retrying`, and goes round again; the test requires
at least two logged failures, so a single exception on the way out cannot pass
for a retry loop. The container is then restarted and a tracer command is
driven all the way through to a `[Result]` with exactly one ledger entry.

GreenMail keeps mailboxes in memory, so anything resident at kill time is
destroyed by the dependency, not by claude-email. That is why this injection
asserts survival and recovery, and the no-loss half of the criterion is carried
by C and D, which break the transport rather than the store.

### B — SIGKILL a worker mid-task

`test_sigkilling_a_worker_mid_task_is_reported_not_silently_dropped`.

A JSON envelope of `kind: "command"` enqueues a real task; `WorkerManager`
spawns a real `python -m src.project_worker`, which forks the CLI stand-in. The
ack envelope names the worker pid, the task row names the CLI pid, and both are
SIGKILLed while the task is provably mid-flight (a `start` line in the ledger,
no `end` line).

Documented outcome, from `src/ghost_reaper.py`: the next housekeeping tick sees
a `running` row whose pid is gone, marks it `failed` with
`worker exited unexpectedly`, logs it, and `notify_task_done` puts a message on
the bus which the relay mails to the user as a `result` envelope with
`data.status == "failed"`. The test asserts the row, the log line and the mail.

The task is **not** retried — at most once again — and the test holds that open
for a settle window and re-counts.

### C — sever the IMAP connection mid-fetch

`test_severing_the_imap_connection_mid_fetch_loses_no_command`.

The harness's TLS terminator is subclassed into one that watches the decrypted
client→server stream and tears the socket pair down the instant it sees
`FETCH`, before forwarding the command. GreenMail therefore never executes the
fetch and never sets `\Seen`: the message survives, the poller logs the error,
retries on the next tick and executes it **exactly once** — one `[Running]`,
one `[Result]`, one ledger start and one ledger end, `Message-ID` recorded in
`STATE_FILE`, message now `\Seen`.

This is the recoverable half of the spectrum, and it is what makes D's loss a
statement about *when* the crash lands rather than about crashes as such.

### D — SIGKILL the poller between accepting and executing

`test_sigkill_between_accepting_and_executing_loses_the_command`.

A TCP gate in front of the SMTP terminator swallows the first connection the
poller makes. That connection is the `[Running]` acknowledgement, which
`main.process_email` sends after the command has been authenticated and
extracted and before `execute_command` forks the CLI — so accepting it and
never answering pins the process in exactly the accept→execute window. SIGKILL
lands there.

The assertions are the ones in the two sections above: never executed, never
executed twice, all three trace artefacts present, no mail to the user, and a
tracer through a restarted poller proving the absence was not simply a dead
process.

## Isolation

Injection A destroys every mailbox on its server, so this module boots a
**second** GreenMail from the same compose file under its own project name,
container name and ephemeral ports, and each test gets its own poller, ledger,
state file and database. The session-scoped `stack` fixture and every other e2e
module are untouched.

## A footgun this module had to work around

`ssl.SSLSocket.shutdown()` is not the fd-level no-op it looks like:

```python
def shutdown(self, how):
    self._checkClosed()
    self._sslobj = None
    super().shutdown(how)
```

It drops the OpenSSL connection before doing the syscall, so calling it while a
sibling thread is inside `recv()` or `sendall()` on the same socket segfaults
the interpreter — the same use-after-free `d029947` fixed for `close()`,
reached through `shutdown()`. A segfaulted run never reaches fixture teardown,
so it also leaks live pollers that go on consuming the shared mailbox and
corrupt every later run.

This module therefore uses its own `half_close()` (`socket.socket.shutdown`
directly), a `pump()` built on it and a `SafeTerminator` that swaps it into the
inherited pumps; the mid-fetch severance touches only the plain upstream
socket and lets EOF propagate to the client through the inherited path.

`tests/e2e/_stack.py` still has the original form, so the same race remains
open for the session-scoped terminators the other e2e modules share. Lifting
`half_close`/`pump` into the harness is the fix, and it belongs in its own
change.

## Running it

```bash
.venv/bin/pytest tests/e2e/test_failure_injection.py -q -m e2e   # needs docker
```

Without docker the module skips with a reason, like the rest of `tests/e2e`.
