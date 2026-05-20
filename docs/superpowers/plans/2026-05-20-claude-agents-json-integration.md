# claude agents --json Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fragile `/proc`-based PPID chain walk with `claude agents --json` as the primary PID-lookup strategy, and add a Stop hook that logs pending work on session exit.

**Architecture:** `find_session_pid_for_cwd()` is added to `process_liveness.py`; `chat_pid_reclaim.py` calls it first and falls back to `find_ancestor_pid_matching` on None. A new `scripts/chat-stop-hook.py` logs a flow event when stopping with background tasks or crons pending; `agent_bootstrap.py` wires it into the Stop hook event alongside the existing drain script.

**Tech Stack:** Python 3.12, subprocess (shell=False), json, pytest monkeypatch

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/hook_utils.py` | Create | Shared hook helpers: `resolved_db_path`, `caller_name`, `read_hook_payload` |
| `src/process_liveness.py` | Modify | Add `find_session_pid_for_cwd()` |
| `src/chat_pid_reclaim.py` | Modify | Use new function when stored PID is stale; PPID walk as fallback |
| `src/agent_bootstrap.py` | Modify | Add `_resolve_script()` helper; `STOP_HOOK_SCRIPT`; wire into Stop event |
| `scripts/chat-precompact-hook.py` | Modify | Import shared helpers from `src/hook_utils` |
| `scripts/chat-stop-hook.py` | Create | Read Stop payload; log flow event on pending work |
| `tests/test_hook_utils.py` | Create | Tests for `src/hook_utils` shared helpers |
| `tests/test_process_liveness_session_pid.py` | Create | Tests for `find_session_pid_for_cwd` |
| `tests/test_chat_pid_reclaim_session_pid.py` | Create | Tests for updated reclaim order (new primary path) |
| `tests/test_chat_drain_inbox_pid_reclaim_noops.py` | Modify | Mock new function so existing tests still test PPID-walk fallback |
| `tests/test_chat_drain_inbox_pid_reclaim_drain.py` | Modify | Same — mock new function to return None |
| `tests/test_chat_drain_inbox_pid_reclaim_force.py` | Modify | Same |
| `tests/test_spawner_session_hook.py` | Modify | Assert Stop hook now includes both drain + stop-hook scripts |
| `tests/test_chat_stop_hook_skip.py` | Create | Subagent skip, stdin edge cases |
| `tests/test_chat_stop_hook_emission.py` | Create | Flow event emitted for background_tasks / session_crons |
| `tests/test_chat_stop_hook_fail_open.py` | Create | DB errors exit 0 |

---

## Task 0: Extract `src/hook_utils.py` — shared hook helpers

`_resolved_db_path`, `_caller_name`, and `_read_hook_payload` are duplicated verbatim between `chat-precompact-hook.py` and the new `chat-stop-hook.py`. Extract them once and import.

**Files:**
- Create: `src/hook_utils.py`
- Modify: `scripts/chat-precompact-hook.py`
- Create: `tests/test_hook_utils.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hook_utils.py`:

```python
"""Tests for src.hook_utils — shared helpers for hook scripts."""
import io
import json
import os
import sys
from pathlib import Path

import pytest


class TestResolvedDbPath:
    def test_absolute_path_returned_as_is(self, monkeypatch, tmp_path):
        from src.hook_utils import resolved_db_path
        p = tmp_path / "bus.db"
        monkeypatch.setenv("CHAT_DB_PATH", str(p))
        assert resolved_db_path(tmp_path) == p

    def test_relative_path_resolved_against_root(self, monkeypatch, tmp_path):
        from src.hook_utils import resolved_db_path
        monkeypatch.setenv("CHAT_DB_PATH", "bus.db")
        assert resolved_db_path(tmp_path) == tmp_path / "bus.db"

    def test_raises_when_env_not_set(self, monkeypatch, tmp_path):
        from src.hook_utils import resolved_db_path
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        with pytest.raises(RuntimeError, match="CHAT_DB_PATH"):
            resolved_db_path(tmp_path)


class TestCallerName:
    def test_uses_env_var_when_set(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-foo")
        monkeypatch.chdir(tmp_path)
        assert caller_name() == "agent-foo"

    def test_falls_back_to_cwd_basename(self, monkeypatch, tmp_path):
        from src.hook_utils import caller_name
        monkeypatch.delenv("CLAUDE_AGENT_NAME", raising=False)
        project = tmp_path / "myproject"
        project.mkdir()
        monkeypatch.chdir(project)
        assert caller_name() == "agent-myproject"


class TestReadHookPayload:
    def test_parses_json_from_stdin(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"key": "val"})))
        assert read_hook_payload() == {"key": "val"}

    def test_returns_empty_on_tty(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        class FakeTty:
            def isatty(self): return True
            def read(self): return ""
        monkeypatch.setattr(sys, "stdin", FakeTty())
        assert read_hook_payload() == {}

    def test_returns_empty_on_invalid_json(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        monkeypatch.setattr(sys, "stdin", io.StringIO("not-json"))
        assert read_hook_payload() == {}

    def test_returns_empty_on_empty_stdin(self, monkeypatch):
        from src.hook_utils import read_hook_payload
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        assert read_hook_payload() == {}
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.venv/bin/pytest tests/test_hook_utils.py -v
```

Expected: `ModuleNotFoundError: No module named 'src.hook_utils'`

- [ ] **Step 3: Create `src/hook_utils.py`**

```python
"""Shared helpers for claude-email hook scripts.

Extracted from chat-precompact-hook.py to avoid duplication across hook
scripts. Import these instead of re-implementing in each script.
"""
import json
import os
import sys
from pathlib import Path, PurePosixPath

from src.agent_name import ENV_VAR_NAME, validated_agent_name


def resolved_db_path(root: Path) -> Path:
    """Return the absolute Path to the chat DB, or raise RuntimeError."""
    raw = os.environ.get("CHAT_DB_PATH", "")
    if not raw:
        raise RuntimeError(
            "CHAT_DB_PATH not set — expected it in .env (e.g. claude-chat.db).",
        )
    p = Path(raw)
    return p if p.is_absolute() else root / p


def caller_name() -> str:
    """Return the agent name from env, falling back to cwd basename."""
    fallback = "agent-" + PurePosixPath(os.getcwd()).name
    return validated_agent_name(os.environ.get(ENV_VAR_NAME), fallback)


def read_hook_payload() -> dict:
    """Read and parse the JSON hook payload from stdin. Returns {} on any failure."""
    try:
        if sys.stdin.isatty():
            return {}
        data = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not data.strip():
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return {}
```

- [ ] **Step 4: Run tests to confirm they pass**

```
.venv/bin/pytest tests/test_hook_utils.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Update `scripts/chat-precompact-hook.py` to import from `src/hook_utils`**

Replace the three duplicate function definitions (`_resolved_db_path`, `_caller_name`, `_read_hook_payload`) with imports from `src.hook_utils`. Change the import block from:

```python
from src.agent_name import ENV_VAR_NAME, validated_agent_name  # noqa: E402
from src.chat_db import ChatDB  # noqa: E402
from src.chat_pid_reclaim import reclaim_pid_best_effort  # noqa: E402
from src.process_liveness import is_alive, is_ancestor_or_self  # noqa: E402
```

to:

```python
from src.chat_db import ChatDB  # noqa: E402
from src.chat_pid_reclaim import reclaim_pid_best_effort  # noqa: E402
from src.hook_utils import caller_name as _caller_name  # noqa: E402
from src.hook_utils import read_hook_payload as _read_hook_payload  # noqa: E402
from src.hook_utils import resolved_db_path as _resolved_db_path  # noqa: E402
from src.process_liveness import is_alive, is_ancestor_or_self  # noqa: E402
```

Delete the three function bodies (`_resolved_db_path`, `_caller_name`, `_read_hook_payload`) from the script. Update the two call sites: `_resolved_db_path()` → `_resolved_db_path(ROOT)` and `_caller_name()` → `_caller_name()` (unchanged). Also remove the `from pathlib import Path, PurePosixPath` import since `PurePosixPath` was only used in `_caller_name` — keep only `Path`.

- [ ] **Step 6: Run the precompact hook tests to confirm no regressions**

```
.venv/bin/pytest tests/test_chat_precompact_hook_emission.py tests/test_chat_precompact_hook_fail_open.py tests/test_chat_precompact_hook_skip.py tests/test_chat_precompact_hook_misc.py -v
```

Expected: all pass.

- [ ] **Step 7: Run full suite**

```
.venv/bin/pytest tests/ -q
```

Expected: 1582 + 9 = 1591 passed.

- [ ] **Step 8: Commit**

```bash
git add src/hook_utils.py tests/test_hook_utils.py scripts/chat-precompact-hook.py
git commit -m "refactor(hooks): extract shared helpers into src/hook_utils.py"
```

---

## Task 1: `find_session_pid_for_cwd` in `process_liveness.py`

**Files:**
- Modify: `src/process_liveness.py` (currently 119 lines)
- Create: `tests/test_process_liveness_session_pid.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_process_liveness_session_pid.py`:

```python
"""Tests for src.process_liveness.find_session_pid_for_cwd."""
import json
import subprocess

import pytest


class TestFindSessionPidForCwd:

    def _run(self, monkeypatch, sessions: list, cwd: str):
        import src.process_liveness as pl
        monkeypatch.setattr(
            pl.subprocess, "run",
            lambda cmd, **kw: type(
                "R", (), {"stdout": json.dumps(sessions), "returncode": 0}
            )(),
        )
        return pl.find_session_pid_for_cwd(cwd)

    def test_matches_exact_cwd(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "cwd": str(tmp_path), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) == 42

    def test_returns_none_when_no_cwd_match(self, monkeypatch, tmp_path):
        sessions = [{"pid": 99, "cwd": str(tmp_path / "other"), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_picks_highest_started_at_on_multiple_matches(self, monkeypatch, tmp_path):
        sessions = [
            {"pid": 10, "cwd": str(tmp_path), "startedAt": 500},
            {"pid": 20, "cwd": str(tmp_path), "startedAt": 1500},
            {"pid": 15, "cwd": str(tmp_path), "startedAt": 1000},
        ]
        assert self._run(monkeypatch, sessions, str(tmp_path)) == 20

    def test_returns_none_on_non_positive_pid(self, monkeypatch, tmp_path):
        sessions = [{"pid": 0, "cwd": str(tmp_path), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_returns_none_when_cwd_field_missing(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_returns_none_when_cwd_field_empty(self, monkeypatch, tmp_path):
        sessions = [{"pid": 42, "cwd": "", "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(tmp_path)) is None

    def test_returns_none_on_subprocess_exception(self, monkeypatch, tmp_path):
        import src.process_liveness as pl
        monkeypatch.setattr(
            pl.subprocess, "run",
            lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("no claude")),
        )
        assert pl.find_session_pid_for_cwd(str(tmp_path)) is None

    def test_returns_none_on_json_decode_error(self, monkeypatch, tmp_path):
        import src.process_liveness as pl
        monkeypatch.setattr(
            pl.subprocess, "run",
            lambda *a, **kw: type("R", (), {"stdout": "not-json", "returncode": 0})(),
        )
        assert pl.find_session_pid_for_cwd(str(tmp_path)) is None

    def test_uses_custom_claude_bin(self, monkeypatch, tmp_path):
        import src.process_liveness as pl
        captured = {}
        sessions = [{"pid": 77, "cwd": str(tmp_path), "startedAt": 1000}]
        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return type("R", (), {"stdout": json.dumps(sessions)})()
        monkeypatch.setattr(pl.subprocess, "run", fake_run)
        pl.find_session_pid_for_cwd(str(tmp_path), claude_bin="/opt/bin/claude")
        assert captured["cmd"][0] == "/opt/bin/claude"

    def test_resolves_cwd_symlinks(self, monkeypatch, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        sessions = [{"pid": 55, "cwd": str(real), "startedAt": 1000}]
        assert self._run(monkeypatch, sessions, str(link)) == 55
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.venv/bin/pytest tests/test_process_liveness_session_pid.py -v
```

Expected: `ImportError: cannot import name 'find_session_pid_for_cwd'`

- [ ] **Step 3: Add `find_session_pid_for_cwd` to `src/process_liveness.py`**

Add `import json` and `import subprocess` to the existing imports block at the top of the file (after `import os`):

```python
import json
import os
import subprocess
from pathlib import Path, PurePosixPath
```

Add this function after `find_ancestor_pid_matching` (before `is_alive`):

```python
def find_session_pid_for_cwd(
    cwd: str,
    claude_bin: str = "claude",
) -> int | None:
    """Return the PID of the live Claude session whose cwd matches ``cwd``.

    Runs ``[claude_bin, "agents", "--json"]`` (shell=False, timeout=5) and
    returns the pid of the session whose cwd resolves to the same path.
    When multiple sessions share the cwd, returns the one with the highest
    ``startedAt`` (most recently started). Returns None on any failure or
    no match — callers fall back to find_ancestor_pid_matching.
    """
    try:
        result = subprocess.run(
            [claude_bin, "agents", "--json"],
            capture_output=True, text=True, timeout=5, shell=False,
        )
        sessions = json.loads(result.stdout)
    except Exception:
        return None
    resolved = str(Path(cwd).resolve())
    matches = [
        s for s in sessions
        if isinstance(s, dict)
        and s.get("cwd")
        and str(Path(s["cwd"]).resolve()) == resolved
    ]
    if not matches:
        return None
    best = max(matches, key=lambda s: s.get("startedAt", 0))
    pid = best.get("pid")
    return pid if isinstance(pid, int) and pid > 0 else None
```

- [ ] **Step 4: Run tests to confirm they pass**

```
.venv/bin/pytest tests/test_process_liveness_session_pid.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Run full suite to confirm no regressions**

```
.venv/bin/pytest tests/ -q
```

Expected: 1591 passed (+ 10 new = 1601 passed).

- [ ] **Step 6: Commit**

```bash
git add src/process_liveness.py tests/test_process_liveness_session_pid.py
git commit -m "feat(process_liveness): add find_session_pid_for_cwd via claude agents --json"
```

---

## Task 2: Update `chat_pid_reclaim.py` — new function as primary

**Files:**
- Modify: `src/chat_pid_reclaim.py`
- Create: `tests/test_chat_pid_reclaim_session_pid.py`
- Modify: `tests/test_chat_drain_inbox_pid_reclaim_noops.py`
- Modify: `tests/test_chat_drain_inbox_pid_reclaim_drain.py`
- Modify: `tests/test_chat_drain_inbox_pid_reclaim_force.py`

- [ ] **Step 1: Write failing tests for the new primary path**

Create `tests/test_chat_pid_reclaim_session_pid.py`:

```python
"""Tests for chat_pid_reclaim — find_session_pid_for_cwd as primary path."""
import os

import pytest

from src.chat_db import ChatDB


class TestReclaimPrimaryPath:
    """find_session_pid_for_cwd is tried first; PPID walk is fallback."""

    def _setup(self, tmp_path, monkeypatch, session_pid, ancestor_pid):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        project = tmp_path / "proj"
        project.mkdir()
        monkeypatch.chdir(project)
        from src import chat_pid_reclaim
        monkeypatch.setattr(
            chat_pid_reclaim, "find_session_pid_for_cwd",
            lambda cwd, **kw: session_pid,
        )
        monkeypatch.setattr(
            chat_pid_reclaim, "find_ancestor_pid_matching",
            lambda _: ancestor_pid,
        )
        return db, project

    def test_uses_session_pid_when_available(self, tmp_path, monkeypatch):
        me = os.getpid()
        db, project = self._setup(tmp_path, monkeypatch, me, None)
        db.register_agent("agent-proj", str(project), pid=99_999_999)
        from src.chat_pid_reclaim import reclaim_pid_best_effort
        reclaim_pid_best_effort(db, "agent-proj", str(project))
        assert db.get_agent("agent-proj")["pid"] == me

    def test_falls_back_to_ppid_walk_when_session_lookup_returns_none(
        self, tmp_path, monkeypatch,
    ):
        me = os.getpid()
        db, project = self._setup(tmp_path, monkeypatch, None, me)
        db.register_agent("agent-proj", str(project), pid=99_999_999)
        from src.chat_pid_reclaim import reclaim_pid_best_effort
        reclaim_pid_best_effort(db, "agent-proj", str(project))
        assert db.get_agent("agent-proj")["pid"] == me

    def test_noop_when_both_sources_return_none(self, tmp_path, monkeypatch):
        db, project = self._setup(tmp_path, monkeypatch, None, None)
        db.register_agent("agent-proj", str(project), pid=12345)
        from src.chat_pid_reclaim import reclaim_pid_best_effort
        reclaim_pid_best_effort(db, "agent-proj", str(project))
        assert db.get_agent("agent-proj")["pid"] == 12345

    def test_ppid_walk_not_called_when_session_pid_found(
        self, tmp_path, monkeypatch,
    ):
        me = os.getpid()
        calls: list = []
        db, project = self._setup(tmp_path, monkeypatch, me, None)
        from src import chat_pid_reclaim
        monkeypatch.setattr(
            chat_pid_reclaim, "find_ancestor_pid_matching",
            lambda _: calls.append("called") or None,
        )
        db.register_agent("agent-proj", str(project), pid=None)
        from src.chat_pid_reclaim import reclaim_pid_best_effort
        reclaim_pid_best_effort(db, "agent-proj", str(project))
        assert calls == []

    def test_skips_subprocess_when_stored_pid_is_ancestor(
        self, tmp_path, monkeypatch,
    ):
        """Hot-path guard: if stored pid IS our ancestor, skip the subprocess call."""
        me = os.getpid()
        db, project = self._setup(tmp_path, monkeypatch, None, None)
        from src import chat_pid_reclaim
        subprocess_calls: list = []
        monkeypatch.setattr(
            chat_pid_reclaim, "find_session_pid_for_cwd",
            lambda cwd, **kw: subprocess_calls.append("called") or None,
        )
        monkeypatch.setattr(
            chat_pid_reclaim, "is_ancestor_or_self",
            lambda pid: pid == me,
        )
        db.register_agent("agent-proj", str(project), pid=me)
        from src.chat_pid_reclaim import reclaim_pid_best_effort
        reclaim_pid_best_effort(db, "agent-proj", str(project))
        assert subprocess_calls == []
```

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

---

## Task 3: `scripts/chat-stop-hook.py` — log pending-work flow event

**Files:**
- Create: `scripts/chat-stop-hook.py`
- Create: `tests/test_chat_stop_hook_skip.py`
- Create: `tests/test_chat_stop_hook_emission.py`
- Create: `tests/test_chat_stop_hook_fail_open.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chat_stop_hook_skip.py`:

```python
"""Tests for scripts/chat-stop-hook.py — subagent skip + stdin edge cases."""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def stop_mod(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "bus.db"))
    spec = importlib.util.spec_from_file_location(
        "chat_stop_hook",
        Path(__file__).resolve().parent.parent / "scripts" / "chat-stop-hook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestStopHookSkip:
    def test_skips_when_agent_id_in_payload(self, stop_mod, monkeypatch, tmp_path):
        payload = json.dumps({"agent_id": "sub-123", "background_tasks": [{"id": 1}]})
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(payload))
        assert stop_mod.main() == 0

    def test_succeeds_with_tty_stdin(self, stop_mod, monkeypatch):
        class FakeTty:
            def isatty(self): return True
            def read(self): return ""
        monkeypatch.setattr(sys, "stdin", FakeTty())
        assert stop_mod.main() == 0

    def test_succeeds_with_empty_stdin(self, stop_mod, monkeypatch):
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(""))
        assert stop_mod.main() == 0
```

Create `tests/test_chat_stop_hook_emission.py`:

```python
"""Tests for scripts/chat-stop-hook.py — flow event emission."""
import importlib
import io
import json
import sys
from pathlib import Path

import pytest

from src.chat_db import ChatDB


@pytest.fixture
def stop_mod(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "chat_stop_hook",
        Path(__file__).resolve().parent.parent / "scripts" / "chat-stop-hook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestStopHookEmission:
    def _run(self, stop_mod, monkeypatch, tmp_path, payload: dict):
        db_file = tmp_path / "bus.db"
        db = ChatDB(str(db_file))
        db.register_agent("agent-proj", str(tmp_path))
        monkeypatch.setenv("CHAT_DB_PATH", str(db_file))
        monkeypatch.setenv("CLAUDE_AGENT_NAME", "agent-proj")
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        rc = stop_mod.main()
        return rc, db

    def test_logs_event_when_background_tasks_present(
        self, stop_mod, monkeypatch, tmp_path,
    ):
        rc, db = self._run(
            stop_mod, monkeypatch, tmp_path,
            {"background_tasks": [{"id": 1, "name": "t1"}], "session_crons": []},
        )
        assert rc == 0
        conn = db._conn
        rows = conn.execute(
            "SELECT event_type, summary FROM events WHERE participant='agent-proj'"
        ).fetchall()
        assert any(r["event_type"] == "hook_stop_pending_work" for r in rows)
        summary = next(r["summary"] for r in rows if r["event_type"] == "hook_stop_pending_work")
        assert "background_tasks=1" in summary

    def test_logs_event_when_session_crons_present(
        self, stop_mod, monkeypatch, tmp_path,
    ):
        rc, db = self._run(
            stop_mod, monkeypatch, tmp_path,
            {"background_tasks": [], "session_crons": [{"id": "c1"}]},
        )
        assert rc == 0
        conn = db._conn
        rows = conn.execute(
            "SELECT event_type, summary FROM events WHERE participant='agent-proj'"
        ).fetchall()
        assert any(r["event_type"] == "hook_stop_pending_work" for r in rows)
        summary = next(r["summary"] for r in rows if r["event_type"] == "hook_stop_pending_work")
        assert "session_crons=1" in summary

    def test_no_event_when_no_pending_work(self, stop_mod, monkeypatch, tmp_path):
        rc, db = self._run(
            stop_mod, monkeypatch, tmp_path,
            {"background_tasks": [], "session_crons": []},
        )
        assert rc == 0
        rows = db._conn.execute(
            "SELECT * FROM events WHERE event_type='hook_stop_pending_work'"
        ).fetchall()
        assert rows == []

    def test_no_event_when_fields_absent(self, stop_mod, monkeypatch, tmp_path):
        rc, db = self._run(stop_mod, monkeypatch, tmp_path, {})
        assert rc == 0
        rows = db._conn.execute(
            "SELECT * FROM events WHERE event_type='hook_stop_pending_work'"
        ).fetchall()
        assert rows == []
```

Create `tests/test_chat_stop_hook_fail_open.py`:

```python
"""Tests for scripts/chat-stop-hook.py — fail-open on DB errors."""
import importlib
import io
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def stop_mod(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    spec = importlib.util.spec_from_file_location(
        "chat_stop_hook",
        Path(__file__).resolve().parent.parent / "scripts" / "chat-stop-hook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestStopHookFailOpen:
    def test_exit_0_when_chat_db_path_not_set(self, stop_mod, monkeypatch, tmp_path):
        monkeypatch.delenv("CHAT_DB_PATH", raising=False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"background_tasks": [{"id": 1}], "session_crons": []}
        )))
        assert stop_mod.main() == 0

    def test_exit_0_when_db_file_missing(self, stop_mod, monkeypatch, tmp_path):
        monkeypatch.setenv("CHAT_DB_PATH", str(tmp_path / "nonexistent.db"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"background_tasks": [{"id": 1}], "session_crons": []}
        )))
        assert stop_mod.main() == 0
```

- [ ] **Step 2: Run tests to confirm they fail**

```
.venv/bin/pytest tests/test_chat_stop_hook_skip.py tests/test_chat_stop_hook_emission.py tests/test_chat_stop_hook_fail_open.py -v
```

Expected: `FileNotFoundError` — script doesn't exist yet.

- [ ] **Step 3: Create `scripts/chat-stop-hook.py`**

```python
#!/usr/bin/env python3
"""Stop hook: log a flow event when the session stops with pending work.

Reads the Stop hook payload from stdin. If background_tasks or session_crons
are present (and non-empty), logs a hook_stop_pending_work flow event so the
dashboard shows the session stopped with unfinished work. Best-effort
telemetry — always exits 0.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.chat_db import ChatDB  # noqa: E402
from src.hook_utils import caller_name, read_hook_payload, resolved_db_path  # noqa: E402


def main() -> int:
    payload = read_hook_payload()
    if payload.get("agent_id"):
        return 0
    background_tasks = payload.get("background_tasks") or []
    session_crons = payload.get("session_crons") or []
    if not background_tasks and not session_crons:
        return 0
    try:
        db_path = resolved_db_path(ROOT)
    except RuntimeError as exc:
        print(f"chat-stop-hook: {exc}", file=sys.stderr)
        return 0
    if not db_path.exists():
        print(f"chat-stop-hook: DB {db_path} does not exist", file=sys.stderr)
        return 0
    try:
        db = ChatDB(str(db_path))
    except Exception as exc:  # noqa: BLE001
        print(f"chat-stop-hook: cannot open DB: {exc}", file=sys.stderr)
        return 0
    summary = f"background_tasks={len(background_tasks)} session_crons={len(session_crons)}"
    try:
        db._log_event(caller_name(), "hook_stop_pending_work", summary)
    except Exception as exc:  # noqa: BLE001
        print(f"chat-stop-hook: log event failed: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make it executable**

```bash
chmod +x scripts/chat-stop-hook.py
```

- [ ] **Step 5: Run the stop hook tests**

```
.venv/bin/pytest tests/test_chat_stop_hook_skip.py tests/test_chat_stop_hook_emission.py tests/test_chat_stop_hook_fail_open.py -v
```

Expected: all pass.

- [ ] **Step 6: Run full suite**

```
.venv/bin/pytest tests/ -q
```

- [ ] **Step 7: Commit**

```bash
git add scripts/chat-stop-hook.py \
    tests/test_chat_stop_hook_skip.py \
    tests/test_chat_stop_hook_emission.py \
    tests/test_chat_stop_hook_fail_open.py
git commit -m "feat(hooks): add chat-stop-hook.py — log pending work on session exit"
```

---

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

- [ ] **Step 6: Commit**

```bash
git add src/agent_bootstrap.py tests/test_spawner_session_hook.py
git commit -m "feat(agent_bootstrap): wire chat-stop-hook.py into Stop event"
```

---

## Task 5: Line-limit check + final verification

- [ ] **Step 1: Check line limits**

```bash
scripts/check-line-limit.sh
```

Expected: no violations. (`process_liveness.py` goes from 119 to ~148 lines; `agent_bootstrap.py` from 179 to ~193 lines — both under 200.)

- [ ] **Step 2: Run full suite one final time**

```
.venv/bin/pytest tests/ -q
```

Note the final test count and confirm 100% pass.

- [ ] **Step 3: Final commit if anything outstanding**

```bash
git status
```

If clean, nothing to do. Otherwise:

```bash
git add <any remaining files>
git commit -m "chore: final cleanup for claude-agents-json integration"
```
