# Amendment E2E — end-to-end encryption, not just signing

**Status**: AMENDMENT to `docs/claude-email-remediation-plan.md`. Requires an ack from
`agent-Claude-Email-App` before any of it is built (both CLAUDE.md files, §15/AppC).
**Raised by**: the user, 2026-08-24 — *"communication between the app and the server are
totally encrypted, meaning, nothing is readable on the mail server"*.

## What this changes

The base plan (§11) specifies **signing only**: a `multipart/signed` message whose
`application/json` envelope travels in **cleartext**. That gives authenticity, integrity and
replay protection — and leaves every command and every result fully readable by anyone with
mailbox or mail-server access. §11.5 L252 explicitly deferred encryption
(*"Reply signing/encryption: out of scope v1"*), which is decision **D2**.

**D2 is now answered: encryption is mandatory, in both directions.** §11 becomes
encrypt-and-sign; §11.5's deferral is withdrawn.

## What "nothing readable" can and cannot mean — read this before agreeing

PGP/MIME encrypts the **body**. It does not encrypt headers. Even with a perfect
implementation, the mail server still sees:

| Still visible | Mitigation |
|---|---|
| SMTP envelope `From` / `To` | None. Inherent to email delivery. |
| `Date`, message size, timing | Padding blunts size; timing is inherent. |
| `Message-ID` | Already opaque (`make_msgid`) — keep it content-free. |
| `In-Reply-To` / `References` | **The conversation graph leaks.** Removing them buys privacy at the cost of mail-client threading — see the open question below. |
| `Subject` | **Fixable**: protected headers ("memory hole") — put the real Subject *inside* the encrypted part and send a constant dummy (`...`) as the outer Subject. Both ends are our own clients, so this is fully available to us. |

So the honest formulation of the requirement is: **no content, no routing metadata, and no
command or result text is readable on the mail server; the fact and shape of a conversation
between two known addresses remains observable.** If that residual is unacceptable, email is
the wrong transport and the conclusion is to move the app onto a direct channel instead — that
is a product decision, not something the crypto can fix.

**This reverses part of the signed-header design.** An earlier draft put routing in signed
`X-Claude-Route` / `X-Claude-Audience` headers. Headers are readable, so routing must move
**inside** the encrypted payload. Nothing outside the ciphertext may influence what executes.

## Crypto construction

**Sign, then encrypt** — the signature lives inside the encrypted layer. This hides *who
signed* from an observer and binds the signature to the plaintext. Consequences:

- The signature is verified **only after** decryption. Identity comes from the **inner**
  signature; never from the outer MIME structure, the envelope sender, or any header.
- The replay/freshness check (nonce + timestamp) runs on the **decrypted** payload.
- Anything arriving *not* encrypted is rejected at admission — including plaintext, a bare
  `multipart/signed`, and a mixed message with any additional part.

## Key management — the substantive new work

Today the server holds only `GPG_FINGERPRINT`, a **public** key used to verify the user. To
decrypt, the server needs **its own keypair**. That is new, and it makes enrollment mutual:

1. **Server keypair** in a dedicated `GNUPGHOME`, mode `0600`, owned by the service user.
   It must stay out of the Phase 2 child-process env allowlist (which already excludes
   `GPG_HOME`) — a spawned agent that can read the server's private key can decrypt every
   command ever sent.
2. **Enrollment becomes a two-way exchange over USB.** The base plan's ceremony (§12) only
   *pulls* the device's public key to the host. It must also **push the server's public key
   to the device**, and the app must **pin** it. Without pinning, a mail-server MITM can
   substitute its own key and the app will happily encrypt to the attacker.
3. **Rotation** now has two keys to think about. Rotating the server key requires re-pushing
   it to every enrolled device — over USB, physically. Budget for that operationally, or
   accept a long-lived server key.

## Failure modes to specify

- **Server cannot decrypt** → silent drop plus a dashboard alert (Phase 1's pre-auth rule
  already forbids telling an unauthenticated sender anything). It must not fall back to
  cleartext, ever.
- **App cannot decrypt a reply** → surface "unreadable reply — re-enroll", not a silent retry.
- **Device revoked** → the server must stop being *able* to encrypt to it, not merely refuse.

## New slices (all blocked on a revised §11 spec + the ack)

**Backend:** `p3-g-server-keypair` · `p3-h-decrypt-then-verify` (inbound: decrypt → verify
inner signature → replay check → parse → dispatch; reject anything unencrypted) ·
`p3-i-encrypt-outbound` (every reply encrypted to the enrolled device and signed by the
server — covers `chat_relay.py`, `chat_handlers.py`, `json_handler.py`, `mailer.py`) ·
`p3-j-protected-headers` (dummy outer Subject, real Subject inside; header minimisation).

**App:** `app-pin-server-key` (receive + pin at enrollment) · `app-encrypt-outbound` ·
`app-decrypt-inbound` (verify the server's signature after decrypting).

**Amended:** `p3-cer-3-challenge` becomes a mutual key exchange. `p1-3-secret-leak`'s
"generated subjects from allowlisted tags" is superseded by the dummy-Subject rule — a
correlation tag in a readable Subject still leaks task identity.

## Open question for the user

**Threading vs. privacy.** Keeping `In-Reply-To`/`References` preserves mail-client threading
and the existing reply-matching, but publishes the conversation graph. Dropping them makes
every message standalone and opaque, at the cost of that threading — the app would correlate
on an id inside the ciphertext instead. Recommendation: **drop them** for app↔server traffic,
since the app is the only client and it can correlate internally; keep the graph only if you
want the mailbox to remain human-browsable.
