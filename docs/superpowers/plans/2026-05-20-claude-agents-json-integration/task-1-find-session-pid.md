> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

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
