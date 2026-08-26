"""HTTP Host/Origin enforcement and read-only dashboard authorization."""

from mcp.server.transport_security import (
    TransportSecurityMiddleware,
    TransportSecuritySettings,
)
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from chat.dashboard_auth import DashboardAuthenticator


DASHBOARD_PATHS = frozenset({
    "/dashboard", "/api/agents", "/api/messages", "/events",
})


class HttpSecurityMiddleware:
    """Apply the transport boundary to every HTTP route and gate the UI."""

    def __init__(self, app, security_settings, dashboard_auth):
        self.app = app
        self.security = TransportSecurityMiddleware(security_settings)
        self.dashboard_auth = dashboard_auth

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope, receive)
        error = await self.security.validate_request(
            request,
            is_post=False,
        )
        if error:
            return await error(scope, receive, send)
        if (
            scope["path"] in DASHBOARD_PATHS
            and not self.dashboard_auth.authorized(request)
        ):
            if scope["path"] == "/dashboard":
                response = RedirectResponse("/dashboard/login", status_code=303)
                return await response(scope, receive, send)
            response = Response(
                "Unauthorized",
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def build_transport_security(host: str, port: int) -> TransportSecuritySettings:
    if host != "127.0.0.1":
        raise ValueError("CHAT_HOST must be the loopback address 127.0.0.1")
    authority = f"{host}:{port}"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[authority],
        allowed_origins=[f"http://{authority}"],
    )
