from __future__ import annotations

import importlib
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@contextmanager
def _trusted_proxy(value: str):
    old = os.environ.get("TRUSTED_PROXY")
    if value:
        os.environ["TRUSTED_PROXY"] = value
    else:
        os.environ.pop("TRUSTED_PROXY", None)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TRUSTED_PROXY", None)
        else:
            os.environ["TRUSTED_PROXY"] = old


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


def _rate_limit_item_count(limiter, key: str) -> int:
    storage = getattr(limiter, "_storage", None)
    if not storage:
        return 0
    items = getattr(storage, "storage", {})
    return sum(value for item_key, value in items.items() if key in item_key)


def _limit_hit_status(limiter, request, limit_text: str) -> list[int]:
    statuses = []
    limit_group = limiter.limit(limit_text)

    @limit_group
    async def endpoint(request):
        return {"ok": True}

    for _ in range(11):
        try:
            limiter._check_request_limit(request, endpoint, False)
            statuses.append(200)
        except Exception as exc:
            if exc.__class__.__name__ == "RateLimitExceeded":
                statuses.append(429)
            else:
                statuses.append(500)
                statuses.append(str(exc))
                break
    return statuses


def _run_probe():
    with _trusted_proxy("127.0.0.1"):
        import app

        app = importlib.reload(app)
        limiter = app.limiter

        key_func_name = getattr(limiter._key_func, "__name__", "")
        key_func_module = getattr(limiter._key_func, "__module__", "")

        trusted_keys = [
            limiter._key_func(
                _request(
                    "/api/login",
                    "127.0.0.1",
                    [(b"x-forwarded-for", f"192.0.2.{i}".encode("ascii"))],
                )
            )
            for i in range(1, 51)
        ]
        untrusted_key = limiter._key_func(
            _request(
                "/api/login",
                "203.0.113.9",
                [(b"x-forwarded-for", b"192.0.2.200")],
            )
        )
        trusted_same_proxy_key = limiter._key_func(
            _request(
                "/api/login",
                "127.0.0.1",
                [(b"x-forwarded-for", b"198.51.100.7, 198.51.100.8")],
            )
        )
        no_forwarded_key = limiter._key_func(_request("/api/login", "127.0.0.1"))

        limit_request = _request(
            "/api/login",
            "127.0.0.1",
            [(b"x-forwarded-for", b"192.0.2.77")],
        )
        statuses = _limit_hit_status(limiter, limit_request, "10/minute")

        checks = {
            "limiter_uses_auth_get_client_ip": (
                key_func_name == "get_client_ip" and key_func_module == "auth"
            ),
            "trusted_proxy_50_distinct_buckets": len(set(trusted_keys)) == 50,
            "trusted_proxy_first_forwarded_ip": trusted_same_proxy_key == "198.51.100.7",
            "untrusted_proxy_ignores_forwarded": untrusted_key == "203.0.113.9",
            "trusted_proxy_without_forwarded_falls_back_peer": no_forwarded_key == "127.0.0.1",
            "same_ip_11th_request_429": statuses[:10] == [200] * 10 and statuses[10] == 429,
        }
        return {
            "key_func": f"{key_func_module}.{key_func_name}",
            "trusted_keys_sample": trusted_keys[:5],
            "trusted_key_count": len(set(trusted_keys)),
            "untrusted_key": untrusted_key,
            "trusted_same_proxy_key": trusted_same_proxy_key,
            "no_forwarded_key": no_forwarded_key,
            "statuses": statuses,
            "rate_limit_counter_for_192_0_2_77": _rate_limit_item_count(limiter, "192.0.2.77"),
            "checks": checks,
            "passed": all(checks.values()),
        }


def _write_report(payload: dict) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "_workspace" / "perf" / "limiter_m2_12" / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "m2_12_limiter_probe.md"
    lines = [
        "# M2-12 SlowAPI Limiter Probe",
        "",
        f"- passed: {payload['passed']}",
        f"- key_func: `{payload['key_func']}`",
        "",
        "| check | result |",
        "|---|---|",
    ]
    for key, value in payload["checks"].items():
        lines.append(f"| {key} | {'PASS' if value else 'FAIL'} |")
    lines.extend(["", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    payload = _run_probe()
    report = _write_report(payload)
    print(f"report={report}")
    print(f"passed={payload['passed']}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
