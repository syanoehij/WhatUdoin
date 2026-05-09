"""M2-19 Front Router SSE proxy 6종 조건 회귀 테스트 (standalone runner).

조건:
  1. buffering 비활성: Cache-Control/X-Accel-Buffering 헤더 존재, chunk 즉시 forward
  2. compression 비활성: gzip/brotli 미들웨어 0건, content-encoding 없음
  3. idle timeout 정책: FrontRouter timeout 없음, heartbeat 25초 코드 존재
  4. client disconnect 전파: is_disconnected+finally unsubscribe, subs 0 복원
  5. 헤더/쿠키 통과: cookie/authorization strip 없음, X-Forwarded-* 만 재작성
  6. 외부 /internal/* 차단: blocked=True + 404, downstream 0건

Run:
    python tests/phase61_front_router_sse_proxy.py
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pass = 0
_fail = 0


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _asgi_get_stream(app_obj, path: str, client: tuple = ("127.0.0.1", 11200),
                     extra_headers: list | None = None, timeout: float = 8.0):
    """ASGI GET 스트리밍 — 첫 connected chunk 수신 후 disconnect."""
    headers_seen: list = []
    body_chunks: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": extra_headers or [],
            "query_string": b"",
            "client": client,
        }
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(0.1)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                headers_seen.append(msg)
            elif msg["type"] == "http.response.body":
                body_chunks.append(msg.get("body", b""))

        await app_obj(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=timeout))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    return headers_seen, body_chunks


# ─────────────────────────────────────────────────────────────────────────────
# [1] buffering 비활성
# ─────────────────────────────────────────────────────────────────────────────

def test_1_buffering():
    print("\n[1] buffering 비활성")
    import sse_service

    # 직접 SSE service
    hs, chunks = _asgi_get_stream(sse_service.app, "/api/stream", ("127.0.0.1", 11201))
    if not hs:
        _ok("1a: response.start 수신", False, "no response"); return
    hdict = {k.lower(): v.decode("latin1") for k, v in hs[0].get("headers", [])}
    _ok("1a: Cache-Control: no-cache", "no-cache" in hdict.get(b"cache-control", ""),
        repr(hdict.get(b"cache-control", "")))
    _ok("1b: X-Accel-Buffering: no", hdict.get(b"x-accel-buffering", "") == "no",
        repr(hdict.get(b"x-accel-buffering", "")))
    _ok("1c: connected chunk 수신", b": connected" in b"".join(chunks))

    # Front Router 경유
    from front_router import FrontRouter
    router = FrontRouter(web_api_app=sse_service.app, sse_app=sse_service.app)
    hs2, chunks2 = _asgi_get_stream(router, "/api/stream", ("127.0.0.1", 11202))
    if not hs2:
        _ok("1d: FrontRouter 경유 response 수신", False, "no response"); return
    hdict2 = {k.lower(): v.decode("latin1") for k, v in hs2[0].get("headers", [])}
    _ok("1d: FrontRouter 경유 Cache-Control", "no-cache" in hdict2.get(b"cache-control", ""),
        repr(hdict2.get(b"cache-control", "")))
    _ok("1e: FrontRouter 경유 X-Accel-Buffering",
        hdict2.get(b"x-accel-buffering", "") == "no",
        repr(hdict2.get(b"x-accel-buffering", "")))


# ─────────────────────────────────────────────────────────────────────────────
# [2] compression 비활성
# ─────────────────────────────────────────────────────────────────────────────

def test_2_compression():
    print("\n[2] compression 비활성")

    targets = [ROOT / "app.py", ROOT / "front_router.py", ROOT / "sse_service.py"]
    for name in ("supervisor.py", "main.py"):
        p = ROOT / name
        if p.exists():
            targets.append(p)

    pat = re.compile(r"GZipMiddleware|gzip|brotli|CompressionMiddleware", re.IGNORECASE)
    hits: list = []
    for f in targets:
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append(f"{f.name}:{i}: {line.strip()}")

    _ok("2a: GZipMiddleware/brotli 0건", len(hits) == 0,
        "hits: " + "; ".join(hits) if hits else "")

    import sse_service
    hs, _ = _asgi_get_stream(sse_service.app, "/api/stream", ("127.0.0.1", 11203))
    if hs:
        hdict = {k.lower(): v.decode("latin1") for k, v in hs[0].get("headers", [])}
        ce = hdict.get(b"content-encoding", "")
        _ok("2b: content-encoding 없음", ce not in ("gzip", "br"), repr(ce))
    else:
        _ok("2b: content-encoding 없음", False, "no response")


# ─────────────────────────────────────────────────────────────────────────────
# [3] idle timeout 정책
# ─────────────────────────────────────────────────────────────────────────────

def test_3_idle_timeout():
    print("\n[3] idle timeout 정책")

    fr_src = (ROOT / "front_router.py").read_text(encoding="utf-8", errors="replace")
    has_wait_for = bool(re.search(r"asyncio\.wait_for", fr_src))
    _ok("3a: FrontRouter asyncio.wait_for 없음", not has_wait_for)

    sse_src = (ROOT / "sse_service.py").read_text(encoding="utf-8", errors="replace")
    _ok("3b: heartbeat last_ping 변수 존재", "last_ping" in sse_src)
    _ok("3c: heartbeat 25.0초 임계값 존재", "25.0" in sse_src)

    import sse_service
    gen_src = inspect.getsource(sse_service.sse_stream)
    _ok("3d: gen() 내 is_disconnected() 존재", "is_disconnected" in gen_src)
    _ok("3e: gen() 내 25.0 heartbeat 임계값", "25.0" in gen_src)


# ─────────────────────────────────────────────────────────────────────────────
# [4] client disconnect 전파
# ─────────────────────────────────────────────────────────────────────────────

def test_4_disconnect():
    print("\n[4] client disconnect 전파")

    import importlib
    import sse_service
    importlib.reload(sse_service)

    before = len(sse_service._broker._subs)
    _ok("4a: 초기 구독자 0", before == 0, str(before))

    mid_subs: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 11204),
        }
        first = True
        connected = asyncio.Event()

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"", "more_body": False}
            await connected.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body":
                chunk = msg.get("body", b"")
                if b": connected" in chunk:
                    mid_subs.append(len(sse_service._broker._subs))
                    connected.set()

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    _ok("4b: connected 시점 구독자 1",
        len(mid_subs) > 0 and mid_subs[0] == 1, str(mid_subs))

    after = len(sse_service._broker._subs)
    _ok("4c: disconnect 후 구독자 0", after == 0, str(after))

    sse_src = (ROOT / "sse_service.py").read_text(encoding="utf-8", errors="replace")
    _ok("4d: is_disconnected 코드 존재", "is_disconnected" in sse_src)
    _ok("4e: unsubscribe 코드 존재", "unsubscribe" in sse_src)

    # FrontRouter receive 패스스루
    from front_router import FrontRouter
    fr_src = inspect.getsource(FrontRouter.__call__)
    # receive가 wrapping 없이 그대로 전달: await app(..., receive, ...)
    _ok("4f: FrontRouter receive 패스스루", "receive," in fr_src or "receive)" in fr_src)


# ─────────────────────────────────────────────────────────────────────────────
# [5] 헤더/쿠키 통과
# ─────────────────────────────────────────────────────────────────────────────

def test_5_headers():
    print("\n[5] 헤더/쿠키 통과")

    from front_router import FORWARDED_HEADER_NAMES

    _ok("5a: cookie 미포함", b"cookie" not in FORWARDED_HEADER_NAMES)
    _ok("5b: authorization 미포함", b"authorization" not in FORWARDED_HEADER_NAMES)

    received_headers: list = []

    async def downstream_app(scope, receive, send):
        received_headers.extend(scope.get("headers", []))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    from front_router import FrontRouter
    router = FrontRouter(web_api_app=downstream_app, sse_app=downstream_app)

    inbound_headers = [
        (b"cookie", b"session_id=abc; csrf=xyz"),
        (b"authorization", b"Bearer browser-token"),
        (b"x-forwarded-for", b"1.2.3.4"),
        (b"host", b"192.168.0.18:8443"),
    ]

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": inbound_headers,
            "query_string": b"",
            "client": ("10.0.0.1", 55556),
            "scheme": "https",
            "server": ("192.168.0.18", 8443),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            pass

        await router(scope, receive, send)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_run())
    loop.close()

    hdict = {k.lower(): v for k, v in received_headers}
    _ok("5c: cookie 그대로 전달",
        hdict.get(b"cookie") == b"session_id=abc; csrf=xyz",
        repr(hdict.get(b"cookie")))
    _ok("5d: authorization 그대로 전달",
        hdict.get(b"authorization") == b"Bearer browser-token",
        repr(hdict.get(b"authorization")))
    _ok("5e: X-Forwarded-For 위조 재작성",
        hdict.get(b"x-forwarded-for") != b"1.2.3.4",
        repr(hdict.get(b"x-forwarded-for")))
    _ok("5f: X-Forwarded-For = 실제 client IP",
        hdict.get(b"x-forwarded-for") == b"10.0.0.1",
        repr(hdict.get(b"x-forwarded-for")))


# ─────────────────────────────────────────────────────────────────────────────
# [6] 외부 /internal/* 차단
# ─────────────────────────────────────────────────────────────────────────────

def test_6_internal_block():
    print("\n[6] 외부 /internal/* 차단")

    from front_router import match_front_route, FrontRouter

    r = match_front_route("/internal/publish")
    _ok("6a: blocked=True", r.blocked is True, repr(r))
    _ok("6b: target=blocked", r.target == "blocked", repr(r.target))
    _ok("6c: reason=external_internal_block",
        r.reason == "external_internal_block", repr(r.reason))

    r2 = match_front_route("/internal/foo")
    _ok("6d: /internal/foo blocked", r2.blocked is True, repr(r2))

    r3 = match_front_route("/internal")
    _ok("6e: /internal (no slash) blocked", r3.blocked is True, repr(r3))

    downstream_called = [0]

    async def downstream_app(scope, receive, send):
        downstream_called[0] += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    router = FrontRouter(
        web_api_app=downstream_app,
        sse_app=downstream_app,
        expose_route_headers=True,
    )

    result: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/internal/publish",
            "headers": [],
            "query_string": b"",
            "client": ("203.0.113.99", 9999),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(msg):
            result.append(msg)

        await router(scope, receive, send)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_run())
    loop.close()

    status = result[0].get("status") if result else None
    _ok("6f: ASGI 404 응답", status == 404, str(status))
    _ok("6g: downstream 호출 0건", downstream_called[0] == 0, str(downstream_called[0]))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("phase61 - M2-19 Front Router SSE proxy 6종 조건")
    print("=" * 65)
    test_1_buffering()
    test_2_compression()
    test_3_idle_timeout()
    test_4_disconnect()
    test_5_headers()
    test_6_internal_block()
    print("\n" + "=" * 65)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 65)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
