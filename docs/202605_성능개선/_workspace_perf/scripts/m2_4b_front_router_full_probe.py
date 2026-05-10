"""
4-B Full 분리 probe: front_router.py 모듈 import 부작용 0, reverse proxy 코드,
supervisor.front_router_service_spec smoke, main.py 토글 grep.

Run:
    python _workspace/perf/scripts/m2_4b_front_router_full_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
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


def check_import_side_effects() -> None:
    print("\n[1] front_router.py 모듈 import 부작용 0")
    try:
        import importlib
        import front_router as fr
        importlib.reload(fr)

        import httpx
        has_client = any(isinstance(getattr(fr, a, None), httpx.AsyncClient) for a in dir(fr))
        _ok("no_httpx_client_at_module_level", not has_client)

        import uvicorn
        has_server = any(isinstance(getattr(fr, a, None), uvicorn.Server) for a in dir(fr))
        _ok("no_uvicorn_server_at_module_level", not has_server)
    except Exception as exc:
        _ok("import_no_exception", False, str(exc))


def check_reverse_proxy_code() -> None:
    print("\n[2] reverse proxy 코드 grep (httpx + Starlette)")
    src = (ROOT / "front_router.py").read_text(encoding="utf-8")
    _ok("httpx_import", "import httpx" in src)
    _ok("starlette_present", "Starlette" in src or "starlette" in src)
    _ok("make_reverse_proxy_app", "_make_reverse_proxy_app" in src)
    _ok("aiter_raw_streaming", "aiter_raw" in src)
    _ok("internal_block", "_handle_internal_block" in src or "route.blocked" in src)
    _ok("strip_then_set_headers", "stripped_names" in src and "x-forwarded-for" in src)


def check_main_entrypoint() -> None:
    print("\n[3] __main__ 진입점 + uvicorn 시작 + cert/key 분기")
    src = (ROOT / "front_router.py").read_text(encoding="utf-8")
    _ok("main_entrypoint_present", 'if __name__ == "__main__":' in src)
    _ok("uvicorn_server_present", "uvicorn.Server" in src)
    _ok("cert_key_branch", "WHATUDOIN_FRONT_ROUTER_CERT" in src and "_have_https" in src)
    _ok("asyncio_gather_servers", "asyncio.gather" in src)
    _ok("sigterm_handler", "SIGTERM" in src)


def check_internal_block() -> None:
    print("\n[4] /internal/* 차단 grep")
    src = (ROOT / "front_router.py").read_text(encoding="utf-8")
    _ok("internal_block_logic", "route.blocked" in src or "_handle_internal_block" in src)


def check_strip_then_set() -> None:
    print("\n[5] strip-then-set forwarded headers grep")
    src = (ROOT / "front_router.py").read_text(encoding="utf-8")
    _ok("forwarded_stripped", "stripped_names" in src)
    _ok("x_forwarded_for_set", "x-forwarded-for" in src)
    _ok("x_real_ip_set", "x-real-ip" in src)
    _ok("x_forwarded_host_set", "x-forwarded-host" in src)
    _ok("x_forwarded_proto_set", "x-forwarded-proto" in src)


def check_supervisor_spec_smoke() -> None:
    print("\n[6] supervisor.front_router_service_spec smoke + protected env")
    import importlib
    import supervisor as sv
    importlib.reload(sv)

    _ok("front_router_service_name_const", getattr(sv, "FRONT_ROUTER_SERVICE_NAME", None) == "front-router")
    _ok("web_api_internal_default_port_8769", getattr(sv, "WEB_API_INTERNAL_DEFAULT_PORT", None) == 8769)

    try:
        spec = sv.front_router_service_spec(
            command=["python", "front_router.py"],
            web_api_url="http://127.0.0.1:8769",
            sse_url="http://127.0.0.1:8765",
        )
        _ok("spec_smoke_name", spec.name == "front-router")
        _ok("spec_smoke_http_port", spec.env.get("WHATUDOIN_FRONT_ROUTER_HTTP_PORT") == "8000")
        _ok("spec_smoke_web_api_url", spec.env.get("WHATUDOIN_WEB_API_INTERNAL_URL") == "http://127.0.0.1:8769")
        _ok("spec_smoke_sse_url", spec.env.get("WHATUDOIN_SSE_SERVICE_URL_FROM_ROUTER") == "http://127.0.0.1:8765")
    except Exception as exc:
        _ok("spec_smoke_call", False, str(exc))

    # protected env override 차단
    try:
        spec2 = sv.front_router_service_spec(
            command=["python", "front_router.py"],
            web_api_url="http://127.0.0.1:8769",
            sse_url="http://127.0.0.1:8765",
            extra_env={
                "WHATUDOIN_FRONT_ROUTER_BIND_HOST": "evil-host",
                "WHATUDOIN_WEB_API_INTERNAL_URL": "http://evil",
            },
        )
        _ok("protected_bind_host_blocked", spec2.env.get("WHATUDOIN_FRONT_ROUTER_BIND_HOST") == "0.0.0.0")
        _ok("protected_web_api_url_blocked", spec2.env.get("WHATUDOIN_WEB_API_INTERNAL_URL") == "http://127.0.0.1:8769")
    except Exception as exc:
        _ok("protected_env_check", False, str(exc))


def check_main_toggle_grep() -> None:
    print("\n[7] main.py 토글 grep + supervisor 4-way 공유")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    _ok("frontend_routing_toggle", "WHATUDOIN_ENABLE_FRONTEND_ROUTING" in src)
    _ok("four_way_supervisor_condition",
        "if _scheduler_sidecar_enabled or _media_sidecar_enabled or _ollama_sidecar_enabled or _frontend_routing_enabled:" in src)
    _ok("front_router_service_spec_imported", "front_router_service_spec" in src)
    _ok("web_api_internal_runtime_spec_imported", "web_api_internal_runtime_spec" in src)


def check_main_listener_skip() -> None:
    print("\n[8] main.py 활성화 시 외부 listener 미가동 분기")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    _ok("external_listener_skipped", "servers: list = []" in src)
    _ok("frontend_routing_enabled_branch", "if _frontend_routing_enabled:" in src)


def check_app_port_env() -> None:
    print("\n[9] app.py direct launcher port env 분기")
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    _ok("port_env_present", "WHATUDOIN_WEB_API_INTERNAL_PORT" in src)
    _ok("port_default_8000", '"8000"' in src)
    _ok("reload_gate_present", "reload=not _internal_only" in src)


def main() -> int:
    print("=" * 64)
    print("m2_4b_front_router_full_probe — 4B full 분리 정적 검증")
    print("=" * 64)

    check_import_side_effects()
    check_reverse_proxy_code()
    check_main_entrypoint()
    check_internal_block()
    check_strip_then_set()
    check_supervisor_spec_smoke()
    check_main_toggle_grep()
    check_main_listener_skip()
    check_app_port_env()

    print("\n" + "=" * 64)
    print(f"결과: {_pass} PASS, {_fail} FAIL")
    print("=" * 64)
    return 0 if _fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
