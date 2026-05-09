"""
M2-12 SlowAPI limiter trusted-proxy regression.

Run:
    python tests/phase54_limiter_trusted_proxy.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _request(path: str, peer: str, headers: list[tuple[bytes, bytes]] | None = None):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers or [],
            "client": (peer, 50123),
            "server": ("whatudoin.local", 8443),
            "scheme": "https",
        }
    )


def _limit_hit_statuses(limiter, request) -> list[int]:
    limit_group = limiter.limit("10/minute")

    @limit_group
    async def endpoint(request):
        return {"ok": True}

    statuses: list[int] = []
    for _ in range(11):
        try:
            limiter._check_request_limit(request, endpoint, False)
            statuses.append(200)
        except Exception as exc:
            if exc.__class__.__name__ == "RateLimitExceeded":
                statuses.append(429)
            else:
                raise
    return statuses


def main() -> int:
    old_trusted = os.environ.get("TRUSTED_PROXY")
    os.environ["TRUSTED_PROXY"] = "127.0.0.1"
    try:
        import app

        limiter = app.limiter
        checks = {
            "limiter_uses_auth_get_client_ip": (
                getattr(limiter._key_func, "__module__", "") == "auth"
                and getattr(limiter._key_func, "__name__", "") == "get_client_ip"
            ),
            "trusted_proxy_50_distinct_buckets": len(
                {
                    limiter._key_func(
                        _request(
                            "/api/login",
                            "127.0.0.1",
                            [(b"x-forwarded-for", f"192.0.2.{i}".encode("ascii"))],
                        )
                    )
                    for i in range(1, 51)
                }
            )
            == 50,
            "trusted_proxy_first_forwarded_ip": limiter._key_func(
                _request(
                    "/api/login",
                    "127.0.0.1",
                    [(b"x-forwarded-for", b"198.51.100.7, 198.51.100.8")],
                )
            )
            == "198.51.100.7",
            "untrusted_proxy_ignores_forwarded": limiter._key_func(
                _request(
                    "/api/login",
                    "203.0.113.9",
                    [(b"x-forwarded-for", b"192.0.2.200")],
                )
            )
            == "203.0.113.9",
        }
        statuses = _limit_hit_statuses(
            limiter,
            _request(
                "/api/login",
                "127.0.0.1",
                [(b"x-forwarded-for", b"192.0.2.77")],
            ),
        )
        checks["same_ip_11th_request_429"] = statuses[:10] == [200] * 10 and statuses[10] == 429

        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            print("FAIL", failed)
            return 1
        print("PASS", checks)
        return 0
    finally:
        if old_trusted is None:
            os.environ.pop("TRUSTED_PROXY", None)
        else:
            os.environ["TRUSTED_PROXY"] = old_trusted


if __name__ == "__main__":
    raise SystemExit(main())
