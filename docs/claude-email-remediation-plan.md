# claude-email Remediation Plan — Security Hardening + App-Only Client

**Status**: AGREED PLAN (design-level), awaiting user decisions in §16 before implementation
**Co-authored and mutually signed by**: Claude (root reviewer, kimi-k3) and Codex (plan reviewer, gpt-5.6-sol)
**Date**: 2026-08-24
**Basis**: unified read-only security review of claude-email (4 subsystem reviewers + root spine pass, all citations verified) + user product decision (app-only client with physical GPG provisioning)
**Scope**: `/home/cocodedk/0-projects/claude-email` (backend) + `/home/cocodedk/0-projects/Claude-Email-App` (Android client)
**HARD RULE honored during planning**: no repo files were modified; this plan file is the only artifact.

---

## 0. Purpose & scope

Two goals, one plan:

1. **Fix the issues found in the security review** (H1–H5, M1–M14, L-batches) with a coherent sequencing, not a grab bag.
2. **Make the Claude-Email-App the only client that can communicate with the server**, where the app's write authorization derives from a device key generated on the phone during a physical USB provisioning ceremony with this machine.

Out of scope: redesigning the chat-bus feature set; adding new user-facing features; iOS support.

---

## 1. Threat model & security invariants

### In scope (we defend against)
- Envelope spoofing (forged From/Return-Path) — no SPF/DKIM/DMARC trust.
- Mail provider snooping and transit interception.
- Replay of captured signed commands.
- Prompt injection arriving via email content reaching the router LLM.
- Malicious web pages reaching localhost services via DNS rebinding.
- Compromised or buggy local processes calling the MCP bus.
- Leaked shared secrets (already assumed compromised; must be rotated).
- Lost/replaced phone.

### Out of scope (accepted residual risk, documented)
- Same-UID local processes with full user privileges reading configs/DB/`/proc` (capability scoping limits blast radius; OS-level isolation is a follow-up decision).
- Rooted device or compromised server host.
- Traffic analysis on mail metadata.
- Compelled disclosure.

### Invariants (must hold at every phase boundary)
- No `shell=True`; 200-line max per file; TDD; 100% backend coverage per phase; README + website (`index.html`, `fa/index.html`) in lockstep with behavior changes.
- Envelope sender constraint is a routing/tenant check, never identity.
- Unauthenticated mail is never parsed deeply, never replied to with detail; silently dropped + server-side audit + rate-limited generic dashboard alert.
- Rollback is always fail-closed (stop writes or signed-only build), never re-enables a weaker auth mode.
- Contract changes are acked with `agent-Claude-Email-App` before merge.

---

## 2. Definition of "app-only client"

"Only client" means the app is the only **external user-command write principal**:

- Production ingress keeps exactly ONE admission path: structured signed envelope (protocol v3), verified against an enrolled device key, replay-checked, schema-authorized.
- Removed from production ingress: `AUTH:` secret subject mode, body-secret scan, In-Reply-To thread-match authentication (thread-match remains as a *routing hint* only), plain-text CLI fallback, `meta.auth` shared-secret envelopes (v2).
- Sender address demoted to universe/tenant selector. Identity = enrolled OpenPGP fingerprint + `client_id`.
- Internal Claude agents are NOT clients — they are backend service principals over a separately authenticated and capability-scoped MCP bus (Phase 2).
- Desktop access: no second weaker door. If desktop ingress is needed later, it uses the same v3 envelope tooling; that is a user decision point (§16-D1).

---

## 3. Target architecture

```
                          ┌─────────────────────────────────────────────┐
   App (phone, USB-       │  claude-email poller                         │
   provisioned key)       │                                              │
        │  SMTP/IMAP      │  src/ingress_gate/  (package)                │
        ▼  signed v3      │   1. sender + size/MIME admission            │
   ┌──────────┐           │   2. locate + verify exact signed entity     │
   │ Mailbox  │ ─────────▶│      → (verified payload bytes, fingerprint) │
   └──────────┘           │   3. replay ledger: atomic (client_id,       │
                          │      command_id) reserve + state machine     │
                          │   4. schema parse of VERIFIED bytes only     │
                          │   5. kind authorization                      │
                          │   6. dispatch → task queue / router          │
                          └──────────┬──────────────────────────────────┘
                                     │
        ┌────────────────────────────┼─────────────────────────────┐
        ▼                            ▼                             ▼
  TaskQueue/workers          Router LLM (env-allowlisted,     MCP bus (localhost,
  (per-project, scoped       MCP control-plane role token)   loopback-only, Host-checked,
   worker tokens)                                            per-principal bearer tokens,
                                                             server-derived identity,
                                                             per-tool ACL, no destructive
                                                             tools over the network)
```

Enrollment/revocation: local-only admin CLI (Unix permissions), never via email or MCP.

---

## 4. Phase 0 — Decisions, baselines, spikes

**Goal**: no unresolved wire semantics before implementation starts.

1. Baseline both repos: full test suites green, current behavior fixtures captured.
2. Write the finding→phase matrix (Appendix A) with testable acceptance criteria.
3. Decide §16 user decisions.
4. Four spikes, each with pass/fail criteria that select a design fork:

   - **S1 — Keystore+OpenPGP signing** (real device): prove OpenPGP signing over a non-exportable Android Keystore key interoperates with GnuPG verification. *Fail fork*: STOP for product/security decision. Only candidate lower-assurance fallback: app-generated OpenPGP key wrapped/encrypted by Android Keystore, exportability/rooted-device limits documented and explicitly approved. Never a machine-generated key.
   - **S2 — MCP auth-header delivery** (real claude client): prove Authorization/custom headers reach both SSE legs and streamable HTTP with session identity preserved. *Fail fork*: per-principal local stdio bridge/proxy reading a 0600 out-of-tree credential and injecting Authorization. Never bearer tokens in URLs or tracked `.mcp.json`.
   - **S3 — MIME round-trip** (Angus Mail → provider → Python/GnuPG): prove strict RFC 3156 multipart/signed survives transit byte-exactly. *Fail fork*: specified exact-byte canonical JSON payload + detached OpenPGP signature wrapper. Verifier returns the exact verified bytes; it must never re-serialize one object and execute another.
   - **S4 — Child env inventory**: dump actual environment requirements of `claude` CLI, git, and MCP config resolution. Output: the explicit allowlist for Phase 1 item 4.

**Exit**: spike reports committed to `docs/superpowers/plans/`; design forks selected; matrix frozen.

---

## 5. Phase 1 — Emergency containment (independently deployable)

Four items, each mergeable alone; none waits on Android work.

1. **MCP transport hardening (fixes H5)**:
   - One shared `TransportSecuritySettings` instance passed to both `SseServerTransport` and `StreamableHTTPSessionManager`: exact `127.0.0.1:<port>` (+ `localhost:<port>` only if actually used), reject browser Origins unless explicitly allowed.
   - Startup FAILS unless `CHAT_HOST` is loopback.
   - Covers `/sse`, `/messages/`, `/mcp`.
   - Dashboard: token-gated, read-only scope, no query tokens — dedicated read-only Authorization credential or local login exchanging it for an HttpOnly SameSite session cookie; every dashboard/API asset protected; tests for missing/wrong credentials and hostile Host/Origin.

2. **Single ingress gate + fail-closed legacy policy (partially fixes H1)**:
   - New `src/ingress_gate/` package (orchestrator; not necessarily one ≤200-line file):
     sender + size/MIME admission → bounded syntactic decode sufficient to extract the legacy credential → constant-time verification → full schema parse → kind authorization → dispatch.
   - Phase 1 admission policy: JSON envelope v2 with configured shared secret ONLY (fail-closed when unconfigured; no silent success). Reject plain-text, subject/body `AUTH:`, and thread authentication.
   - Thread-match (In-Reply-To) becomes routing-only immediately.
   - RFC Message-ID used as provisional delivery dedupe only — never claimed as replay security.
   - Pre-auth failures: silent drop + audit/dashboard alert. Detailed parse/auth error replies only after authentication.

3. **Stop secret leakage (fixes H2)**:
   - Never reflect raw inbound subjects. Temporary v2 reply subjects parsed against a strict identifier grammar and reconstructed from allowlisted correlation tags.
   - Centralized sanitization in `mailer.py` + redaction before DB persistence (`tasks.origin_subject`) and log output. Precise rule: remove the configured legacy `AUTH:<secret>` token after RFC-2047 decode/prefix handling.
   - Rotate the shared secret immediately at this phase's deploy.
   - Document residual risk (already-sent mail) + mailbox-retention guidance.

4. **Explicit subprocess environments (fixes H3)**:
   - Replace every `{**os.environ, ...}` and env-inheriting spawn with the S4-derived allowlist + explicit safe overlays + intentional MCP credential.
   - Applies to `executor.py`, `spawner.py`, `worker_manager.py`, `wake_spawn.py`.
   - Native tests assert absence of `EMAIL_PASSWORD`/`SHARED_SECRET`/IMAP/SMTP creds in `/proc/<pid>/environ` of spawned children.

---

## 6. Phase 2 — MCP service-principal security (fixes H4, M5)

Prerequisite: S2 spike result.

1. **Credential registry** (new table): hashed token, principal name, role, canonical project scope, status, expiry. Minted during trusted spawn or a local `agent-provision` CLI command — never via an MCP tool.
2. **Authenticate every MCP request/session**; server derives caller/project from the credential; `_caller` field removed/ignored.
3. **Roles & per-tool ACL** (testable matrix in §13):
   - `agent` — chat ops on own identity: ask/notify/message/check/list/deregister.
   - `worker` — queue operations on its one canonical project.
   - `router` — control-plane narrow: enqueue-with-origin-stamp only.
   - `provisioning-admin` — local CLI only, never exposed over MCP.
4. **Destructive/control-plane tools removed from network MCP schemas and dispatch** (not merely gated by a powerful bearer role): reset, confirm-reset, commit, push, spawn. If the product retains them, an authenticated v3 app request invokes the corresponding LOCAL control-plane API directly; spawn becomes an explicit signed envelope kind, not an agent-inferable MCP capability. "Never agent-reachable" is enforced by absence from the schema — testable.
5. **Registration binding**: `chat_register` activates/refreshes a pre-provisioned binding (name+project+expected identity); no arbitrary name/path; no `pid=None` takeover; `user` reserved; `agent-*` namespace minted only by trusted provisioning.
6. `check_messages` requires a registered authenticated recipient.
7. Audit records + rate limits per principal.
8. Token rotation runbook + restart/reprovision of existing sessions (S2-informed delivery: env-expanded config or 0600 out-of-tree file; never tracked `.mcp.json`).
9. Tests: cross-principal impersonation, token-theft scope, unauthenticated list/check/destructive calls, both SSE and streamable paths, proc-scan no longer enrolls arbitrary sessions (fixes M5).

**Documented limit**: bearer tokens cannot strongly exclude arbitrary same-UID local processes. Capability scoping bounds damage; OS isolation (separate user for bus) is an explicit follow-up decision (§16-D4).

---

## 7. Phase 3 — Device enrollment & signed protocol v3 (behind disabled enforcement flag)

### 7.1 Backend
- **Authorized-client registry** (per universe): `client_id` (UUID), OpenPGP fingerprint, public cert, status (active/revoked), enrolled_at, revoked_at, metadata. Multi-device-capable schema; exactly one device enrolled at cutover.
- **Replay ledger**: `(client_id, command_id, received_at, state, outcome_ref)` with `UNIQUE(client_id, command_id)`; state machine `received → dispatched → completed` with leases (crash-safe; see §11.4).
- Enrollment/revocation/rotation: local admin CLI only (`scripts/enroll-app.sh`, `scripts/revoke-app.sh`), Unix-permission-gated.
- v3 verifier: strict `multipart/signed` (exactly one `application/json` part + one `application/pgp-signature`) OR the S3-fallback canonical-JSON+detached wrapper. API returns `(verified_payload_bytes, fingerprint)` or REJECTS. No inline clearsign in prod. Any extra executable MIME part → reject.
- Ingress gate v3 admission path added behind `--enforce-v3` flag, disabled by default until Phase 4.
- Dedicated verification-only GPG home for enrolled public certs.

### 7.2 App (Claude-Email-App)
- On-device key generation ONLY (S1 outcome): non-exportable Android Keystore preferred, StrongBox preferred-not-required (minSdk 24), private material never exported, excluded from backup.
- Provisioning screen: explicit user approval, shows key/security level.
- Signing integration for every outbound envelope kind (command, reply, status, cancel, retry, list — ALL kinds signed for a simple boundary).
- Remove `meta.auth` shared secret from storage/build/test logging at cutover.

### 7.3 Coordinated
- Golden MIME/signature fixtures shared across repos.
- Protocol v3 spec (§11) acked with `agent-Claude-Email-App`.

---

## 8. Phase 4 — Fail-closed app-only cutover

1. Run the provisioning ceremony (§12) for the physical release device.
2. Canary: signed no-op/read envelopes end-to-end while legacy v2 still admitted.
3. Atomic cutover (single deploy): ingress admits ONLY registered active device keys + expected sender/universe + v3 signed envelopes. Rejects: plaintext, inline mixed bodies, `meta.auth`, `AUTH:`, forwarded/quoted credentials, thread auth, unknown/revoked keys, replays, prod aliases, test senders.
4. Remove shared secret from service config; rotate mailbox app-passwords/sessions; clean known secret-bearing DB fields.
5. `authorized_senders` = exactly the app mailbox; aliases pruned; `.env.test` gated behind an explicit non-prod flag refusing primary `CLAUDE_CWD`.
6. Verification battery: revocation, replay, altered signed bytes, extra MIME parts, wrong sender/universe/key, delayed mail, duplicate IMAP fetch, phone reinstall/loss.
7. Rollback drill: rollback = signed-only build or stop writes. NEVER re-enable `AUTH:`/v2.

---

## 9. Phase 5 — Message/process reliability (four separate small changes)

Each with its own schema migration, compatibility behavior, failure-injection tests, rollout note.

- **5-A. Durable delivery (M1, M13, M14)**: leased delivery — `pending → leased(token, deadline) → acked`, redelivery after lease expiry, consumer dedupe, `chat_ack_messages`; results persisted before send + resend on recovery; `mark_processed` only after durable handoff; authenticated error replies on permanent failure.
- **5-B. Process lifecycle (M2)**: new session/process group for executor, workers, wake turns; TERM grace → group KILL; bounded reaping; no unbounded `communicate()`.
- **5-C. PID identity (M3, M4)**: store `(pid, starttime, expected argv/cwd)`; identity-checked liveness and signals (pidfd where practical); exact `/proc` argv worker discovery replacing `pgrep -f` substring matching.
- **5-D. Wake spend breaker (M6, M12)**: defined quotas (turns/cost/time per agent per window), persistent open state, manual reset, alert; wake turns propagate the agent's principal identity env so custom-name inboxes drain correctly.

---

## 10. Phase 6 — Operational findings + closeout

Separate items, each mapped to review findings (Appendix A):
- DB busy-timeout unification; loud migration failures; `PRAGMA user_version` (M11).
- Trusted branch base + dirty-state rules in branch_prep (M8).
- Reaction routing never answers blocking asks; plan gates require explicit signed replies (M9).
- Relay per-universe scoping fix (M10).
- Log rotation 10 MiB; journald retention; secret redaction audit (L).
- `TokenStore.purge` scheduling; Starlette public-API migration; nudge thread-safety; guarded env float parsing (L).
- IMAP: BODY.PEEK fetch, socket timeout, headers-first pre-scan (L).
- `--max-budget-usd` argv position (L).
- Installer/hook hygiene: python version check, empty-value rejection, sed-escape, jq dependency check, hook failure surfacing, systemd hardening + StartLimit (L).
- Ghost-reaper vs cancel race; `is_clean` untracked-file policy documented (L).
- Mailer nits: case-insensitive `Re:`, full References chain, `to` header sanitization (L).
- Final: e2e verification, staged restart order, monitoring, revocation/rollback drill, closeout matrix sign-off.

---

## 11. Protocol v3 specification

### 11.1 Envelope
- Base: existing v2 envelope schema (`v, kind, task_id, body, project, meta, data, error`) with `v: 3`.
- `meta` gains: `client_id` (device UUID from enrollment), `command_id` (128-bit random, hex), `sent_at` (UTC ISO-8601). `meta.auth` REMOVED.
- All kinds signed, including status/list — one uniform boundary.

### 11.2 Wire form
- Primary (S3 pass): strict RFC 3156 `multipart/signed`, exactly two parts: `application/json` (the envelope) + `application/pgp-signature`. Any additional executable part → reject.
- Fallback (S3 fail): documented exact-byte canonical JSON + detached OpenPGP signature wrapper. Verifier returns exact verified bytes; never re-serializes.

### 11.3 Verification API
```
verify(msg) → (payload_bytes, fingerprint, client_id) | REJECT
```
Then: fingerprint → registry lookup (active?) → replay ledger → parse VERIFIED bytes only → kind authorize → dispatch.

### 11.4 Replay & idempotency
- Atomic: `INSERT INTO command_ledger (client_id, command_id, received_at, state) VALUES (...) ON CONFLICT → existing` in the same transaction that creates the queue/control-plane side effect, OR a recoverable `received → dispatched → completed` state machine with leases. A crash after reservation must not strand the command; a retry never creates a second side effect.
- Freshness: `sent_at` must be within [now − 7 days, now + 5 min skew] (UTC).
- Duplicate while pending → return original task/request ID + current state. After completion → stored outcome pointer. Retention: 30 days.
- RFC Message-ID: transport-level delivery dedupe only; no security claim.

### 11.5 Replies
- Subjects generated from signed correlation fields + allowlisted tags only. Never reflect inbound strings.
- Reply signing/encryption: out of scope v1; documented follow-up (§16-D2).

---

## 12. Provisioning ceremony specification

Script: `scripts/provision-app.sh` (local admin, Unix-permission-gated). App side: provisioning flow screen.

1. Verify physical attachment:
   - Enumerate `adb devices`; require explicit serial selection; state must be `device`.
   - Verify USB transport (`adb -s <serial> get-devpath` + sysfs correlation); reject `host:port` (wireless) transports; reject emulators (`ro.kernel.qemu=1`).
2. Verify the app:
   - Installed package `com.cocode.claudeemailapp`; release variant only.
   - Verify APK signing-certificate digest against the pinned expected release digest in trusted local provisioning config (`pm path` + `apksigner` or equivalent). CI secret setup is not a trust source; the pinned digest is.
   - Require device unlocked; app shows provisioning approval screen; user confirms in-app.
3. Challenge-response over `adb forward` local channel:
   - Machine sends one-time nonce (≥128 bits).
   - App generates the signing keypair ON-DEVICE (S1 outcome) and signs the nonce (proof of possession).
4. Enrollment:
   - Machine verifies signature, imports ONLY the public cert into the dedicated verification GPG home, records `(client_id, fingerprint, cert, enrolled_at)` in the registry.
   - Returns client_id + server configuration + unsigned enrollment receipt over the same ADB channel (unsigned receipt is the v1 choice; a signed receipt requires a separate machine enrollment key + app-pinned trust anchor — follow-up only).
5. Rotation: enroll new on-device key over USB, verify it, then revoke old. Loss: local revocation + re-ceremony.

---

## 13. MCP identity & ACL matrix

| Tool / operation | agent | worker | router | admin (local CLI) |
|---|---|---|---|---|
| chat_register (activate binding) | ✓ own | – | – | ✓ |
| chat_ask / chat_notify / chat_check_messages / chat_list_agents / chat_deregister | ✓ own identity | – | ✓ (relay needs) | ✓ |
| enqueue task (with origin stamp) | – | – | ✓ | ✓ |
| queue ops (claim/done/fail) | – | ✓ own project | – | ✓ |
| reset / confirm-reset / commit / push / spawn | NOT IN SCHEMA | NOT IN SCHEMA | NOT IN SCHEMA | ✓ local only |
| enroll / revoke / rotate device | – | – | – | ✓ local only |

Destructive operations are absent from the network MCP schema — enforcement by construction, not by role checks.

---

## 14. Migration, rollback, staged restart

- Phases 1→2→3 deploy independently; Phase 4 is the atomic cutover.
- Staged restart order: claude-chat first (MCP identity), then claude-email (ingress gate), verify, then app release with v3 signing.
- Writes may be briefly unavailable during cutover — documented and scheduled.
- Rollback per phase: fail-closed only. Cutover rollback = signed-only build or poller stopped. NEVER re-enable `AUTH:` secret mode or thread-match auth.
- Post-cutover: shared secret eradicated from app storage, service config, tests, docs; mailbox app-passwords rotated.

---

## 15. App-repo (Claude-Email-App) coordinated changes

| Phase | App-side work |
|---|---|
| P1 | None (backend-only containment) |
| P3 | Keystore key generation, OpenPGP signing adapter (S1), provisioning screen, sign-all-envelopes integration, remove `meta.auth` plumbing |
| P4 | Release build with pinned cert; provisioning acceptance; remove secret from storage/logs/tests |
| P5 | Lease/ack consumer semantics if delivery protocol changes touch the app |
| Always | `superpowers` skills per its CLAUDE.md; 200-line files; TDD; contract changes acked with `agent-claude-email` |

---

## 16. Open decisions for the user

- **D1**: Literal app-only, or does a desktop CLI signer tool (same v3 envelope, separately enrolled key) get admitted? Default plan: literal app-only; desktop enrolls later through the same ceremony if ever needed.
- **D2**: Reply signing/encryption to the app — v1 skips it (correlation is enough for UX); confirm or prioritize.
- **D3**: Spike S1 failure fallback — pre-approve the Keystore-wrapped software-key fallback, or hard stop for discussion?
- **D4**: Same-UID threat: accept capability-scoping + documentation (v1), or schedule OS-level isolation work?
- **D5**: Multiple devices — schema supports it; cutover enrolls one. OK?
- **D6**: Test universe policy — keep `.env.test` behind explicit non-prod flag (plan default) or remove entirely?
- **D7**: Migration window timing — when can writes be briefly unavailable?

---

## Appendix A — Finding → phase matrix

| Finding | Sev | Title | Phase | Acceptance criterion |
|---|---|---|---|---|
| H1 | high | Auth varies by route; thread-match precedes GPG; JSON skips GPG | P1+P3/P4 | Single ingress gate; thread-match routing-only; v2 fail-closed; v3 signed-only at cutover |
| H2 | high | AUTH secret echoed into reply subjects | P1 | Generated subjects; mailer strip; origin_subject redaction; secret rotated; unit tests |
| H3 | high | Secrets propagate into child process env | P1 | Env allowlist; `/proc/<pid>/environ` absence tests |
| H4 | high | MCP bus unauthenticated | P2 | Token principals; `_caller` removed; ACL matrix tests; pre-provisioned registration |
| H5 | high | DNS-rebinding protection off | P1 | TransportSecuritySettings both transports; loopback startup gate; dashboard auth tests |
| M1 | med | Claim-then-crash loses messages | P5-A | Lease/ack/redelivery; failure-injection test |
| M2 | med | Timeout kills only direct child; unbounded communicate() | P5-B | Process groups; bounded reap; orphan test |
| M3 | med | PID liveness kill(pid,0) only | P5-C | (pid,starttime,argv) identity; PID-reuse simulation test |
| M4 | med | Unescaped pgrep phantom worker | P5-C | Exact argv match; metachar-path test |
| M5 | med | proc_reconcile enrolls arbitrary sessions | P2 | Pre-provisioned binding required; reconcile only refreshes |
| M6 | med | Wake turns miss agent identity env | P5-D | Principal env passed; custom-name drain test |
| M7 | med | Test universe falls back to primary base | P6 | Explicit flag; refuses primary CLAUDE_CWD |
| M8 | med | branch_prep arbitrary HEAD / commits user dirt | P6 | Trusted branch base; dirty-state rules + tests |
| M9 | med | Reaction yes/no approves plan gates | P6 | Reactions never answer asks; explicit signed reply required |
| M10 | med | Relay misroutes test-universe non-task mail | P3+P6 | Scoped relay config; cross-universe leak test |
| M11 | med | Busy-timeout inconsistency; silent migration failures | P6 | Unified policy; loud failures; user_version |
| M12 | med | No wake spend circuit breaker | P5-D | Persistent quotas + reset + alert |
| M13 | med | mark_processed in finally loses commands | P5-A | Retry semantics; durable handoff |
| M14 | med | Result-send failure unrecoverable | P5-A | Persist-before-send; resend test |
| L1 | low | IMAP \Seen-before-process; no socket timeout; body-fetch pre-auth; argv order | P6 | PEEK fetch; timeout; headers-first; order fixed |
| L2 | low | Log rotation 70KB; TokenStore.purge; Starlette _send; nudge thread-safety; env float parse | P6 | Per-item fixes + tests |
| L3 | low | install.sh/jq/hooks fail-open/systemd hardening; ghost-vs-cancel race; is_clean untracked | P6 | Per-item acceptance criteria |
| L4 | low | Mailer nits (Re: case, References, to-header) | P6 | Mailer unit tests |

No finding is silently dropped; anything not in P1–P6 above is explicitly deferred here with rationale — currently none.

## Appendix B — Per-phase test/acceptance criteria

- **P0**: spike reports with pass/fail outcomes; matrix frozen; both repos baseline-green.
- **P1**: DNS-rebinding PoC fails post-fix; pre-auth mail silently dropped + alerted; subject round-trip contains no secret; child env absence tests pass; coverage 100%.
- **P2**: impersonation/token-theft/unauthenticated-call test battery passes on SSE + streamable; ACL matrix enforced by schema absence; existing sessions reprovisioned per runbook.
- **P3**: golden fixtures verify cross-repo; verifier returns exact bytes; replay ledger crash-recovery test; enrollment/revocation CLI works end-to-end on a real device.
- **P4**: full rejection battery (§8.6) passes; canary → cutover → rollback drill executed.
- **P5**: per-item failure-injection tests; no orphan processes after timeout; PID-reuse simulations; spend-breaker trip test.
- **P6**: per-item criteria; closeout matrix signed.

## Appendix C — Docs & cross-repo coordination

- Every behavior/config/contract change updates README + `website/index.html` + `website/fa/index.html` in the same PR (repo invariant).
- Envelope/provisioning contract changes acked with `agent-Claude-Email-App` via `chat_message_agent` before merge (per both CLAUDE.md files).
- Test count in docs updated when it changes.
- `.claude/tasks.jsonl` / CHANGELOG hygiene maintained per repo conventions.

---

*Signed: Claude (root reviewer) & Codex (plan reviewer, gpt-5.6-sol) — consensus reached 2026-08-24 after two reconciliation rounds.*
