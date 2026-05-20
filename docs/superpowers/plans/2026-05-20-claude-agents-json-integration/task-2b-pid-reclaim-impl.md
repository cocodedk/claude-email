> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

- [ ] **Step 2: Run tests to confirm they fail**

```
.venv/bin/pytest tests/test_chat_pid_reclaim_session_pid.py -v
```

Expected: `AttributeError: module 'src.chat_pid_reclaim' has no attribute 'find_session_pid_for_cwd'`

- [ ] **Step 3: Update `src/chat_pid_reclaim.py`**

Add the import and `_CLAUDE_BIN` constant. Change the import line from:

```python
from src.process_liveness import find_ancestor_pid_matching, is_ancestor_or_self
```

to:

```python
from src.process_liveness import (
    find_ancestor_pid_matching, find_session_pid_for_cwd, is_ancestor_or_self,
)
```

Add `_CLAUDE_BIN` after `_CLAUDE_CMDLINE_MARKER`:

```python
_CLAUDE_CMDLINE_MARKER = os.environ.get("CLAUDE_PROCESS_MARKER", "claude")
_CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
```

In `reclaim_pid_best_effort`, replace:

```python
        claude_pid = find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER)
        if claude_pid is None:
            return
        agent = db.get_agent(caller)
        if agent is None:
            return
        if agent.get("pid") == claude_pid:
            return
```

with:

```python
        agent = db.get_agent(caller)
        if agent is None:
            return
        stored_pid = agent.get("pid")
        if stored_pid and is_ancestor_or_self(stored_pid):
            return  # stored pid IS our ancestor — row already correct
        claude_pid = find_session_pid_for_cwd(cwd, claude_bin=_CLAUDE_BIN)
        if claude_pid is None:
            claude_pid = find_ancestor_pid_matching(_CLAUDE_CMDLINE_MARKER)
        if claude_pid is None:
            return
        if stored_pid == claude_pid:
            return
```

The `is_ancestor_or_self` early-exit means `find_session_pid_for_cwd` (a subprocess call) only fires when the stored PID is stale or missing — not on every drain tick when the session is healthy.

- [ ] **Step 4: Patch existing pid reclaim tests to mock the new function**

In `tests/test_chat_drain_inbox_pid_reclaim_noops.py`, `tests/test_chat_drain_inbox_pid_reclaim_drain.py`, and `tests/test_chat_drain_inbox_pid_reclaim_force.py`, find each `_prepare` or `monkeypatch.setattr` block that mocks `find_ancestor_pid_matching` and add a parallel mock for `find_session_pid_for_cwd` returning `None` so the tests exercise the PPID fallback path (their existing assertions remain valid):

In each file's `_prepare` or equivalent setup helper, add after the existing `monkeypatch.setattr(chat_pid_reclaim, "find_ancestor_pid_matching", ...)`:

```python
        monkeypatch.setattr(
            chat_pid_reclaim, "find_session_pid_for_cwd",
            lambda cwd, **kw: None,
        )
```

- [ ] **Step 5: Run the full pid reclaim test suite**

```
.venv/bin/pytest tests/test_chat_pid_reclaim_session_pid.py tests/test_chat_drain_inbox_pid_reclaim_noops.py tests/test_chat_drain_inbox_pid_reclaim_drain.py tests/test_chat_drain_inbox_pid_reclaim_force.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```
.venv/bin/pytest tests/ -q
```

Expected: 1590 + new tests passed.

- [ ] **Step 7: Commit**

```bash
git add src/chat_pid_reclaim.py \
    tests/test_chat_pid_reclaim_session_pid.py \
    tests/test_chat_drain_inbox_pid_reclaim_noops.py \
    tests/test_chat_drain_inbox_pid_reclaim_drain.py \
    tests/test_chat_drain_inbox_pid_reclaim_force.py
git commit -m "feat(chat_pid_reclaim): use find_session_pid_for_cwd as primary PID source"
```
