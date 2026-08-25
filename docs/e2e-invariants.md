# End-to-end invariants over a stream of real traffic

`tests/e2e/test_invariants.py`

Three properties, asserted over one generated batch of messages that were
actually put on the wire — real GreenMail in docker, real SMTP and IMAP
sockets, a real `main.py` process, the real SQLite bus. Nothing in the system
under test is patched; the only stand-in is the `claude` CLI, a third-party
program *outside* the product, reached by a real fork/exec from the real
`src/executor.py`.

| # | Property |
|---|----------|
| 1 | No outbound mail — in **any header value** or any body part — contains the shared secret. |
| 2 | Every accepted inbound message produced exactly one row in the execution ledger. |
| 3 | No command's effect is observed twice: one `[Running]`, one `[Result]`, one bus row, one ledger line each. |

## The finding: the secret went out in the Subject

The shared secret is a **bearer** credential. `src/security.py` accepts a
message whose Subject starts with `AUTH:<secret>` — that is the route the
README advertises — or whose body contains the token anywhere.

`src/chat_handlers.send_threaded_reply` builds every reply's Subject by
prepending `[Running]` / `[Result]` to the **inbound** Subject. So on the
subject-bearer route, every single reply carried the live credential in a
header, out through the SMTP relay chain and into the sender's mailbox.

A body-only check would have passed the whole time. Checking headers is what
catches it, and it is why the property is stated as *body **or** header*.

### The fix

`src/secret_redact.py`, applied inside `src/mailer.send_reply` — the single
function every outbound mail in the product passes through, so its three
callers (`chat_handlers`, `chat_relay`, `json_handler`) and any future one are
covered at once. Each call site passes `secrets=configured_secrets(config)`;
that helper takes the union of the top-level secret and every universe's,
because `relay_outbound_messages` runs from `main`'s housekeeping with the
top-level config rather than a per-universe one.

Three things the scrub does that a naive `body.replace(f"AUTH:{secret}", "")`
would not:

* **Redacts the bare secret**, not only the `AUTH:` token. Only the token form
  is stripped on the way in, so a copy pasted into the middle of a command
  rides the prompt into the CLI and back out in the `[Result]` body.
* **Looks through RFC 2047 encoded-words.** `is_authorized` decodes the Subject
  before checking it, so `=?utf-8?B?...?=` authenticates — and would otherwise
  be echoed back with the secret intact, merely base64-wrapped, invisible to a
  substring scan.
* **Covers every header**, not just the Subject. `In-Reply-To` and `References`
  are copied verbatim from the inbound `Message-ID`.

**Accepted trade:** a thread whose own `Message-ID` contains the secret loses
its threading headers on the reply, so that thread will not auto-auth by
thread-match. Only a party who already holds the secret can craft such a
Message-ID, and emitting the credential is the worse outcome.

## Why this module boots its own poller

`src/security.py` short-circuits:

```python
if gpg_fingerprint:
    return verify_gpg_signature(message, gpg_fingerprint, gpg_home)
```

The session `stack` fixture configures a fingerprint, so on that poller the
shared-secret routes are **unreachable** and the leak cannot be observed at
all. This module therefore boots a second real `main.py` with
`GPG_FINGERPRINT=""` and `SHARED_SECRET` set — the bearer-token deployment,
supported by `main.py`'s own startup guard, which requires exactly one of the
two. It polls the `bystander` mailbox so it cannot race the session poller, and
gets its own `CLAUDE_BIN`, state file, log and database.

## The generated stream

Each case carries a fresh nonce, so the ledger, the mailbox and the bus can all
be counted per message. The variation is in *where the secret sits* — each
placement is a different outbound surface it could escape through.

| Case | Credential placement | Expected |
|------|---------------------|----------|
| `subject_secret` | `AUTH:<secret>` in the Subject | accepted |
| `body_secret` | `AUTH:<secret>` in the body, clean Subject | accepted |
| `bare_secret_in_command` | token in the body **plus** a bare copy inside the command text | accepted |
| `encoded_word_subject_secret` | `AUTH:<secret>` inside an RFC 2047 base64 Subject | accepted |
| `secret_in_message_id` | the secret inside the inbound `Message-ID` | accepted |
| `stranger_with_secret` | valid secret, but `From` is not the authorized sender | **rejected** |

Then: byte-identical redeliveries of two accepted messages, then a tracer.

## What is deliberately *not* asserted

A bearer-authenticated message re-sent under a **fresh `Message-ID`** executes
again, by design. No credential on that route covers any header, and `CLAUDE.md`
records that the poller's idempotency store is the only temporal control there.
Asserting otherwise would fail against correct behaviour and could only be
"fixed" by inventing a freshness window.

So the duplicates in this stream are **byte-identical redeliveries** — same
Message-ID — and invariant 2 is stated per *accepted inbound message*, not per
payload. Content-bound replay of a **signed** message is
`docs/e2e-replay.md`'s subject and is not restated here.

## Independent oracles

Absence assertions are worthless without controls, so this file carries four:

* **The tracer.** A last message, awaited in full, proves the poller worked
  through the batch. "Nothing happened" is then scoped by a demonstrably awake
  poller rather than by a timeout.
* **The scanner is validated against the inbound corpus.** `find_secret` is run
  over the messages in the *polled* mailbox, where the secret demonstrably is —
  including the encoded-word case, asserted to be genuinely encoded so the
  decoding half of the scanner is exercised. A scanner that found nothing
  anywhere would make every absence vacuous.
* **Every accepted command was answered.** One `[Running]` and one `[Result]`
  each — which separates "the secret was redacted" from "the message was
  dropped".
* **The ledger's total line count** is checked, not only the per-case counts,
  so an extra execution under a prompt carrying no nonce cannot hide.

The execution count comes from an append-only ledger written by the CLI
stand-in; `O_APPEND` on a short write is atomic, so concurrent executions
cannot lose or interleave a record. The mail comes back off a real IMAP socket
and is diffed against a snapshot of the mailbox taken before the batch was
sent, so *every* mail the poller emitted is scanned — not only the ones that
can be threaded back to a case. The bus rows are read read-only from outside
the writing process.

## Operator hand-off — rotate `SHARED_SECRET`

**This is a manual action. The code fix does not and cannot do it.**

Any deployment that has been running the subject-bearer auth route
(`Subject: AUTH:<secret> <command>`) has **already emailed its live shared
secret** to the authorized sender, in the Subject of every `[Running]` and
`[Result]` reply. Those messages are sitting in a real mailbox and passed
through real mail relays on the way there. The scrub above stops future
leakage; it cannot un-send what already went out.

1. Generate a fresh secret and set `SHARED_SECRET` in `.env` on the server.
2. Update the same value in every client that sends `AUTH:` commands or JSON
   envelopes.
3. `systemctl --user restart claude-email.service`.
4. Consider deleting the historical `[Running]` / `[Result]` mail that carries
   the old secret — from the sender's mailbox and from Sent, if the mail client
   keeps a copy.

Until step 1 is done, treat the old secret as public.
