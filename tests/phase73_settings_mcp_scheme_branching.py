"""
M5 후속 fix 회귀: /settings/mcp 안내 URL이 사용자 접속 scheme에 따라 분기.

배경: M2-3 canonical URL 정책 도입 시 /settings/mcp 페이지가 무조건
_public_base_url(request, "https")를 사용해 HTTPS 8443 URL만 안내했다.
이는 인증서 미신뢰 PC에서 HTTP 8000으로 접속한 사용자에게도 HTTPS URL을
보여줘 MCP 클라이언트 설정이 동작하지 않는 회귀를 만들었다(phase72에서
HTTP fallback MCP write 차단 회복은 했지만 안내는 그대로 HTTPS).

본 fix는 request.url.scheme에 따라 분기:
  - HTTP 접속  → _public_base_url(request, "http")  → http://<host>:8000/mcp/
  - HTTPS 접속 → _public_base_url(request, "https") → https://<host>:8443/mcp/

M2-3 canonical URL 정책의 핵심(host 통일)은 깨지 않는다. host는 여전히
WHATUDOIN_PUBLIC_BASE_URL env 또는 request hostname fallback에서
결정되고, scheme/port만 사용자 접속에 맞춰 분기한다.

본 회귀 테스트는 다음을 잠근다:
  1. HTTP 접속 시 base 응답이 http:// 로 시작하고 :8000 포함
  2. HTTPS 접속 시 base 응답이 https:// 로 시작하고 :8443 포함
  3. 응답 내 cline_config / codex_config / claude_desktop_config /
     claude_code_cmd 4종이 모두 같은 scheme의 mcp_base 사용
  4. M2-3 host 통일 보존 — env WHATUDOIN_PUBLIC_BASE_URL 설정 시 host가
     그 값으로 고정되고 scheme만 분기

Run:
    python tests/phase73_settings_mcp_scheme_branching.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

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


def _make_fake_request(scheme: str, host: str = "192.168.0.18"):
    """request.url.scheme + request.url.hostname을 가진 가짜 객체."""
    class _Url:
        def __init__(self, _scheme: str, _host: str):
            self.scheme = _scheme
            self.hostname = _host

    class _Headers(dict):
        def get(self, k, default=None):
            return super().get(k, default)

    class _Req:
        def __init__(self, _scheme: str, _host: str):
            self.url = _Url(_scheme, _host)
            self.headers = _Headers()
            self.cookies = {}
            self.client = None

    return _Req(scheme, host)


def _check_branching(env_public_base: str | None) -> dict:
    """env 설정/미설정 두 케이스 단언."""
    import importlib
    import app
    importlib.reload(app)

    checks: dict[str, bool] = {}

    # env 설정 시뮬레이션
    if env_public_base is not None:
        os.environ["WHATUDOIN_PUBLIC_BASE_URL"] = env_public_base
    else:
        os.environ.pop("WHATUDOIN_PUBLIC_BASE_URL", None)

    # _public_base_url를 직접 호출해 scheme 분기 단언
    req_http = _make_fake_request("http", "192.168.0.18")
    req_https = _make_fake_request("https", "192.168.0.18")

    base_http = app._public_base_url(req_http, "http")
    base_https = app._public_base_url(req_https, "https")

    suffix = f"_env={env_public_base or 'none'}"
    checks[f"http_base_starts_with_http{suffix}"] = base_http.startswith("http://")
    checks[f"http_base_port_8000{suffix}"] = ":8000" in base_http
    checks[f"https_base_starts_with_https{suffix}"] = base_https.startswith("https://")
    checks[f"https_base_port_8443{suffix}"] = ":8443" in base_https

    # host 통일 보존: env 설정 시 host가 env 값과 일치
    if env_public_base:
        from urllib.parse import urlparse
        env_host = urlparse(env_public_base).hostname or ""
        if env_host:
            checks[f"http_host_matches_env{suffix}"] = env_host in base_http
            checks[f"https_host_matches_env{suffix}"] = env_host in base_https

    # /settings/mcp 핸들러 자체의 분기 코드 grep 단언
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    checks[f"handler_has_scheme_branch{suffix}"] = (
        'scheme = "http" if request.url.scheme == "http" else "https"' in src
    )
    checks[f"handler_uses_branched_scheme{suffix}"] = (
        'base = _public_base_url(request, scheme)' in src
    )

    return checks


def _check_assets_consistency() -> dict:
    """settings_mcp 응답 4종(cline/codex/claude_desktop/claude_code)이 모두 같은 mcp_base 사용."""
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    # 분기된 base/mcp_base 변수 4곳에서 사용
    handler_start = src.find("def settings_mcp_page(request: Request):")
    handler_end = src.find("\n@app.", handler_start + 1)
    handler_src = src[handler_start:handler_end] if handler_end > 0 else src[handler_start:]

    checks["handler_uses_mcp_base_4_times"] = handler_src.count("{mcp_base}/mcp/") >= 4
    checks["handler_no_hardcoded_https_8443"] = ":8443/mcp/" not in handler_src
    checks["handler_no_hardcoded_http_8000"] = ":8000/mcp/" not in handler_src

    return checks


def main() -> int:
    print("=" * 64)
    print("phase73 - /settings/mcp scheme 분기 잠금")
    print("=" * 64)

    print("\n[A] env 미설정 fallback")
    for name, passed in _check_branching(None).items():
        _ok(name, passed)

    print("\n[B] env 설정 (canonical host 통일 보존)")
    for name, passed in _check_branching("https://whatudoin.local:8443").items():
        _ok(name, passed)

    # cleanup
    os.environ.pop("WHATUDOIN_PUBLIC_BASE_URL", None)

    print("\n[C] settings_mcp_page 핸들러 4종 안내가 같은 mcp_base 사용")
    for name, passed in _check_assets_consistency().items():
        _ok(name, passed)

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
