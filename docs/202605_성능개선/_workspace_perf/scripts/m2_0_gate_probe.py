"""
M2-0 gate probe: decide whether SSE service split is justified.

This script intentionally measures only the gate condition. It does not change
production code.

Scenarios:
  1. GET /api/events baseline without SSE clients.
  2. GET /api/events while 50 SSE clients are connected.
  3. GET /api/events immediately after a main app restart while 50 SSE clients
     reconnect.

Output:
  _workspace/perf/baseline_2026-05-09/m2_0_<HHMMSS>/m2_0_gate_report.{json,md}
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
PYTHON = sys.executable
BASELINE_DIR = REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"
SEED_SCRIPT = REPO_ROOT / "_workspace" / "perf" / "fixtures" / "seed_users.py"
CLEANUP_SCRIPT = REPO_ROOT / "_workspace" / "perf" / "fixtures" / "cleanup.py"
SNAPSHOT_SCRIPT = REPO_ROOT / "_workspace" / "perf" / "scripts" / "snapshot_db.py"
COOKIES_JSON = REPO_ROOT / "_workspace" / "perf" / "fixtures" / "session_cookies.json"
DB_PATH = REPO_ROOT / "whatudoin.db"
HOST = "https://localhost:8443"


def log(message: str) -> None:
    print(f"[m2-0] {message}", flush=True)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * pct)))
    return round(ordered[idx], 1)


def port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("localhost", port)) == 0
    finally:
        sock.close()


def run_py(script: Path, run_dir: Path, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        [PYTHON, str(script)],
        cwd=str(REPO_ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    (run_dir / f"{script.stem}_stdout.log").write_text(result.stdout or "", encoding="utf-8")
    (run_dir / f"{script.stem}_stderr.log").write_text(result.stderr or "", encoding="utf-8")
    return result


def db_count(query: str, params: tuple[Any, ...] = ()) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        return int(conn.execute(query, params).fetchone()[0])
    finally:
        conn.close()


def load_cookies() -> list[tuple[str, str]]:
    if not COOKIES_JSON.exists():
        return []
    data = json.loads(COOKIES_JSON.read_text(encoding="utf-8"))
    return [(name, item["session_id"]) for name, item in sorted(data.items())]


def start_server(run_dir: Path, suffix: str) -> subprocess.Popen:
    stdout = open(run_dir / f"server_{suffix}_stdout.log", "w", encoding="utf-8", errors="replace")
    stderr = open(run_dir / f"server_{suffix}_stderr.log", "w", encoding="utf-8", errors="replace")
    cmd = [
        PYTHON,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8443",
        "--ssl-certfile",
        "whatudoin-cert.pem",
        "--ssl-keyfile",
        "whatudoin-key.pem",
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=stdout,
        stderr=stderr,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    log(f"server {suffix} pid={proc.pid}")
    return proc


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


async def wait_ready(proc: subprocess.Popen, timeout_s: float = 30.0) -> float:
    start = time.perf_counter()
    deadline = start + timeout_s
    async with httpx.AsyncClient(verify=False, timeout=2.0) as client:
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.returncode}")
            try:
                resp = await client.get(f"{HOST}/api/notifications/count")
                if resp.status_code == 200:
                    return round((time.perf_counter() - start) * 1000, 1)
            except Exception:
                await asyncio.sleep(0.5)
    raise RuntimeError("server readiness timeout")


@dataclass
class ApiProbeResult:
    scenario: str
    duration_s: float
    concurrency: int
    request_count: int
    failure_count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    rps: float


async def probe_api(scenario: str, cookies: list[tuple[str, str]], duration_s: float, concurrency: int) -> ApiProbeResult:
    latencies: list[float] = []
    failures = 0
    deadline = time.perf_counter() + duration_s
    cookie_values = [session for _, session in cookies] or [""]
    lock = asyncio.Lock()

    async def worker(worker_id: int) -> None:
        nonlocal failures
        session_id = cookie_values[worker_id % len(cookie_values)]
        headers = {"Cookie": f"session_id={session_id}"} if session_id else {}
        async with httpx.AsyncClient(verify=False, timeout=10.0, headers=headers) as client:
            while time.perf_counter() < deadline:
                start = time.perf_counter()
                ok = False
                try:
                    resp = await client.get(f"{HOST}/api/events")
                    ok = resp.status_code == 200
                except Exception:
                    ok = False
                elapsed = (time.perf_counter() - start) * 1000
                async with lock:
                    latencies.append(elapsed)
                    if not ok:
                        failures += 1
                await asyncio.sleep(0.05)

    await asyncio.gather(*(worker(i) for i in range(concurrency)))
    count = len(latencies)
    return ApiProbeResult(
        scenario=scenario,
        duration_s=duration_s,
        concurrency=concurrency,
        request_count=count,
        failure_count=failures,
        p50_ms=percentile(latencies, 0.50),
        p95_ms=percentile(latencies, 0.95),
        p99_ms=percentile(latencies, 0.99),
        min_ms=round(min(latencies), 1) if latencies else 0.0,
        max_ms=round(max(latencies), 1) if latencies else 0.0,
        rps=round(count / duration_s, 2) if duration_s else 0.0,
    )


@dataclass
class SseStat:
    conn_id: int
    username: str
    first_connected_at: float = 0.0
    last_connected_at: float = 0.0
    reconnect_count: int = 0
    line_count: int = 0
    connect_failures: int = 0
    last_error: str = ""


async def sse_client(stat: SseStat, session_id: str, stop_event: asyncio.Event) -> None:
    cookies = {"session_id": session_id} if session_id else {}
    while not stop_event.is_set():
        try:
            async with httpx.AsyncClient(
                verify=False,
                timeout=httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0),
                cookies=cookies,
            ) as client:
                async with client.stream("GET", f"{HOST}/api/stream") as resp:
                    if resp.status_code != 200:
                        stat.connect_failures += 1
                        stat.last_error = f"HTTP {resp.status_code}"
                        await asyncio.sleep(0.5)
                        continue
                    now = time.perf_counter()
                    if stat.first_connected_at == 0.0:
                        stat.first_connected_at = now
                    else:
                        stat.reconnect_count += 1
                    stat.last_connected_at = now
                    async for line in resp.aiter_lines():
                        if stop_event.is_set():
                            return
                        if line.startswith(":") or line.startswith("event:") or line.startswith("data:"):
                            stat.line_count += 1
        except asyncio.CancelledError:
            return
        except Exception as exc:
            stat.connect_failures += 1
            stat.last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(0.5)


async def wait_sse_count(stats: list[SseStat], minimum: int, timeout_s: float, after: float = 0.0) -> tuple[int, float]:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout_s:
        count = sum(1 for stat in stats if stat.last_connected_at > after)
        if count >= minimum:
            return count, round((time.perf_counter() - start) * 1000, 1)
        await asyncio.sleep(0.2)
    count = sum(1 for stat in stats if stat.last_connected_at > after)
    return count, round((time.perf_counter() - start) * 1000, 1)


def summarize_sse(stats: list[SseStat], after: float = 0.0) -> dict[str, Any]:
    connected = sum(1 for stat in stats if stat.first_connected_at > 0)
    connected_after = sum(1 for stat in stats if stat.last_connected_at > after) if after else connected
    return {
        "requested": len(stats),
        "connected_initial": connected,
        "connected_after_marker": connected_after,
        "total_reconnect_count": sum(stat.reconnect_count for stat in stats),
        "total_connect_failures": sum(stat.connect_failures for stat in stats),
        "total_lines": sum(stat.line_count for stat in stats),
        "last_errors": sorted({stat.last_error for stat in stats if stat.last_error})[:10],
    }


def gate_verdict(base: ApiProbeResult, with_sse: ApiProbeResult, restart: ApiProbeResult, sse_restart: dict[str, Any]) -> tuple[str, list[str]]:
    reasons: list[str] = []

    def regression(candidate: ApiProbeResult, label: str) -> bool:
        if candidate.failure_count:
            reasons.append(f"{label}: failures={candidate.failure_count}")
            return True
        delta = candidate.p95_ms - base.p95_ms
        ratio = candidate.p95_ms / base.p95_ms if base.p95_ms else 999.0
        if candidate.p95_ms >= 500 and ratio >= 1.5 and delta >= 200:
            reasons.append(f"{label}: p95 {candidate.p95_ms}ms vs baseline {base.p95_ms}ms")
            return True
        return False

    go = regression(with_sse, "with_50_sse") or regression(restart, "restart_reconnect")
    if sse_restart.get("connected_after_marker", 0) < int(sse_restart.get("requested", 0) * 0.95):
        reasons.append("restart_reconnect: fewer than 95% SSE clients reconnected")
        go = True

    if go:
        return "GO", reasons

    soft_ratio = max(
        with_sse.p95_ms / base.p95_ms if base.p95_ms else 1.0,
        restart.p95_ms / base.p95_ms if base.p95_ms else 1.0,
    )
    if soft_ratio >= 1.2:
        reasons.append(f"soft p95 increase observed, max ratio={soft_ratio:.2f}")
        return "CONDITIONAL", reasons

    reasons.append("no measurable p95 regression under query/SSE/restart probes")
    return "NO-GO", reasons


async def run(args: argparse.Namespace) -> int:
    run_dir = BASELINE_DIR / f"m2_0_{datetime.now().strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    log(f"run_dir={run_dir}")

    if port_open(8443):
        raise RuntimeError("port 8443 already in use")

    base_env = {"WHATUDOIN_PERF_FIXTURE": "allow", "WHATUDOIN_DB_PATH": str(DB_PATH)}
    snapshot_hash = "N/A"
    server: subprocess.Popen | None = None
    try:
        snapshot = run_py(
            SNAPSHOT_SCRIPT,
            run_dir,
            {**base_env, "WHATUDOIN_PERF_BASELINE_DIR": str(run_dir)},
        )
        if snapshot.returncode != 0:
            raise RuntimeError("snapshot_db.py failed")
        snap_db = next(run_dir.glob("db_snapshot*/whatudoin.db"), None)
        if snap_db:
            snapshot_hash = hashlib.sha256(snap_db.read_bytes()).hexdigest()

        seed = run_py(SEED_SCRIPT, run_dir, base_env)
        if seed.returncode != 0:
            raise RuntimeError("seed_users.py failed")
        user_count = db_count("SELECT COUNT(*) FROM users WHERE name LIKE ?", ("test_perf_%",))
        session_count = db_count(
            "SELECT COUNT(*) FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
            ("test_perf_%",),
        )
        cookies = load_cookies()
        if user_count < 50 or session_count < 50 or len(cookies) < 50:
            raise RuntimeError(f"fixture seed incomplete: users={user_count}, sessions={session_count}, cookies={len(cookies)}")

        server = start_server(run_dir, "initial")
        ready_ms = await wait_ready(server)
        log(f"ready initial={ready_ms}ms")

        baseline = await probe_api("baseline_no_sse", cookies, args.api_duration, args.api_concurrency)
        log(f"baseline p95={baseline.p95_ms}ms count={baseline.request_count} failures={baseline.failure_count}")

        stop_event = asyncio.Event()
        sse_stats = [SseStat(i, cookies[i % len(cookies)][0]) for i in range(args.sse_clients)]
        sse_tasks = [
            asyncio.create_task(sse_client(sse_stats[i], cookies[i % len(cookies)][1], stop_event))
            for i in range(args.sse_clients)
        ]
        initial_connected, initial_connect_ms = await wait_sse_count(sse_stats, args.sse_clients, 15.0)
        log(f"sse initial connected={initial_connected}/{args.sse_clients} in {initial_connect_ms}ms")
        with_sse = await probe_api("with_50_sse", cookies, args.api_duration, args.api_concurrency)
        log(f"with_sse p95={with_sse.p95_ms}ms count={with_sse.request_count} failures={with_sse.failure_count}")

        restart_marker = time.perf_counter()
        stop_server(server)
        server = None
        await asyncio.sleep(args.restart_gap)
        server = start_server(run_dir, "restart")
        restart_ready_ms = await wait_ready(server)
        log(f"ready restart={restart_ready_ms}ms")

        # Run API probe immediately while SSE clients are reconnecting.
        restart_probe_task = asyncio.create_task(
            probe_api("restart_reconnect_window", cookies, args.restart_api_duration, args.api_concurrency)
        )
        reconnected, reconnect_ms = await wait_sse_count(sse_stats, int(args.sse_clients * 0.95), args.restart_api_duration, restart_marker)
        restart_probe = await restart_probe_task
        log(f"restart p95={restart_probe.p95_ms}ms; reconnected={reconnected}/{args.sse_clients} in {reconnect_ms}ms")

        stop_event.set()
        await asyncio.sleep(0.2)
        for task in sse_tasks:
            task.cancel()
        await asyncio.gather(*sse_tasks, return_exceptions=True)

        sse_initial_summary = summarize_sse(sse_stats)
        sse_restart_summary = summarize_sse(sse_stats, restart_marker)
        verdict, reasons = gate_verdict(baseline, with_sse, restart_probe, sse_restart_summary)

        result = {
            "run_dir": str(run_dir),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot_sha256": snapshot_hash,
            "host": HOST,
            "durations": {
                "api_duration_s": args.api_duration,
                "restart_api_duration_s": args.restart_api_duration,
                "api_concurrency": args.api_concurrency,
                "sse_clients": args.sse_clients,
                "restart_gap_s": args.restart_gap,
            },
            "server_ready_ms": {"initial": ready_ms, "restart": restart_ready_ms},
            "api": {
                "baseline_no_sse": asdict(baseline),
                "with_50_sse": asdict(with_sse),
                "restart_reconnect_window": asdict(restart_probe),
            },
            "sse": {
                "initial_connect_ms": initial_connect_ms,
                "initial": sse_initial_summary,
                "restart_reconnect_ms_to_95_percent": reconnect_ms,
                "restart": sse_restart_summary,
            },
            "verdict": verdict,
            "reasons": reasons,
            "limits": [
                "single PC measurement; server and probe share CPU/network",
                "query pressure only; upload pressure was not part of this M2-0 probe",
                "SSE publish latency is approximated by connection/reconnect behavior; broker has no server timestamp",
            ],
        }

        json_path = run_dir / "m2_0_gate_report.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        csv_path = run_dir / "m2_0_api_summary.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(baseline).keys()))
            writer.writeheader()
            for item in (baseline, with_sse, restart_probe):
                writer.writerow(asdict(item))

        md_path = run_dir / "m2_0_gate_report.md"
        md_path.write_text(render_markdown(result), encoding="utf-8")
        log(f"report={md_path}")
        return 0
    finally:
        stop_server(server)
        cleanup = run_py(CLEANUP_SCRIPT, run_dir, base_env)
        if cleanup.returncode != 0:
            log("cleanup.py failed; see cleanup logs")


def render_markdown(result: dict[str, Any]) -> str:
    api = result["api"]
    sse = result["sse"]
    rows = []
    for key in ("baseline_no_sse", "with_50_sse", "restart_reconnect_window"):
        item = api[key]
        rows.append(
            f"| {item['scenario']} | {item['request_count']} | {item['failure_count']} | "
            f"{item['p50_ms']} | {item['p95_ms']} | {item['p99_ms']} | {item['rps']} |"
        )

    reasons = "\n".join(f"- {reason}" for reason in result["reasons"])
    limits = "\n".join(f"- {limit}" for limit in result["limits"])
    return f"""# M2-0 gate report

Created: {result['created_at']}
Run dir: `{result['run_dir']}`
Host: `{result['host']}`

## Verdict

**{result['verdict']}**

{reasons}

## API probe

| scenario | requests | failures | p50 ms | p95 ms | p99 ms | rps |
|----------|----------|----------|--------|--------|--------|-----|
{chr(10).join(rows)}

## SSE probe

- Initial connect: {sse['initial']['connected_initial']}/{sse['initial']['requested']} in {sse['initial_connect_ms']} ms
- Restart reconnect: {sse['restart']['connected_after_marker']}/{sse['restart']['requested']} reached 95% threshold in {sse['restart_reconnect_ms_to_95_percent']} ms
- Total reconnect count: {sse['restart']['total_reconnect_count']}
- Total connect failures: {sse['restart']['total_connect_failures']}

## Server readiness

- Initial readiness: {result['server_ready_ms']['initial']} ms
- Restart readiness: {result['server_ready_ms']['restart']} ms

## Limits

{limits}
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-duration", type=float, default=30.0)
    parser.add_argument("--restart-api-duration", type=float, default=30.0)
    parser.add_argument("--api-concurrency", type=int, default=10)
    parser.add_argument("--sse-clients", type=int, default=50)
    parser.add_argument("--restart-gap", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except Exception as exc:
        log(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
