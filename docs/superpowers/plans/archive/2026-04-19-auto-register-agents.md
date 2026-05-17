# Auto-Register Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Claude Code session opened in a claude-chat-enabled project receives a SessionStart hook that tells the agent to register with the chat bus and how to use it — eliminating the need to manually say "please register" and the risk that agents dump bus-bound questions to stdout.

**Architecture:** Per-project `.claude/settings.json` sibling to `.mcp.json` points its `SessionStart` hook at a shell script in the claude-email install (`scripts/chat-session-start-hook.sh`). The script emits `hookSpecificOutput.additionalContext` JSON whose body is the literal content of `scripts/chat-agent-instruction.txt`. The spawner's existing `inject_mcp_config` flow is extended with `inject_session_start_hook` so both files are written atomically; the existing `install-chat-mcp.py` migration tool is updated to the same surface.

**Tech Stack:** Python 3.12, pytest/pytest-mock, POSIX shell, `jq` (already available via the claude-email service host — verify in Task 0).

**Spec:** `docs/superpowers/specs/2026-04-19-auto-register-agents-design.md`

---

## Task 0: Preflight — confirm `jq` availability

**Files:**
- Inspect only: none

- [ ] **Step 1: Confirm `jq` is installed on the target host**

Run: `command -v jq && jq --version`
Expected: a path like `/usr/bin/jq` and a version string. If missing, install via `sudo apt-get install -y jq` before continuing; the hook script depends on it.

- [ ] **Step 2: Confirm Python venv is usable**

Run: `.venv/bin/pytest tests/ -q 2>&1 | tail -3`
Expected: `284 passed` (or the current count — use this as the green baseline).

---

## Task 1: Add the agent instruction text file

**Files:**
- Create: `scripts/chat-agent-instruction.txt`

- [ ] **Step 1: Create `scripts/chat-agent-instruction.txt` with the exact text from the spec**

Write this file verbatim (copy from spec §3, unchanged):

```
You are a chat-connected agent on the claude-chat bus. Messages arriving
on the bus must be answered through bus tools, never through stdout.

Identity: your caller name is literally
  caller = "agent-" + basename(cwd)
Compute it once. Use the exact same string for every _caller= argument below.
Do not invent a new name later in the session.

1. Register (first chat-bus tool call of the session; idempotent — safe to
   repeat if you are unsure whether the spawner pre-registered you):
     mcp__claude-chat__chat_register(name=caller, project_path="<absolute cwd>")

2. Ask the user (blocks until the user replies or up to 1 hour, after which
   the tool returns {"error": ...}):
     mcp__claude-chat__chat_ask(_caller=caller, message="...")

3. One-way progress update (no reply expected):
     mcp__claude-chat__chat_notify(_caller=caller, message="...")

4. Inbox drain — consume-with-ack: messages returned are marked delivered
   and will NOT be seen again. Only call when you will stay alive long
   enough to act on what you receive. Never poll in a loop; never call as
   a "final drain" on the way out.
     mcp__claude-chat__chat_check_messages(_caller=caller)

5. Deregister on clean exit (best-effort; the server also reaps dead
   agents automatically, so this is a courtesy, not a guarantee):
     mcp__claude-chat__chat_deregister(_caller=caller)

Parameter reminders:
- chat_register uses name= and project_path=. Every other tool uses _caller=.
  The string value is the same in all of them — only the parameter name differs.
- Keep messages short — one paragraph. The user may be reading them in an
  email client; no raw logs, no long dumps.
```

- [ ] **Step 2: Verify the file was written with a trailing newline and no tab characters**

Run: `tail -c 1 scripts/chat-agent-instruction.txt | xxd`
Expected: last byte is `0a` (newline).

Run: `grep -P '\t' scripts/chat-agent-instruction.txt && echo HAS_TABS || echo NO_TABS`
Expected: `NO_TABS`.

- [ ] **Step 3: Commit**

```bash
git add scripts/chat-agent-instruction.txt
git commit -m "feat(chat): add agent startup instruction text"
```

---

## Task 2: Add the hook shell script

**Files:**
- Create: `scripts/chat-session-start-hook.sh`

- [ ] **Step 1: Create the hook script**

Write `scripts/chat-session-start-hook.sh` with exactly this content:

```sh
#!/bin/sh
# Emits SessionStart hook output for Claude Code telling the session to
# behave as a chat-bus agent. Reads the instruction body from a sibling
# file so it stays under version control and can be edited without
# rewriting shell-embedded JSON.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTRUCTION_FILE="$HERE/chat-agent-instruction.txt"
if [ ! -r "$INSTRUCTION_FILE" ]; then
    # Emit nothing on stdout so Claude Code falls back to its default; log
    # to stderr for post-mortem.
    echo "chat-session-start-hook: missing $INSTRUCTION_FILE" >&2
    exit 0
fi
jq -nc --rawfile ctx "$INSTRUCTION_FILE" \
    '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $ctx}}'
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/chat-session-start-hook.sh`

- [ ] **Step 3: Smoke-test the script**

Run: `scripts/chat-session-start-hook.sh | jq -r '.hookSpecificOutput.hookEventName'`
Expected: `SessionStart`

Run: `scripts/chat-session-start-hook.sh | jq -r '.hookSpecificOutput.additionalContext' | head -3`
Expected: first three lines of the instruction text.

- [ ] **Step 4: Commit**

```bash
git add scripts/chat-session-start-hook.sh
git commit -m "feat(chat): add SessionStart hook script for agent bootstrap"
```

---

## Task 3: Test — inject_session_start_hook writes a correct settings.json from scratch

**Files:**
- Modify: `tests/test_spawner.py` (add new class)

- [ ] **Step 1: Add a failing test**

Append this class to `tests/test_spawner.py`:

```python
class TestInjectSessionStartHook:
    def test_creates_settings_file(self, tmp_path):
        from src.spawner import inject_session_start_hook

        project_dir = str(tmp_path)
        hook_path = "/opt/claude-email/scripts/chat-session-start-hook.sh"
        inject_session_start_hook(project_dir, hook_path)

        settings_file = tmp_path / ".claude" / "settings.json"
        assert settings_file.exists()
        data = json.loads(settings_file.read_text())
        assert data == {
            "hooks": {
                "SessionStart": [{
                    "matcher": "startup|resume",
                    "hooks": [{"type": "command", "command": hook_path}],
                }]
            }
        }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_spawner.py::TestInjectSessionStartHook::test_creates_settings_file -v`
Expected: FAIL with `ImportError: cannot import name 'inject_session_start_hook' from 'src.spawner'`.

- [ ] **Step 3: Implement `inject_session_start_hook` in `src/spawner.py`**

Add this function near `inject_mcp_config` (right below it):

```python
def inject_session_start_hook(project_dir: str, hook_script_path: str) -> None:
    """Write .claude/settings.json so each session in project_dir invokes the
    SessionStart hook at hook_script_path. Merges with any existing settings.

    hook_script_path MUST be absolute — Claude Code resolves hook commands
    from the session cwd, not the repo root.
    """
    settings_dir = os.path.join(project_dir, ".claude")
    settings_path = os.path.join(settings_dir, "settings.json")
    os.makedirs(settings_dir, exist_ok=True)

    try:
        with open(settings_path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    hooks["SessionStart"] = [{
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": hook_script_path}],
    }]

    with open(settings_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info("Wrote SessionStart hook to %s", settings_path)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_spawner.py::TestInjectSessionStartHook::test_creates_settings_file -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/spawner.py tests/test_spawner.py
git commit -m "feat(spawner): inject_session_start_hook writes .claude/settings.json"
```

---

## Task 4: Test — merge with existing `.claude/settings.json`

**Files:**
- Modify: `tests/test_spawner.py`

- [ ] **Step 1: Add the failing test to `TestInjectSessionStartHook`**

```python
    def test_merges_existing_settings(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        existing = {
            "theme": "dark",
            "hooks": {
                "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": "/bin/true"}]}],
            },
        }
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps(existing))

        hook_path = "/opt/claude-email/scripts/chat-session-start-hook.sh"
        inject_session_start_hook(str(tmp_path), hook_path)

        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        # Untouched top-level field preserved
        assert data["theme"] == "dark"
        # Unrelated hook entry preserved
        assert data["hooks"]["UserPromptSubmit"] == existing["hooks"]["UserPromptSubmit"]
        # SessionStart hook added
        assert data["hooks"]["SessionStart"] == [{
            "matcher": "startup|resume",
            "hooks": [{"type": "command", "command": hook_path}],
        }]
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/pytest tests/test_spawner.py::TestInjectSessionStartHook::test_merges_existing_settings -v`
Expected: PASS (the implementation from Task 3 already handles merge).

- [ ] **Step 3: Commit**

```bash
git add tests/test_spawner.py
git commit -m "test(spawner): verify inject_session_start_hook merges existing settings"
```

---

## Task 5: Test — idempotent and replaces stale SessionStart entry

**Files:**
- Modify: `tests/test_spawner.py`

- [ ] **Step 1: Add two more tests to `TestInjectSessionStartHook`**

```python
    def test_is_idempotent(self, tmp_path):
        from src.spawner import inject_session_start_hook
        hook_path = "/opt/claude-email/scripts/chat-session-start-hook.sh"
        inject_session_start_hook(str(tmp_path), hook_path)
        inject_session_start_hook(str(tmp_path), hook_path)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert len(data["hooks"]["SessionStart"]) == 1

    def test_replaces_stale_session_start_when_path_changes(self, tmp_path):
        from src.spawner import inject_session_start_hook
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
            "hooks": {"SessionStart": [{
                "matcher": "startup",
                "hooks": [{"type": "command", "command": "/old/path/hook.sh"}],
            }]}
        }))
        new_path = "/new/path/hook.sh"
        inject_session_start_hook(str(tmp_path), new_path)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert cmd == new_path
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/pytest tests/test_spawner.py::TestInjectSessionStartHook -v`
Expected: all four tests in the class PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_spawner.py
git commit -m "test(spawner): inject_session_start_hook is idempotent and refreshes stale path"
```

---

## Task 6: Test — `spawn_agent` writes both `.mcp.json` and `.claude/settings.json`

**Files:**
- Modify: `tests/test_spawner.py`
- Modify: `src/spawner.py`

- [ ] **Step 1: Add a failing test at the end of `TestSpawnAgent`**

```python
    def test_spawn_agent_writes_session_start_hook(self, db, tmp_path, mocker):
        from src.spawner import spawn_agent

        mock_proc = mocker.MagicMock()
        mock_proc.pid = 101
        mocker.patch("src.spawner.subprocess.Popen", return_value=mock_proc)
        # Let the real injection helpers run against tmp_path
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()

        spawn_agent(db, str(project_dir), "http://localhost:8080/mcp")

        assert (project_dir / ".mcp.json").exists()
        settings = json.loads((project_dir / ".claude" / "settings.json").read_text())
        cmd = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        # Absolute path from claude-email install; we assert shape, not literal.
        assert os.path.isabs(cmd)
        assert cmd.endswith("/scripts/chat-session-start-hook.sh")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_spawner.py::TestSpawnAgent::test_spawn_agent_writes_session_start_hook -v`
Expected: FAIL — `.claude/settings.json` is not created.

- [ ] **Step 3: Wire `inject_session_start_hook` into `spawn_agent`**

In `src/spawner.py`, add a module-level constant and extend `spawn_agent`. At the top of the file, add:

```python
_HOOK_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "chat-session-start-hook.sh",
)
```

In `spawn_agent`, immediately after the existing `inject_mcp_config(project_dir, chat_url)` line, add:

```python
    inject_session_start_hook(project_dir, _HOOK_SCRIPT)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_spawner.py::TestSpawnAgent::test_spawn_agent_writes_session_start_hook -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite to check nothing else broke**

Run: `.venv/bin/pytest tests/ -q`
Expected: all tests pass. Many `TestSpawnAgent` tests previously mocked `inject_mcp_config` only — they may now try to write real `.claude/settings.json` files into `tmp_path`. Those writes are fine (tmp_path is scoped). If any test fails because it asserts on file absence, add a `mocker.patch("src.spawner.inject_session_start_hook")` to that test.

- [ ] **Step 6: Commit**

```bash
git add src/spawner.py tests/test_spawner.py
git commit -m "feat(spawner): spawn_agent writes SessionStart hook alongside .mcp.json"
```

---

## Task 7: Enforce absolute hook path

**Files:**
- Modify: `tests/test_spawner.py`
- Modify: `src/spawner.py`

- [ ] **Step 1: Add a failing test**

Append to `TestInjectSessionStartHook`:

```python
    def test_rejects_relative_hook_path(self, tmp_path):
        from src.spawner import inject_session_start_hook
        with pytest.raises(ValueError, match="absolute"):
            inject_session_start_hook(str(tmp_path), "scripts/chat-session-start-hook.sh")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_spawner.py::TestInjectSessionStartHook::test_rejects_relative_hook_path -v`
Expected: FAIL — no ValueError raised.

- [ ] **Step 3: Add the guard to `inject_session_start_hook`**

At the very top of the function body, add:

```python
    if not os.path.isabs(hook_script_path):
        raise ValueError(
            f"hook_script_path must be absolute; got {hook_script_path!r}"
        )
```

- [ ] **Step 4: Run the test**

Run: `.venv/bin/pytest tests/test_spawner.py::TestInjectSessionStartHook -v`
Expected: all five tests in the class PASS.

- [ ] **Step 5: Commit**

```bash
git add src/spawner.py tests/test_spawner.py
git commit -m "feat(spawner): inject_session_start_hook requires absolute path"
```

---

## Task 8: Check and (if needed) split `src/spawner.py`

**Files:**
- Possibly modify: `src/spawner.py`
- Possibly create: `src/agent_bootstrap.py`

- [ ] **Step 1: Check current line count**

Run: `scripts/check-line-limit.sh`
Expected: PASS. The project rule is ≤200 lines per file.

- [ ] **Step 2: If `src/spawner.py` exceeds 200 lines, extract the injection helpers**

If the check fails:

  1. Create `src/agent_bootstrap.py` with `_CHAT_MCP_SERVER_NAME`, `inject_mcp_config`, `inject_session_start_hook`, `approve_mcp_server_for_project`, and `_HOOK_SCRIPT`.
  2. In `src/spawner.py`, replace those definitions with `from src.agent_bootstrap import inject_mcp_config, inject_session_start_hook, approve_mcp_server_for_project, _HOOK_SCRIPT, _CHAT_MCP_SERVER_NAME`.
  3. Update imports in tests (`tests/test_spawner.py`) — leave the `from src.spawner import X` style in place; re-export the names from spawner so tests still pass.
  4. Rerun: `.venv/bin/pytest tests/ -q` — expect all green.
  5. Rerun: `scripts/check-line-limit.sh` — expect PASS.

- [ ] **Step 3: Commit (skip if no change was needed)**

```bash
git add src/spawner.py src/agent_bootstrap.py
git commit -m "refactor(spawner): extract injection helpers to agent_bootstrap module"
```

---

## Task 9: Extend `scripts/install-chat-mcp.py` to also install the hook

**Files:**
- Modify: `scripts/install-chat-mcp.py`
- Modify: `tests/test_spawner.py` (no — see Step 4)

- [ ] **Step 1: Read the current script**

Run: `cat scripts/install-chat-mcp.py`
Note the skip list (it currently skips `claude-email`) and the call to `inject_mcp_config`.

- [ ] **Step 2: Remove `claude-email` from the skip list**

Per the 2026-04-19 spec, claude-email now participates on the bus as `agent-claude-email`. Edit the `SKIP_NAMES` constant so it is an empty set:

```python
SKIP_NAMES: set[str] = set()
```

- [ ] **Step 3: Import and invoke `inject_session_start_hook` in the same loop**

At the top:

```python
from src.spawner import inject_mcp_config, inject_session_start_hook, _HOOK_SCRIPT  # noqa: E402
```

In the loop that calls `inject_mcp_config(str(d), chat_url)`, add on the next line:

```python
        inject_session_start_hook(str(d), _HOOK_SCRIPT)
```

- [ ] **Step 4: Manual smoke — run the script against `/tmp` as base**

Run:
```
mkdir -p /tmp/install-mcp-smoke/proj-a
CHAT_URL=http://127.0.0.1:8420/sse .venv/bin/python scripts/install-chat-mcp.py /tmp/install-mcp-smoke
```
Expected: stdout lists `/tmp/install-mcp-smoke/proj-a` as touched; `/tmp/install-mcp-smoke/proj-a/.mcp.json` and `/tmp/install-mcp-smoke/proj-a/.claude/settings.json` both exist.

Cleanup: `rm -rf /tmp/install-mcp-smoke`.

- [ ] **Step 5: Commit**

```bash
git add scripts/install-chat-mcp.py
git commit -m "feat(install): bootstrap SessionStart hook alongside .mcp.json"
```

---

## Task 10: Migrate the two already-bootstrapped projects

**Files:**
- Touch: `/home/cocodedk/0-projects/claude-email/.claude/settings.json`
- Touch: `/home/cocodedk/0-projects/Dune-Browser-Game/.claude/settings.json`

- [ ] **Step 1: Run the install script against the live projects base**

Run (substitute your actual base path):
```
CHAT_URL=http://127.0.0.1:8420/sse .venv/bin/python scripts/install-chat-mcp.py /home/cocodedk/0-projects
```
Expected: both `claude-email` and `Dune-Browser-Game` appear in the touched list and now contain `.claude/settings.json`.

- [ ] **Step 2: Inspect the written settings**

Run: `jq . /home/cocodedk/0-projects/claude-email/.claude/settings.json`
Expected: a JSON object with `hooks.SessionStart[0].hooks[0].command` pointing at the absolute path of `chat-session-start-hook.sh` inside the claude-email install.

Run the same on `/home/cocodedk/0-projects/Dune-Browser-Game/.claude/settings.json`.

- [ ] **Step 3: Commit the new `.claude/settings.json` in the claude-email repo only**

(The Dune-Browser-Game change belongs to that repo and is out of scope for this commit.)

```bash
git add .claude/settings.json
git commit -m "chore(chat): install SessionStart hook for claude-email's own sessions"
```

---

## Task 11: End-to-end verification

**Files:** none

- [ ] **Step 1: Open a new Claude Code session in a throwaway project**

```
mkdir -p /tmp/e2e-auto-reg && cd /tmp/e2e-auto-reg
CHAT_URL=http://127.0.0.1:8420/sse /home/cocodedk/0-projects/claude-email/.venv/bin/python /home/cocodedk/0-projects/claude-email/scripts/install-chat-mcp.py /tmp
claude --print "what is your caller name?"
```

Expected: the `--print` output mentions `agent-e2e-auto-reg` or shows it registered with that name — evidence that the SessionStart hook delivered the instruction and the agent acted on it.

- [ ] **Step 2: Verify it registered on the bus**

Run (from the claude-email repo):
```
.venv/bin/python -c "from src.chat_db import ChatDB; import json; db=ChatDB('claude-chat.db'); print(json.dumps([a['name'] for a in db.list_agents()], indent=2))"
```
Expected: `agent-e2e-auto-reg` appears in the list with `status == "running"` or `"disconnected"` (the one-shot exited).

- [ ] **Step 3: Clean up**

Run: `rm -rf /tmp/e2e-auto-reg`

No commit — this is verification only.

---

## Task 12: Update user-facing docs (per CLAUDE.md lockstep rule)

**Files:**
- Modify: `README.md`
- Modify: `website/index.html`
- Modify: `website/fa/index.html`

- [ ] **Step 1: Add a "How agents auto-register" subsection to `README.md`**

Near the existing chat section, add a subsection that says, in plain English, that every session opened in a project bootstrapped by `scripts/install-chat-mcp.py` receives both a `.mcp.json` entry for claude-chat and a `.claude/settings.json` SessionStart hook that instructs the agent to register and how to use the bus. Keep it under 15 lines. Reference `scripts/chat-agent-instruction.txt` as the source of truth for the instruction text.

- [ ] **Step 2: Mirror the change in `website/index.html` and `website/fa/index.html`**

Both files must be updated in the same commit (CLAUDE.md rule: "update ... in lockstep"). In the English page, add the same content in the existing feature list / "How it works" area. In the Farsi page, translate (or ask the user to translate — if so, leave a clearly-marked `<!-- TRANSLATE: ... -->` block for them rather than emitting machine translation).

- [ ] **Step 3: Commit**

```bash
git add README.md website/index.html website/fa/index.html
git commit -m "docs: explain auto-register SessionStart hook"
```

---

## Task 13: Final verification and review request

**Files:** none

- [ ] **Step 1: Full test suite, line-limit, and coverage**

Run: `.venv/bin/pytest tests/ --cov=src --cov=chat --cov-report=term-missing -q`
Expected: 100% coverage on production code (per project CLAUDE.md); the new `inject_session_start_hook` and its `_HOOK_SCRIPT` constant must be covered by the tests added in Tasks 3–7.

Run: `scripts/check-line-limit.sh`
Expected: PASS.

- [ ] **Step 2: Invoke `simplify`**

Per project CLAUDE.md: "After implementing — reviewing quality → `simplify`." Run the skill on the new/changed files.

- [ ] **Step 3: Invoke `superpowers:requesting-code-review`**

Per project CLAUDE.md. Address any findings before merging.

- [ ] **Step 4: Restart services — requires explicit user confirmation**

Do NOT run `systemctl --user restart claude-email.service` without asking; spawned child agents may be disrupted. Ask the user whether to restart now or wait. If approved, run:

```bash
systemctl --user restart claude-chat.service claude-email.service
systemctl --user status claude-chat.service claude-email.service --no-pager | head -20
```

Expected: both services `active (running)`.

---

## Self-Review Notes

1. **Spec coverage**
   - §1 Problem — addressed by Tasks 3–6 (auto-register) and Task 2 (instruction delivery).
   - §2 Decision (SessionStart hook) — implemented in Tasks 2, 3, 6.
   - §3 Instruction text — Task 1.
   - §4.1 `.claude/settings.json` shape — Task 3 test enforces it.
   - §4.2 Hook script — Task 2.
   - §4.3 Instruction file — Task 1.
   - §4.4 Spawner changes — Task 6.
   - §4.5 Migration — Tasks 9 and 10.
   - §5 Testing — Tasks 3–7 for unit tests; Task 11 for e2e.
   - §6 Rollout — Tasks 10–13.
   - §7 Risks (identity collision, hook not executable, path drift, instruction drift, prompt compliance ≠ determinism) — documented in spec; no new code needed this iteration.
   - §8 Future work (identity redesign) — explicitly out of scope.

2. **Placeholder scan** — none; every step has concrete code or commands.

3. **Type consistency** — `inject_session_start_hook(project_dir: str, hook_script_path: str) -> None` used consistently across tasks. `_HOOK_SCRIPT` module-level constant same name in Tasks 6, 8, 9.
