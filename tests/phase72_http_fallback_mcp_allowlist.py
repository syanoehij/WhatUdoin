"""
M5 후속 fix 회귀: HTTP 8000 fallback에서 MCP unsafe write 통과 잠금.

배경: M2-4 _HTTPFallbackWriteGuardMiddleware가 HTTP scheme + unsafe method
(POST/PUT/PATCH/DELETE) 요청을 화이트리스트 외에는 403으로 차단한다. 초기
화이트리스트는 AVR 흐름(`/avr`, `/remote`, `/api/avr`, `/api/avr/*`)만
허용했으나, MCP Streamable HTTP transport(POST 기반)가 차단되어 HTTP
사용자가 MCP를 못 쓰는 회귀가 발생했다. 본 step에서 `/mcp` 및 `/mcp/*`를
화이트리스트에 추가했다(plan §13 (대안) "HTTP write 유지 + 명시 화이트
리스트" 정책 일부 채택, 사용자 의도 우선).

본 회귀 테스트는 다음을 잠근다:
  1. HTTP scheme + POST /mcp/        → 통과 (downstream 도달)
  2. HTTP scheme + POST /mcp/initialize → 통과 (downstream 도달)
  3. HTTP scheme + POST /mcp         → 통과 (downstream 도달, exact match)
  4. HTTP scheme + POST /api/events  → 차단 (403)
  5. HTTP scheme + POST /avr         → 통과 (회귀 보존)
  6. HTTPS scheme + POST /api/events → 통과 (HTTPS는 분기 외)
  7. HTTP scheme + GET /mcp/         → 통과 (unsafe method 아님)

Run:
    python tests/phase72_http_fallback_mcp_allowlist.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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
        print(f"  [FAIL] {name}" + (f" - {detail}" if detail else ""))


def _make_scope(method: str, path: str, scheme: str) -> dict:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": scheme,
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }


async def _run_through_middleware(mw, scope: dict) -> tuple[int | None, list[bytes], bool]:
    """middleware 통과 시 status / body / downstream_reached 반환."""
    captured_status: list[int | None] = [None]
    captured_body: list[bytes] = []
    downstream: list[bool] = [False]

    async def send(msg: dict) -> None:
        if msg.get("type") == "http.response.start":
            captured_status[0] = msg.get("status")
        elif msg.get("type") == "http.response.body":
            captured_body.append(msg.get("body") or b"")

    received_first = False

    async def receive():
        nonlocal received_first
        if not received_first:
            received_first = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    # downstream 가짜 app — 200 OK 반환 + 도달 표시
    async def downstream_app(scope_, receive_, send_):
        downstream[0] = True
        await send_({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        })
        await send_({"type": "http.response.body", "body": b"ok"})

    # mw가 self.app 갖는 ASGI middleware 패턴
    instance = mw.__class__(downstream_app)
    await instance(scope, receive, send)
    return captured_status[0], captured_body, downstream[0]


def _allowed(path: str) -> bool:
    import app as _app
    return _app._is_http_fallback_write_allowed(path)


async def _run_checks() -> dict:
    import app as _app
    mw = _app._HTTPFallbackWriteGuardMiddleware(None)

    checks: dict[str, bool] = {}

    # 1. HTTP + POST /mcp/ → 통과
    s, _, reached = await _run_through_middleware(mw, _make_scope("POST", "/mcp/", "http"))
    checks["http_post_mcp_root_allowed"] = (s == 200 and reached)

    # 2. HTTP + POST /mcp/initialize → 통과
    s, _, reached = await _run_through_middleware(mw, _make_scope("POST", "/mcp/initialize", "http"))
    checks["http_post_mcp_initialize_allowed"] = (s == 200 and reached)

    # 3. HTTP + POST /mcp (exact, 슬래시 없음) → 통과
    s, _, reached = await _run_through_middleware(mw, _make_scope("POST", "/mcp", "http"))
    checks["http_post_mcp_exact_allowed"] = (s == 200 and reached)

    # 4. HTTP + POST /api/events → 차단 (403)
    s, _, reached = await _run_through_middleware(mw, _make_scope("POST", "/api/events", "http"))
    checks["http_post_api_events_blocked"] = (s == 403 and not reached)

    # 5. HTTP + POST /avr → 통과 (회귀 보존)
    s, _, reached = await _run_through_middleware(mw, _make_scope("POST", "/avr", "http"))
    checks["http_post_avr_allowed_regression"] = (s == 200 and reached)

    # 6. HTTPS + POST /api/events → 통과 (HTTPS는 분기 외)
    s, _, reached = await _run_through_middleware(mw, _make_scope("POST", "/api/events", "https"))
    checks["https_post_api_events_passthrough"] = (s == 200 and reached)

    # 7. HTTP + GET /mcp/ → 통과 (unsafe method 아님)
    s, _, reached = await _run_through_middleware(mw, _make_scope("GET", "/mcp/", "http"))
    checks["http_get_mcp_passthrough"] = (s == 200 and reached)

    # 8. allow predicate 직접 호출 단언
    checks["pred_mcp_root_allowed"] = _allowed("/mcp/") is True
    checks["pred_mcp_initialize_allowed"] = _allowed("/mcp/initialize") is True
    checks["pred_mcp_exact_allowed"] = _allowed("/mcp") is True
    checks["pred_avr_allowed_regression"] = _allowed("/avr") is True
    checks["pred_api_events_blocked"] = _allowed("/api/events") is False

    return checks


def main() -> int:
    print("=" * 64)
    print("phase72 - M5 후속 fix: HTTP fallback MCP 화이트리스트")
    print("=" * 64)

    checks = asyncio.run(_run_checks())
    for name, passed in checks.items():
        _ok(name, passed)

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
