"""
M2-15 HTTPS probe middleware Front Router 호환 조건 probe.

FrontRouter ASGI dispatcher를 거친 scope.scheme이 inbound scheme과 동일하게
유지됨을 검증하고, _BrowserHTTPSRedirectMiddleware가 라우터 유무와 무관하게
올바르게 동작함을 단언한다.

Run:
    python _workspace/perf/scripts/m2_15_https_probe_compat.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# HTML probe signature markers (실제 렌더링된 HTML 기반)
# ---------------------------------------------------------------------------
PROBE_MARKER_TITLE = "알람용 인증서 확인 중"
PROBE_MARKER_COOKIE = "wd-cert-skip=1"
PROBE_MARKER_FETCH = "fetch(httpsBase + '/api/health'"

# ---------------------------------------------------------------------------
# ASGI helper: body collector
# ---------------------------------------------------------------------------

async def _collect_response(scope: dict, app) -> dict[str, Any]:
    """ASGI app을 호출하고 status + body를 반환."""
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

    await app(scope, receive, send)

    start = next((m for m in messages if m.get("type") == "http.response.start"), {})
    body_parts = [
        m.get("body", b"")
        for m in messages
        if m.get("type") == "http.response.body"
    ]
    body = b"".join(body_parts).decode("utf-8", errors="replace")
    return {"status": start.get("status"), "body": body}


def _make_scope(
    method: str,
    path: str,
    scheme: str,
    host: str,
    peer: str = "203.0.113.5",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
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


BROWSER_HEADERS: list[tuple[bytes, bytes]] = [
    (b"user-agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
    (b"accept", b"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
    (b"sec-fetch-mode", b"navigate"),
    (b"sec-fetch-dest", b"document"),
]

CERT_SKIP_HEADERS: list[tuple[bytes, bytes]] = BROWSER_HEADERS + [
    (b"cookie", b"wd-cert-skip=1"),
]


# ---------------------------------------------------------------------------
# Probe scenarios
# ---------------------------------------------------------------------------

async def _run_checks() -> dict[str, bool]:
    import app as _app
    from front_router import FrontRouter, _scope_with_router_forwarded_headers

    checks: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # 1. _scope_with_router_forwarded_headers가 scheme을 변경하지 않는다
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
        checks[f"scheme_preserved_in_forwarded_scope_{test_scheme}"] = (
            scope_out["scheme"] == test_scheme
        )

    # ------------------------------------------------------------------
    # Monkey-patch _https_available to return True (cert 파일 유무와 독립)
    # ------------------------------------------------------------------
    orig_https_available = _app._https_available
    _app._https_available = lambda: True

    try:
        # ------------------------------------------------------------------
        # 2. 캡처 sub-app 준비: downstream scheme 기록
        # ------------------------------------------------------------------
        captured_schemes: list[str] = []

        async def capture_app(scope, receive, send):
            captured_schemes.append(scope.get("scheme", ""))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"downstream_ok"})

        # middleware 래핑된 capture app (라우터 downstream으로 사용)
        from app import _BrowserHTTPSRedirectMiddleware
        mw_capture = _BrowserHTTPSRedirectMiddleware(capture_app)

        router = FrontRouter(
            web_api_app=mw_capture,
            sse_app=mw_capture,
            expose_route_headers=False,
        )

        # ------------------------------------------------------------------
        # 3. scheme=https → middleware 우회 → downstream 통과 (probe HTML 없음)
        # ------------------------------------------------------------------
        captured_schemes.clear()
        scope_https = _make_scope("GET", "/", "https", "192.168.0.18:8443", extra_headers=BROWSER_HEADERS)
        result = await _collect_response(scope_https, router)
        checks["https_inbound_reaches_downstream"] = result["status"] == 200
        checks["https_inbound_no_probe_html_title"] = PROBE_MARKER_TITLE not in result["body"]
        checks["https_inbound_no_probe_html_cookie"] = PROBE_MARKER_COOKIE not in result["body"]
        # downstream이 받은 scope.scheme이 https로 보존되어야 한다
        checks["https_inbound_downstream_scheme_preserved"] = (
            len(captured_schemes) >= 1 and captured_schemes[-1] == "https"
        )

        # ------------------------------------------------------------------
        # 4. scheme=http + 브라우저 UA → middleware 동작 → probe HTML 반환
        # ------------------------------------------------------------------
        captured_schemes.clear()
        scope_http_browser = _make_scope("GET", "/", "http", "192.168.0.18:8000", extra_headers=BROWSER_HEADERS)
        result_http = await _collect_response(scope_http_browser, router)
        checks["http_browser_inbound_probe_html_title"] = PROBE_MARKER_TITLE in result_http["body"]
        checks["http_browser_inbound_probe_html_cookie"] = PROBE_MARKER_COOKIE in result_http["body"]
        checks["http_browser_inbound_probe_html_fetch"] = PROBE_MARKER_FETCH in result_http["body"]
        checks["http_browser_inbound_status_200"] = result_http["status"] == 200
        # middleware가 probe HTML을 직접 반환하므로 downstream에 도달하지 않는다
        checks["http_browser_inbound_no_downstream_reach"] = len(captured_schemes) == 0

        # ------------------------------------------------------------------
        # 5. scheme=http + wd-cert-skip=1 쿠키 → middleware 우회
        # ------------------------------------------------------------------
        captured_schemes.clear()
        scope_cert_skip = _make_scope("GET", "/", "http", "192.168.0.18:8000", extra_headers=CERT_SKIP_HEADERS)
        result_skip = await _collect_response(scope_cert_skip, router)
        checks["http_cert_skip_cookie_bypasses_probe"] = PROBE_MARKER_TITLE not in result_skip["body"]
        checks["http_cert_skip_reaches_downstream"] = result_skip["status"] == 200

        # ------------------------------------------------------------------
        # 6. scheme=http + /api/health → middleware 우회 (skip prefix)
        # ------------------------------------------------------------------
        captured_schemes.clear()
        scope_api = _make_scope("GET", "/api/health", "http", "192.168.0.18:8000", extra_headers=BROWSER_HEADERS)
        result_api = await _collect_response(scope_api, router)
        checks["http_api_skip_prefix_bypasses_probe"] = PROBE_MARKER_TITLE not in result_api["body"]
        checks["http_api_skip_reaches_downstream"] = result_api["status"] == 200

        # ------------------------------------------------------------------
        # 7. scheme=http + HEAD / → middleware 우회
        # ------------------------------------------------------------------
        captured_schemes.clear()
        scope_head = _make_scope("HEAD", "/", "http", "192.168.0.18:8000", extra_headers=BROWSER_HEADERS)
        result_head = await _collect_response(scope_head, router)
        checks["http_head_bypasses_probe"] = PROBE_MARKER_TITLE not in result_head["body"]

        # ------------------------------------------------------------------
        # 8. scheme=http + Sec-Fetch-Mode: cors (non-navigate) → middleware 우회
        # ------------------------------------------------------------------
        captured_schemes.clear()
        cors_headers: list[tuple[bytes, bytes]] = [
            (b"user-agent", b"Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
            (b"accept", b"*/*"),
            (b"sec-fetch-mode", b"cors"),
            (b"sec-fetch-dest", b"empty"),
        ]
        scope_cors = _make_scope("GET", "/", "http", "192.168.0.18:8000", extra_headers=cors_headers)
        result_cors = await _collect_response(scope_cors, router)
        checks["http_cors_non_navigate_bypasses_probe"] = PROBE_MARKER_TITLE not in result_cors["body"]
        checks["http_cors_non_navigate_reaches_downstream"] = result_cors["status"] == 200

        # ------------------------------------------------------------------
        # 9. 외부 직접 8000 접속 (라우터 미경유) — 동일 middleware 동작
        #    middleware만 직접 호출. 라우터 유무와 무관하게 probe HTML 반환
        # ------------------------------------------------------------------
        direct_mw = _BrowserHTTPSRedirectMiddleware(capture_app)
        captured_schemes.clear()
        scope_direct_http = _make_scope("GET", "/", "http", "192.168.0.18:8000", extra_headers=BROWSER_HEADERS)
        result_direct = await _collect_response(scope_direct_http, direct_mw)
        checks["direct_http_no_router_probe_html_title"] = PROBE_MARKER_TITLE in result_direct["body"]
        checks["direct_http_no_router_probe_html_cookie"] = PROBE_MARKER_COOKIE in result_direct["body"]
        checks["direct_http_no_router_probe_html_fetch"] = PROBE_MARKER_FETCH in result_direct["body"]
        checks["direct_http_no_router_no_downstream_reach"] = len(captured_schemes) == 0

    finally:
        _app._https_available = orig_https_available

    # ------------------------------------------------------------------
    # 10. 운영 코드 변경 0건 확인
    # ------------------------------------------------------------------
    import subprocess
    result_diff = subprocess.run(
        ["git", "diff", "--name-only", "--",
         "app.py", "front_router.py", "auth.py", "supervisor.py", "main.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    checks["no_operational_code_changes"] = result_diff.stdout.strip() == ""

    return checks


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(checks: dict[str, bool], all_passed: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "_workspace" / "perf" / "https_probe_m2_15" / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "m2_15_https_probe_compat.md"

    lines = [
        "# M2-15 HTTPS Probe Middleware Front Router 호환 Probe 결과",
        "",
        f"- 실행 시각(UTC): {stamp}",
        f"- 전체 결과: {'PASS' if all_passed else 'FAIL'}",
        "",
        "## 검증 항목",
        "",
        "| 검증 항목 | 결과 |",
        "|---|---|",
    ]
    for name, passed in checks.items():
        lines.append(f"| `{name}` | {'PASS' if passed else 'FAIL'} |")

    failed = [n for n, p in checks.items() if not p]
    lines += [
        "",
        "## 실패 항목",
        "",
        f"{'없음' if not failed else ', '.join(failed)}",
        "",
        "## 결론",
        "",
        "- `_scope_with_router_forwarded_headers()`는 `scheme` 키를 변경하지 않는다.",
        "- FrontRouter ASGI dispatcher를 통해 dispatch된 downstream scope.scheme이 inbound scope.scheme과 동일하게 보존된다.",
        "- 외부 https(8443) 접속 시 scope.scheme=https → `_BrowserHTTPSRedirectMiddleware`의 `scope.get('scheme') != 'http'` 분기로 자연스럽게 통과.",
        "- 외부 직접 http(8000) 접속 시 scope.scheme=http → middleware 동작 → probe HTML 반환.",
        "- middleware 동작은 라우터 유무와 무관하게 동일하다.",
        "- 운영 코드 변경 0건.",
        "",
        "## 운영 코드 변경",
        "",
        "- `app.py`, `front_router.py`, `auth.py`, `supervisor.py`, `main.py` 변경 **없음**.",
        "- 신규 파일만 추가됨: probe 스크립트, regression 테스트.",
        "",
    ]

    report.write_text("\n".join(lines), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    checks = asyncio.run(_run_checks())
    all_passed = all(checks.values())
    report = _write_report(checks, all_passed)
    print(f"report={report}")
    if all_passed:
        print(f"PASS {checks}")
    else:
        failed = [n for n, p in checks.items() if not p]
        print(f"FAIL {failed}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
