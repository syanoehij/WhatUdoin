"""
분리 4단계 회귀: Front Router + Web API(internal) + SSE full 분리.

WHATUDOIN_ENABLE_FRONTEND_ROUTING=1 토글 시 main.py가
Front Router + Web API(internal-only) + SSE service 3 프로세스를 spawn하고
main.py 자체는 외부 listener를 직접 띄우지 않는다.

본 회귀 테스트는 다음을 잠근다:
  1. WHATUDOIN_ENABLE_FRONTEND_ROUTING 토글 코드 grep
  2. _frontend_routing_enabled 분기 패턴
  3. supervisor 인스턴스가 1·2·3·4단계 공유
  4. SSE / Web API / Front Router spawn 코드 grep
  5. main.py 활성화 시 외부 listener 미가동 (servers=[])
  6. supervisor.front_router_service_spec 시그니처/smoke
  7. supervisor.web_api_internal_runtime_spec 시그니처/smoke
  8. supervisor STOP_ORDER 6종 + front-router 마지막
  9. app.py direct launcher port env 분기
  10. 1·2·3단계 분기 보존 (phase76/77/78 회귀 잠금)
  11. front_router.py 모듈 import 부작용 0

Run:
    python tests/phase79_sidecar_stage4_frontend.py
"""

from __future__ import annotations

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


# ── A. main.py 토글 + supervisor 통합 코드 grep ──────────────────────────────

def _check_main_grep() -> dict:
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    checks["frontend_routing_toggle_env_present"] = "WHATUDOIN_ENABLE_FRONTEND_ROUTING" in src

    checks["frontend_routing_branch_pattern"] = (
        "_frontend_routing_enabled" in src
        and 'os.environ.get("WHATUDOIN_ENABLE_FRONTEND_ROUTING"' in src
    )

    # supervisor 인스턴스 공유 — 4단계 포함
    checks["supervisor_shared_construction_four_way"] = (
        "if _scheduler_sidecar_enabled or _media_sidecar_enabled or _ollama_sidecar_enabled or _frontend_routing_enabled:" in src
    )

    # SSE / web-api / front-router spawn
    checks["sse_service_spec_imported"] = "sse_service_spec" in src
    checks["web_api_internal_runtime_spec_imported"] = "web_api_internal_runtime_spec" in src
    checks["front_router_service_spec_imported"] = "front_router_service_spec" in src

    # spawn 호출
    checks["sse_spawn_called"] = "_supervisor_instance.start_service(_fr_sse_spec)" in src
    checks["web_api_spawn_called"] = "_supervisor_instance.start_service(_fr_web_api_spec)" in src
    checks["front_router_spawn_called"] = "_supervisor_instance.start_service(_fr_spec)" in src

    # 활성화 시 외부 listener 미가동
    checks["external_listener_skipped_when_active"] = "servers: list = []" in src

    # 1·2·3단계 분기 보존 (회귀 잠금)
    checks["scheduler_branch_preserved"] = "if _scheduler_sidecar_enabled:" in src
    checks["media_branch_preserved"] = "if _media_sidecar_enabled:" in src
    checks["ollama_branch_preserved"] = "if _ollama_sidecar_enabled:" in src

    # fallback default
    checks["fallback_default_none"] = "_supervisor_instance = None" in src

    # 종료 시 stop_all 호출
    checks["stop_all_on_shutdown"] = "_supervisor_instance.stop_all(timeout=5.0)" in src

    return checks


# ── B. supervisor.py 상수 + factory 검증 ──────────────────────────────────────

def _check_supervisor_spec() -> dict:
    import importlib
    import supervisor as sv
    importlib.reload(sv)

    checks: dict[str, bool] = {}

    # 상수
    checks["front_router_service_name"] = getattr(sv, "FRONT_ROUTER_SERVICE_NAME", None) == "front-router"
    checks["front_router_bind_host_env"] = hasattr(sv, "FRONT_ROUTER_BIND_HOST_ENV")
    checks["front_router_http_port_env"] = hasattr(sv, "FRONT_ROUTER_HTTP_PORT_ENV")
    checks["front_router_https_port_env"] = hasattr(sv, "FRONT_ROUTER_HTTPS_PORT_ENV")
    checks["front_router_cert_env"] = hasattr(sv, "FRONT_ROUTER_CERT_ENV")
    checks["front_router_key_env"] = hasattr(sv, "FRONT_ROUTER_KEY_ENV")
    checks["web_api_internal_port_env"] = hasattr(sv, "WEB_API_INTERNAL_PORT_ENV")
    checks["web_api_internal_default_port"] = getattr(sv, "WEB_API_INTERNAL_DEFAULT_PORT", None) == 8769

    # STOP_ORDER 6종 + front-router 마지막
    stop_order = getattr(sv, "STOP_ORDER", ())
    checks["stop_order_six_entries"] = len(stop_order) == 6
    checks["stop_order_front_router_last"] = (
        len(stop_order) > 0 and stop_order[-1] == "front-router"
    )
    checks["stop_order_contains_all_six"] = set(stop_order) == {
        "ollama", "media", "sse", "scheduler", "web-api", "front-router"
    }

    # front_router_service_spec smoke
    checks["front_router_spec_factory_callable"] = callable(getattr(sv, "front_router_service_spec", None))
    try:
        spec = sv.front_router_service_spec(
            command=["python", "front_router.py"],
            web_api_url="http://127.0.0.1:8769",
            sse_url="http://127.0.0.1:8765",
        )
        checks["front_router_spec_smoke_name"] = spec.name == "front-router"
        checks["front_router_spec_smoke_bind_host"] = spec.env.get("WHATUDOIN_FRONT_ROUTER_BIND_HOST") == "0.0.0.0"
        checks["front_router_spec_smoke_http_port"] = spec.env.get("WHATUDOIN_FRONT_ROUTER_HTTP_PORT") == "8000"
        checks["front_router_spec_smoke_https_port"] = spec.env.get("WHATUDOIN_FRONT_ROUTER_HTTPS_PORT") == "8443"
        checks["front_router_spec_smoke_web_api_url"] = spec.env.get("WHATUDOIN_WEB_API_INTERNAL_URL") == "http://127.0.0.1:8769"
        checks["front_router_spec_smoke_sse_url"] = spec.env.get("WHATUDOIN_SSE_SERVICE_URL_FROM_ROUTER") == "http://127.0.0.1:8765"
    except Exception as exc:
        for k in ["front_router_spec_smoke_name", "front_router_spec_smoke_bind_host",
                  "front_router_spec_smoke_http_port", "front_router_spec_smoke_https_port",
                  "front_router_spec_smoke_web_api_url", "front_router_spec_smoke_sse_url"]:
            checks[k] = False
        print(f"    (note: front_router_service_spec smoke 예외: {exc})")

    # protected env override 차단
    try:
        spec2 = sv.front_router_service_spec(
            command=["python", "front_router.py"],
            web_api_url="http://127.0.0.1:8769",
            sse_url="http://127.0.0.1:8765",
            extra_env={"WHATUDOIN_FRONT_ROUTER_BIND_HOST": "evil", "WHATUDOIN_WEB_API_INTERNAL_URL": "evil"},
        )
        checks["front_router_spec_protected_env_blocked"] = (
            spec2.env.get("WHATUDOIN_FRONT_ROUTER_BIND_HOST") == "0.0.0.0"
            and spec2.env.get("WHATUDOIN_WEB_API_INTERNAL_URL") == "http://127.0.0.1:8769"
        )
    except Exception as exc:
        checks["front_router_spec_protected_env_blocked"] = False
        print(f"    (note: protected env test 예외: {exc})")

    # web_api_internal_runtime_spec smoke
    checks["web_api_internal_runtime_spec_callable"] = callable(getattr(sv, "web_api_internal_runtime_spec", None))
    try:
        spec3 = sv.web_api_internal_runtime_spec(command=["python", "app.py"])
        checks["web_api_internal_runtime_spec_smoke"] = (
            spec3.name == "web-api"
            and spec3.env.get("WHATUDOIN_WEB_API_INTERNAL_PORT") == str(sv.WEB_API_INTERNAL_DEFAULT_PORT)
            and spec3.env.get("WHATUDOIN_WEB_API_INTERNAL_ONLY") == "1"
            and spec3.env.get("WHATUDOIN_BIND_HOST") == "127.0.0.1"
        )
    except Exception as exc:
        checks["web_api_internal_runtime_spec_smoke"] = False
        print(f"    (note: web_api_internal_runtime_spec smoke 예외: {exc})")

    return checks


# ── C. front_router.py 역방향 프록시 구조 grep ────────────────────────────────

def _check_front_router_grep() -> dict:
    src = (ROOT / "front_router.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}

    # httpx + Starlette
    checks["httpx_import_present"] = "import httpx" in src
    checks["starlette_present"] = "Starlette" in src or "starlette" in src

    # /internal/* 차단
    checks["internal_block_present"] = (
        "route.blocked" in src or "_handle_internal_block" in src
    )

    # /api/stream SSE streaming
    checks["sse_streaming_present"] = (
        "aiter_raw" in src or "stream_generator" in src or "_stream_generator" in src
    )

    # M2-11 strip-then-set forwarded headers
    checks["strip_then_set_forwarded"] = (
        "x-forwarded-for" in src and "x-real-ip" in src and "stripped_names" in src
    )

    # __main__ 진입점
    checks["main_entrypoint"] = 'if __name__ == "__main__":' in src

    # uvicorn.Server 사용
    checks["uvicorn_server_in_main"] = "uvicorn.Server" in src

    # cert/key 분기
    checks["cert_key_branch"] = (
        "WHATUDOIN_FRONT_ROUTER_CERT" in src
        and "WHATUDOIN_FRONT_ROUTER_KEY" in src
        and "_have_https" in src
    )

    # env 변수 존재
    checks["bind_host_env"] = "WHATUDOIN_FRONT_ROUTER_BIND_HOST" in src
    checks["http_port_env"] = "WHATUDOIN_FRONT_ROUTER_HTTP_PORT" in src
    checks["https_port_env"] = "WHATUDOIN_FRONT_ROUTER_HTTPS_PORT" in src
    checks["web_api_internal_url_env"] = "WHATUDOIN_WEB_API_INTERNAL_URL" in src
    checks["sse_url_from_router_env"] = "WHATUDOIN_SSE_SERVICE_URL_FROM_ROUTER" in src

    return checks


# ── D. front_router.py 모듈 import 부작용 0 ──────────────────────────────────

def _check_front_router_import_side_effects() -> dict:
    checks: dict[str, bool] = {}
    try:
        import importlib
        import front_router as fr
        importlib.reload(fr)
        # httpx.AsyncClient 인스턴스 없음 (모듈 레벨)
        import httpx
        has_client_at_module = any(
            isinstance(getattr(fr, attr, None), httpx.AsyncClient)
            for attr in dir(fr)
        )
        checks["no_httpx_client_at_module_level"] = not has_client_at_module
        # uvicorn.Server 인스턴스 없음 (모듈 레벨)
        import uvicorn
        has_server_at_module = any(
            isinstance(getattr(fr, attr, None), uvicorn.Server)
            for attr in dir(fr)
        )
        checks["no_uvicorn_server_at_module_level"] = not has_server_at_module
        checks["import_side_effects_clean"] = True
    except Exception as exc:
        checks["import_side_effects_clean"] = False
        print(f"    (note: import 예외: {exc})")
    return checks


# ── E. app.py direct launcher port env 분기 ──────────────────────────────────

def _check_app_direct_launcher() -> dict:
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    checks: dict[str, bool] = {}
    checks["port_env_branch_present"] = "WHATUDOIN_WEB_API_INTERNAL_PORT" in src
    checks["port_default_8000"] = '"8000"' in src
    checks["reload_internal_only_gate"] = (
        "WHATUDOIN_WEB_API_INTERNAL_ONLY" in src
        and "reload=not _internal_only" in src
    )
    return checks


def main() -> int:
    print("=" * 64)
    print("phase79 - 분리 4단계: Front Router full 분리 잠금")
    print("=" * 64)

    print("\n[A] main.py 토글 + supervisor 통합 코드 grep")
    for name, passed in _check_main_grep().items():
        _ok(name, passed)

    print("\n[B] supervisor.py 상수 + factory 시그니처 + STOP_ORDER 6종")
    for name, passed in _check_supervisor_spec().items():
        _ok(name, passed)

    print("\n[C] front_router.py reverse proxy 구조 grep")
    for name, passed in _check_front_router_grep().items():
        _ok(name, passed)

    print("\n[D] front_router.py 모듈 import 부작용 0")
    for name, passed in _check_front_router_import_side_effects().items():
        _ok(name, passed)

    print("\n[E] app.py direct launcher port env 분기")
    for name, passed in _check_app_direct_launcher().items():
        _ok(name, passed)

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
