"""Browser-compatible authentication tests for the local dashboard."""
import asyncio

import httpx
import pytest


AUTHORITY = "127.0.0.1:8420"
TOKEN = "dashboard-test-token"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", TOKEN)
    from chat.server import create_app
    from src.chat_db import ChatDB
    db_path = str(tmp_path / "test.db")
    local_app = create_app(db_path, "127.0.0.1", 8420)
    local_app.state.chat_db = ChatDB(db_path)
    return local_app


async def _login_page(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=f"http://{AUTHORITY}",
    ) as client:
        return await client.get("/dashboard/login")


def test_login_page_contains_password_form_without_token(app):
    response = asyncio.run(_login_page(app))
    assert response.status_code == 200
    assert 'action="/dashboard/login"' in response.text
    assert 'type="password"' in response.text
    assert TOKEN not in response.text


async def _wrong_login(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=f"http://{AUTHORITY}",
    ) as client:
        return await client.post(
            "/dashboard/login",
            data={"token": "wrong"},
            headers={"origin": f"http://{AUTHORITY}"},
        )


def test_wrong_login_is_rejected_without_cookie(app):
    response = asyncio.run(_wrong_login(app))
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


async def _bad_login_request(app, **request_kwargs):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=f"http://{AUTHORITY}",
    ) as client:
        return await client.post("/dashboard/login", **request_kwargs)


def test_login_rejects_non_form_content(app):
    response = asyncio.run(_bad_login_request(app, json={"token": TOKEN}))
    assert response.status_code == 400


def test_login_rejects_missing_token(app):
    response = asyncio.run(_bad_login_request(app, data={"unrelated": TOKEN}))
    assert response.status_code == 401


async def _browser_flow(app):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url=f"http://{AUTHORITY}",
        follow_redirects=False,
    ) as client:
        login = await client.post(
            "/dashboard/login",
            data={"token": TOKEN},
            headers={"origin": f"http://{AUTHORITY}"},
        )
        page = await client.get("/dashboard")
        agents = await client.get("/api/agents")
        messages = await client.get("/api/messages")
        return login, page, agents, messages


def test_login_cookie_authenticates_dashboard_and_fetches(app):
    login, page, agents, messages = asyncio.run(_browser_flow(app))
    cookie = login.headers["set-cookie"].lower()

    assert login.status_code == 303
    assert login.headers["location"] == "/dashboard"
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert TOKEN not in cookie
    assert page.status_code == 200
    assert agents.json() == {"agents": []}
    assert messages.json() == {"messages": []}
