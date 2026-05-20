> Part of [claude agents --json Integration Plan](../2026-05-20-claude-agents-json-integration.md)

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
