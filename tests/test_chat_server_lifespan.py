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
