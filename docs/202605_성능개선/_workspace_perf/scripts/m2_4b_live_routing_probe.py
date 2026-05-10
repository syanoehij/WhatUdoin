"""
4-B 라이브 검증 probe:
1. supervisor 인스턴스 + 토큰
2. SSE service spawn (8765 internal)
3. Web API service spawn (8769 internal, internal-only)
4. Front Router service spawn (8000/8443 외부, cert/key)
5. 외부 http://127.0.0.1:8000/healthz → Front Router → Web API → 200
6. 외부 https://127.0.0.1:8443/healthz → 동일
7. 외부 /api/stream → Front Router → SSE service → 200 + text/event-stream
8. 외부 /internal/publish → 404
9. supervisor.stop_all() → 3 service status=stopped graceful

Run:
    python _workspace/perf/scripts/m2_4b_live_routing_probe.py
"""

from __future__ import annotations

import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_BASE = ROOT / "_workspace" / "perf" / "m2_4b_full_routing" / "runs"
UTC_TAG = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
RUN_DIR = OUTPUT_BASE / UTC_TAG
RUN_DIR.mkdir(parents=True, exist_ok=True)

CERT_PATH = str(ROOT / "whatudoin-cert.pem")
KEY_PATH = str(ROOT / "whatudoin-key.pem")
HAVE_TLS = os.path.isfile(CERT_PATH) and os.path.isfile(KEY_PATH)

_pass = 0
_fail = 0
_lines: list[str] = []


def _log(line: str) -> None:
    print(line)
    _lines.append(line)


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    tag = "PASS" if cond else "FAIL"
    msg = f"  [{tag}] {name}" + (f" — {detail}" if detail else "")
    _log(msg)
    if cond:
        _pass += 1
    else:
        _fail += 1


def _get(url: str, *, timeout: float = 5.0) -> tuple[int, str, dict]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read(16384).decode("utf-8", errors="replace")
            headers = dict(resp.headers)
            return resp.status, body, headers
    except urllib.error.HTTPError as e:
        body = e.read(4096).decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body, {}
    except Exception as exc:
        return 0, str(exc), {}


def main() -> int:
    _log("=" * 64)
    _log(f"m2_4b_live_routing_probe — {UTC_TAG}")
    _log("=" * 64)

    # ── 1. supervisor 인스턴스 + 토큰 ────────────────────────────
    _log("\n[1] supervisor 인스턴스 + 토큰 발급")
    from supervisor import (
        WhatUdoinSupervisor,
        sse_service_spec, SSE_SERVICE_DEFAULT_PORT,
        web_api_internal_runtime_spec, WEB_API_INTERNAL_DEFAULT_PORT,
        front_router_service_spec,
    )
    sv = WhatUdoinSupervisor(run_dir=str(ROOT))
    tok = sv.ensure_internal_token()
    _ok("token_file_exists", Path(tok.path).exists(), tok.path)

    # ── 2. SSE service spawn ──────────────────────────────────────
    _log("\n[2] SSE service spawn (127.0.0.1:8765)")
    sse_spec = sse_service_spec(command=[sys.executable, str(ROOT / "sse_service.py")])
    sse_state = sv.start_service(sse_spec)
    _ok("sse_running", sse_state.status == "running", f"status={sse_state.status} pid={sse_state.pid}")

    time.sleep(1.5)
    sse_health = sv.probe_healthz(sse_state, f"http://127.0.0.1:{SSE_SERVICE_DEFAULT_PORT}")
    _ok("sse_healthz_ok", sse_health["ok"], str(sse_health))

    # ── 3. Web API service spawn ──────────────────────────────────
    _log("\n[3] Web API service spawn (127.0.0.1:8769 internal-only)")
    web_api_spec = web_api_internal_runtime_spec(
        command=[sys.executable, str(ROOT / "app.py")],
        port=WEB_API_INTERNAL_DEFAULT_PORT,
    )
    web_api_state = sv.start_service(web_api_spec)
    _ok("web_api_running", web_api_state.status == "running",
        f"status={web_api_state.status} pid={web_api_state.pid}")

    _log("  waiting 3s for Web API startup...")
    time.sleep(3.0)
    web_api_health = sv.probe_healthz(web_api_state, f"http://127.0.0.1:{WEB_API_INTERNAL_DEFAULT_PORT}")
    _ok("web_api_healthz_ok", web_api_health["ok"], str(web_api_health))

    # ── 4. Front Router service spawn ────────────────────────────
    _log("\n[4] Front Router service spawn (0.0.0.0:8000/8443)")
    fr_spec = front_router_service_spec(
        command=[sys.executable, str(ROOT / "front_router.py")],
        bind_host="0.0.0.0",
        http_port=8000,
        https_port=8443,
        cert_path=CERT_PATH if HAVE_TLS else None,
        key_path=KEY_PATH if HAVE_TLS else None,
        web_api_url=f"http://127.0.0.1:{WEB_API_INTERNAL_DEFAULT_PORT}",
        sse_url=f"http://127.0.0.1:{SSE_SERVICE_DEFAULT_PORT}",
    )
    fr_state = sv.start_service(fr_spec)
    _ok("front_router_running", fr_state.status == "running",
        f"status={fr_state.status} pid={fr_state.pid}")

    _log("  waiting 1.5s for Front Router startup...")
    time.sleep(1.5)

    # ── 5. 외부 HTTP /healthz ─────────────────────────────────────
    _log("\n[5] 외부 http://127.0.0.1:8000/healthz → Front Router → Web API")
    status, body, hdrs = _get("http://127.0.0.1:8000/healthz")
    _ok("http_healthz_status_200", status == 200, f"status={status}")
    _ok("http_healthz_body_ok", '"ok"' in body or "ok" in body.lower(), f"body={body[:200]!r}")
    _log(f"    body: {body[:200]!r}")

    # ── 6. 외부 HTTPS /healthz ────────────────────────────────────
    _log("\n[6] 외부 https://127.0.0.1:8443/healthz → Front Router → Web API")
    if HAVE_TLS:
        status_s, body_s, hdrs_s = _get("https://127.0.0.1:8443/healthz")
        _ok("https_healthz_status_200", status_s == 200, f"status={status_s}")
        _ok("https_healthz_body_ok", '"ok"' in body_s or "ok" in body_s.lower(), f"body={body_s[:200]!r}")
        _log(f"    body: {body_s[:200]!r}")
    else:
        _log("  [SKIP] HTTPS — cert/key 없음")

    # ── 7. 외부 /api/stream → SSE service ────────────────────────
    _log("\n[7] 외부 /api/stream → Front Router → SSE service")
    import socket
    _stream_ok = False
    _stream_detail = ""
    try:
        conn = socket.create_connection(("127.0.0.1", 8000), timeout=5.0)
        conn.sendall(b"GET /api/stream HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        resp_data = b""
        conn.settimeout(3.0)
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                resp_data += chunk
                if b"\r\n\r\n" in resp_data:
                    break
        except socket.timeout:
            pass
        conn.close()
        resp_str = resp_data.decode("utf-8", errors="replace")
        _stream_detail = resp_str[:300]
        _stream_ok = "200" in resp_str[:20] and ("text/event-stream" in resp_str or "event-stream" in resp_str)
    except Exception as exc:
        _stream_detail = str(exc)
    _ok("stream_response_200_and_event_stream", _stream_ok, _stream_detail[:200])

    # ── 8. 외부 /internal/publish → 404 ──────────────────────────
    _log("\n[8] 외부 /internal/publish → 404")
    status_i, body_i, _ = _get("http://127.0.0.1:8000/internal/publish", timeout=3.0)
    _ok("internal_block_404", status_i == 404, f"status={status_i}")

    # ── 9. supervisor.stop_all() ──────────────────────────────────
    _log("\n[9] supervisor.stop_all() → 3 service graceful shutdown")
    sv.stop_all(timeout=5.0)
    sv.poll_all()
    for name in ["sse", "web-api", "front-router"]:
        state = sv.services.get(name)
        if state:
            _ok(f"{name}_stopped", state.status == "stopped", f"status={state.status}")
        else:
            _ok(f"{name}_not_registered", False, "service not found in supervisor")

    # ── 결과 기록 ────────────────────────────────────────────────
    _log("\n" + "=" * 64)
    _log(f"결과: {_pass} PASS, {_fail} FAIL")
    _log("=" * 64)

    report_path = RUN_DIR / "full_routing_probe.md"
    report_path.write_text("\n".join(_lines), encoding="utf-8")
    print(f"\n[report] {report_path}")

    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
