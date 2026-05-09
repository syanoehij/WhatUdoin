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
import secrets
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

def _verify_internal_token(request: Request) -> bool:
    """Bearer 토큰 검증. timing-safe 비교.

    env 미설정이면 모든 요청을 거부(production에서는 항상 토큰 발급).
    토큰 raw 값은 로그에 절대 출력하지 않는다.
    """
    expected = os.environ.get("WHATUDOIN_INTERNAL_TOKEN", "").strip()
    if not expected:
        # 토큰 미설정 — 안전 동작: 거부
        return False
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    provided = auth[len("Bearer "):]
    if not provided:
        return False
    return secrets.compare_digest(expected, provided)


async def internal_publish(request: Request) -> JSONResponse:
    """IPC publish 엔드포인트. loopback 출처 + Bearer 토큰 검증.

    loopback IP 가드 후 토큰 검증. 불일치 시 401.
    토큰 raw 값은 access log/app log에 출력하지 않는다.
    """
    # loopback 가드
    client = request.client
    client_host = client.host if client else ""
    if client_host not in _LOOPBACK_HOSTS:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    # Bearer 토큰 인증
    if not _verify_internal_token(request):
        # 메타만 기록 — raw 토큰 값 절대 미출력
        print("unauthorized internal publish attempt", flush=True)
        return JSONResponse({"error": "unauthorized"}, status_code=401)

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


# ── /healthz ─────────────────────────────────────────────────────────────────

async def healthz_endpoint(request: Request) -> JSONResponse:
    """서비스 상태 및 구독자 수 반환."""
    return JSONResponse({
        "status": "ok",
        "subscribers": len(_broker._subs),
        "service": "sse",
    })


# ── ASGI app ─────────────────────────────────────────────────────────────────

app = Starlette(
    lifespan=_lifespan,
    routes=[
        Route("/api/stream", sse_stream, methods=["GET"]),
        Route("/internal/publish", internal_publish, methods=["POST"]),
        Route("/healthz", healthz_endpoint, methods=["GET"]),
    ],
)


# ── standalone 진입점 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("WHATUDOIN_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("WHATUDOIN_SSE_SERVICE_PORT", "8765"))
    uvicorn.run("sse_service:app", host=host, port=port, reload=False)
