> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

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
