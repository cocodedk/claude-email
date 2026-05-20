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
