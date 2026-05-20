> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

## Task 4: Wire `chat-stop-hook.py` into `agent_bootstrap.py`

**Files:**
- Modify: `src/agent_bootstrap.py` (currently 179 lines)
- Modify: `tests/test_spawner_session_hook.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_spawner_session_hook.py`, find `test_creates_settings_file_with_all_events` and update its expected `Stop` hooks block. The test currently asserts:

```python
"Stop": [{
    "matcher": "",
    "hooks": [{"type": "command", "command": self.DRAIN}],
}],
```

Change it to:

```python
"Stop": [{
    "matcher": "",
    "hooks": [
        {"type": "command", "command": self.DRAIN},
        {"type": "command", "command": stop_hook},
    ],
}],
```

And add `stop_hook = "/opt/claude-email/scripts/chat-stop-hook.py"` at the top of the test method, plus pass it to `inject_session_start_hook` as a sixth positional argument.

- [ ] **Step 2: Run the test to confirm it fails**

```
.venv/bin/pytest tests/test_spawner_session_hook.py::TestInjectSessionStartHook::test_creates_settings_file_with_all_events -v
```

Expected: FAIL — Stop hook list has 1 command, expected 2.

- [ ] **Step 3: Update `src/agent_bootstrap.py`**

Add `_resolve_script` helper at module level (after the existing imports, before the constants) to replace the repeated `if None → default; if not isabs → raise` pattern that appears for each script parameter:

```python
def _resolve_script(path: str | None, default: str, name: str) -> str:
    if path is None:
        path = default
    if not os.path.isabs(path):
        raise ValueError(f"{name} must be absolute; got {path!r}")
    return path
```

Add the constant after `POSTTOOL_DRAIN_SCRIPT`:

```python
STOP_HOOK_SCRIPT = os.path.join(_SCRIPTS, "chat-stop-hook.py")
```

Replace all five validation blocks in `inject_session_start_hook` with `_resolve_script` calls:

```python
    hook_script_path = _resolve_script(hook_script_path, HOOK_SCRIPT, "hook_script_path")
    drain_script_path = _resolve_script(drain_script_path, DRAIN_SCRIPT, "drain_script_path")
    precompact_script_path = _resolve_script(
        precompact_script_path, PRECOMPACT_SCRIPT, "precompact_script_path",
    )
    posttool_drain_script_path = _resolve_script(
        posttool_drain_script_path, POSTTOOL_DRAIN_SCRIPT, "posttool_drain_script_path",
    )
    stop_hook_script_path = _resolve_script(
        stop_hook_script_path, STOP_HOOK_SCRIPT, "stop_hook_script_path",
    )
```

Add `stop_hook_script_path: str | None = None` as the last parameter of `inject_session_start_hook`.

Change the Stop event merge from:

```python
    _merge_hook_event(
        hooks, "Stop", "",
        [drain_script_path],
    )
```

to:

```python
    _merge_hook_event(
        hooks, "Stop", "",
        [drain_script_path, stop_hook_script_path],
    )
```

Update the `logger.info` call at the bottom to mention `Stop` now includes two scripts.

- [ ] **Step 4: Run the updated bootstrap test**

```
.venv/bin/pytest tests/test_spawner_session_hook.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```
.venv/bin/pytest tests/ -q
```

Expected: all tests pass. Note the new total.

- [ ] **Step 5b: Register `chat-stop-hook.py` in `src/hook_merge.py`**

Open `src/hook_merge.py` and find `_OUR_SCRIPT_BASENAMES` (line ~9). Add `"chat-stop-hook.py"` so the idempotency guard recognises the new stop hook and won't inject it twice on subsequent calls:

```python
_OUR_SCRIPT_BASENAMES = {
    "chat-session-start-hook.sh",
    "chat-drain-inbox.py",
    "chat-precompact-hook.py",
    "chat-drain-on-bash-commit.sh",
    "chat-stop-hook.py",
}
```

Confirm the existing `test_spawner_session_hook.py` idempotency tests still pass after this change:

```
.venv/bin/pytest tests/test_spawner_session_hook.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/agent_bootstrap.py src/hook_merge.py tests/test_spawner_session_hook.py
git commit -m "feat(agent_bootstrap): wire chat-stop-hook.py into Stop event"
```
