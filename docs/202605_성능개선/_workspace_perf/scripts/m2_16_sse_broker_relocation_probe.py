"""
M2-16 SSE broker 이전 probe.

시나리오:
  1. publish 추상화 — in-process fallback: wu_broker.subscribe 큐가 메시지 수신
  2. publish 추상화 — IPC mock HTTP 서버: publish 도착 확인
  3. publish 추상화 — IPC unreachable: silent fail + sse_publish_failure 카운터 증가
  4. SSE service ASGI /api/stream: SSE 헤더 + 연결 메시지 확인
  5. SSE service /internal/publish: loopback 200, 외부 IP 403, 잘못된 JSON 400
  6. app.py /api/stream WHATUDOIN_SSE_SERVICE_URL 설정 시 503 반환
  7. app.py /api/stream 미설정 시 정상 SSE 응답

Run:
    python _workspace/perf/scripts/m2_16_sse_broker_relocation_probe.py
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PASS = 0
_FAIL = 0
_RESULTS: list[dict] = []


def _record(name: str, ok: bool, detail: str = ""):
    global _PASS, _FAIL
    status = "PASS" if ok else "FAIL"
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
    _RESULTS.append({"name": name, "status": status, "detail": detail})
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: in-process fallback
# ─────────────────────────────────────────────────────────────────────────────
def test_inprocess_fallback():
    print("\n[1] publish 추상화 — in-process fallback")
    # env에 WHATUDOIN_SSE_PUBLISH_URL 없어야 함
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)

    # publisher 재로드 (env 변경 반영)
    import importlib
    import publisher as pub_mod
    importlib.reload(pub_mod)

    from broker import SSEBroker
    test_broker = SSEBroker()

    received: list = []
    loop = asyncio.new_event_loop()

    async def _run():
        test_broker.start_on_loop(loop)
        q = await test_broker.subscribe()
        pub_mod.wu_broker = test_broker  # 테스트용 broker 교체
        pub_mod.publish("test.event", {"x": 1})
        await asyncio.sleep(0.05)
        try:
            msg = q.get_nowait()
            received.append(msg)
        except asyncio.QueueEmpty:
            pass
        test_broker.unsubscribe(q)

    loop.run_until_complete(_run())
    loop.close()

    ok = len(received) == 1 and received[0] == ("test.event", {"x": 1})
    _record("in-process: wu_broker.subscribe 큐 메시지 수신", ok,
            f"received={received}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: IPC mock HTTP 서버 — publish 도착 확인
# ─────────────────────────────────────────────────────────────────────────────
def test_ipc_publish():
    print("\n[2] publish 추상화 — IPC mock HTTP 서버")
    arrived: list[dict] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            arrived.append(json.loads(body))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, *args): pass

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()

    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = f"http://127.0.0.1:{port}/internal/publish"
    import importlib
    import publisher as pub_mod
    importlib.reload(pub_mod)

    pub_mod.publish("ipc.test", {"val": 42})
    t.join(timeout=2.0)
    srv.server_close()

    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)

    ok = len(arrived) == 1 and arrived[0].get("event") == "ipc.test" and arrived[0].get("data") == {"val": 42}
    _record("IPC: mock 서버에 publish 도착", ok, f"arrived={arrived}")

    # Authorization 헤더 확인
    arrived2: list[dict] = []
    headers_seen: list[dict] = []

    class _Handler2(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            arrived2.append(json.loads(body))
            headers_seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        def log_message(self, *args): pass

    srv2 = HTTPServer(("127.0.0.1", 0), _Handler2)
    port2 = srv2.server_address[1]
    t2 = threading.Thread(target=srv2.handle_request, daemon=True)
    t2.start()

    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = f"http://127.0.0.1:{port2}/internal/publish"
    os.environ["WHATUDOIN_INTERNAL_TOKEN"] = "testtoken123"
    importlib.reload(pub_mod)
    pub_mod.publish("ipc.auth", {"v": 1})
    t2.join(timeout=2.0)
    srv2.server_close()
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)
    os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)

    auth_header = headers_seen[0].get("Authorization", "") if headers_seen else ""
    ok2 = "Bearer testtoken123" in auth_header
    _record("IPC: Authorization Bearer 헤더 전달", ok2, f"Authorization={auth_header!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: IPC unreachable — silent fail + 카운터 증가
# ─────────────────────────────────────────────────────────────────────────────
def test_ipc_silent_fail():
    print("\n[3] publish 추상화 — IPC unreachable → silent fail + 카운터 증가")
    # 사용하지 않는 포트로 IPC 시도
    os.environ["WHATUDOIN_SSE_PUBLISH_URL"] = "http://127.0.0.1:19999/internal/publish"
    import importlib
    import publisher as pub_mod
    importlib.reload(pub_mod)
    pub_mod.sse_publish_failure = 0

    raised = False
    try:
        pub_mod.publish("fail.test", {"x": 0})
    except Exception as e:
        raised = True

    ok_no_raise = not raised
    ok_counter = pub_mod.sse_publish_failure == 1
    _record("IPC unreachable: publish 예외 미발생(silent)", ok_no_raise)
    _record("IPC unreachable: sse_publish_failure 카운터 1 증가", ok_counter,
            f"counter={pub_mod.sse_publish_failure}")
    os.environ.pop("WHATUDOIN_SSE_PUBLISH_URL", None)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4: SSE service ASGI /api/stream
# ─────────────────────────────────────────────────────────────────────────────
def test_sse_service_stream():
    print("\n[4] SSE service ASGI /api/stream — SSE 헤더 + connected 메시지")
    import sse_service

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

        await sse_service.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    if not result_headers:
        _record("SSE service /api/stream: HTTP 200", False, "no response received")
        return

    h = result_headers[0]
    status = h.get("status")
    headers_dict = {k.lower(): v for k, v in h.get("headers", [])}
    ct = headers_dict.get(b"content-type", b"").decode("latin1")
    cc = headers_dict.get(b"cache-control", b"").decode("latin1")
    xab = headers_dict.get(b"x-accel-buffering", b"").decode("latin1")

    _record("SSE service /api/stream: HTTP 200", status == 200, str(status))
    _record("SSE service /api/stream: text/event-stream Content-Type",
            "text/event-stream" in ct)
    _record("SSE service /api/stream: Cache-Control: no-cache", "no-cache" in cc)
    _record("SSE service /api/stream: X-Accel-Buffering: no", xab == "no")

    full_body = b"".join(result_body)
    _record("SSE service /api/stream: ': connected' 첫 메시지 수신",
            b": connected" in full_body, f"first={full_body[:60]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5: SSE service /internal/publish — ASGI 직접 호출
# ─────────────────────────────────────────────────────────────────────────────
def _asgi_post_probe(app_obj, path: str, body: bytes, client_host: str = "127.0.0.1") -> dict:
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


def test_sse_service_internal_publish():
    print("\n[5] SSE service /internal/publish — loopback 200 / 외부 403 / 잘못된 JSON 400")
    import sse_service

    # loopback POST → 200
    r = _asgi_post_probe(sse_service.app, "/internal/publish",
                         json.dumps({"event": "x", "data": {"k": 1}}).encode())
    _record("SSE service /internal/publish: loopback → 200",
            r["status"] == 200, f"status={r['status']}")
    try:
        ok_val = json.loads(r["body"]).get("ok") is True
    except Exception:
        ok_val = False
    _record("SSE service /internal/publish: 응답 ok=true", ok_val)

    # 잘못된 JSON → 400
    r2 = _asgi_post_probe(sse_service.app, "/internal/publish", b"not-json")
    _record("SSE service /internal/publish: 잘못된 JSON → 400",
            r2["status"] == 400, f"status={r2['status']}")

    # 필드 누락 → 400
    r3 = _asgi_post_probe(sse_service.app, "/internal/publish",
                          json.dumps({"event": "x"}).encode())
    _record("SSE service /internal/publish: data 누락 → 400",
            r3["status"] == 400, f"status={r3['status']}")


def test_sse_service_external_blocked():
    """외부 IP에서 /internal/publish 접근 시 403 — TestClient는 loopback이므로
    ASGI scope를 직접 조작하여 검증."""
    print("\n[5b] SSE service /internal/publish — 외부 IP 403")
    import asyncio as _aio
    from starlette.testclient import TestClient
    import sse_service

    # TestClient의 기본 client는 testclient라는 special scope를 사용함.
    # scope["client"]를 외부 IP로 바꿔 ASGI 직접 호출로 검증.
    result: list = []

    async def _call():
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/internal/publish",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"30"),
            ],
            "query_string": b"",
            "client": ("203.0.113.1", 12345),  # 외부 IP
        }
        body = json.dumps({"event": "x", "data": {}}).encode()
        recv_iter = iter([
            {"type": "http.request", "body": body, "more_body": False}
        ])

        async def receive():
            return next(recv_iter)

        responses: list = []

        async def send(message):
            responses.append(message)

        await sse_service.app(scope, receive, send)
        result.extend(responses)

    loop = _aio.new_event_loop()
    loop.run_until_complete(_call())
    loop.close()

    status = result[0].get("status") if result else None
    _record("SSE service /internal/publish: 외부 IP → 403",
            status == 403, f"status={status}")


# ─────────────────────────────────────────────────────────────────────────────
# Scenario 6 & 7: app.py /api/stream env 분기
# ─────────────────────────────────────────────────────────────────────────────
def test_app_stream_env_gate():
    print("\n[6] app.py /api/stream — WHATUDOIN_SSE_SERVICE_URL 설정 시 503")
    os.environ["WHATUDOIN_SSE_SERVICE_URL"] = "http://127.0.0.1:8765"
    import app as app_mod

    result: list = []

    async def _call():
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/stream",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(m):
            result.append(m)

        await app_mod.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_call())
    loop.close()
    status = result[0].get("status") if result else None
    _record("app.py /api/stream: SSE_SERVICE_URL 설정 시 503",
            status == 503, f"status={status}")
    os.environ.pop("WHATUDOIN_SSE_SERVICE_URL", None)


def test_app_stream_fallback():
    print("\n[7] app.py /api/stream — SSE_SERVICE_URL 미설정 시 SSE 정상 응답")
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

        await app_mod.app(scope, receive, send)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(asyncio.wait_for(_run(), timeout=8.0))
    except asyncio.TimeoutError:
        pass
    finally:
        loop.close()

    if not result_headers:
        _record("app.py /api/stream: 미설정 시 HTTP 200", False, "no response")
        return

    h = result_headers[0]
    status = h.get("status")
    headers_dict = {k.lower(): v for k, v in h.get("headers", [])}
    ct = headers_dict.get(b"content-type", b"").decode("latin1")

    _record("app.py /api/stream: 미설정 시 HTTP 200", status == 200, str(status))
    _record("app.py /api/stream: 미설정 시 text/event-stream", "text/event-stream" in ct)
    full_body = b"".join(result_body)
    _record("app.py /api/stream: 미설정 시 ': connected' 수신",
            b": connected" in full_body)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("M2-16 SSE Broker Relocation Probe")
    print("=" * 60)

    test_inprocess_fallback()
    test_ipc_publish()
    test_ipc_silent_fail()
    test_sse_service_stream()
    test_sse_service_internal_publish()
    test_sse_service_external_blocked()
    test_app_stream_env_gate()
    test_app_stream_fallback()

    print("\n" + "=" * 60)
    print(f"TOTAL: {_PASS + _FAIL}  PASS: {_PASS}  FAIL: {_FAIL}")
    print("=" * 60)

    # 결과 markdown 저장
    _save_report()

    if _FAIL > 0:
        sys.exit(1)


def _save_report():
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "_workspace" / "perf" / "sse_broker_m2_16" / "runs" / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# M2-16 SSE Broker Relocation Probe Result",
        f"",
        f"**Run:** {ts}  **PASS:** {_PASS}  **FAIL:** {_FAIL}",
        "",
        "| # | Scenario | Status | Detail |",
        "|---|----------|--------|--------|",
    ]
    for i, r in enumerate(_RESULTS, 1):
        lines.append(f"| {i} | {r['name']} | {r['status']} | {r['detail'][:80]} |")
    (out_dir / "m2_16_sse_broker_probe.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n결과 저장: {out_dir / 'm2_16_sse_broker_probe.md'}")


if __name__ == "__main__":
    main()
