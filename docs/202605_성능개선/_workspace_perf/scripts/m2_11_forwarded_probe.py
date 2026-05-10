from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from front_router import FORWARDED_HEADER_NAMES, FrontRouter  # noqa: E402


SPOOF_VALUES = {
    "10.10.10.10",
    "198.51.100.9",
    "attacker.example",
    "8000",
    "for=10.0.0.5",
}


def _asgi_app(name: str, calls: list[dict]):
    async def app(scope, receive, send):
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers", [])
        }
        calls.append(
            {
                "service": name,
                "path": scope.get("path"),
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    return app


async def _call(app, path: str, scheme: str = "https"):
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

    headers = [
        (b"host", b"whatudoin.local:8443"),
        (b"cookie", b"session_id=test-session"),
        (b"authorization", b"Bearer keep-me"),
        (b"x-forwarded-for", b"10.10.10.10, 198.51.100.9"),
        (b"x-forwarded-host", b"attacker.example"),
        (b"x-forwarded-proto", b"http"),
        (b"x-forwarded-port", b"8000"),
        (b"x-real-ip", b"198.51.100.9"),
        (b"forwarded", b"for=10.0.0.5;host=attacker.example;proto=http"),
    ]
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": headers,
        "client": ("192.0.2.10", 50123),
        "server": ("whatudoin.local", 8443),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    return {"status": start["status"]}


def _header_result(call: dict, expected_service: str):
    headers = call["headers"]
    raw_values = "\n".join(headers.values())
    forwarded_keys_present = [
        name.decode("ascii")
        for name in FORWARDED_HEADER_NAMES
        if name.decode("ascii") in headers
    ]
    spoof_values_absent = all(value not in raw_values for value in SPOOF_VALUES)
    expected = {
        "x-forwarded-for": "192.0.2.10",
        "x-forwarded-host": "whatudoin.local:8443",
        "x-forwarded-proto": "https",
        "x-forwarded-port": "8443",
        "x-real-ip": "192.0.2.10",
        "cookie": "session_id=test-session",
        "authorization": "Bearer keep-me",
        "host": "whatudoin.local:8443",
    }
    expected_match = all(headers.get(key) == value for key, value in expected.items())
    no_forwarded_header = "forwarded" not in headers
    return {
        "service": call["service"],
        "path": call["path"],
        "forwarded_keys_present": forwarded_keys_present,
        "expected_headers": {key: headers.get(key) for key in expected},
        "spoof_values_absent": spoof_values_absent,
        "no_forwarded_header": no_forwarded_header,
        "passed": (
            call["service"] == expected_service
            and expected_match
            and spoof_values_absent
            and no_forwarded_header
        ),
    }


async def _run_probe():
    calls: list[dict] = []
    router = FrontRouter(
        web_api_app=_asgi_app("web_api", calls),
        sse_app=_asgi_app("sse", calls),
    )

    cases = [
        {
            "name": "web_api_forwarded_strip_then_set",
            "path": "/api/events",
            "expected_service": "web_api",
        },
        {
            "name": "sse_forwarded_strip_then_set",
            "path": "/api/stream",
            "expected_service": "sse",
        },
        {
            "name": "internal_blocked_no_downstream_headers",
            "path": "/internal/publish",
            "expected_service": None,
        },
    ]

    results = []
    for case in cases:
        before = len(calls)
        response = await _call(router, case["path"])
        after_calls = calls[before:]
        if case["expected_service"] is None:
            result = {
                "service": None,
                "path": case["path"],
                "status": response["status"],
                "downstream_calls": len(after_calls),
                "passed": response["status"] == 404 and len(after_calls) == 0,
            }
        else:
            result = _header_result(after_calls[0], case["expected_service"])
            result["status"] = response["status"]
            result["passed"] = result["passed"] and response["status"] == 200
        result["name"] = case["name"]
        results.append(result)

    return {"results": results, "passed": all(item["passed"] for item in results)}


def _write_report(payload: dict) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "_workspace" / "perf" / "front_router_m2_11" / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "m2_11_forwarded_probe.md"
    lines = [
        "# M2-11 Forwarded Header Probe",
        "",
        f"- passed: {payload['passed']}",
        "",
        "| case | service | status | result |",
        "|---|---|---:|---|",
    ]
    for item in payload["results"]:
        result = "PASS" if item["passed"] else "FAIL"
        service = item.get("service") or "-"
        lines.append(f"| {item['name']} | `{service}` | {item['status']} | {result} |")
    lines.extend(["", "```json", json.dumps(payload, ensure_ascii=False, indent=2), "```", ""])
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> int:
    payload = asyncio.run(_run_probe())
    report = _write_report(payload)
    print(f"report={report}")
    print(f"passed={payload['passed']}")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
