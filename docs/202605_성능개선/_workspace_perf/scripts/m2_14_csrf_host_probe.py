"""
M2-14 Front Router CSRF Host 보존 조건 probe.

ASGI stub을 downstream으로 두고 FrontRouter를 통해 dispatch하여
Host 헤더 보존 및 _check_csrf() 동작을 isolated ASGI level에서 검증한다.

Run:
    python _workspace/perf/scripts/m2_14_csrf_host_probe.py
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
# Helpers: ASGI stub downstream
# ---------------------------------------------------------------------------

def _make_scope(
    method: str,
    path: str,
    host: str,
    peer: str = "192.168.0.1",
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> dict:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode("latin1"))]
    if extra_headers:
        headers.extend(extra_headers)
    return {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": (peer, 40000),
        "server": (peer, 8443),
    }


async def _dispatch(path: str, scope: dict) -> dict[str, Any]:
    """FrontRouter로 scope를 dispatch하고 downstream이 받은 headers를 반환."""
    from front_router import FrontRouter

    received_headers: list[tuple[bytes, bytes]] = []

    async def stub_app(s, receive, send):
        received_headers.extend(s.get("headers") or [])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    router = FrontRouter(web_api_app=stub_app, sse_app=stub_app, expose_route_headers=False)

    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg):
        messages.append(msg)

    await router(scope, receive, send)

    def _get(name: bytes) -> str | None:
        for k, v in received_headers:
            if k.lower() == name:
                return v.decode("latin1", errors="replace")
        return None

    start = next((m for m in messages if m.get("type") == "http.response.start"), {})
    return {
        "status": start.get("status"),
        "downstream_host": _get(b"host"),
        "downstream_x_forwarded_host": _get(b"x-forwarded-host"),
    }


# ---------------------------------------------------------------------------
# _check_csrf direct call helpers
# ---------------------------------------------------------------------------

def _csrf_request(method: str, host: str, origin: str | None = None, referer: str | None = None):
    """Starlette Request를 직접 생성하여 _check_csrf 검증에 사용."""
    from starlette.requests import Request

    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode("latin1"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("latin1")))
    if referer is not None:
        headers.append((b"referer", referer.encode("latin1")))
    return Request({
        "type": "http",
        "method": method,
        "path": "/api/test",
        "headers": headers,
        "client": ("192.168.0.1", 40000),
        "server": ("192.168.0.18", 8443),
        "scheme": "https",
    })


def _csrf_passes(host: str, origin: str | None = None, referer: str | None = None) -> bool:
    """_check_csrf가 예외 없이 통과하면 True, HTTPException(403)이면 False."""
    import app as _app
    from fastapi import HTTPException

    req = _csrf_request("POST", host, origin=origin, referer=referer)
    try:
        _app._check_csrf(req)
        return True
    except HTTPException as exc:
        if exc.status_code == 403:
            return False
        raise


# ---------------------------------------------------------------------------
# Probe scenarios
# ---------------------------------------------------------------------------

async def _run_checks() -> dict[str, bool]:
    checks: dict[str, bool] = {}

    # --- FORWARDED_HEADER_NAMES does not contain 'host' ---
    from front_router import FORWARDED_HEADER_NAMES
    checks["host_not_in_stripped_headers"] = b"host" not in FORWARDED_HEADER_NAMES

    # --- Downstream sees original Host (normal POST, web_api path) ---
    external_host = "192.168.0.18:8443"
    scope_normal = _make_scope("POST", "/api/events", external_host)
    result_normal = await _dispatch("/api/events", scope_normal)
    checks["downstream_host_preserved_normal"] = result_normal["downstream_host"] == external_host

    # --- X-Forwarded-Host set by router equals external original ---
    checks["downstream_x_forwarded_host_equals_external"] = (
        result_normal["downstream_x_forwarded_host"] == external_host
    )

    # --- Attacker inbound X-Forwarded-Host is stripped, not forwarded ---
    scope_attack = _make_scope(
        "POST",
        "/api/events",
        external_host,
        extra_headers=[(b"x-forwarded-host", b"attacker.example")],
    )
    result_attack = await _dispatch("/api/events", scope_attack)
    checks["downstream_host_unchanged_on_xff_host_attack"] = (
        result_attack["downstream_host"] == external_host
    )
    checks["downstream_x_forwarded_host_not_attacker"] = (
        result_attack["downstream_x_forwarded_host"] != "attacker.example"
    )
    checks["downstream_x_forwarded_host_is_external_after_attack"] = (
        result_attack["downstream_x_forwarded_host"] == external_host
    )

    # --- SSE path: Host preserved ---
    scope_sse = _make_scope("GET", "/api/stream", external_host)
    result_sse = await _dispatch("/api/stream", scope_sse)
    checks["sse_downstream_host_preserved"] = result_sse["downstream_host"] == external_host
    checks["sse_downstream_x_forwarded_host_equals_external"] = (
        result_sse["downstream_x_forwarded_host"] == external_host
    )

    # --- _check_csrf scenarios ---
    # (a) LAN IP host+origin: PASS
    checks["csrf_a_lan_ip_same_origin_passes"] = _csrf_passes(
        "192.168.0.18:8443", origin="https://192.168.0.18:8443/calendar"
    )
    # (b) Hostname host+origin: PASS
    checks["csrf_b_hostname_same_origin_passes"] = _csrf_passes(
        "whatudoin-host:8443", origin="https://whatudoin-host:8443/calendar"
    )
    # (c) Cross-origin: FAIL (403)
    checks["csrf_c_cross_origin_blocked"] = not _csrf_passes(
        "192.168.0.18:8443", origin="https://attacker.example/"
    )
    # (d) No src (no Origin/Referer): PASS (검증 우회 현재 정책)
    checks["csrf_d_no_src_bypasses_check"] = _csrf_passes("192.168.0.18:8443")

    # --- Multi-domain policy: both LAN IP and hostname pass, cross-origin fails ---
    checks["multi_domain_lan_ip_passes"] = _csrf_passes(
        "192.168.0.18:8443", origin="https://192.168.0.18:8443/"
    )
    checks["multi_domain_hostname_passes"] = _csrf_passes(
        "whatudoin-host:8443", origin="https://whatudoin-host:8443/"
    )
    checks["multi_domain_cross_origin_blocked"] = not _csrf_passes(
        "192.168.0.18:8443", origin="https://whatudoin-host:8443/"
    )

    return checks


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _write_report(checks: dict[str, bool], all_passed: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "_workspace" / "perf" / "csrf_host_m2_14" / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "m2_14_csrf_probe.md"

    lines = [
        "# M2-14 CSRF Host 보존 조건 Probe 결과",
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
        "## 채택 결론: Host 보존 방식",
        "",
        "- `front_router.FORWARDED_HEADER_NAMES`에 `host`가 없으므로 inbound Host 헤더는 strip되지 않는다.",
        "- FrontRouter는 ASGI scope를 그대로 downstream에 전달하므로 외부 원본 Host가 유지된다.",
        "- `app._check_csrf()`는 `Host` 헤더만 비교하므로 운영 코드 변경 없이 CSRF 검증이 동작한다.",
        "- M2-11 strip-then-set 정책으로 외부 위조 X-Forwarded-Host는 라우터가 외부 원본 값으로 덮어씌워 무력화된다.",
        "",
        "## 정책 결과: 외부 도메인 다중 정책",
        "",
        "외부 도메인이 PC LAN IP(`192.168.0.18:8443`)와 사내 hostname(`whatudoin-host:8443`) 두 개일 때:",
        "",
        "- 동일 origin 내부 요청(Origin netloc == Host)은 모두 통과한다.",
        "  - `192.168.0.18:8443` → `https://192.168.0.18:8443/`: **통과**",
        "  - `whatudoin-host:8443` → `https://whatudoin-host:8443/`: **통과**",
        "- Cross-origin 요청(Origin netloc != Host)은 403으로 차단된다.",
        "  - Host=`192.168.0.18:8443`, Origin=`https://whatudoin-host:8443/`: **403**",
        "  - Host=`192.168.0.18:8443`, Origin=`https://attacker.example/`: **403**",
        "",
        "> 주의: Origin/Referer 모두 없는 경우(`src == ''`)는 검증이 우회된다(현재 정책).",
        "> 이는 의도된 동작이며 별도 보안 개선이 필요하면 M3 이후 검토.",
        "",
        "## 운영 코드 변경",
        "",
        "- `app.py`, `front_router.py`, `auth.py`, `supervisor.py` 변경 **없음**.",
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
