"""
M2-14 Front Router CSRF Host 보존 조건 회귀 테스트.

Run:
    python tests/phase56_front_router_csrf_host.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Helpers
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


async def _dispatch(scope: dict) -> dict[str, Any]:
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


def _csrf_request(method: str, host: str, origin: str | None = None, referer: str | None = None):
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
# main
# ---------------------------------------------------------------------------

def main() -> int:
    from front_router import FORWARDED_HEADER_NAMES

    checks: dict[str, bool] = {}

    # --- FORWARDED_HEADER_NAMES에 host 없음 ---
    checks["host_not_in_stripped_headers"] = b"host" not in FORWARDED_HEADER_NAMES

    external_host = "192.168.0.18:8443"

    # --- 일반 POST: downstream Host 보존 ---
    scope_normal = _make_scope("POST", "/api/events", external_host)
    result_normal = asyncio.run(_dispatch(scope_normal))
    checks["downstream_host_preserved_normal"] = result_normal["downstream_host"] == external_host

    # --- X-Forwarded-Host가 외부 원본과 동일 ---
    checks["downstream_x_forwarded_host_equals_external"] = (
        result_normal["downstream_x_forwarded_host"] == external_host
    )

    # --- 위조 X-Forwarded-Host 공격: downstream Host 불변, X-Forwarded-Host 라우터가 덮음 ---
    scope_attack = _make_scope(
        "POST",
        "/api/events",
        external_host,
        extra_headers=[(b"x-forwarded-host", b"attacker.example")],
    )
    result_attack = asyncio.run(_dispatch(scope_attack))
    checks["downstream_host_unchanged_on_xff_host_attack"] = (
        result_attack["downstream_host"] == external_host
    )
    checks["downstream_x_forwarded_host_not_attacker"] = (
        result_attack["downstream_x_forwarded_host"] != "attacker.example"
    )
    checks["downstream_x_forwarded_host_is_external_after_attack"] = (
        result_attack["downstream_x_forwarded_host"] == external_host
    )

    # --- SSE path: Host 보존 ---
    scope_sse = _make_scope("GET", "/api/stream", external_host)
    result_sse = asyncio.run(_dispatch(scope_sse))
    checks["sse_downstream_host_preserved"] = result_sse["downstream_host"] == external_host
    checks["sse_downstream_x_forwarded_host_equals_external"] = (
        result_sse["downstream_x_forwarded_host"] == external_host
    )

    # --- _check_csrf 시나리오 4종 ---
    # (a) LAN IP 동일 origin: PASS
    checks["csrf_a_lan_ip_same_origin_passes"] = _csrf_passes(
        "192.168.0.18:8443", origin="https://192.168.0.18:8443/calendar"
    )
    # (b) hostname 동일 origin: PASS
    checks["csrf_b_hostname_same_origin_passes"] = _csrf_passes(
        "whatudoin-host:8443", origin="https://whatudoin-host:8443/calendar"
    )
    # (c) Cross-origin: 403
    checks["csrf_c_cross_origin_blocked"] = not _csrf_passes(
        "192.168.0.18:8443", origin="https://attacker.example/"
    )
    # (d) Origin/Referer 없음: PASS (현재 정책: src 비어있으면 검증 우회)
    checks["csrf_d_no_src_bypasses_check"] = _csrf_passes("192.168.0.18:8443")

    # --- 다중 도메인 정책 ---
    checks["multi_domain_lan_ip_passes"] = _csrf_passes(
        "192.168.0.18:8443", origin="https://192.168.0.18:8443/"
    )
    checks["multi_domain_hostname_passes"] = _csrf_passes(
        "whatudoin-host:8443", origin="https://whatudoin-host:8443/"
    )
    checks["multi_domain_cross_origin_blocked"] = not _csrf_passes(
        "192.168.0.18:8443", origin="https://whatudoin-host:8443/"
    )

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print("FAIL", failed)
        return 1
    print("PASS", checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
