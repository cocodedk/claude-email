"""Tests for the MCP SSE server (chat/server.py)."""
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient


@pytest.fixture
def app(tmp_path):
    from chat.server import create_app
    return create_app(str(tmp_path / "test.db"), "127.0.0.1", 8420)


class TestSanitizeStr:
    """Covers line 157: non-string value rejected."""

    def test_non_string_raises_value_error(self):
        from chat.dispatch import _sanitize_str
        with pytest.raises(ValueError, match="must be a string"):
            _sanitize_str(123, 10, "myfield")

    def test_integer_zero_raises_value_error(self):
        from chat.dispatch import _sanitize_str
        with pytest.raises(ValueError, match="must be a string"):
            _sanitize_str(0, 10, "field")

    def test_none_raises_value_error(self):
        from chat.dispatch import _sanitize_str
        with pytest.raises(ValueError, match="must be a string"):
            _sanitize_str(None, 10, "field")


class TestSSEHandler:
    """Covers lines 131-132: SSE handler body (connect_sse context manager)."""

    def test_sse_endpoint_exists_and_is_callable(self, app):
        """Verify the /sse route endpoint is present — integration smoke test."""
        sse_route = None
        for route in app.routes:
            if getattr(route, "path", None) == "/sse":
                sse_route = route
                break
        assert sse_route is not None
        assert callable(sse_route.endpoint)

    def test_sse_handler_calls_server_run(self, tmp_path):
        """handle_sse forwards request.scope/receive/_send into connect_sse
        and then invokes server.run with the yielded streams.

        Asserts the handoff arguments so a regression that passed wrong or
        dropped channels (e.g. scope/scope/scope) would be caught — the
        fake connect_sse no longer silently ignores its args.
        """
        import asyncio
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from chat.server import create_app
        local_app = create_app(str(tmp_path / "test.db"), "127.0.0.1", 8422)

        handle_sse = None
        for route in local_app.routes:
            if getattr(route, "path", None) == "/sse":
                handle_sse = route.endpoint
                break
        assert handle_sse is not None

        mock_read_stream = AsyncMock()
        mock_write_stream = AsyncMock()
        mock_streams = (mock_read_stream, mock_write_stream)
        mock_server_run = AsyncMock()

        handoff_capture = {}

        @asynccontextmanager
        async def fake_connect_sse(self_sse, scope, receive, send):
            handoff_capture["scope"] = scope
            handoff_capture["receive"] = receive
            handoff_capture["send"] = send
            yield mock_streams

        async def _test():
            fake_request = SimpleNamespace(
                scope={"type": "http", "marker": "from-fake-request"},
                receive=AsyncMock(),
                _send=AsyncMock(),
            )

            with patch(
                "mcp.server.sse.SseServerTransport.connect_sse",
                new=fake_connect_sse,
            ):
                with patch.object(local_app.state.mcp_server, "run", mock_server_run):
                    result = await handle_sse(fake_request)
            return result, fake_request

        result, fake_request = asyncio.run(_test())

        # server.run received the streams yielded by connect_sse
        mock_server_run.assert_awaited_once()
        call_args = mock_server_run.call_args
        assert call_args.args[0] is mock_read_stream
        assert call_args.args[1] is mock_write_stream

        # connect_sse received the request's actual scope/receive/_send
        assert handoff_capture["scope"] is fake_request.scope
        assert handoff_capture["receive"] is fake_request.receive
        assert handoff_capture["send"] is fake_request._send

        # The handler returns an empty Response after the SSE stream closes
        from starlette.responses import Response
        assert isinstance(result, Response)
