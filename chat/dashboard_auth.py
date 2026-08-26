"""Browser-compatible authentication for the read-only dashboard."""
import hmac
import secrets
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route


SESSION_COOKIE = "claude_chat_dashboard_session"
LOGIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Dashboard login</title></head>
<body><main><h1>Claude Chat Dashboard</h1>
<form action="/dashboard/login" method="post">
<label>Dashboard token <input name="token" type="password" required></label>
<button type="submit">Log in</button>
</form></main></body></html>"""


class DashboardAuthenticator:
    def __init__(self, token: str):
        self.token = token
        self.session = ""

    def authorized(self, request: Request) -> bool:
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if self.session and hmac.compare_digest(cookie, self.session):
            return True
        scheme, separator, supplied = request.headers.get(
            "authorization", "",
        ).partition(" ")
        return bool(
            self.token
            and separator
            and scheme.lower() == "bearer"
            and hmac.compare_digest(supplied, self.token)
        )

    def start_session(self, supplied: str) -> str:
        if not self.token or not hmac.compare_digest(supplied, self.token):
            return ""
        self.session = secrets.token_urlsafe(32)
        return self.session


async def _login(request: Request) -> Response:
    if request.method == "GET":
        return HTMLResponse(LOGIN_HTML)
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type != "application/x-www-form-urlencoded":
        return Response("Invalid request", status_code=400)
    fields = parse_qs((await request.body()).decode("utf-8", errors="replace"))
    values = fields.get("token", [])
    supplied = values[0] if len(values) == 1 else ""
    session = request.app.state.dashboard_auth.start_session(supplied)
    if not session:
        return Response("Unauthorized", status_code=401)
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        session,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


def build_routes() -> list[Route]:
    return [Route("/dashboard/login", _login, methods=["GET", "POST"])]
