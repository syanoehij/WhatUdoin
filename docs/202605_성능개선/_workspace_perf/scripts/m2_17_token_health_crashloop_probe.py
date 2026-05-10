"""
M2-17 probe: SSE 토큰 인증 + /healthz + supervisor crash-loop 검증.

결과: _workspace/perf/m2_17_health_token_crashloop/runs/<UTC타임스탬프>/probe.md

Run:
    python _workspace/perf/scripts/m2_17_token_health_crashloop_probe.py
"""

from __future__ import annotations

import asyncio
import datetime
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

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_results: list[tuple[str, bool, str]] = []


def _chk(name: str, cond: bool, detail: str = "") -> None:
    _results.append((name, cond, detail))
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))


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
    return {"status": result[0].get("status") if result else None,
            "body": b"".join(body_chunks)}


def _asgi_get(app_obj, path: str) -> dict:
    result: list = []
    body_chunks: list = []

    async def _call():
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 9999),
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
    return {"status": result[0].get("status") if result else None,
            "body": b"".join(body_chunks)}


# ─────────────────────────────────────────────────────────────────────────────
# A. 토큰 인증 시나리오
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== A. SSE service 토큰 인증 ===")
import importlib

valid_token = "probe-valid-token-abc123"
valid_body = json.dumps({"event": "probe", "data": {"x": 1}}).encode()

# A1. env 설정 + 헤더 없음 → 401
os.environ["WHATUDOIN_INTERNAL_TOKEN"] = valid_token
import sse_service
importlib.reload(sse_service)
r = _asgi_post(sse_service.app, "/internal/publish", valid_body)
_chk("A1 헤더 없음 → 401", r["status"] == 401, str(r["status"]))

# A2. 잘못된 토큰 → 401
r2 = _asgi_post(sse_service.app, "/internal/publish", valid_body,
                extra_headers=[(b"authorization", b"Bearer WRONG")])
_chk("A2 잘못된 토큰 → 401", r2["status"] == 401, str(r2["status"]))

# A3. 올바른 토큰 → 200
r3 = _asgi_post(sse_service.app, "/internal/publish", valid_body,
                extra_headers=[(b"authorization", f"Bearer {valid_token}".encode())])
_chk("A3 올바른 토큰 → 200", r3["status"] == 200, str(r3["status"]))

# A4. env 미설정 + 헤더 없음 → 401
os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)
importlib.reload(sse_service)
r4 = _asgi_post(sse_service.app, "/internal/publish", valid_body)
_chk("A4 env 미설정+헤더 없음 → 401", r4["status"] == 401, str(r4["status"]))

# A5. timing-safe 비교 확인 (소스 grep)
sse_src = (ROOT / "sse_service.py").read_text(encoding="utf-8")
_chk("A5 secrets.compare_digest 사용", "secrets.compare_digest" in sse_src)

# ─────────────────────────────────────────────────────────────────────────────
# B. /healthz 응답
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== B. /healthz 응답 ===")
os.environ.pop("WHATUDOIN_INTERNAL_TOKEN", None)
importlib.reload(sse_service)

r_h = _asgi_get(sse_service.app, "/healthz")
_chk("B1 SSE /healthz → 200", r_h["status"] == 200, str(r_h["status"]))
try:
    d = json.loads(r_h["body"])
    _chk("B2 SSE status=ok", d.get("status") == "ok", str(d))
    _chk("B3 SSE service=sse", d.get("service") == "sse", str(d))
    _chk("B4 SSE subscribers key", "subscribers" in d, str(d))
except Exception as e:
    _chk("B2 SSE healthz JSON", False, str(e))

# Web API healthz
from starlette.testclient import TestClient
import app as app_mod
with TestClient(app_mod.app, raise_server_exceptions=False) as c:
    rw = c.get("/healthz")
    _chk("B5 WebAPI /healthz → 200", rw.status_code == 200, str(rw.status_code))
    try:
        dw = rw.json()
        _chk("B6 WebAPI status=ok", dw.get("status") == "ok", str(dw))
        _chk("B7 WebAPI service=web-api", dw.get("service") == "web-api", str(dw))
    except Exception as e:
        _chk("B5 WebAPI JSON", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
# C. supervisor crash-loop
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== C. supervisor crash-loop ===")
import tempfile
from supervisor import (
    CRASH_LOOP_MAX_FAILURES,
    CRASH_LOOP_WINDOW_SECONDS,
    ServiceState,
    ServiceSpec,
    WhatUdoinSupervisor,
)

sup = WhatUdoinSupervisor(run_dir=tempfile.mkdtemp())
sup.ensure_runtime_dirs()
sup.token_path.write_text("dummy-probe-token", encoding="utf-8")

state = ServiceState(name="probe-svc")
sup.services["probe-svc"] = state

# 5분 안 3회 crash 시뮬레이션
now = time.time()
state.crash_history = [now - 10, now - 20, now - 30]

_chk("C1 3회 누적 → crash-loop 감지", sup._is_crash_loop(state))

spec = ServiceSpec(
    name="probe-svc",
    command=[sys.executable, "-c", "import time; time.sleep(60)"],
)

# 4번째 start_service → spawn 없이 degraded
r_cl = sup.start_service(spec)
_chk("C2 start_service → degraded", r_cl.status == "degraded", r_cl.status)
_chk("C3 crash-loop blocked message", "crash-loop blocked" in r_cl.last_error, r_cl.last_error)
_chk("C4 pid None (spawn 없음)", r_cl.pid is None, str(r_cl.pid))

# reset_crash_loop 후 정상 spawn
sup.reset_crash_loop("probe-svc")
_chk("C5 reset 후 status=stopped",
     sup.services["probe-svc"].status == "stopped")
_chk("C6 reset 후 crash_history 비어있음",
     len(sup.services["probe-svc"].crash_history) == 0)

spec2 = ServiceSpec(
    name="probe-svc",
    command=[sys.executable, "-c", "import time; time.sleep(5)"],
    startup_grace_seconds=0.2,
)
r_ok = sup.start_service(spec2)
_chk("C7 reset 후 start → running", r_ok.status == "running", r_ok.status)
sup.stop_service("probe-svc")

# ─────────────────────────────────────────────────────────────────────────────
# D. probe_healthz 헬퍼
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== D. probe_healthz ===")

sup2 = WhatUdoinSupervisor(run_dir=tempfile.mkdtemp())
probe_state = ServiceState(name="probe-h")


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
probe_result = sup2.probe_healthz(probe_state, f"http://127.0.0.1:{port}")
t.join(timeout=2.0)
srv.server_close()
_chk("D1 probe 200+ok → True", probe_result["ok"] is True, str(probe_result))

# 연결 불가
probe_fail = sup2.probe_healthz(probe_state, "http://127.0.0.1:19994")
_chk("D2 probe 연결 불가 → False", probe_fail["ok"] is False, str(probe_fail))

# 404
class NotFoundHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(404)
        self.end_headers()
    def log_message(self, *_): pass

srv3 = HTTPServer(("127.0.0.1", 0), NotFoundHandler)
port3 = srv3.server_address[1]
t3 = threading.Thread(target=srv3.handle_request, daemon=True)
t3.start()
probe_404 = sup2.probe_healthz(probe_state, f"http://127.0.0.1:{port3}")
t3.join(timeout=2.0)
srv3.server_close()
_chk("D3 probe 404 → False", probe_404["ok"] is False, str(probe_404))

# ─────────────────────────────────────────────────────────────────────────────
# 결과 저장
# ─────────────────────────────────────────────────────────────────────────────
total = len(_results)
passed = sum(1 for _, ok, _ in _results if ok)
failed = total - passed

print(f"\nTOTAL: {total}  PASS: {passed}  FAIL: {failed}")

ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
out_dir = ROOT / "_workspace" / "perf" / "m2_17_health_token_crashloop" / "runs" / ts
out_dir.mkdir(parents=True, exist_ok=True)

lines = [
    f"# M2-17 probe — {ts}",
    "",
    "## 결과 요약",
    "",
    f"| 항목 | 결과 |",
    f"|------|------|",
]
for name, ok, detail in _results:
    tag = "PASS" if ok else "FAIL"
    d = f" ({detail})" if detail and not ok else ""
    lines.append(f"| {name} | {tag}{d} |")

lines += [
    "",
    f"**TOTAL {total} / PASS {passed} / FAIL {failed}**",
    "",
    "## 상수",
    f"- CRASH_LOOP_WINDOW_SECONDS = {CRASH_LOOP_WINDOW_SECONDS}",
    f"- CRASH_LOOP_MAX_FAILURES = {CRASH_LOOP_MAX_FAILURES}",
    "",
    "## /healthz 응답 구조",
    "- SSE service: `{\"status\": \"ok\", \"subscribers\": <int>, \"service\": \"sse\"}`",
    "- Web API: `{\"status\": \"ok\", \"service\": \"web-api\"}`",
]

(out_dir / "probe.md").write_text("\n".join(lines), encoding="utf-8")
print(f"결과 저장: {out_dir / 'probe.md'}")

if failed:
    sys.exit(1)
