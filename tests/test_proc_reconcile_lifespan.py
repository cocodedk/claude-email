"""Startup-wiring tests for src/proc_reconcile.reconcile_live_agents.

Split from tests/test_proc_reconcile.py (which covers the /proc walker,
cwd reader, and DB upsert logic) so each test module stays under the
200-line cap.
"""


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
