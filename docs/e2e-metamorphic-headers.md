# Metamorphic headers — mutating the unsigned envelope must not change what runs

`tests/e2e/test_metamorphic_headers.py`, eight tests, docker-gated like the rest
of `tests/e2e/`.

## The property

> One valid signed payload, delivered repeatedly with mutated Subject /
> In-Reply-To / References / To. The executed command, the routing target and
> the reconstructed prompt are identical every time, or the message is
> rejected. Never silently re-routed.

This is the property an attacker with mailbox access actually attacks, and no
unit test expresses it. An OpenPGP signature in this system covers the
`multipart/signed` MIME part and nothing else, so **every** header outside that
part is attacker-writable by anyone who can read the mailbox and re-send.

## Why those four headers, specifically

They are not decorative. Each is a live lever in production code:

| Header | Where it steers | Effect of a successful mutation |
|---|---|---|
| `Subject` | `classify_email` — `@name` prefix | body diverted to an arbitrary agent |
| `Subject` | `classify_email` — `status` / `spawn` / `restart` | a meta-command the signer never wrote, with **unsigned arguments** |
| `In-Reply-To` | `classify_email` — chat-reply route | message becomes a reply into someone else's thread |
| `In-Reply-To` | `is_authorized` — known-thread bearer branch | authorises without consulting GPG at all |
| `In-Reply-To`, `References` | `prepare_router_command` → `build_email_thread_transcript` | prior turns **prepended to the CLI prompt** |
| `To` | nothing today | the control — see below |

`To` is included because the acceptance criterion names it and because a
control matters: a payload refused for carrying a replayed credential must be
refused whether or not the mutation was load-bearing.

## Which branch of the disjunction the system takes

The **rejection** branch — and deliberately not by canonicalising or signing
headers. The signature *is* the credential; `src/replay_guard.py` keys on the
signature bytes; `EmailPoller.fetch_unseen` consults that key, against both the
persisted store and the current batch, before yielding a message to `main.py`.

A mutant carries the captured signature verbatim, so it collides with the
original's key however the envelope around it was rewritten, and is dropped
**before any routing code sees it**.

Stated plainly, and this is the load-bearing sentence: *header mutation is
unexploitable because a captured payload is single-use, not because the router
ignores the headers.* The router still reads `Subject` and `In-Reply-To`, and
would still act on them. Nothing reaches it holding a spent credential.

## What the test does

One real, freshly GPG-signed command is delivered and awaited to completion
(`[Running]` + `[Result]`), so its replay key is persisted rather than merely
in-batch. Then five mutants are sent: the captured signature and signed body
byte for byte, a fresh `Message-ID` on each — the exact edit an interceptor can
make — and one mutated header apiece. A differently-signed tracer goes last and
is awaited in full.

Nothing in the system under test is patched: real GreenMail in docker, real
SMTP and IMAP sockets, the real `gpg` binary, the real `main.py` process, the
real SQLite bus. The only stand-in is the `claude` CLI, a third-party program
*outside* the product, reached by a real fork/exec from the real
`src/executor.py`, and it is there only to make executions countable and
prompts recordable.

**No mutant is re-signed.** A freshly signed `@agent` Subject routes to that
agent by design; that is a feature, not the property under test. The attacker
modelled here holds one captured mail and no key.

### Controls, without which every absence is worthless

1. Every mutant is proved present in the polled mailbox — "refused" is not
   "never arrived".
2. Every mutant is proved to still satisfy an out-of-band, real `gpg --verify`
   over the same bytes `src/gpg_verify.py` would use — "refused" is not
   "corrupted in transit", and the refusal is a replay decision rather than a
   signature-validation one.
3. The tracer completes after all five mutants — "refused" is not "not
   processed yet".

### What is asserted

Effects on outside surfaces, never a log line:

- exactly one execution of the CLI across all six deliveries;
- every recorded prompt equal to the signed body **byte for byte** — equality,
  not containment, is what pins thread-preamble injection shut;
- no mail threaded on any mutant `Message-ID`;
- exactly one inbound bus row for the command, `user → router`, keyed on the
  original `Message-ID`;
- no bus row keyed on a mutant `Message-ID`, and none naming the ghost agent
  from the mutated Subject.

## Verified by reversion, not by argument

Neutralising `replay_key` (returning `""`, the pre-`a4fa481` behaviour) turns
five of the eight tests red and leaves the three controls green — which is
exactly right, since a control that depended on the implementation would not be
one. Observed failures: the ledger gains extra executions, a prompt appears
with the baseline turn prepended, replies are threaded on mutant IDs, and bus
rows appear keyed on mutant IDs.

## Residuals

- **The router still trusts unsigned headers.** This slice proves the exposure
  is unreachable *given* the replay guard. It does not remove the coupling. Any
  future inbound path that reaches routing without passing
  `EmailPoller.fetch_unseen` — a webhook, a re-injection tool, a second
  consumer of the mailbox — reopens all of the above and would need its own
  guard. Signing the routing-relevant headers is the durable fix.
- **Bearer routes get no key.** `replay_key` returns `""` for messages carrying
  no content-bound credential, so the shared-secret and known-thread routes are
  protected by `Message-ID` idempotency alone. That is recorded in
  [e2e-auth-matrix.md](e2e-auth-matrix.md) and is unchanged here.
- **One-second signature resolution.** An OpenPGP signature packet hashes its
  own creation time at one-second resolution, so two genuine signatures over
  identical text made within the same second are byte-identical and the second
  is refused as a replay. This test never re-signs, so it does not hit that
  bound; `src/replay_guard.py` documents it.
