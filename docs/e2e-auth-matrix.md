# The authentication matrix

`tests/e2e/test_auth_matrix.py` drives every inbound route through every
authentication condition against the live stack — real GreenMail, real SMTP and
IMAP sockets, the real `gpg` binary, the real `main.py` process, the real SQLite
bus. Nothing in the system under test is patched. The only stand-in is the
`claude` CLI, which is a third-party program *outside* the product and is
reached by a real fork/exec from the real executor.

## The grid

Rows are the six routes `src/chat_router.classify_email` and
`src/json_envelope.is_json_email` can send a message down. Columns are the five
conditions a message can arrive in.

| route | unsigned | wrong key | stale timestamp | replayed nonce | valid |
|---|---|---|---|---|---|
| plaintext command | dropped | dropped | accepted | no second run | `[Running]` + `[Result]` |
| JSON envelope | `unauthorized` | `unauthorized` | accepted | no second reply | `ack` |
| thread reply | dropped | dropped | accepted | no second row | `[Answer]` + bus row |
| `@agent` | dropped | dropped | accepted | no second row | `[Dispatched]` + bus row |
| meta (`status`/`spawn`/`restart`) | dropped | dropped | accepted | no second reply | `[Status]` listing |
| reaction | dropped | dropped | accepted | no second row | `[Reaction]` + `yes` on the bus |

## What each condition means per route

The credential differs by route, so *unsigned* and *wrong key* are expressed in
the currency each route actually accepts.

- **plaintext / `@agent` / meta** — the credential is a GPG signature over the
  MIME part, checked against `GPG_FINGERPRINT`. *Unsigned* is a bare
  `text/plain` mail. *Wrong key* is a real detached signature made by a real
  second key generated in a **separate** `GNUPGHOME`, never imported into the
  stack's keyring, so the cell is red whether `verify_gpg_signature` compares
  fingerprints or merely asks "was this signed by a key I hold".
- **JSON envelope** — the credential is `meta.auth`. GPG is never consulted on
  this path.
- **thread reply / reaction** — the credential is *possession of a Message-ID
  the system itself issued*. The negatives forge one; the accepted cells are
  deliberately unsigned, because the `In-Reply-To` **is** the credential.

The negatives assert three independent absences: no mail threaded on the
message, its nonce nowhere in the mailbox at all, and its nonce nowhere on the
bus. That absence is only meaningful because a signed tracer sent afterwards has
already completed its own full round trip, plus a settle window — so "not yet
processed" cannot masquerade as "rejected".

## Finding: there is no freshness window

**claude-email compares no timestamp on any route.** Neither the `Date` header
nor the envelope's `meta.sent_at` is checked against the clock. A validly signed
message stamped 2001 is executed exactly like one stamped now, on every route.
The stale-timestamp column therefore asserts *acceptance* — that is the truthful
state of the system, and the tests are the evidence for the claim.

The consequence: **the replayed-nonce column is the only temporal control.**
Replay protection is the poller's idempotency store (`STATE_FILE`, default
`processed_ids.json`), which holds the most recent 20 000 keys.

That column was written when the store held `Message-ID`s alone, and
`tests/e2e/test_replay.py` later showed why that was not enough: nothing in
this system signs the `Message-ID`, so re-sending a captured signed payload
under a fresh one executed it again. The store now also holds a `sig:<sha256>`
digest of the OpenPGP signature (`src/replay_guard.py`), which makes a captured
*signed* credential single-use however its envelope is rewritten. The unsigned
bearer routes — thread reply, reaction, JSON envelope — are unchanged and still
rest on the `Message-ID` alone; see [replaying a captured
message](e2e-replay.md) for why a digest fallback there would buy nothing. On
every route, an intercepted message stays executable if its keys age out of the
store or the store is deleted.

Adding a freshness window would be a behaviour change with new configuration
surface, so it is recorded here as a decision for the operator rather than made
unilaterally. **Operator hand-off:** decide whether a `Date`/`meta.sent_at`
skew limit is wanted; until then, do not delete `processed_ids.json` in
production (already a repo invariant). Treat command mails as replayable
secrets for as long as their keys sit in that bounded store — content-bound
keys make a captured signed message single-use, but they do not bound how long
a signature stays valid.

## Fixed here: the JSON envelope path fails closed

`src/json_handler.py` used to gate its comparison on the secret being
configured:

```python
if expected and env.auth != expected:   # before
```

`main.py`'s startup guard requires *one* of `GPG_FINGERPRINT` or
`SHARED_SECRET`, so a GPG-only deployment is supported — and on such a
deployment `expected` is empty, the comparison short-circuits, and **every**
JSON envelope was accepted with no credential at all. The only claim to
authority left was a `From`/`Return-Path` pair, which anyone who can reach the
operator's MX can write. A `list_projects` envelope then disclosed the
operator's project names to an unauthenticated caller.

The check now fails closed and compares in constant time on bytes:

```python
def _auth_ok(presented: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
```

**Contract change:** `SHARED_SECRET` is now required for the JSON envelope
path. A deployment that authenticates with GPG alone keeps every plain-text
route and loses only the structured-client path, which answers
`error.code == "unauthorized"` with a hint naming `SHARED_SECRET`. Set
`SHARED_SECRET` on the server and in the app to restore it.

Unit tests missed this for the reason unit tests usually miss this class of
bug: one of them pinned the behaviour as intended
(`tests/json_handler/test_command.py::test_no_auth_required_when_universe_secret_empty`,
now inverted). Seeing it needs a second real poller booted with
`SHARED_SECRET=""`, which the `secretless` fixture provides.
