"""
M2-4 정책 재결정 회귀: HTTP fallback unsafe write 가드 미적용 잠금.

배경: M2-4 초기 권장안(HTTP unsafe write 차단)은 사용자 의도와 충돌했다.
사용자 의도는 사내 인트라넷 망 전제(§2)에서 HTTP/HTTPS 기능 동등이고,
HTTPS는 브라우저 알람(Notification API secure context) 전용이다. HTTP로
접속한 사용자도 로그인/일정 등록/문서 저장 등 모든 unsafe write를 사용
가능해야 한다. plan §13 (대안) "HTTP write 유지" 정책으로 재채택.

회선 신뢰 보호는 다음으로 유지:
  - §2 사내 LAN 운영 전제(회선 자체 도청 위험 낮음)
  - Front Router strip-then-set forwarded headers(M2-11) — 외부 위조 헤더
    차단
  - TRUSTED_PROXY + 외부 직접 접근 차단 한 세트(M2-13) — supervisor 사용 시

본 회귀 테스트는 다음을 잠근다:
  1. _HTTPFallbackWriteGuardMiddleware 클래스가 app.py에 부재
  2. _HTTP_FALLBACK_* 상수 부재
  3. app.add_middleware(_HTTPFallbackWriteGuardMiddleware) 호출 부재
  4. 정책 결정 사유 코멘트 grep
  5. ASGI level: HTTP scheme + POST 모든 경로(api/events, mcp, avr, login)
     downstream 도달 (가드 부재로 분기 없이 통과)

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


def _check_source_grep() -> dict:
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    checks["middleware_class_absent"] = "class _HTTPFallbackWriteGuardMiddleware" not in src
    checks["allow_exact_const_absent"] = "_HTTP_FALLBACK_WRITE_ALLOW_EXACT" not in src
    checks["allow_prefixes_const_absent"] = "_HTTP_FALLBACK_WRITE_ALLOW_PREFIXES" not in src
    checks["unsafe_methods_const_absent"] = "_HTTP_FALLBACK_UNSAFE_METHODS" not in src
    checks["predicate_absent"] = "_is_http_fallback_write_allowed" not in src
    checks["add_middleware_call_absent"] = "_HTTPFallbackWriteGuardMiddleware" not in src

    # 정책 결정 사유 코멘트 grep
    checks["policy_comment_present"] = (
        "HTTP/HTTPS 기능 동등" in src
        or "HTTP write 유지" in src
    )

    return checks


async def _passthrough_check(method: str, path: str, scheme: str) -> bool:
    """app.py 미들웨어 스택 통과 시 가드 메시지가 응답 body에 0건인지 확인.

    실제 라우팅 결과(401/403/404/405/422/200 등)는 무관 — 정상 인증/CSRF
    흐름의 4xx와 가드 차단을 구분하기 위해 가드 전용 detail 문자열
    "HTTP fallback에서는 쓰기 요청이 차단됩니다"의 부재를 단언한다.
    """
    import importlib
    import app as _app
    importlib.reload(_app)

    body_chunks: list[bytes] = []

    async def send(msg: dict) -> None:
        if msg.get("type") == "http.response.body":
            body_chunks.append(msg.get("body") or b"")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": scheme,
        "headers": [
            (b"host", b"127.0.0.1:8000"),
            (b"content-type", b"application/json"),
        ],
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
        "root_path": "",
    }

    try:
        await _app.app(scope, receive, send)
    except Exception:
        pass

    body = b"".join(body_chunks).decode("utf-8", errors="replace")
    return "HTTP fallback에서는 쓰기 요청이 차단됩니다" not in body


async def _run_passthrough_checks() -> dict:
    """HTTP scheme + 다양한 unsafe POST가 가드 부재로 차단 없이 통과(라우팅까지 도달)."""
    checks: dict[str, bool] = {}

    targets = [
        ("POST", "/api/events", "http", "http_post_api_events_no_guard_block"),
        ("POST", "/mcp/", "http", "http_post_mcp_no_guard_block"),
        ("POST", "/avr", "http", "http_post_avr_no_guard_block"),
        ("POST", "/api/login", "http", "http_post_api_login_no_guard_block"),
        ("PUT", "/api/doc/1", "http", "http_put_api_doc_no_guard_block"),
        ("DELETE", "/api/events/1", "http", "http_delete_api_events_no_guard_block"),
        ("POST", "/api/events", "https", "https_post_api_events_passthrough"),
    ]

    for method, path, scheme, name in targets:
        try:
            checks[name] = await _passthrough_check(method, path, scheme)
        except Exception as exc:
            checks[name] = False
            print(f"    (note: {name} 실행 중 예외: {exc})")

    return checks


def main() -> int:
    print("=" * 64)
    print("phase72 - M2-4 정책 재결정: HTTP unsafe write 가드 미적용 잠금")
    print("=" * 64)

    print("\n[A] 운영 코드 grep — 가드 부재 + 정책 사유 코멘트")
    for name, passed in _check_source_grep().items():
        _ok(name, passed)

    print("\n[B] ASGI level — HTTP unsafe write가 403 가드 차단 없이 통과")
    for name, passed in asyncio.run(_run_passthrough_checks()).items():
        _ok(name, passed)

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
