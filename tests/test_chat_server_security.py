"""Security boundary tests for the claude-chat HTTP surface."""
import asyncio

import httpx
import pytest


AUTHORITY = "127.0.0.1:8420"
TOKEN = "dashboard-test-token"
DASHBOARD_API_PATHS = ("/api/agents", "/api/messages", "/events")
MCP_PATHS = ("/sse", "/messages/", "/mcp")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)
    from chat.server import create_app
    return create_app(str(tmp_path / "test.db"), "127.0.0.1", 8420)


async def _request(app, path, *, headers=None, method="GET"):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=f"http://{AUTHORITY}",
    ) as client:
        return await asyncio.wait_for(
            client.request(method, path, headers=headers, json={} if method == "POST" else None),
            timeout=0.25,
        )


def _bounded_request(app, path, *, headers=None, method="GET"):
    try:
        return asyncio.run(_request(app, path, headers=headers, method=method))
    except TimeoutError:
        pytest.fail(f"request to {path} was admitted instead of rejected")


def test_refuses_non_loopback_bind_host(tmp_path):
    from chat.server import create_app
    with pytest.raises(ValueError, match="CHAT_HOST"):
        create_app(str(tmp_path / "test.db"), "0.0.0.0", 8420)


def test_security_middleware_passes_through_non_http_scope():
    from chat.dashboard_auth import DashboardAuthenticator
    from chat.http_security import HttpSecurityMiddleware, build_transport_security

    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope)
        return "passed through"

    middleware = HttpSecurityMiddleware(
        downstream,
        build_transport_security("127.0.0.1", 8420),
        DashboardAuthenticator(TOKEN),
    )
    scope = {"type": "lifespan"}
    result = asyncio.run(middleware(scope, None, None))

    assert result == "passed through"
    assert seen == [scope]


def test_sse_and_streamable_share_enabled_security_settings(app):
    from mcp.server.sse import SseServerTransport

    sse_route = next(route for route in app.routes if route.path == "/sse")
    sse = next(
        cell.cell_contents for cell in sse_route.endpoint.__closure__
        if isinstance(cell.cell_contents, SseServerTransport)
    )
    mcp_mount = next(route for route in app.routes if route.path == "/mcp")
    streamable = mcp_mount.app.__self__
    settings = sse._security.settings

    assert streamable.security_settings is settings
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == [AUTHORITY]
    assert settings.allowed_origins == [f"http://{AUTHORITY}"]


@pytest.mark.parametrize("path", MCP_PATHS)
def test_mcp_routes_reject_hostile_host(app, path):
    method = "GET" if path == "/sse" else "POST"
    response = _bounded_request(
        app,
        path,
        method=method,
        headers={"host": "attacker.example"},
    )
    assert response.status_code == 421


@pytest.mark.parametrize("path", MCP_PATHS)
def test_mcp_routes_reject_hostile_origin(app, path):
    method = "GET" if path == "/sse" else "POST"
    response = _bounded_request(
        app,
        path,
        method=method,
        headers={"origin": "https://attacker.example"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("path", DASHBOARD_API_PATHS)
def test_dashboard_routes_require_authorization(app, path):
    response = _bounded_request(app, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("path", DASHBOARD_API_PATHS)
def test_dashboard_routes_reject_wrong_authorization(app, path):
    response = _bounded_request(
        app,
        path,
        headers={"authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_dashboard_redirects_missing_authorization_to_login(app):
    response = _bounded_request(app, "/dashboard")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/login"


def test_dashboard_rejects_query_token(app):
    response = _bounded_request(app, f"/dashboard?token={TOKEN}")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard/login"


def test_dashboard_accepts_bearer_token_from_allowed_origin(app):
    response = _bounded_request(
        app,
        "/dashboard",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "origin": f"http://{AUTHORITY}",
        },
    )
    assert response.status_code == 200


def test_dashboard_rejects_hostile_host_before_authorization(app):
    response = _bounded_request(
        app,
        "/dashboard",
        headers={"authorization": f"Bearer {TOKEN}", "host": "attacker.example"},
    )
    assert response.status_code == 421


def test_dashboard_rejects_hostile_origin_before_authorization(app):
    response = _bounded_request(
        app,
        "/dashboard",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "origin": "https://attacker.example",
        },
    )
    assert response.status_code == 403
