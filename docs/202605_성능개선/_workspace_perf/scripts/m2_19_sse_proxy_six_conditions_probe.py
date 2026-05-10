"""M2-19 SSE proxy 6종 조건 probe.

ASGI dispatcher 구조에서 6종 조건이 자연 보장되는지 단언으로 잠근다.
네트워크 연결 없이 ASGI in-process 호출만 사용.

조건:
  1. buffering 비활성: chunk 즉시 forward, Cache-Control/X-Accel-Buffering 헤더 존재
  2. compression 비활성: gzip/brotli 미들웨어 0건
  3. idle timeout 정책: ASGI dispatch timeout 없음, heartbeat 25초 코드 존재
  4. client disconnect 전파: is_disconnected() + finally unsubscribe, subs 0 복원
  5. 헤더/쿠키 통과: cookie/authorization strip 없음, X-Forwarded-* 만 재작성
  6. 외부 /internal/* 차단: blocked=True + 404, downstream 호출 0건

Run:
    python _workspace/perf/scripts/m2_19_sse_proxy_six_conditions_probe.py
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

ROOT = Path(__file__).resolve().parents[3]
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


# ─────────────────────────────────────────────────────────────────────────────
# 조건 1: buffering 비활성
# ─────────────────────────────────────────────────────────────────────────────

def test_condition1_buffering():
    """SSE 응답 헤더에 Cache-Control: no-cache + X-Accel-Buffering: no 존재.
    ASGI dispatch를 통해 chunk가 즉시 send로 도달하는지 확인.
    """
    print("\n[조건 1] buffering 비활성")
    import sse_service

    headers_seen: list = []
    chunks: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 11111),
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
                chunks.append(msg.get("body", b""))

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    if not headers_seen:
        _ok("조건1: 응답 수신", False, "no response.start")
        return

    h = headers_seen[0]
    hdict = {k.lower(): v.decode("latin1") for k, v in h.get("headers", [])}

    cc = hdict.get(b"cache-control", "")
    _ok("조건1-a: Cache-Control: no-cache", "no-cache" in cc, repr(cc))

    xab = hdict.get(b"x-accel-buffering", "")
    _ok("조건1-b: X-Accel-Buffering: no", xab == "no", repr(xab))

    # chunk 즉시 도달 — connected 메시지가 별도 chunk로 도달
    full = b"".join(chunks)
    _ok("조건1-c: connected chunk 수신", b": connected" in full, repr(full[:40]))

    # Front Router를 통해서도 동일 헤더 보장 — ASGI dispatch 통과 확인
    from front_router import FrontRouter
    fr_headers: list = []
    fr_chunks: list = []

    async def _run_fr():
        router = FrontRouter(
            web_api_app=sse_service.app,
            sse_app=sse_service.app,
            expose_route_headers=False,
        )
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 11112),
            "scheme": "https",
            "server": ("192.168.0.18", 8443),
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
                fr_headers.append(msg)
            elif msg["type"] == "http.response.body":
                fr_chunks.append(msg.get("body", b""))

        await router(scope, receive, send)

    loop2 = asyncio.new_event_loop()
    try:
        loop2.run_until_complete(asyncio.wait_for(_run_fr(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop2.close()

    if fr_headers:
        fh = {k.lower(): v.decode("latin1") for k, v in fr_headers[0].get("headers", [])}
        _ok("조건1-d: FrontRouter 경유 Cache-Control", "no-cache" in fh.get(b"cache-control", ""),
            repr(fh.get(b"cache-control", "")))
        _ok("조건1-e: FrontRouter 경유 X-Accel-Buffering",
            fh.get(b"x-accel-buffering", "") == "no",
            repr(fh.get(b"x-accel-buffering", "")))
    else:
        _ok("조건1-d: FrontRouter 경유 Cache-Control", False, "no response from router")
        _ok("조건1-e: FrontRouter 경유 X-Accel-Buffering", False, "no response from router")


# ─────────────────────────────────────────────────────────────────────────────
# 조건 2: compression 비활성
# ─────────────────────────────────────────────────────────────────────────────

def test_condition2_compression():
    """gzip/brotli 미들웨어 0건 grep + 응답 헤더에 content-encoding 없음."""
    print("\n[조건 2] compression 비활성")

    targets = [
        ROOT / "app.py",
        ROOT / "front_router.py",
        ROOT / "sse_service.py",
    ]
    # supervisor.py, main.py 있으면 포함
    for name in ("supervisor.py", "main.py"):
        p = ROOT / name
        if p.exists():
            targets.append(p)

    pattern = re.compile(r"GZipMiddleware|gzip|brotli|CompressionMiddleware", re.IGNORECASE)
    hits: list = []
    for f in targets:
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{f.name}:{i}: {line.strip()}")

    _ok("조건2-a: gzip/brotli/GZipMiddleware 0건",
        len(hits) == 0,
        "hits: " + "; ".join(hits) if hits else "")

    # ASGI level: SSE 응답에 content-encoding 없음
    import sse_service
    headers_seen: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 11113),
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

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    if headers_seen:
        hdict = {k.lower(): v.decode("latin1") for k, v in headers_seen[0].get("headers", [])}
        ce = hdict.get(b"content-encoding", "")
        _ok("조건2-b: content-encoding 없음", ce == "" or ce not in ("gzip", "br"),
            repr(ce))
    else:
        _ok("조건2-b: content-encoding 없음", False, "no response")


# ─────────────────────────────────────────────────────────────────────────────
# 조건 3: idle timeout 정책
# ─────────────────────────────────────────────────────────────────────────────

def test_condition3_idle_timeout():
    """ASGI dispatch에 timeout 없음 + SSE heartbeat 25초 코드 존재."""
    print("\n[조건 3] idle timeout 정책")

    # Front Router에 asyncio.wait_for / timeout 미사용
    fr_src = (ROOT / "front_router.py").read_text(encoding="utf-8", errors="replace")
    has_timeout = bool(re.search(r"asyncio\.wait_for|\.timeout\b", fr_src))
    _ok("조건3-a: FrontRouter timeout 없음", not has_timeout,
        "found asyncio.wait_for or .timeout in front_router.py" if has_timeout else "")

    # SSE service gen(): _last_ping 및 25.0 임계값 코드 존재
    sse_src = (ROOT / "sse_service.py").read_text(encoding="utf-8", errors="replace")
    has_last_ping = "last_ping" in sse_src
    _ok("조건3-b: SSE heartbeat last_ping 변수 존재", has_last_ping)

    has_25 = "25.0" in sse_src
    _ok("조건3-c: heartbeat 25.0초 임계값 존재", has_25)

    # gen() 함수 소스에서 확인
    import sse_service
    gen_src = inspect.getsource(sse_service.sse_stream)
    _ok("조건3-d: gen() 내 is_disconnected 호출",
        "is_disconnected" in gen_src)
    _ok("조건3-e: gen() 내 25.0 heartbeat 임계값",
        "25.0" in gen_src)


# ─────────────────────────────────────────────────────────────────────────────
# 조건 4: client disconnect 전파
# ─────────────────────────────────────────────────────────────────────────────

def test_condition4_disconnect():
    """SSE service gen()의 finally에서 unsubscribe 호출.
    disconnect 후 _broker._subs 카운트 0 복원.
    """
    print("\n[조건 4] client disconnect 전파")

    import sse_service
    import importlib
    importlib.reload(sse_service)

    # disconnect 전 구독자 0
    before_subs = len(sse_service._broker._subs)
    _ok("조건4-a: 초기 구독자 0", before_subs == 0, str(before_subs))

    mid_subs: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 11114),
        }
        first = True
        connected_seen = asyncio.Event()

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": b"", "more_body": False}
            # connected 메시지가 오면 구독자 수 기록 후 disconnect
            await connected_seen.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body":
                chunk = msg.get("body", b"")
                if b": connected" in chunk:
                    mid_subs.append(len(sse_service._broker._subs))
                    connected_seen.set()

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    _ok("조건4-b: connected 시점 구독자 1",
        len(mid_subs) > 0 and mid_subs[0] == 1,
        str(mid_subs))

    after_subs = len(sse_service._broker._subs)
    _ok("조건4-c: disconnect 후 구독자 0 (unsubscribe 확인)",
        after_subs == 0, str(after_subs))

    # is_disconnected + break 코드 존재
    sse_src = (ROOT / "sse_service.py").read_text(encoding="utf-8", errors="replace")
    _ok("조건4-d: is_disconnected() 코드 존재", "is_disconnected" in sse_src)
    _ok("조건4-e: unsubscribe 코드 존재", "unsubscribe" in sse_src)

    # Front Router의 receive 패스스루 — FrontRouter __call__이 receive를 그대로 forward
    fr_src = (ROOT / "front_router.py").read_text(encoding="utf-8", errors="replace")
    # receive가 별도 wrapping 없이 그대로 전달되는지 확인
    # await app(forwarded_scope, receive, ...) 패턴
    _ok("조건4-f: FrontRouter receive 패스스루",
        "await app(" in fr_src and "receive," in fr_src)


# ─────────────────────────────────────────────────────────────────────────────
# 조건 5: 헤더/쿠키 통과
# ─────────────────────────────────────────────────────────────────────────────

def test_condition5_headers():
    """cookie, authorization은 FORWARDED_HEADER_NAMES에 없음.
    ASGI level: downstream이 cookie/authorization 그대로 수신.
    X-Forwarded-For 위조는 라우터가 재작성.
    """
    print("\n[조건 5] 헤더/쿠키 통과")

    from front_router import FORWARDED_HEADER_NAMES, strip_then_set_forwarded_headers

    # FORWARDED_HEADER_NAMES에 cookie / authorization 미포함
    _ok("조건5-a: cookie 미포함",
        b"cookie" not in FORWARDED_HEADER_NAMES,
        str(FORWARDED_HEADER_NAMES))
    _ok("조건5-b: authorization 미포함",
        b"authorization" not in FORWARDED_HEADER_NAMES,
        str(FORWARDED_HEADER_NAMES))

    # ASGI level: downstream이 cookie/authorization 그대로 수신
    received_headers: list = []

    async def downstream_app(scope, receive, send):
        received_headers.extend(scope.get("headers", []))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    from front_router import FrontRouter
    router = FrontRouter(
        web_api_app=downstream_app,
        sse_app=downstream_app,
    )

    inbound_headers = [
        (b"cookie", b"session_id=abc; csrf=xyz"),
        (b"authorization", b"Bearer browser-token"),
        (b"x-forwarded-for", b"1.2.3.4"),  # 위조 — 재작성되어야 함
        (b"host", b"192.168.0.18:8443"),
    ]

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": inbound_headers,
            "query_string": b"",
            "client": ("10.0.0.1", 55555),
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

    cookie_val = hdict.get(b"cookie", b"")
    _ok("조건5-c: cookie 그대로 전달",
        cookie_val == b"session_id=abc; csrf=xyz",
        repr(cookie_val))

    auth_val = hdict.get(b"authorization", b"")
    _ok("조건5-d: authorization 그대로 전달",
        auth_val == b"Bearer browser-token",
        repr(auth_val))

    # X-Forwarded-For는 라우터가 실제 client IP로 재작성
    xff_val = hdict.get(b"x-forwarded-for", b"")
    _ok("조건5-e: X-Forwarded-For 위조 → 실제 IP로 재작성",
        xff_val != b"1.2.3.4",
        repr(xff_val))
    # 실제 client는 10.0.0.1
    _ok("조건5-f: X-Forwarded-For = 실제 client IP",
        xff_val == b"10.0.0.1",
        repr(xff_val))


# ─────────────────────────────────────────────────────────────────────────────
# 조건 6: 외부 /internal/* 차단
# ─────────────────────────────────────────────────────────────────────────────

def test_condition6_internal_block():
    """match_front_route('/internal/foo') → blocked=True, 404.
    ASGI level: downstream 호출 0건.
    """
    print("\n[조건 6] 외부 /internal/* 차단")

    from front_router import match_front_route, FrontRouter

    # route table 확인
    r = match_front_route("/internal/publish")
    _ok("조건6-a: blocked=True", r.blocked is True, repr(r))
    _ok("조건6-b: target=blocked", r.target == "blocked", repr(r.target))
    _ok("조건6-c: reason=external_internal_block",
        r.reason == "external_internal_block", repr(r.reason))

    r2 = match_front_route("/internal/foo")
    _ok("조건6-d: /internal/foo blocked", r2.blocked is True, repr(r2))

    r3 = match_front_route("/internal")
    _ok("조건6-e: /internal (no slash) blocked", r3.blocked is True, repr(r3))

    # ASGI level: 404 응답 + downstream 호출 0
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
    _ok("조건6-f: ASGI 404 응답", status == 404, str(status))
    _ok("조건6-g: downstream 호출 0건", downstream_called[0] == 0,
        str(downstream_called[0]))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("m2_19_sse_proxy_six_conditions_probe")
    print("=" * 65)

    test_condition1_buffering()
    test_condition2_compression()
    test_condition3_idle_timeout()
    test_condition4_disconnect()
    test_condition5_headers()
    test_condition6_internal_block()

    print("\n" + "=" * 65)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 65)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
