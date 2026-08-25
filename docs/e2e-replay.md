# Replaying a captured message

`tests/e2e/test_replay.py` takes one real, authorised, GPG-signed command mail
off the wire and hands it back to the live stack — real GreenMail in docker,
real SMTP and IMAP sockets, the real `gpg` binary, the real `main.py` process,
the real SQLite bus. Nothing in the system under test is patched. The only
stand-in is the `claude` CLI, a third-party program *outside* the product,
reached by a real fork/exec from the real `src/executor.py`.

## The finding: Message-ID dedupe was not replay protection

`src/poller.py` remembers the `Message-ID` of every message it has processed.
That makes redelivery idempotent. It does **not** make a captured message
unusable, because **no credential this system accepts covers the Message-ID
header**:

- the GPG signature is computed over the `multipart/signed` MIME part alone;
- the shared secret lives in the body;
- a thread reply's credential is an `In-Reply-To` the system itself issued.

So an interceptor holding one authorised command mail could rewrite its
`Message-ID` — and its `Date`, equally unsigned — hand the untouched signed
payload back to the mailbox, and watch the signature verify and the command run
a second time. The e2e test demonstrated exactly that before the fix: a second
`[Running]`, a second `[Result]`, a second bus row, a second execution.

It sends a third replay as well — the same signature packet with its ASCII
armour stripped, which gpg verifies identically — because that is what catches
a key computed over the *packaging* instead of over the credential.

## The fix: a content-bound replay key

`src/replay_guard.replay_key` fingerprints the *credential* a message presents,
so re-presenting a captured credential is refused however the envelope around it
is rewritten. Today one credential is cryptographically bound to content — an
OpenPGP signature — so the key is `sig:<sha256 of the signature packet>`, taken
from either the PGP/MIME detached part or an inline clearsigned block.

The key has to be stable under every re-encoding gpg still accepts, or a
captured signature can be laundered by re-wrapping it. Three rules do the work,
and each exists because a review round found the mutation that broke its
absence:

- **Digest the signature, not its packaging.** The armour is dearmoured to the
  binary packet, and the base64 is read the way gpg's radix-64 reader reads it:
  whitespace anywhere is ignored, and the CRC line and anything after `END` are
  not data.
- **Digest the bytes gpg would verify.** `src/gpg_verify.py` loops over the
  parts without breaking, so it verifies the *last* signature part; inline it
  breaks on the first `text/plain` part of a multipart and, for a single-part
  message, reads the body whatever its Content-Type says. Choosing differently
  is a bypass in *either* direction — take the first signature part and a
  prepended junk part mints a fresh key; filter single parts on Content-Type and
  relabelling a captured mail `application/octet-stream` empties it.
- **Never answer "no credential" when there is one.** An empty key reads as "no
  opinion" in `fetch_unseen` and lets the message through, so armour the parser
  cannot decode falls back to its own whitespace-stripped bytes.

**Verified stable** against real gpg and a real signature under: binary rather
than armoured form, re-wrapping at a different line width, added armour headers,
a dropped CRC line, CRLF versus LF, whitespace inside a base64 line, bytes
appended after `END`, a trailing lone or quoted `BEGIN` line, and a prepended
junk signature part. Every mutation not on that list which gpg *rejects* is
moot: the message is dropped on the authentication path regardless.

The key is stored alongside the `Message-ID` in the same `STATE_FILE` set and
checked in `EmailPoller.fetch_unseen` — against that persisted set *and* against
the keys already seen in the batch being assembled, because the store is only
written once `main.py` has finished with a message and two copies delivered
inside one poll interval would otherwise both run. That fetch is the single
choke point every inbound route passes through — plaintext command, JSON envelope, thread reply,
`@agent`, meta and reaction all arrive through this one fetch — so there is no
sibling call-site to miss.

## Deliberately no fallback for unsigned routes

The thread-reply, reaction and JSON-envelope routes authenticate on a bearer
value that binds nothing about the message. A digest over "the headers a sender
controls, plus the body" would look like protection and provide none: the
attacker replaying the message also controls those headers, so bumping the
unsigned `Date` by one second defeats it. It would, however, silently drop a
legitimate duplicate — two identical status requests in the same second. Buying
no security at a real false-positive cost is a bad trade, so those routes keep
the Message-ID store as their only control.

## Residual findings, recorded rather than papered over

1. **Same-second re-signing.** An OpenPGP signature packet hashes its own
   creation time at one-second resolution, so re-signing identical text within
   the same second reproduces identical signature bytes and the second send is
   refused as a replay. A second apart, it is accepted. Deterministic signature
   algorithms make this exact, not probabilistic.
2. **Pre-poisoning parity.** `main.py` marks a message processed in a `finally`,
   including unauthorised ones, so anyone who can deliver to the mailbox can
   pre-record a captured signature's key and suppress the legitimate original.
   This is precisely the pre-existing weakness of the Message-ID store; the new
   key inherits it and does not worsen it. Closing it means threading the
   authorisation outcome back into the poller.
3. **Bounded store.** A signed message consumes two entries, so
   `_MAX_PROCESSED_IDS` is doubled to 20 000 to hold the idempotency horizon
   where it was. Within a message the Message-ID is inserted first and so
   evicted first, which is the right order: losing it costs one duplicate
   execution of a redelivered mail, while losing the replay key costs a
   captured credential becoming usable again. Both still age out eventually —
   `processed_ids.json` remains a file you must not delete in production
   (already a repo invariant).
4. **Refusal leans on IMAP's implicit `\Seen`.** A refused replay is never
   handed to `mark_processed`, so nothing in this codebase flags it or records
   its Message-ID. It is not re-fetched anyway, because a plain
   `UID FETCH (RFC822)` — exactly what `fetch_unseen` issues — implicitly sets
   `\Seen`, so the message drops out of the `UNSEEN` search on the next cycle.
   Verified against the live server rather than assumed. The dependency is
   real and nobody chose it: switching the poller to `BODY.PEEK[]` for any
   reason would leave every refused message in the `UNSEEN` set forever, to be
   re-fetched and re-refused once a second.
5. **The armour parser is a reimplementation.** `_dearmor` is a short reader
   for a forgiving format, so a re-encoding gpg accepts but this canonicalises
   differently would mint a fresh key and buy the attacker one replay — never a
   silent pass, because the fallback guarantees a key exists. Three review
   rounds each found one such divergence and each is now fixed and pinned; the
   list of verified-stable transformations above is what is actually claimed.
   **The durable fix is to stop reimplementing:** key on gpg's own
   `signature_id`, which `python-gnupg` already returns from
   `verify_data`/`verify`. That means moving the replay check from the poller
   to just after verification, so it touches `src/gpg_verify.py`,
   `src/security.py` and `main.py` — a slice of its own. It would also close
   residual 2, since only *authenticated* messages would record a key.
6. **Still no freshness window.** No route compares a timestamp against the
   clock — see `docs/e2e-auth-matrix.md`. Content-bound keys make a *captured*
   message single-use; they do not bound how long a signed message stays valid.

**Operator hand-off:** nothing to run. The state file's format is unchanged (a
JSON list of strings), so an existing `processed_ids.json` is read as-is and
gains `sig:` entries from the next signed message onward; no migration, no
restart beyond the ordinary service restart that picks up new code. Item 5
remains the open decision recorded in `docs/e2e-auth-matrix.md`.
