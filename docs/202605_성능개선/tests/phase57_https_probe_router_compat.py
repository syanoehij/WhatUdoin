"""
M2-15 HTTPS probe middleware Front Router 호환 조건 회귀 테스트.

_BrowserHTTPSRedirectMiddleware를 dummy sub-app으로 격리하여 unit-level로 검증.
진짜 Web API app을 import하지 않고 dummy sub-app만 사용한다.

Run:
    python tests/phase57_https_probe_router_compat.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# HTML probe signature markers (실제 렌더링된 HTML 기반)
# ---------------------------------------------------------------------------
PROBE_MARKER_TITLE = "알람용 인증서 확인 중"
PROBE_MARKER_COOKIE = "wd-cert-skip=1"
PROBE_MARKER_FETCH = "fetch(httpsBase + '/api/health'"


# ---------------------------------------------------------------------------
# ASGI helpers
# ---------------------------------------------------------------------------

async def _collect_response(scope: dict, asgi_app) -> dict:
    """ASGI app을 호출하고 status + body 반환."""
    messages: list[dict] = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        messages.append(msg)

    await asgi_app(scope, receive, send)

    start = next((m for m in messages if m.get("type") == "http.response.start"), {})
    body = b"".join(
        m.get("body", b"") for m in messages if m.get("type") == "http.response.body"
    ).decode("utf-8", errors="replace")
    return {"status": start.get("status"), "body": body}


def _make_scope(
    method: str,
    path: str,
    scheme: str,
    host: str,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
    peer: str = "203.0.113.5",
) -> dict:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode("latin1"))]
    if extra_headers:
        headers.extend(extra_headers)
    port = 8443 if scheme == "https" else 8000
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": (peer, 50000),
        "server": ("192.168.0.18", port),
    }


BROWSER_NAV_HEADERS: list[tuple[bytes, bytes]] = [
    (b"user-agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
    (b"accept", b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    (b"sec-fetch-mode", b"navigate"),
    (b"sec-fetch-dest", b"document"),
]

CERT_SKIP_HEADERS: list[tuple[bytes, bytes]] = BROWSER_NAV_HEADERS + [
    (b"cookie", b"wd-cert-skip=1"),
]


# ---------------------------------------------------------------------------
# Test cases (async)
# ---------------------------------------------------------------------------

async def _run_checks() -> dict[str, bool]:
    import app as _app
    from front_router import FrontRouter, _scope_with_router_forwarded_headers
    from app import _BrowserHTTPSRedirectMiddleware

    checks: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # A. _scope_with_router_forwarded_headers가 scheme을 변경하지 않는다
    # ------------------------------------------------------------------
    for test_scheme in ("https", "http"):
        scope_in = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "scheme": test_scheme,
            "headers": [(b"host", b"192.168.0.18:8443")],
            "client": ("192.168.0.18", 50000),
            "server": ("192.168.0.18", 8443),
        }
        scope_out = _scope_with_router_forwarded_headers(scope_in)
        checks[f"scheme_not_modified_by_forwarded_headers_{test_scheme}"] = (
            scope_out["scheme"] == test_scheme
        )

    # ------------------------------------------------------------------
    # Monkey-patch _https_available: cert 파일 유무와 독립적으로 테스트
    # ------------------------------------------------------------------
    orig_https_available = _app._https_available
    _app._https_available = lambda: True

    try:
        # dummy downstream: scheme + path 기록
        captured: list[dict] = []

        async def dummy_downstream(scope, receive, send):
            captured.append({"scheme": scope.get("scheme", ""), "path": scope.get("path", "")})
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"dummy_ok"})

        # middleware wrapped dummy
        mw = _BrowserHTTPSRedirectMiddleware(dummy_downstream)

        # router → middleware → dummy
        router = FrontRouter(web_api_app=mw, sse_app=mw, expose_route_headers=False)

        # ------------------------------------------------------------------
        # B. scheme=https → middleware 우회 → downstream 통과, probe HTML 없음
        # ------------------------------------------------------------------
        captured.clear()
        scope_https = _make_scope("GET", "/", "https", "192.168.0.18:8443", BROWSER_NAV_HEADERS)
        r = await _collect_response(scope_https, router)
        checks["B_https_inbound_reaches_downstream"] = r["status"] == 200
        checks["B_https_inbound_no_probe_title"] = PROBE_MARKER_TITLE not in r["body"]
        checks["B_https_inbound_no_probe_cookie"] = PROBE_MARKER_COOKIE not in r["body"]
        # downstream이 받은 scheme이 https로 보존
        checks["B_https_downstream_scheme_is_https"] = (
            len(captured) >= 1 and captured[-1]["scheme"] == "https"
        )

        # ------------------------------------------------------------------
        # C. scheme=http + 브라우저 UA → probe HTML 반환, downstream 미도달
        # ------------------------------------------------------------------
        captured.clear()
        scope_http = _make_scope("GET", "/", "http", "192.168.0.18:8000", BROWSER_NAV_HEADERS)
        r = await _collect_response(scope_http, router)
        checks["C_http_browser_probe_html_title"] = PROBE_MARKER_TITLE in r["body"]
        checks["C_http_browser_probe_html_cookie"] = PROBE_MARKER_COOKIE in r["body"]
        checks["C_http_browser_probe_html_fetch"] = PROBE_MARKER_FETCH in r["body"]
        checks["C_http_browser_probe_status_200"] = r["status"] == 200
        checks["C_http_browser_no_downstream_reach"] = len(captured) == 0

        # ------------------------------------------------------------------
        # D. scheme=http + wd-cert-skip=1 쿠키 → middleware 우회
        # ------------------------------------------------------------------
        captured.clear()
        scope_skip = _make_scope("GET", "/", "http", "192.168.0.18:8000", CERT_SKIP_HEADERS)
        r = await _collect_response(scope_skip, router)
        checks["D_cert_skip_cookie_no_probe_title"] = PROBE_MARKER_TITLE not in r["body"]
        checks["D_cert_skip_reaches_downstream"] = r["status"] == 200

        # ------------------------------------------------------------------
        # E. scheme=http + /api/health (skip prefix) → middleware 우회
        # ------------------------------------------------------------------
        captured.clear()
        scope_api = _make_scope("GET", "/api/health", "http", "192.168.0.18:8000", BROWSER_NAV_HEADERS)
        r = await _collect_response(scope_api, router)
        checks["E_api_skip_prefix_no_probe"] = PROBE_MARKER_TITLE not in r["body"]
        checks["E_api_skip_reaches_downstream"] = r["status"] == 200

        # ------------------------------------------------------------------
        # F. scheme=http + HEAD / → middleware 우회
        # ------------------------------------------------------------------
        captured.clear()
        scope_head = _make_scope("HEAD", "/", "http", "192.168.0.18:8000", BROWSER_NAV_HEADERS)
        r = await _collect_response(scope_head, router)
        checks["F_head_request_no_probe"] = PROBE_MARKER_TITLE not in r["body"]

        # ------------------------------------------------------------------
        # G. scheme=http + Sec-Fetch-Mode: cors (non-navigate) → 우회
        # ------------------------------------------------------------------
        captured.clear()
        cors_hdrs: list[tuple[bytes, bytes]] = [
            (b"user-agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
            (b"accept", b"*/*"),
            (b"sec-fetch-mode", b"cors"),
            (b"sec-fetch-dest", b"empty"),
        ]
        scope_cors = _make_scope("GET", "/", "http", "192.168.0.18:8000", cors_hdrs)
        r = await _collect_response(scope_cors, router)
        checks["G_cors_non_navigate_no_probe"] = PROBE_MARKER_TITLE not in r["body"]
        checks["G_cors_non_navigate_reaches_downstream"] = r["status"] == 200

        # ------------------------------------------------------------------
        # H. 외부 직접 8000 접속 (라우터 미경유) — 동일 middleware 동작
        # ------------------------------------------------------------------
        direct_mw = _BrowserHTTPSRedirectMiddleware(dummy_downstream)
        captured.clear()
        scope_direct = _make_scope("GET", "/", "http", "192.168.0.18:8000", BROWSER_NAV_HEADERS)
        r = await _collect_response(scope_direct, direct_mw)
        checks["H_direct_http_no_router_probe_title"] = PROBE_MARKER_TITLE in r["body"]
        checks["H_direct_http_no_router_probe_cookie"] = PROBE_MARKER_COOKIE in r["body"]
        checks["H_direct_http_no_router_probe_fetch"] = PROBE_MARKER_FETCH in r["body"]
        checks["H_direct_http_no_downstream_reach"] = len(captured) == 0

    finally:
        _app._https_available = orig_https_available

    return checks


def main() -> int:
    checks = asyncio.run(_run_checks())
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print("FAIL", failed)
        return 1
    print("PASS", checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
