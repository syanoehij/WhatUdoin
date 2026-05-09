"""SSE service — 독립 프로세스로 실행되는 SSE 브로커 ASGI app.

역할:
- GET  /api/stream        : SSE 스트림 (클라이언트 직접 연결)
- POST /internal/publish  : Web API → SSE service IPC 채널 (loopback only)

Front Router (M2-10)가 /api/stream을 이 앱으로 라우팅하고,
Web API(app.py)는 publisher.publish()로 IPC POST를 통해 이벤트를 전달한다.

단독 실행:
    python sse_service.py
    WHATUDOIN_SSE_SERVICE_PORT=8765 python sse_service.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from broker import SSEBroker

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# 이 service 전용 broker 인스턴스
_broker = SSEBroker()


# ── lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: Starlette):
    _broker.start_on_loop(asyncio.get_running_loop())
    yield


# ── /api/stream ─────────────────────────────────────────────────────────────

async def sse_stream(request: Request) -> Response:
    """캘린더·칸반·간트 실시간 동기화용 SSE 엔드포인트.

    - 비로그인 게스트 포함 — 페이로드는 id/action 메타 한정
    - 25초마다 ping 주석으로 프록시·브라우저 타임아웃 방지
    - 클라이언트 연결 종료 시 subscribe한 큐를 자동 해제
    """
    from starlette.responses import StreamingResponse

    async def gen():
        queue = await _broker.subscribe()
        last_ping = time.monotonic()
        try:
            yield b": connected\n\n"
            while True:
                try:
                    ev, data = await asyncio.wait_for(queue.get(), timeout=3.0)
                    line = f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    yield line.encode("utf-8")
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    if time.monotonic() - last_ping >= 25.0:
                        yield b": ping\n\n"
                        last_ping = time.monotonic()
        finally:
            _broker.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── /internal/publish ────────────────────────────────────────────────────────

async def internal_publish(request: Request) -> JSONResponse:
    """IPC publish 엔드포인트. loopback 출처만 허용.

    M2-17이 토큰 인증을 추가 예정. 본 step에서는 loopback IP 가드만.
    """
    # loopback 가드
    client = request.client
    client_host = client.host if client else ""
    if client_host not in _LOOPBACK_HOSTS:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    event = body.get("event")
    data = body.get("data")
    if not isinstance(event, str) or not isinstance(data, dict):
        return JSONResponse({"error": "event(str) and data(dict) required"}, status_code=400)

    _broker.publish(event, data)
    return JSONResponse({"ok": True})


# ── ASGI app ─────────────────────────────────────────────────────────────────

app = Starlette(
    lifespan=_lifespan,
    routes=[
        Route("/api/stream", sse_stream, methods=["GET"]),
        Route("/internal/publish", internal_publish, methods=["POST"]),
    ],
)


# ── standalone 진입점 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("WHATUDOIN_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("WHATUDOIN_SSE_SERVICE_PORT", "8765"))
    uvicorn.run("sse_service:app", host=host, port=port, reload=False)
