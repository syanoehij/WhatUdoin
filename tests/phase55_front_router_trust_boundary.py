"""
M2-13 Front Router trusted-proxy and direct-access boundary regression.

Run:
    python tests/phase55_front_router_trust_boundary.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _set_env(name: str, value: str | None):
    old = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    return old


def _restore_env(name: str, old: str | None) -> None:
    if old is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = old


def _request(peer: str, forwarded: str):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/me",
            "headers": [(b"x-forwarded-for", forwarded.encode("ascii"))],
            "client": (peer, 50123),
            "server": ("127.0.0.1", 18080),
            "scheme": "http",
        }
    )


async def _guard_call(peer: str, internal_only: bool, forwarded: str = "127.0.0.1"):
    import app

    calls: list[str] = []

    async def downstream(scope, receive, send):
        calls.append(scope.get("path", ""))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    guard = app._FrontRouterAccessGuardMiddleware(downstream)
    old = _set_env(app.WEB_API_INTERNAL_ONLY_ENV, "1" if internal_only else None)
    try:
        messages: list[dict] = []
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/me",
            "raw_path": b"/api/me",
            "query_string": b"",
            "headers": [(b"x-forwarded-for", forwarded.encode("ascii"))],
            "client": (peer, 50123),
            "server": ("127.0.0.1", 18080),
        }
        await guard(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        return {"status": start["status"], "downstream_calls": len(calls)}
    finally:
        _restore_env(app.WEB_API_INTERNAL_ONLY_ENV, old)


async def _guard_passthrough(scope: dict, internal_only: bool) -> int:
    import app

    calls = 0

    async def downstream(scope, receive, send):
        nonlocal calls
        calls += 1

    guard = app._FrontRouterAccessGuardMiddleware(downstream)
    old = _set_env(app.WEB_API_INTERNAL_ONLY_ENV, "1" if internal_only else None)
    try:
        async def receive():
            return {"type": "lifespan.startup"}

        async def send(message):
            return None

        await guard(scope, receive, send)
        return calls
    finally:
        _restore_env(app.WEB_API_INTERNAL_ONLY_ENV, old)


def main() -> int:
    import app
    import auth
    import supervisor

    checks = {}
    service_spec = supervisor.web_api_service_spec([sys.executable, "main.py"])
    service_env = dict(service_spec.env)
    checks["supervisor_factory_uses_web_api_name"] = (
        service_spec.name == supervisor.WEB_API_SERVICE_NAME
    )
    checks["supervisor_factory_preserves_command"] = (
        list(service_spec.command) == [sys.executable, "main.py"]
    )
    checks["supervisor_factory_keeps_internal_env_together"] = (
        service_env == supervisor.web_api_internal_service_env()
    )
    overridden_spec = supervisor.web_api_service_spec(
        [sys.executable, "main.py"],
        extra_env={
            supervisor.TRUSTED_PROXY_ENV: "203.0.113.10",
            supervisor.WEB_API_BIND_HOST_ENV: "0.0.0.0",
            supervisor.WEB_API_INTERNAL_ONLY_ENV: "0",
            "WHATUDOIN_PUBLIC_BASE_URL": "https://192.0.2.10:8443",
        },
    )
    overridden_env = dict(overridden_spec.env)
    checks["supervisor_factory_protects_internal_env_from_extra_env"] = (
        overridden_env.get(supervisor.TRUSTED_PROXY_ENV) == "127.0.0.1"
        and overridden_env.get(supervisor.WEB_API_BIND_HOST_ENV) == "127.0.0.1"
        and overridden_env.get(supervisor.WEB_API_INTERNAL_ONLY_ENV) == "1"
        and overridden_env.get("WHATUDOIN_PUBLIC_BASE_URL") == "https://192.0.2.10:8443"
    )
    checks["supervisor_sets_trusted_proxy"] = (
        service_env.get(supervisor.TRUSTED_PROXY_ENV) == "127.0.0.1"
    )
    checks["supervisor_sets_loopback_bind"] = (
        service_env.get(supervisor.WEB_API_BIND_HOST_ENV) == "127.0.0.1"
    )
    checks["supervisor_requires_front_router"] = (
        service_env.get(app.WEB_API_INTERNAL_ONLY_ENV) == "1"
    )

    main_source = (ROOT / "main.py").read_text(encoding="utf-8")
    checks["main_supports_bind_host_env"] = "WHATUDOIN_BIND_HOST" in main_source
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    checks["app_direct_launcher_supports_bind_host_env"] = (
        'os.environ.get("WHATUDOIN_BIND_HOST")' in app_source
        and 'uvicorn.run("app:app", host=bind_host' in app_source
    )

    old_trusted = _set_env("TRUSTED_PROXY", "127.0.0.1")
    try:
        checks["trusted_proxy_uses_xff"] = (
            auth.get_client_ip(_request("127.0.0.1", "198.51.100.7")) == "198.51.100.7"
        )
        checks["untrusted_direct_ignores_xff"] = (
            auth.get_client_ip(_request("203.0.113.9", "198.51.100.7")) == "203.0.113.9"
        )
    finally:
        _restore_env("TRUSTED_PROXY", old_trusted)

    external_blocked = asyncio.run(_guard_call("203.0.113.9", True, "127.0.0.1"))
    checks["internal_only_blocks_external_even_with_loopback_xff"] = (
        external_blocked["status"] == 403 and external_blocked["downstream_calls"] == 0
    )

    loopback_allowed = asyncio.run(_guard_call("127.0.0.1", True, "198.51.100.7"))
    checks["internal_only_allows_loopback_router"] = (
        loopback_allowed["status"] == 200 and loopback_allowed["downstream_calls"] == 1
    )

    default_open = asyncio.run(_guard_call("203.0.113.9", False, "198.51.100.7"))
    checks["default_runtime_not_changed"] = (
        default_open["status"] == 200 and default_open["downstream_calls"] == 1
    )
    checks["guard_missing_type_passthrough"] = asyncio.run(_guard_passthrough({}, True)) == 1
    checks["guard_lifespan_passthrough"] = (
        asyncio.run(_guard_passthrough({"type": "lifespan"}, True)) == 1
    )

    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print("FAIL", failed)
        return 1
    print("PASS", checks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
