from __future__ import annotations

import os
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


# ── Standalone reverse-proxy mode (4-B) ──────────────────────────────────────
# Env 변수 (미설정 시 기본값 적용):
#   WHATUDOIN_FRONT_ROUTER_BIND_HOST     (기본: "0.0.0.0")
#   WHATUDOIN_FRONT_ROUTER_HTTP_PORT     (기본: 8000)
#   WHATUDOIN_FRONT_ROUTER_HTTPS_PORT    (기본: 8443)
#   WHATUDOIN_FRONT_ROUTER_CERT          (미설정 시 HTTPS 비활성)
#   WHATUDOIN_FRONT_ROUTER_KEY
#   WHATUDOIN_WEB_API_INTERNAL_URL       (예: http://127.0.0.1:8769)
#   WHATUDOIN_SSE_SERVICE_URL_FROM_ROUTER (예: http://127.0.0.1:8765)
#
# 모듈 import 시 부작용 0 — httpx client/uvicorn.Server는 __main__ 진입점에서만 생성.


def _make_reverse_proxy_app(web_api_url: str, sse_url: str):
    """Starlette + httpx 기반 reverse HTTP proxy ASGI app 생성.

    - /internal/* → 404 (M2-10 정책)
    - /api/stream  → SSE service streaming proxy (chunk-by-chunk, idle timeout 없음)
    - 그 외        → Web API HTTP forward
    - M2-11 strip-then-set forwarded headers 적용
    - Host 헤더: 외부 원본 보존 (M2-14)
    """
    import asyncio
    import httpx
    from starlette.requests import Request
    from starlette.responses import Response, StreamingResponse
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount

    # httpx client — module 내부 지연 생성, __main__ 진입점에서만 활성화
    _client: httpx.AsyncClient | None = None

    def _get_client() -> httpx.AsyncClient:
        nonlocal _client
        if _client is None:
            _client = httpx.AsyncClient(
                verify=False,
                timeout=httpx.Timeout(30.0, read=None),
                follow_redirects=False,
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
            )
        return _client

    async def _build_upstream_headers(request: Request) -> dict[str, str]:
        """M2-11 strip-then-set + M2-14 Host 보존."""
        stripped_names = {
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-forwarded-port",
            "x-forwarded-proto",
            "x-real-ip",
        }
        headers: dict[str, str] = {}
        for name, value in request.headers.items():
            if name.lower() not in stripped_names:
                headers[name] = value

        # M2-14: Host 헤더는 외부 원본 보존 — 위 루프에서 이미 포함됨
        client_host = request.client.host if request.client else "127.0.0.1"
        scheme = request.url.scheme
        host = request.headers.get("host", str(request.url.netloc))
        # port 추출 (IPv6 고려)
        if host.startswith("["):
            end = host.find("]")
            rest = host[end + 1:] if end >= 0 else ""
            port_str = rest[1:] if rest.startswith(":") else str(request.url.port or "")
        elif ":" in host:
            port_str = host.rsplit(":", 1)[1]
        else:
            port_str = str(request.url.port or "")

        headers["x-forwarded-for"] = client_host
        headers["x-forwarded-host"] = host
        headers["x-forwarded-proto"] = scheme
        headers["x-forwarded-port"] = port_str
        headers["x-real-ip"] = client_host
        return headers

    async def _proxy_sse(request: Request) -> Response:
        """SSE service streaming proxy — chunk-by-chunk, client disconnect 시 upstream close."""
        client = _get_client()
        headers = await _build_upstream_headers(request)
        target_url = sse_url.rstrip("/") + str(request.url.path)
        if request.url.query:
            target_url += "?" + request.url.query

        async def _stream_generator():
            try:
                async with client.stream(
                    request.method,
                    target_url,
                    headers=headers,
                    content=await request.body(),
                    timeout=httpx.Timeout(None, connect=10.0),
                ) as upstream:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
            except (httpx.RemoteProtocolError, httpx.ReadError, asyncio.CancelledError):
                return

        # upstream 첫 응답 헤더를 얻어야 status/content-type을 넘길 수 있음
        # StreamingResponse는 headers를 직접 설정 가능
        return StreamingResponse(
            _stream_generator(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    async def _proxy_web_api(request: Request) -> Response:
        """Web API HTTP forward — request body/headers 그대로, 응답 body/headers 그대로."""
        client = _get_client()
        headers = await _build_upstream_headers(request)
        target_url = web_api_url.rstrip("/") + str(request.url.path)
        if request.url.query:
            target_url += "?" + request.url.query

        body = await request.body()
        upstream = await client.request(
            request.method,
            target_url,
            headers=headers,
            content=body,
        )
        # 응답 헤더에서 transfer-encoding 제거 (httpx가 이미 디코딩함)
        resp_headers: dict[str, str] = {}
        skip_headers = {"transfer-encoding", "content-encoding", "content-length"}
        for name, value in upstream.headers.items():
            if name.lower() not in skip_headers:
                resp_headers[name] = value

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=resp_headers,
        )

    async def _handle_internal_block(request: Request) -> Response:
        """M2-10: /internal/* 차단 → 404."""
        return Response("not found", status_code=404, media_type="text/plain")

    async def _dispatch(request: Request) -> Response:
        path = request.url.path
        route = match_front_route(path)
        if route.blocked:
            return await _handle_internal_block(request)
        if route.target == "sse_service":
            return await _proxy_sse(request)
        return await _proxy_web_api(request)

    app = Starlette(routes=[Route("/{path:path}", _dispatch), Route("/", _dispatch)])
    app._front_router_get_client = _get_client  # 테스트/shutdown 접근용
    return app


if __name__ == "__main__":
    import asyncio
    import signal
    import sys
    import uvicorn

    _bind_host = os.environ.get("WHATUDOIN_FRONT_ROUTER_BIND_HOST", "0.0.0.0").strip() or "0.0.0.0"
    _http_port = int(os.environ.get("WHATUDOIN_FRONT_ROUTER_HTTP_PORT", "8000") or "8000")
    _https_port = int(os.environ.get("WHATUDOIN_FRONT_ROUTER_HTTPS_PORT", "8443") or "8443")
    _cert = os.environ.get("WHATUDOIN_FRONT_ROUTER_CERT", "").strip()
    _key = os.environ.get("WHATUDOIN_FRONT_ROUTER_KEY", "").strip()
    _web_api_url = os.environ.get("WHATUDOIN_WEB_API_INTERNAL_URL", "http://127.0.0.1:8769").strip()
    _sse_url = os.environ.get("WHATUDOIN_SSE_SERVICE_URL_FROM_ROUTER", "http://127.0.0.1:8765").strip()

    _app = _make_reverse_proxy_app(_web_api_url, _sse_url)

    _servers: list[uvicorn.Server] = []

    _http_cfg = uvicorn.Config(_app, host=_bind_host, port=_http_port, log_level="info")
    _servers.append(uvicorn.Server(_http_cfg))

    _have_https = bool(_cert and _key and os.path.isfile(_cert) and os.path.isfile(_key))
    if _have_https:
        _https_cfg = uvicorn.Config(
            _app, host=_bind_host, port=_https_port, log_level="info",
            ssl_certfile=_cert, ssl_keyfile=_key,
        )
        _servers.append(uvicorn.Server(_https_cfg))

    _stop_event = asyncio.Event()

    def _handle_sigterm(*_):
        print("[front-router] SIGTERM received, shutting down")
        for _s in _servers:
            _s.should_exit = True
        _stop_event.set()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigterm)

    async def _run():
        await asyncio.gather(*(_s.serve() for _s in _servers))

    print(f"[front-router] HTTP  http://{_bind_host}:{_http_port}")
    if _have_https:
        print(f"[front-router] HTTPS https://{_bind_host}:{_https_port}")
    print(f"[front-router] web-api  -> {_web_api_url}")
    print(f"[front-router] sse      -> {_sse_url}")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass


__all__ = [
    "FORWARDED_HEADER_NAMES",
    "FRONT_ROUTER_ROUTE_TABLE",
    "FrontRoute",
    "FrontRouter",
    "create_front_router_app",
    "match_front_route",
    "strip_then_set_forwarded_headers",
]
