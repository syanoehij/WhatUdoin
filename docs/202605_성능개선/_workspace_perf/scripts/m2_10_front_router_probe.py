from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from front_router import FrontRouter, FRONT_ROUTER_ROUTE_TABLE, match_front_route  # noqa: E402


def _asgi_app(name: str, calls: list[dict]):
    async def app(scope, receive, send):
        calls.append({"service": name, "path": scope.get("path")})
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"x-downstream-service", name.encode("ascii")),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": f"{name}:{scope.get('path')}".encode("utf-8"),
            }
        )

    return app


async def _call(app, path: str):
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
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": [(b"host", b"whatudoin.local:8443")],
        "client": ("192.0.2.10", 50123),
        "server": ("whatudoin.local", 8443),
    }
    await app(scope, receive, send)

    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    headers = {
        key.decode("latin1").lower(): value.decode("latin1")
        for key, value in start.get("headers", [])
    }
    return {
        "path": path,
        "status": start["status"],
        "headers": headers,
        "body": body.decode("utf-8", errors="replace"),
    }


async def _run_probe():
    calls: list[dict] = []
    router = FrontRouter(
        web_api_app=_asgi_app("web_api", calls),
        sse_app=_asgi_app("sse", calls),
        expose_route_headers=True,
    )

    cases = [
        {
            "name": "api_stream_to_sse",
            "path": "/api/stream",
            "status": 200,
            "router_target": "sse_service",
            "downstream": "sse",
            "called_path": "/api/stream",
        },
        {
            "name": "api_events_to_web_api",
            "path": "/api/events",
            "status": 200,
            "router_target": "web_api_service",
            "downstream": "web_api",
            "called_path": "/api/events",
        },
        {
            "name": "root_to_web_api",
            "path": "/",
            "status": 200,
            "router_target": "web_api_service",
            "downstream": "web_api",
            "called_path": "/",
        },
        {
            "name": "uploads_meetings_to_web_api",
            "path": "/uploads/meetings/2026/05/test.png",
            "status": 200,
            "router_target": "web_api_service",
            "downstream": "web_api",
            "called_path": "/uploads/meetings/2026/05/test.png",
        },
        {
            "name": "internal_blocked",
            "path": "/internal/publish",
            "status": 404,
            "router_target": "blocked",
            "downstream": None,
            "called_path": None,
        },
    ]

    results = []
    for case in cases:
        before = len(calls)
        response = await _call(router, case["path"])
        after_calls = calls[before:]
        matched = match_front_route(case["path"])
        passed = (
            response["status"] == case["status"]
            and response["headers"].get("x-whatudoin-router-target") == case["router_target"]
            and matched.target == case["router_target"]
        )
        if case["downstream"] is None:
            passed = passed and not after_calls
        else:
            passed = (
                passed
                and len(after_calls) == 1
                and after_calls[0]["service"] == case["downstream"]
                and after_calls[0]["path"] == case["called_path"]
                and response["headers"].get("x-downstream-service") == case["downstream"]
            )
        results.append(
            {
                "name": case["name"],
                "path": case["path"],
                "status": response["status"],
                "router_target": response["headers"].get("x-whatudoin-router-target"),
                "router_rule": response["headers"].get("x-whatudoin-router-rule"),
                "downstream_calls": after_calls,
                "passed": passed,
            }
        )

    return {
        "route_table": FRONT_ROUTER_ROUTE_TABLE,
        "results": results,
        "passed": all(item["passed"] for item in results),
    }


def _write_report(payload: dict) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "_workspace" / "perf" / "front_router_m2_10" / "runs" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "m2_10_front_router_probe.md"
    lines = [
        "# M2-10 Front Router Probe",
        "",
        f"- passed: {payload['passed']}",
        f"- route_table: `{payload['route_table']}`",
        "",
        "| case | path | status | router_target | router_rule | downstream | result |",
        "|---|---|---:|---|---|---|---|",
    ]
    for item in payload["results"]:
        downstream = ",".join(call["service"] for call in item["downstream_calls"]) or "-"
        result = "PASS" if item["passed"] else "FAIL"
        lines.append(
            f"| {item['name']} | `{item['path']}` | {item['status']} | "
            f"`{item['router_target']}` | `{item['router_rule']}` | `{downstream}` | {result} |"
        )
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
