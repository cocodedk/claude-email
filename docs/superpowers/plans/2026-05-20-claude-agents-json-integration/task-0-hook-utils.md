> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

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
