"""
M2-16 SSE broker 이전 회귀 테스트 (standalone runner).

시나리오 (phase57 스타일, unit-level):
  1. publisher.publish in-process fallback: wu_broker 큐 메시지 수신
  2. publisher.publish IPC 모드: mock HTTP 서버에 JSON 도착 확인
  3. publisher.publish IPC unreachable: silent fail + sse_publish_failure 증가
  4. IPC 모드 — WHATUDOIN_INTERNAL_TOKEN 설정 시 Authorization 헤더 첨부
  5. SSE service /api/stream: 200, text/event-stream, Cache-Control, X-Accel-Buffering, connected
  6. SSE service /internal/publish loopback → 200 / JSON 오류 → 400 / 필드 누락 → 400
  7. SSE service /internal/publish 외부 IP → 403 (ASGI scope 조작)
  8. app.py /api/stream: WHATUDOIN_SSE_SERVICE_URL 설정 시 503
  9. app.py /api/stream: 미설정 시 200 + text/event-stream

Run:
    python tests/phase58_sse_broker_relocation.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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


# ─────────────────────────────────────────────────────────────────────────────
# 1. in-process fallback
# ─────────────────────────────────────────────────────────────────────────────
def test_inprocess():
    print("\n[1] in-process fallback")
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    import importlib
    import publisher as pub
    importlib.reload(pub)

    from broker import SSEBroker
    b = SSEBroker()
    received: list = []
    loop = asyncio.new_event_loop()

    async def _run():
        b.start_on_loop(loop)
        q = await b.subscribe()
        pub.wu_broker = b
        pub.publish("t.ev", {"n": 1})
        await asyncio.sleep(0.05)
        try:
            received.append(q.get_nowait())
        except asyncio.QueueEmpty:
            pass
        b.unsubscribe(q)

    loop.run_until_complete(_run())
    loop.close()
    _ok("in-process: 큐 메시지 수신", received == [("t.ev", {"n": 1})], str(received))


# ─────────────────────────────────────────────────────────────────────────────
# 2. IPC mock 서버 — publish 도착
# ─────────────────────────────────────────────────────────────────────────────
def test_ipc_arrive():
    print("\n[2] IPC publish 도착")
    arrived: list = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            arrived.append(json.loads(self.rfile.read(n)))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        def log_message(self, *_): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = f"http://127.0.0.1:{port}/internal/publish"
    import importlib
    import publisher as pub
    importlib.reload(pub)
    pub.publish("ipc.ev", {"v": 7})
    t.join(timeout=2.0)
    srv.server_close()
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    _ok("IPC: 도착 event=ipc.ev, data.v=7",
        len(arrived) == 1 and arrived[0] == {"event": "ipc.ev", "data": {"v": 7}},
        str(arrived))


# ─────────────────────────────────────────────────────────────────────────────
# 3. IPC unreachable — silent fail + counter
# ─────────────────────────────────────────────────────────────────────────────
def test_ipc_silent():
    print("\n[3] IPC unreachable — silent + counter")
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19998/internal/publish"
    import importlib
    import publisher as pub
    importlib.reload(pub)
    pub.sse_publish_failure = 0
    raised = False
    try:
        pub.publish("x", {"y": 0})
    except Exception:
        raised = True
    _ok("IPC unreachable: 예외 없음", not raised)
    _ok("IPC unreachable: 카운터=1", pub.sse_publish_failure == 1, str(pub.sse_publish_failure))
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# 4. IPC — Authorization 헤더
# ─────────────────────────────────────────────────────────────────────────────
def test_ipc_auth_header():
    print("\n[4] IPC Authorization Bearer 헤더")
    headers_seen: list = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            headers_seen.append(dict(self.headers))
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
        def log_message(self, *_): pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = f"http://127.0.0.1:{port}/internal/publish"
    os.environ["WHATUDOIN_INTERNAL_TOKEN"] = "tok42"
    import importlib
    import publisher as pub
    importlib.reload(pub)
    pub.publish("a", {"b": 1})
    t.join(timeout=2.0)
    srv.server_close()
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)
    auth = headers_seen[0].get("Authorization", "") if headers_seen else ""
    _ok("IPC: Authorization: Bearer tok42", "Bearer tok42" in auth, repr(auth))


# ─────────────────────────────────────────────────────────────────────────────
# 5. SSE service /api/stream — ASGI 직접 호출로 SSE 헤더 + connected 검증
# ─────────────────────────────────────────────────────────────────────────────
def test_sse_service_stream():
    print("\n[5] SSE service /api/stream")
    import sse_service

    result_headers: list = []
    result_body: list = []
    # disconnect 이벤트 전달용 — 첫 connected 수신 후 연결 종료
    disconnected = threading.Event()

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }

        first_request_sent = False

        async def receive():
            nonlocal first_request_sent
            if not first_request_sent:
                first_request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            # connected 메시지 수신 후 disconnect 대기
            await asyncio.sleep(0.1)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                result_headers.append(msg)
            elif msg["type"] == "http.response.body":
                chunk = msg.get("body", b"")
                result_body.append(chunk)
                if b"connected" in b"".join(result_body):
                    disconnected.set()

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    if not result_headers:
        _ok("SSE service /api/stream: 200", False, "no response.start received")
        return

    h = result_headers[0]
    status = h.get("status")
    headers_dict = {k.lower(): v for k, v in h.get("headers", [])}
    ct = headers_dict.get(b"content-type", b"").decode("latin1")
    cc = headers_dict.get(b"cache-control", b"").decode("latin1")
    xab = headers_dict.get(b"x-accel-buffering", b"").decode("latin1")

    _ok("SSE service /api/stream: 200", status == 200, str(status))
    _ok("SSE service /api/stream: text/event-stream", "text/event-stream" in ct)
    _ok("SSE service /api/stream: Cache-Control no-cache", "no-cache" in cc)
    _ok("SSE service /api/stream: X-Accel-Buffering no", xab == "no")

    full_body = b"".join(result_body)
    _ok("SSE service /api/stream: ': connected' 수신",
        b": connected" in full_body, repr(full_body[:40]))


# ─────────────────────────────────────────────────────────────────────────────
# 6. SSE service /internal/publish — ASGI 직접 호출로 loopback / JSON 오류 / 필드 누락
# ─────────────────────────────────────────────────────────────────────────────
def _asgi_post(app_obj, path: str, body: bytes, client_host: str = "127.0.0.1") -> dict:
    """ASGI POST 직접 호출 → {"status": int, "body": bytes}"""
    result: list = []
    body_chunks: list = []

    async def _call():
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "query_string": b"",
            "client": (client_host, 9999),
        }
        recv_iter = iter([{"type": "http.request", "body": body, "more_body": False}])

        async def receive():
            return next(recv_iter)

        async def send(m):
            if m["type"] == "http.response.start":
                result.append(m)
            elif m["type"] == "http.response.body":
                body_chunks.append(m.get("body", b""))

        await app_obj(scope, receive, send)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_call())
    loop.close()
    status = result[0].get("status") if result else None
    return {"status": status, "body": b"".join(body_chunks)}


def test_sse_service_publish_endpoint():
    print("\n[6] SSE service /internal/publish")
    import sse_service

    # loopback POST → 200
    r = _asgi_post(sse_service.app,
                   "/internal/publish",
                   json.dumps({"event": "e", "data": {"k": 1}}).encode())
    _ok("/internal/publish: loopback → 200", r["status"] == 200, str(r["status"]))
    try:
        ok_val = json.loads(r["body"]).get("ok") is True
    except Exception:
        ok_val = False
    _ok("/internal/publish: ok=true", ok_val)

    # 잘못된 JSON → 400
    r2 = _asgi_post(sse_service.app, "/internal/publish", b"bad-json")
    _ok("/internal/publish: 잘못된 JSON → 400", r2["status"] == 400, str(r2["status"]))

    # 필드 누락(data 없음) → 400
    r3 = _asgi_post(sse_service.app, "/internal/publish", json.dumps({"event": "e"}).encode())
    _ok("/internal/publish: data 누락 → 400", r3["status"] == 400, str(r3["status"]))


# ─────────────────────────────────────────────────────────────────────────────
# 7. SSE service /internal/publish 외부 IP → 403
# ─────────────────────────────────────────────────────────────────────────────
def test_sse_service_external_403():
    print("\n[7] SSE service /internal/publish 외부 IP → 403")
    import sse_service
    result: list = []

    async def _call():
        body = json.dumps({"event": "x", "data": {}}).encode()
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/internal/publish",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "query_string": b"",
            "client": ("203.0.113.99", 9999),
        }
        recv_iter = iter([{"type": "http.request", "body": body, "more_body": False}])

        async def receive():
            return next(recv_iter)

        async def send(m):
            result.append(m)

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_call())
    loop.close()
    status = result[0].get("status") if result else None
    _ok("/internal/publish: 외부 IP → 403", status == 403, str(status))


# ─────────────────────────────────────────────────────────────────────────────
# 8. app.py /api/stream — SSE_SERVICE_URL 설정 시 503
# ─────────────────────────────────────────────────────────────────────────────
def test_app_stream_503():
    print("\n[8] app.py /api/stream — SSE_SERVICE_URL 설정 시 503")
    os.environ["WHATUDOIN_SSE_SERVICE_URL"] = "http://127.0.0.1:8765"
    from starlette.testclient import TestClient
    import app as app_mod
    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        r = c.get("/api/stream")
        _ok("/api/stream: 503", r.status_code == 503, str(r.status_code))
    os.environ.pop("WHATUDOIN_SSE_SERVICE_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# 9. app.py /api/stream — 미설정 시 SSE 정상 (ASGI 직접 호출)
# ─────────────────────────────────────────────────────────────────────────────
def test_app_stream_fallback():
    print("\n[9] app.py /api/stream - fallback SSE")
    os.environ.pop("WHATUDOIN_SSE_SERVICE_URL", None)
    import app as app_mod

    result_headers: list = []
    result_body: list = []

    async def _run():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }
        first_sent = False

        async def receive():
            nonlocal first_sent
            if not first_sent:
                first_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(0.1)
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.start":
                result_headers.append(msg)
            elif msg["type"] == "http.response.body":
                result_body.append(msg.get("body", b""))
                if b"connected" in b"".join(result_body):
                    pass  # disconnect will happen via receive()

        await app_mod.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    if not result_headers:
        _ok("/api/stream 미설정: 200", False, "no response received")
        return

    h = result_headers[0]
    status = h.get("status")
    headers_dict = {k.lower(): v for k, v in h.get("headers", [])}
    ct = headers_dict.get(b"content-type", b"").decode("latin1")

    _ok("/api/stream 미설정: 200", status == 200, str(status))
    _ok("/api/stream 미설정: text/event-stream", "text/event-stream" in ct)
    full_body = b"".join(result_body)
    _ok("/api/stream 미설정: connected", b": connected" in full_body, repr(full_body[:40]))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("phase58 - M2-16 SSE Broker Relocation")
    print("=" * 60)
    test_inprocess()
    test_ipc_arrive()
    test_ipc_silent()
    test_ipc_auth_header()
    test_sse_service_stream()
    test_sse_service_publish_endpoint()
    test_sse_service_external_403()
    test_app_stream_503()
    test_app_stream_fallback()
    print("\n" + "=" * 60)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 60)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
