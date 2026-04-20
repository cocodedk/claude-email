"""Verify the chat server's Starlette lifespan starts and stops the wake watcher."""
import os
import tempfile

from starlette.testclient import TestClient


def test_lifespan_starts_and_stops_wake_watcher(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        monkeypatch.setenv("WAKE_WATCHER_INTERVAL_SECS", "0.05")
        monkeypatch.setenv("WAKE_SUBPROCESS_TIMEOUT_SECS", "5")
        from chat.server import create_app
        app = create_app(path, "127.0.0.1", 0)
        with TestClient(app):
            task = getattr(app.state, "wake_watcher_task", None)
            assert task is not None
            assert not task.done()
        assert app.state.wake_watcher_task.done()
    finally:
        os.unlink(path)


def test_chat_server_worker_manager_forwards_router_mcp_config(monkeypatch):
    """chat_enqueue_task spawns project_worker via WorkerManager — that
    subprocess reads ROUTER_MCP_CONFIG from env, so the manager MUST forward
    it. Without this wiring the worker crashes on startup with KeyError."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        monkeypatch.setenv("WAKE_WATCHER_INTERVAL_SECS", "0.05")
        monkeypatch.setenv("WAKE_SUBPROCESS_TIMEOUT_SECS", "5")
        from chat.server import create_app
        from src.llm_router import ROUTER_MCP_CONFIG_PATH
        app = create_app(path, "127.0.0.1", 0)
        with TestClient(app):
            mgr = getattr(app.state, "worker_manager", None)
            assert mgr is not None
            assert mgr._module_env == {"ROUTER_MCP_CONFIG": ROUTER_MCP_CONFIG_PATH}
    finally:
        os.unlink(path)


def test_lifespan_wires_wake_nudge_into_db(monkeypatch):
    """Nudge event must be attached to both the watcher and the ChatDB so
    inserts wake the loop without waiting for the poll tick."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        monkeypatch.setenv("WAKE_WATCHER_INTERVAL_SECS", "0.05")
        monkeypatch.setenv("WAKE_SUBPROCESS_TIMEOUT_SECS", "5")
        from chat.server import create_app
        app = create_app(path, "127.0.0.1", 0)
        with TestClient(app):
            nudge = getattr(app.state, "wake_watcher_nudge", None)
            db = getattr(app.state, "chat_db", None)
            assert nudge is not None
            assert db is not None
            assert db._wake_nudge is nudge
    finally:
        os.unlink(path)
