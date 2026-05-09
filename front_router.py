from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


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

    M2-10 only owns public route selection. The actual SSE broker move,
    forwarded-header policy, and SSE proxy tuning are later M2 steps.
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
        await app(scope, receive, _route_header_send(send, route, self.expose_route_headers))


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
    "FRONT_ROUTER_ROUTE_TABLE",
    "FrontRoute",
    "FrontRouter",
    "create_front_router_app",
    "match_front_route",
]
