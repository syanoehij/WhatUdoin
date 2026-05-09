"""
M2-17 SSE 토큰 인증 + /healthz + supervisor crash-loop 테스트 (standalone runner).

시나리오:
  1. SSE service /internal/publish 토큰 누락 → 401
  2. SSE service /internal/publish 잘못된 토큰 → 401
  3. SSE service /internal/publish 올바른 토큰 → 200
  4. SSE service /internal/publish env 미설정 + 헤더 없음 → 401
  5. secrets.compare_digest 사용 확인 (소스 grep)
  6. SSE service GET /healthz → 200 + {"status":"ok","service":"sse"}
  7. Web API GET /healthz → 200 + {"status":"ok","service":"web-api"}
  8. supervisor crash-loop: 5분 안 3회 → status=degraded
  9. supervisor crash-loop: 4번째 start_service → spawn 없이 degraded 유지
 10. supervisor reset_crash_loop 후 정상 start_service 가능
 11. probe_healthz: mock 서버 200 + ok → True
 12. probe_healthz: timeout/4xx → False

Run:
    python tests/phase59_sse_token_health_crashloop.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

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
# ASGI helper
# ─────────────────────────────────────────────────────────────────────────────

def _asgi_post(app_obj, path: str, body: bytes, client_host: str = "127.0.0.1",
               extra_headers: list | None = None) -> dict:
    result: list = []
    body_chunks: list = []

    async def _call():
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        if extra_headers:
            headers.extend(extra_headers)
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
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


def _asgi_get(app_obj, path: str, client_host: str = "127.0.0.1") -> dict:
    result: list = []
    body_chunks: list = []

    async def _call():
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": (client_host, 9999),
        }
        recv_iter = iter([{"type": "http.request", "body": b"", "more_body": False}])

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


# ─────────────────────────────────────────────────────────────────────────────
# 1-4. SSE service 토큰 인증
# ─────────────────────────────────────────────────────────────────────────────

def test_token_auth():
    print("\n[1-4] SSE service /internal/publish 토큰 인증")
    import importlib

    valid_body = json.dumps({"event": "e", "data": {"k": 1}}).encode()
    valid_token = "correct-token-xyz"

    # 1. 토큰 누락 (Authorization 헤더 없음, env 설정)
    os.environ["WHATUDOIN_INTERNAL_TOKEN"] = valid_token
    import sse_service
    importlib.reload(sse_service)
    r = _asgi_post(sse_service.app, "/internal/publish", valid_body)
    _ok("[1] 헤더 없음 → 401", r["status"] == 401, str(r["status"]))

    # 2. 잘못된 토큰
    r2 = _asgi_post(sse_service.app, "/internal/publish", valid_body,
                    extra_headers=[(b"authorization", b"Bearer wrong-token")])
    _ok("[2] 잘못된 토큰 → 401", r2["status"] == 401, str(r2["status"]))

    # 3. 올바른 토큰
    r3 = _asgi_post(sse_service.app, "/internal/publish", valid_body,
                    extra_headers=[(b"authorization", f"Bearer {valid_token}".encode())])
    _ok("[3] 올바른 토큰 → 200", r3["status"] == 200, str(r3["status"]))

    # 4. env 미설정 + 헤더 없음 → 401
    os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)
    importlib.reload(sse_service)
    r4 = _asgi_post(sse_service.app, "/internal/publish", valid_body)
    _ok("[4] env 미설정 + 헤더 없음 → 401", r4["status"] == 401, str(r4["status"]))

    os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)


# ─────────────────────────────────────────────────────────────────────────────
# 5. secrets.compare_digest 사용 확인
# ─────────────────────────────────────────────────────────────────────────────

def test_compare_digest_used():
    print("\n[5] secrets.compare_digest 사용 확인")
    sse_src = (ROOT / "sse_service.py").read_text(encoding="utf-8")
    _ok("[5] secrets.compare_digest in sse_service.py",
        "secrets.compare_digest" in sse_src)


# ─────────────────────────────────────────────────────────────────────────────
# 6. SSE service /healthz
# ─────────────────────────────────────────────────────────────────────────────

def test_sse_healthz():
    print("\n[6] SSE service GET /healthz")
    import importlib
    import sse_service
    importlib.reload(sse_service)
    r = _asgi_get(sse_service.app, "/healthz")
    _ok("[6a] /healthz → 200", r["status"] == 200, str(r["status"]))
    try:
        data = json.loads(r["body"])
        _ok("[6b] status=ok", data.get("status") == "ok", str(data))
        _ok("[6c] service=sse", data.get("service") == "sse", str(data))
        _ok("[6d] subscribers key present", "subscribers" in data, str(data))
    except Exception as e:
        _ok("[6b] JSON parse", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 7. Web API /healthz
# ─────────────────────────────────────────────────────────────────────────────

def test_app_healthz():
    print("\n[7] Web API GET /healthz")
    from starlette.testclient import TestClient
    import app as app_mod
    with TestClient(app_mod.app, raise_server_exceptions=False) as c:
        r = c.get("/healthz")
        _ok("[7a] /healthz → 200", r.status_code == 200, str(r.status_code))
        try:
            data = r.json()
            _ok("[7b] status=ok", data.get("status") == "ok", str(data))
            _ok("[7c] service=web-api", data.get("service") == "web-api", str(data))
            # M2-18: 새 키 강제 검증
            _ok("[7d] sse_publish_failures key", "sse_publish_failures" in data, str(data))
            _ok("[7e] sse_publish_last_event key", "sse_publish_last_event" in data, str(data))
            _ok("[7f] sse_publish_last_at key", "sse_publish_last_at" in data, str(data))
        except Exception as e:
            _ok("[7b] JSON parse", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# 8-10. supervisor crash-loop
# ─────────────────────────────────────────────────────────────────────────────

def test_crash_loop():
    print("\n[8-10] supervisor crash-loop 차단")
    from supervisor import (
        CRASH_LOOP_MAX_FAILURES,
        CRASH_LOOP_WINDOW_SECONDS,
        ServiceState,
        WhatUdoinSupervisor,
        ServiceSpec,
    )
    import tempfile

    sup = WhatUdoinSupervisor(run_dir=tempfile.mkdtemp())
    sup.ensure_runtime_dirs()

    # crash_history에 3회 push (5분 윈도우 안)
    state = ServiceState(name="test-svc")
    sup.services["test-svc"] = state

    now = time.time()
    state.crash_history = [now - 10, now - 20, now - 30]

    # 8. 3회 누적 → _is_crash_loop True
    _ok("[8] 3회 누적 → crash-loop 감지",
        sup._is_crash_loop(state))

    # 9. start_service 진입 시 degraded 반환, spawn 없음
    spec = ServiceSpec(
        name="test-svc",
        command=["python", "-c", "import time; time.sleep(60)"],
    )
    # token 파일 필요
    sup.token_path.write_text("dummy-token", encoding="utf-8")

    result_state = sup.start_service(spec)
    _ok("[9a] start_service → status=degraded",
        result_state.status == "degraded", result_state.status)
    _ok("[9b] start_service → last_error=crash-loop blocked",
        "crash-loop blocked" in result_state.last_error, result_state.last_error)
    _ok("[9c] start_service → 프로세스 spawn 없음 (pid None)",
        result_state.pid is None, str(result_state.pid))

    # 10. reset_crash_loop 후 정상 spawn 가능
    sup.reset_crash_loop("test-svc")
    _ok("[10a] reset 후 status=stopped",
        sup.services["test-svc"].status == "stopped")
    _ok("[10b] reset 후 crash_history 비어있음",
        len(sup.services["test-svc"].crash_history) == 0)
    # 실제 spawn 시도 (유효한 명령으로)
    import sys as _sys
    py = _sys.executable
    spec2 = ServiceSpec(
        name="test-svc",
        command=[py, "-c", "import time; time.sleep(5)"],
        startup_grace_seconds=0.2,
    )
    result2 = sup.start_service(spec2)
    _ok("[10c] reset 후 start_service → running",
        result2.status == "running", result2.status)
    # 정리
    sup.stop_service("test-svc")


# ─────────────────────────────────────────────────────────────────────────────
# 11-12. probe_healthz 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def test_probe_healthz():
    print("\n[11-12] probe_healthz 헬퍼")
    from supervisor import WhatUdoinSupervisor, ServiceState
    import tempfile

    sup = WhatUdoinSupervisor(run_dir=tempfile.mkdtemp())
    state = ServiceState(name="probe-test")

    # 11. mock 서버 — 200 + {"status":"ok"}
    class OkHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_): pass

    srv = HTTPServer(("127.0.0.1", 0), OkHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()
    result = sup.probe_healthz(state, f"http://127.0.0.1:{port}")
    t.join(timeout=2.0)
    srv.server_close()
    _ok("[11] probe_healthz 200+ok → True", result["ok"] is True, str(result))

    # 12a. 연결 불가 (미사용 포트)
    result2 = sup.probe_healthz(state, "http://127.0.0.1:19996")
    _ok("[12a] probe_healthz 연결 실패 → False", result2["ok"] is False, str(result2))

    # 12b. 404 응답
    class NotFoundHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(404)
            self.end_headers()
        def log_message(self, *_): pass

    srv2 = HTTPServer(("127.0.0.1", 0), NotFoundHandler)
    port2 = srv2.server_address[1]
    t2 = threading.Thread(target=srv2.handle_request, daemon=True)
    t2.start()
    result3 = sup.probe_healthz(state, f"http://127.0.0.1:{port2}")
    t2.join(timeout=2.0)
    srv2.server_close()
    _ok("[12b] probe_healthz 404 → False", result3["ok"] is False, str(result3))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("phase59 - M2-17 토큰 인증 + healthz + crash-loop")
    print("=" * 65)
    test_token_auth()
    test_compare_digest_used()
    test_sse_healthz()
    test_app_healthz()
    test_crash_loop()
    test_probe_healthz()
    print("\n" + "=" * 65)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 65)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
