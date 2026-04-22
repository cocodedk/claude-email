"""Tests for src/proc_reconcile.py — startup /proc scan + agent upsert."""
import os

import pytest

from src.chat_db import ChatDB
from src.proc_reconcile import (
    _cwd_of,
    _iter_claude_pids,
    reconcile_live_agents,
)


@pytest.fixture
def db(tmp_path):
    return ChatDB(str(tmp_path / "bus.db"))


class TestIterClaudePids:
    def test_matches_basename(self, monkeypatch, tmp_path):
        # Fake /proc with three entries: one claude, one python, one invalid.
        procdir = tmp_path / "proc"
        procdir.mkdir()
        for pid, cmdline in [
            (100, b"claude\0--print\0"),
            (200, b"/usr/bin/python3\0main.py\0"),
            (300, b"claude\0--continue\0"),
        ]:
            d = procdir / str(pid)
            d.mkdir()
            (d / "cmdline").write_bytes(cmdline)
        (procdir / "notanint").mkdir()  # should be skipped
        real_listdir = os.listdir
        monkeypatch.setattr(os, "listdir", lambda p: real_listdir(str(procdir)))
        real_open = open

        def fake_open(path, mode="r", *a, **k):
            if isinstance(path, str) and path.startswith("/proc/"):
                path = str(procdir / path[len("/proc/"):])
            return real_open(path, mode, *a, **k)
        monkeypatch.setattr("builtins.open", fake_open)
        pids = _iter_claude_pids()
        assert sorted(pids) == [100, 300]

    def test_handles_unreadable_proc(self, monkeypatch):
        def raise_fnf(_):
            raise FileNotFoundError("/proc")
        monkeypatch.setattr(os, "listdir", raise_fnf)
        assert _iter_claude_pids() == []

    def test_skips_process_that_vanishes_mid_scan(self, monkeypatch):
        """Between listdir and open, a pid can exit. Must not raise."""
        monkeypatch.setattr(os, "listdir", lambda p: ["100", "200"])

        def flaky(path, *a, **k):
            raise FileNotFoundError(path)
        monkeypatch.setattr("builtins.open", flaky)
        assert _iter_claude_pids() == []

    def test_skips_empty_cmdline(self, monkeypatch, tmp_path):
        """Kernel threads have empty cmdline — don't match."""
        procdir = tmp_path / "proc"
        procdir.mkdir()
        (procdir / "1").mkdir()
        (procdir / "1" / "cmdline").write_bytes(b"")
        real_listdir = os.listdir
        monkeypatch.setattr(os, "listdir", lambda p: real_listdir(str(procdir)))
        real_open = open

        def fake_open(path, mode="r", *a, **k):
            if isinstance(path, str) and path.startswith("/proc/"):
                path = str(procdir / path[len("/proc/"):])
            return real_open(path, mode, *a, **k)
        monkeypatch.setattr("builtins.open", fake_open)
        assert _iter_claude_pids() == []


class TestCwdOf:
    def test_returns_readlink_target(self, monkeypatch):
        monkeypatch.setattr(os, "readlink", lambda p: "/home/u/proj")
        assert _cwd_of(42) == "/home/u/proj"

    def test_returns_none_when_unreadable(self, monkeypatch):
        def boom(_):
            raise ProcessLookupError("gone")
        monkeypatch.setattr(os, "readlink", boom)
        assert _cwd_of(42) is None


class TestReconcileLiveAgents:
    def test_upserts_row_per_live_claude(self, db, monkeypatch):
        from src import proc_reconcile
        monkeypatch.setattr(proc_reconcile, "_iter_claude_pids", lambda marker=None: [100, 200])
        cwd_map = {100: "/home/u/alpha", 200: "/home/u/beta"}
        monkeypatch.setattr(proc_reconcile, "_cwd_of", lambda pid: cwd_map.get(pid))
        touched = reconcile_live_agents(db)
        assert sorted(touched) == ["agent-alpha", "agent-beta"]
        assert db.get_agent("agent-alpha")["pid"] == 100
        assert db.get_agent("agent-alpha")["project_path"] == "/home/u/alpha"
        assert db.get_agent("agent-beta")["pid"] == 200

    def test_takes_over_rows_with_dead_pids(self, db, monkeypatch):
        from src import proc_reconcile
        # A stale row from before a restart — dead pid
        db.register_agent("agent-alpha", "/home/u/alpha", pid=99999999)
        monkeypatch.setattr(proc_reconcile, "_iter_claude_pids", lambda marker=None: [42])
        monkeypatch.setattr(proc_reconcile, "_cwd_of", lambda pid: "/home/u/alpha")
        touched = reconcile_live_agents(db)
        assert touched == ["agent-alpha"]
        assert db.get_agent("agent-alpha")["pid"] == 42

    def test_skips_pids_with_no_cwd(self, db, monkeypatch):
        from src import proc_reconcile
        monkeypatch.setattr(proc_reconcile, "_iter_claude_pids", lambda marker=None: [42])
        monkeypatch.setattr(proc_reconcile, "_cwd_of", lambda pid: None)
        assert reconcile_live_agents(db) == []

    def test_continues_after_individual_upsert_failure(self, db, monkeypatch, caplog):
        from src import proc_reconcile
        monkeypatch.setattr(proc_reconcile, "_iter_claude_pids", lambda marker=None: [1, 2])
        monkeypatch.setattr(proc_reconcile, "_cwd_of", lambda pid: f"/tmp/p{pid}")
        real_register = db.register_agent
        calls = {"n": 0}

        def flaky_register(name, path, pid=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("hostile row")
            return real_register(name, path, pid=pid)
        monkeypatch.setattr(db, "register_agent", flaky_register)
        touched = reconcile_live_agents(db)
        assert len(touched) == 1
        assert "failed to upsert" in caplog.text

    def test_returns_empty_on_empty_proc(self, db, monkeypatch):
        from src import proc_reconcile
        monkeypatch.setattr(proc_reconcile, "_iter_claude_pids", lambda marker=None: [])
        assert reconcile_live_agents(db) == []

    def test_basename_collision_falls_back_to_parent_qualified(
        self, db, monkeypatch,
    ):
        """/home/u/work/app and /home/u/backup/app both derive agent-app.
        The second session must not be silently dropped; retry with a
        parent-qualified name so both live sessions stay visible.

        Both PIDs must report alive so the second register_agent raises
        AgentNameTaken / AgentProjectTaken instead of silently taking over
        a stale slot.
        """
        from src import proc_reconcile
        import src.agent_registry
        monkeypatch.setattr(
            proc_reconcile, "_iter_claude_pids", lambda marker=None: [100, 200],
        )
        cwd_map = {100: "/home/u/work/app", 200: "/home/u/backup/app"}
        monkeypatch.setattr(proc_reconcile, "_cwd_of", lambda pid: cwd_map.get(pid))
        monkeypatch.setattr(src.agent_registry, "is_alive", lambda pid: True)
        touched = reconcile_live_agents(db)
        assert "agent-app" in touched
        # Second session reconciled under the parent-qualified name.
        assert "agent-backup-app" in touched
        assert db.get_agent("agent-app")["pid"] == 100
        assert db.get_agent("agent-backup-app")["pid"] == 200

    def test_fallback_upsert_failure_logged(self, db, monkeypatch, caplog):
        """If even the parent-qualified retry fails, log and move on."""
        from src import proc_reconcile
        from src.chat_errors import AgentNameTaken
        monkeypatch.setattr(
            proc_reconcile, "_iter_claude_pids", lambda marker=None: [1],
        )
        monkeypatch.setattr(proc_reconcile, "_cwd_of", lambda pid: "/a/b")
        calls = {"n": 0}

        def always_raise(name, path, pid=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise AgentNameTaken(name, 999)
            raise RuntimeError("fallback failed too")
        monkeypatch.setattr(db, "register_agent", always_raise)
        touched = reconcile_live_agents(db)
        assert touched == []
        assert "fallback upsert failed" in caplog.text


class TestWiredIntoServerLifespan:
    """The reconciler must run when the MCP server starts so the radar
    repopulates on its own after a claude-chat bounce."""

    def test_server_startup_calls_reconcile(self, tmp_path, monkeypatch):
        from starlette.testclient import TestClient
        called = {"n": 0}
        from chat import server as chat_server

        def fake_reconcile(db, *, marker="claude"):
            called["n"] += 1
            return []
        monkeypatch.setattr(chat_server, "reconcile_live_agents", fake_reconcile)
        app = chat_server.create_app(str(tmp_path / "bus.db"), "127.0.0.1", 0)
        with TestClient(app):
            # Lifespan.startup has fired here
            assert called["n"] == 1

    def test_server_startup_survives_reconcile_failure(self, tmp_path, monkeypatch):
        """A broken /proc must not prevent the server from coming up."""
        from starlette.testclient import TestClient
        from chat import server as chat_server

        def boom(db, *, marker="claude"):
            raise RuntimeError("proc unreadable")
        monkeypatch.setattr(chat_server, "reconcile_live_agents", boom)
        app = chat_server.create_app(str(tmp_path / "bus.db"), "127.0.0.1", 0)
        with TestClient(app):
            # no exception — server is serving
            pass
