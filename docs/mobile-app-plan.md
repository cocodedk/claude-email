# claude-email Mobile App — Design Plan

Status: **draft / proposal** — not yet implemented.
Last updated: 2026-04-17.

---

## 1. Purpose

Build a dedicated mobile app that lets one user remote-control multiple `claude-email` servers (each with its own mailbox) over end-to-end-encrypted email.

Each server the user installs generates its own symmetric key. The user pairs a server with the app by entering the server's email address and the key. Thereafter, every command the app sends is AEAD-encrypted under that key, and every reply the server sends is encrypted the same way. The mail provider — and any party with access to IMAP/SMTP logs — sees only ciphertext.

This is the transport-layer replacement for the current `AUTH:<secret>` subject prefix and the optional GPG path. Envelope validation (From / Return-Path) stays; the shared secret goes away.

---

## 2. Non-goals

- **Not a messaging app.** No DMs between users, no group chats. One-to-one between a user's phone and their own servers.
- **Not a replacement for GPG support.** GPG stays as a desktop/compat path. The app is the mobile-primary path.
- **Not cross-provider federation.** Each server's mailbox is whatever mail provider the user chose. The app does not run its own relay.
- **Not a password manager.** Keys live in the device keychain, period.

---

## 3. Threat model

### In scope (we defend against)

- **Mail provider snooping** (one.com, Gmail, etc. reading bodies). AEAD makes this ineffective.
- **IMAP/SMTP-transit interception.** Even without TLS, the body is ciphertext.
- **Subject-line leakage.** Nothing sensitive appears in the subject.
- **Replay attacks.** Captured ciphertext replayed by an attacker is rejected.
- **Tampering.** Any bit flip in the ciphertext fails AEAD verification.

### Out of scope (accept the risk)

- **Endpoint compromise.** A rooted phone or compromised server exposes the key. Use Android Keystore / iOS Keychain to raise the bar.
- **Traffic analysis.** The attacker still sees `From`, `To`, timing, and ciphertext size. Padding could address this; we don't bother in v1.
- **Social engineering the pairing.** User must verify the key out-of-band (QR ⇒ camera ⇒ keychain).
- **Compelled disclosure.** A court order to the user forces key disclosure. Cryptography doesn't solve legal threats.

---

## 4. High-level architecture

```
┌──────────────┐   SMTP (encrypted body)     ┌──────────────────────────────┐
│  Mobile App  │ ──────────────────────────▶ │  Server A: claude@example.com │
│              │                              │  (runs claude-email)          │
│  [server A]  │ ◀────────────────────────── │                                │
│  [server B]  │   IMAP (encrypted body)     └──────────────────────────────┘
│  [server C]  │
│              │   SMTP (encrypted body)     ┌──────────────────────────────┐
│  keychain:   │ ──────────────────────────▶ │  Server B: ops@otherco.dk     │
│  - key A     │                              │  (runs claude-email)          │
│  - key B     │ ◀────────────────────────── │                                │
│  - key C     │                              └──────────────────────────────┘
└──────────────┘
```

- The app holds one symmetric key per paired server, indexed by server email address.
- The app uses the user's own mailbox (IMAP/SMTP credentials entered once at first run) to send and receive.
- Each server only ever sees mail from and sends mail to the user's authorized sender address.
- No central service. No broker. No third-party chat platform.

---

## 5. Protocol specification

### 5.1 Envelope

An encrypted command email has:

- **Subject**: `claude-cmd` (or any fixed innocuous string; never carries data)
- **Body (text/plain)**:

```
-----BEGIN CLAUDE ENVELOPE v1-----
<base64url blob>
-----END CLAUDE ENVELOPE v1-----
```

The blob is the concatenation of:

| Field        | Bytes | Notes                                      |
|--------------|-------|--------------------------------------------|
| `version`    | 1     | `0x01`                                     |
| `nonce`      | 24    | Random, per-message (XChaCha20)            |
| `timestamp`  | 8     | Unix seconds, big-endian, AAD              |
| `ciphertext` | N     | AEAD-encrypted plaintext                   |
| `tag`        | 16    | Poly1305 authentication tag (appended by AEAD) |

AAD (associated data, authenticated but not encrypted) = `version || timestamp`.

Plaintext is UTF-8. For commands it is literally the user's command text. For replies it is the server's output.

### 5.2 Cryptographic primitives

- **Algorithm**: XChaCha20-Poly1305 (libsodium `crypto_aead_xchacha20poly1305_ietf_encrypt`).
- **Why XChaCha20-Poly1305**: 24-byte random nonces (no counter state required on either side), widely available libraries on iOS/Android/Python, strong security margin.
- **Key**: 32 random bytes from `os.urandom` (server) / `SecureRandom` (app). Never derived from a password or reused across servers.

### 5.3 Replay protection

Two independent layers:

1. **Timestamp window**: server rejects messages where `|now - timestamp| > 300s`. Clock skew tolerance.
2. **Nonce cache**: server stores every accepted `(timestamp, nonce)` pair for 600 seconds in an in-memory LRU (backed to the SQLite DB so it survives restart). Repeated nonces within the window are rejected.

An attacker replaying an old ciphertext loses both ways — either the timestamp is too old, or the nonce is already burned.

### 5.4 Key format & distribution

- Stored server-side at `~/.config/claude-email/keys/default.key` (mode `0600`), raw 32 bytes.
- Displayed to the user during `install.sh` as:
  - A QR code in the terminal (via a small `qrencode` invocation).
  - A hex fingerprint (first 8 bytes of SHA-256 of the key) for visual verification.
- The user scans the QR with the app, types the server's email address, and saves.
- Stored on the phone in Android Keystore (hardware-backed where available) / iOS Keychain, tagged with the server email address.

### 5.5 Key rotation

- `scripts/rotate-key.sh`: generates new key, replaces file atomically, displays new QR.
- Server accepts both old and new keys for a 24-hour grace window, then drops the old one.
- App pairs the new key either by scanning a fresh QR or receiving an in-band rotation message (v2 feature — out of scope for v1).

---

## 6. Server-side changes (`claude-email`)

### 6.1 New module: `src/envelope.py`

~80-line module with four public functions:

```python
def load_key(path: str) -> bytes
def encrypt(plaintext: str, key: bytes) -> str   # returns armored body
def decrypt(armored: str, key: bytes) -> tuple[str, int]  # (plaintext, timestamp)
def parse_armored(body: str) -> bytes | None     # returns None if not an envelope
```

Depends on `pynacl` (libsodium Python binding — small, pure-binding wheel).

### 6.2 Replay store

Add a table to the existing `claude-chat.db`:

```sql
CREATE TABLE IF NOT EXISTS envelope_nonces (
    nonce      BLOB PRIMARY KEY,
    seen_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_envelope_nonces_seen_at ON envelope_nonces(seen_at);
```

Prune entries older than 600 seconds on every check — cheap, indexed.

### 6.3 `src/security.py` integration

Add a third auth mode, priority order:

1. **Envelope mode** (new): if the body contains `BEGIN CLAUDE ENVELOPE v1`, attempt decryption with the configured key. Success = authenticated; the plaintext is the command.
2. **GPG mode** (existing).
3. **Secret mode** (existing, to be deprecated once the app ships).

Envelope-mode success bypasses the subject check entirely (no `AUTH:<secret>` needed). Envelope and GPG are mutually exclusive per message — whichever format the body is in.

### 6.4 `src/executor.py` integration

`extract_command()` gets a small branch: if the body is an envelope, return the decrypted plaintext (already done inside `security.is_authorized`, but the command also needs to flow through). Simpler: carry the decrypted plaintext in a per-request context object, set by `security.is_authorized`, read by `extract_command`.

### 6.5 `src/mailer.py` integration

`send_reply()` gets a new parameter: `encrypt_with: bytes | None`. When set, the body is encrypted into an envelope before sending. `process_email` passes the server's key when the inbound message was itself an envelope (encrypted-in ⇒ encrypted-out).

Existing callers (plain CLI fallback, meta commands) pass `None` and behave unchanged.

### 6.6 `install.sh` changes

1. Generate a 32-byte random key if one doesn't exist.
2. Write to `~/.config/claude-email/keys/default.key` with mode `0600`.
3. Display QR (via `qrencode -t ANSI256`) + hex fingerprint.
4. Instruct the user: "Open the mobile app, add a server, scan this QR."

### 6.7 Config

New env var: `ENVELOPE_KEY_FILE=~/.config/claude-email/keys/default.key`. Optional; if missing, envelope mode is disabled (existing GPG/secret paths still work — smooth migration).

### 6.8 Tests

- `tests/test_envelope.py`: round-trip, tamper rejection, wrong-key rejection, replay rejection (nonce reuse), timestamp-window rejection.
- Extend `tests/test_security.py` with envelope-authorized messages.
- Extend `tests/test_main.py` end-to-end: inbound envelope → CLI exec → outbound envelope.

Target: ≥15 new tests, 100% line coverage on `envelope.py`.

---

## 7. Mobile app design

### 7.1 Platform & stack (v1)

- **Android first.** Kotlin + Jetpack Compose. Minimum SDK 26 (Android 8.0).
- **Why Android first**: user's primary phone is Android; Outlook-on-Android was the motivating pain point.
- **iOS later** via a second native app (SwiftUI), not cross-platform. Each platform's keychain integration is worth native code.

### 7.2 Screens

```
┌──────────────────────┐
│ Servers              │    Main list of paired servers.
│                      │    Each row: name, email, last seen,
│  [+]  Add server     │    unread count.
│                      │    Tap → conversation.
│ ─ prod-box           │
│   claude@prod.co     │
│   2m ago · 3 unread  │
│                      │
│ ─ laptop             │
│   agent@example.com   │
│   1h ago             │
└──────────────────────┘

┌──────────────────────┐
│ < prod-box           │    Per-server conversation view.
│                      │    Threaded messages, newest bottom.
│ Me: run tests        │    Send box at bottom.
│ 10:14                │
│                      │
│ Server: 187 passed.  │
│ 10:15                │
│                      │
│ ┌──────────────────┐ │
│ │ Type a command...│ │
│ └──────────────────┘ │
│         [ Send ]     │
└──────────────────────┘

┌──────────────────────┐
│ Add server           │    Two tabs:
│  [Scan QR]  [Manual] │    - QR: camera opens, decodes key
│                      │    - Manual: paste hex + email
│  Name: _________     │
│  Email: ________     │
│  Key:   ________     │
│                      │
│        [ Pair ]      │
└──────────────────────┘

┌──────────────────────┐
│ Settings             │    - Mail account (IMAP/SMTP creds)
│                      │    - Notification preferences
│ Mail: user@example.com   │    - Backup/export encrypted server list
│ Poll: 60s            │    - About / version
│ ...                  │
└──────────────────────┘
```

### 7.3 Storage

- **Room DB** (SQLite) for messages, server list metadata (no keys here).
- **Android Keystore** for per-server keys and the user's IMAP/SMTP password. Hardware-backed where the device supports it.
- Messages stored locally, indexed by server + timestamp. User can purge per-server history from Settings.

### 7.4 Email handling

Two clear paths:

**Outbound (send)**:
1. User types command, picks server.
2. App encrypts under the server's key → envelope body.
3. App opens an authenticated SMTP session to the user's configured SMTP server and sends:
   - `From`: user's address
   - `To`: server's address
   - `Subject`: `claude-cmd`
   - `Body`: envelope
4. Message stored locally as "sent", status `pending-reply`.

**Inbound (receive)**:
1. Background worker (`WorkManager` on Android) wakes on push or polling interval.
2. Opens IMAP, fetches unseen mail.
3. For each unseen mail, if `From` matches a paired server address, attempt envelope decrypt with that server's key.
4. On successful decrypt: store plaintext as reply, flag IMAP message as Seen, fire notification.
5. On failed decrypt (wrong key, not an envelope): ignore — might be non-app mail. Don't mark as seen.

### 7.5 Push vs poll

- **Poll only in v1**: configurable interval (30s / 1min / 5min / manual). Simple, no server dependency.
- **FCM push in v2**: server sends an empty FCM notification on new reply; app wakes up and does an IMAP fetch. Requires a Firebase project per-app — deferred.

### 7.6 Pairing flow

```
server$ ./install.sh
        ...
        Generated envelope key: fp=a1b2c3d4...
        Scan this QR with your mobile app:
        ██▀▀▀▀▀██▄▄... (QR)
        Or type the hex manually:
        a1b2c3d4e5f6...

phone:  [open app] → [+] → [Scan QR]
        → camera activates, decodes → prefills key field
        → type name: "prod-box"
        → type email: claude@prod.co
        → [ Pair ]
        → sanity ping: app sends a hardcoded "ping"
          command, waits 60s for "pong" reply
          → success → server saved, conversation opens
          → failure → show error, offer to retry / re-scan
```

### 7.7 Tests

- Unit tests for envelope encrypt/decrypt (must match the server's implementation bit-for-bit — test vectors shared in a fixture file).
- Instrumentation test for pairing flow with a mock server.
- Integration test (optional): pair against a real `claude-email` instance in CI.

### 7.8 Size & scope estimate

| Area              | Rough LoC | Notes                          |
|-------------------|-----------|--------------------------------|
| Crypto            | ~150      | libsodium-jni wrapper          |
| Mail (IMAP/SMTP)  | ~400      | JavaMail or Commons Email      |
| UI (Compose)      | ~800      | 4 screens + nav                |
| Storage (Room)    | ~200      |                                |
| Background work   | ~150      | WorkManager                    |
| Pairing / QR      | ~150      | ZXing for QR decode            |
| **Total**         | **~1850** | ~2–3 weekends for a v1 cut     |

---

## 8. Multi-server UX details

- Every server entry = `{name, email, key, added_at, last_seen_at}`. Name is user-editable; email + key are pairing-determined.
- Server list sortable by "last seen" (default) or name.
- Per-server conversation histories are fully independent.
- Deleting a server wipes its key from Keystore and its messages from Room.
- No assumption that servers know about each other — each pairing is isolated.

---

## 9. Security considerations

- **Why symmetric and not public-key?** Simpler to implement, smaller footprint, one key per pairing is easy to reason about. The downside — compromise of either side's key exposes both directions — is acceptable because both endpoints are controlled by the same person. Upgrade path to public-key (NaCl `crypto_box`) is open if needed.
- **Why not Noise Protocol?** Noise is excellent but stateful across messages. For an email-latency, intermittent-connection scenario, stateless AEAD with timestamp+nonce replay protection is simpler and sufficient. No handshake to resume, no session state to rebuild on app restart.
- **Why not reuse TLS?** IMAP and SMTP are TLS-protected in transit, but the mail provider terminates the TLS and sees the cleartext. E2E body encryption is the only way to hide content from the provider.
- **Key logging.** Keys never appear in logs at any level. The install script prints to stdout during setup only.
- **Envelope format is versioned.** Future upgrades (e.g., post-quantum KEM) bump the version byte and both sides negotiate.

---

## 10. Rollout plan

### Phase 1 — Server-side crypto (this repo, ~1 week of evenings)

- [ ] Add `pynacl` dependency
- [ ] `src/envelope.py` (encrypt / decrypt / parse / TypeError on tamper)
- [ ] `envelope_nonces` table + prune-on-check logic
- [ ] `install.sh`: generate key, print QR (terminal ANSI256 via `qrencode`) + fingerprint
- [ ] `security.is_authorized`: envelope-mode path
- [ ] `mailer.send_reply`: optional encryption
- [ ] `process_email`: if inbound was envelope, encrypt reply
- [ ] Tests (see §6.8)
- [ ] README section on envelope mode

**Deliverable**: a `claude-email` server that accepts plain, GPG, or envelope-encrypted mail; testable from a command-line client (see Phase 2).

### Phase 2 — CLI reference client (~3 days)

- [ ] `scripts/send-envelope.py <server-email> <command>` — sends an encrypted command, waits for encrypted reply.
- Validates the whole protocol end-to-end without needing an app yet.
- Becomes the fixture for cross-implementation testing against the Android app.

### Phase 3 — Android app v1 (~2–3 weekends)

- [ ] Project scaffold (Kotlin, Compose, minSdk 26)
- [ ] Crypto module (libsodium-jni)
- [ ] Mail module (JavaMail)
- [ ] Storage (Room + Keystore)
- [ ] UI: server list, conversation, add-server, settings
- [ ] WorkManager poll job
- [ ] QR scan via ZXing
- [ ] Pairing flow with ping/pong sanity check
- [ ] Playstore-style internal-track build; sideload for dogfooding

**Deliverable**: "send command from phone, see output appear a moment later" works end-to-end.

### Phase 4 — Polish (~1–2 weeks)

- [ ] Push notifications (FCM)
- [ ] Error surfacing (decrypt failures, SMTP failures, stale replies)
- [ ] Rate limiting per server
- [ ] Export/import server list (encrypted blob)
- [ ] App signing, Play Store if desired

### Phase 5 — iOS (optional, later)

- SwiftUI counterpart. Same protocol; reuse server-side unchanged.

---

## 11. Open questions

1. **Reply threading**: do we preserve email threading (`In-Reply-To`) across encrypted messages? If yes, the Message-ID must be in the outer envelope (plaintext) which is fine — it's a mail-handling concern, not a secrecy one. Decision: **yes, preserve threading**; Message-IDs carry no sensitive data.
2. **Subject choice**: `claude-cmd` vs. something less conspicuous. Fixed string is easier to filter on. Decision: **fixed `claude-cmd`** for v1.
3. **Attachments**: v1 ignores them. A reply that exceeds N KB gets truncated with a "[truncated]" marker (same as current CLI-fallback behavior).
4. **Multi-user per server**: v1 assumes one `authorized_sender` per server. A server with multiple paired users is out of scope until somebody actually wants it.
5. **Backup of server keys**: if the user loses the phone, they lose the keys. Options: (a) show a recovery phrase (BIP-39 style) at pairing time; (b) nothing — re-pair with a fresh key from the server. Decision: **(b) for v1**, (a) later if demand.

---

## 12. References

- Noise Protocol Framework: <http://www.noiseprotocol.org/>
- libsodium: <https://libsodium.gitbook.io/doc/>
- PyNaCl: <https://pynacl.readthedocs.io/>
- libsodium-jni (Android): <https://github.com/joshjdevl/libsodium-jni>
- XChaCha20-Poly1305: RFC draft-irtf-cfrg-xchacha
