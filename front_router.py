from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]

FORWARDED_HEADER_NAMES = frozenset(
    {
        b"forwarded",
        b"x-forwarded-for",
        b"x-forwarded-host",
        b"x-forwarded-port",
        b"x-forwarded-proto",
        b"x-real-ip",
    }
)


@dataclass(frozen=True)
class FrontRoute:
    target: str
    reason: str
    blocked: bool = False


FRONT_ROUTER_ROUTE_TABLE: tuple[tuple[str, str], ...] = (
    ("/api/stream", "sse_service"),
    ("/uploads/meetings/*", "web_api_service"),
    ("/internal/*", "blocked"),
    ("/*", "web_api_service"),
)


def match_front_route(path: str) -> FrontRoute:
    normalized = path or "/"
    if normalized == "/internal" or normalized.startswith("/internal/"):
        return FrontRoute("blocked", "external_internal_block", blocked=True)
    if normalized == "/api/stream":
        return FrontRoute("sse_service", "sse_stream")
    if normalized.startswith("/uploads/meetings/"):
        return FrontRoute("web_api_service", "protected_meeting_upload")
    return FrontRoute("web_api_service", "default_web_api")


class FrontRouter:
    """Minimal ASGI path dispatcher for the M2 service split.

    M2-10 owns public route selection. M2-11 owns strip-then-set forwarded
    headers. The actual SSE broker move and SSE proxy tuning are later M2
    steps.
    """

    def __init__(
        self,
        web_api_app: ASGIApp,
        sse_app: ASGIApp | None = None,
        *,
        expose_route_headers: bool = False,
    ):
        self.web_api_app = web_api_app
        self.sse_app = sse_app or web_api_app
        self.expose_route_headers = expose_route_headers

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope.get("type") != "http":
            await self.web_api_app(scope, receive, send)
            return

        route = match_front_route(scope.get("path", "/"))
        if route.blocked:
            await _send_blocked(route, send, self.expose_route_headers)
            return

        app = self.sse_app if route.target == "sse_service" else self.web_api_app
        forwarded_scope = _scope_with_router_forwarded_headers(scope)
        await app(
            forwarded_scope,
            receive,
            _route_header_send(send, route, self.expose_route_headers),
        )


def create_front_router_app(
    web_api_app: ASGIApp | None = None,
    sse_app: ASGIApp | None = None,
    *,
    expose_route_headers: bool = False,
) -> FrontRouter:
    if web_api_app is None:
        from app import app as web_api_app
    return FrontRouter(
        web_api_app=web_api_app,
        sse_app=sse_app,
        expose_route_headers=expose_route_headers,
    )


def _route_header_send(send, route: FrontRoute, expose_route_headers: bool):
    async def _send(message: dict) -> None:
        if expose_route_headers and message.get("type") == "http.response.start":
            headers = list(message.get("headers") or [])
            headers.extend(
                [
                    (b"x-whatudoin-router-target", route.target.encode("ascii")),
                    (b"x-whatudoin-router-rule", route.reason.encode("ascii")),
                ]
            )
            message = {**message, "headers": headers}
        await send(message)

    return _send


def _scope_with_router_forwarded_headers(scope: dict) -> dict:
    return {**scope, "headers": strip_then_set_forwarded_headers(scope)}


def strip_then_set_forwarded_headers(scope: dict) -> list[tuple[bytes, bytes]]:
    headers = list(scope.get("headers") or [])
    clean_headers = [
        (name, value)
        for name, value in headers
        if name.lower() not in FORWARDED_HEADER_NAMES
    ]
    host = _host_from_scope(scope, headers)
    client_host = _client_host(scope)
    scheme = str(scope.get("scheme") or "http")
    port = _port_from_host(host) or _server_port(scope)
    clean_headers.extend(
        [
            (b"x-forwarded-for", _header_bytes(client_host)),
            (b"x-forwarded-host", _header_bytes(host)),
            (b"x-forwarded-proto", _header_bytes(scheme)),
            (b"x-forwarded-port", _header_bytes(port)),
            (b"x-real-ip", _header_bytes(client_host)),
        ]
    )
    return clean_headers


def _host_from_scope(scope: dict, headers: list[tuple[bytes, bytes]]) -> str:
    for name, value in headers:
        if name.lower() == b"host":
            return value.decode("latin1", errors="replace").strip()
    server = scope.get("server")
    if isinstance(server, tuple) and len(server) >= 2:
        host = str(server[0])
        port = server[1]
        return f"{host}:{port}" if port else host
    return "localhost"


def _client_host(scope: dict) -> str:
    client = scope.get("client")
    if isinstance(client, tuple) and client:
        return str(client[0])
    return "127.0.0.1"


def _server_port(scope: dict) -> str:
    server = scope.get("server")
    if isinstance(server, tuple) and len(server) >= 2 and server[1]:
        return str(server[1])
    return ""


def _port_from_host(host: str) -> str:
    if host.startswith("["):
        end = host.find("]")
        rest = host[end + 1 :] if end >= 0 else ""
        return rest[1:] if rest.startswith(":") else ""
    if host.count(":") == 1:
        return host.rsplit(":", 1)[1]
    return ""


def _header_bytes(value: str) -> bytes:
    return value.encode("latin1", errors="replace")


async def _send_blocked(route: FrontRoute, send, expose_route_headers: bool) -> None:
    headers = [(b"content-type", b"text/plain; charset=utf-8")]
    if expose_route_headers:
        headers.extend(
            [
                (b"x-whatudoin-router-target", route.target.encode("ascii")),
                (b"x-whatudoin-router-rule", route.reason.encode("ascii")),
            ]
        )
    await send({"type": "http.response.start", "status": 404, "headers": headers})
    await send({"type": "http.response.body", "body": b"not found"})


__all__ = [
    "FORWARDED_HEADER_NAMES",
    "FRONT_ROUTER_ROUTE_TABLE",
    "FrontRoute",
    "FrontRouter",
    "create_front_router_app",
    "match_front_route",
    "strip_then_set_forwarded_headers",
]
