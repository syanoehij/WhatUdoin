"""
M2-5 HSTS policy probe.

The probe verifies that WhatUdoin does not emit Strict-Transport-Security while
HTTP 8000 fallback remains an active operational path.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[2]
PYTHON = sys.executable
BASELINE_DIR = REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"
HTTPS_CERT = REPO_ROOT / "whatudoin-cert.pem"
HTTPS_KEY = REPO_ROOT / "whatudoin-key.pem"


@dataclass
class HeaderProbe:
    url: str
    status_code: int
    strict_transport_security: str
    elapsed_ms: float


def log(message: str) -> None:
    print(f"[m2-5] {message}", flush=True)


def port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def start_server(run_dir: Path, port: int, https: bool) -> subprocess.Popen:
    if port_open(port):
        raise RuntimeError(f"port {port} is already in use")
    stdout = open(run_dir / f"server_{port}_stdout.log", "w", encoding="utf-8", errors="replace")
    stderr = open(run_dir / f"server_{port}_stderr.log", "w", encoding="utf-8", errors="replace")
    cmd = [
        PYTHON,
        "-m",
        "uvicorn",
        "app:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    if https:
        if not HTTPS_CERT.exists() or not HTTPS_KEY.exists():
            raise RuntimeError("HTTPS certificate/key files are missing")
        cmd.extend(["--ssl-certfile", str(HTTPS_CERT), "--ssl-keyfile", str(HTTPS_KEY)])
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=stdout,
        stderr=stderr,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    (run_dir / f"server_{port}.pid").write_text(str(proc.pid), encoding="utf-8")
    log(f"server port={port} pid={proc.pid}")
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


def wait_ready(url: str, proc: subprocess.Popen, verify: bool = True, timeout_s: float = 30.0) -> None:
    deadline = time.perf_counter() + timeout_s
    with httpx.Client(timeout=2.0, verify=verify) as client:
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.returncode}")
            try:
                resp = client.get(url)
                if resp.status_code < 500:
                    return
            except Exception:
                time.sleep(0.5)
    raise RuntimeError(f"server readiness timeout: {url}")


def probe(url: str, verify: bool) -> HeaderProbe:
    with httpx.Client(timeout=10.0, verify=verify, follow_redirects=False) as client:
        started = time.perf_counter()
        resp = client.get(url)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        return HeaderProbe(
            url=url,
            status_code=resp.status_code,
            strict_transport_security=resp.headers.get("strict-transport-security", ""),
            elapsed_ms=elapsed_ms,
        )


def write_report(run_dir: Path, rows: list[HeaderProbe], cleanup: dict[str, bool]) -> bool:
    passed = all(not row.strict_transport_security for row in rows)
    data = {
        "passed": passed,
        "rows": [asdict(row) for row in rows],
        "cleanup": cleanup,
    }
    (run_dir / "m2_5_hsts_probe.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# M2-5 HSTS probe",
        "",
        f"- verdict: {'PASS' if passed else 'FAIL'}",
        "",
        "| url | status | strict-transport-security | elapsed_ms |",
        "|---|---:|---|---:|",
    ]
    for row in rows:
        value = row.strict_transport_security or "(absent)"
        lines.append(f"| `{row.url}` | {row.status_code} | `{value}` | {row.elapsed_ms} |")
    lines.extend([
        "",
        "## Cleanup",
        "",
        "```json",
        json.dumps(cleanup, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    (run_dir / "m2_5_hsts_probe.md").write_text("\n".join(lines), encoding="utf-8")
    return passed


def main() -> int:
    run_dir = BASELINE_DIR / f"m2_5_{datetime.now().strftime('%H%M%S')}_hsts"
    run_dir.mkdir(parents=True, exist_ok=True)
    http_proc: subprocess.Popen | None = None
    https_proc: subprocess.Popen | None = None
    rows: list[HeaderProbe] = []
    cleanup: dict[str, bool] = {}
    try:
        http_proc = start_server(run_dir, 8000, https=False)
        https_proc = start_server(run_dir, 8443, https=True)
        wait_ready("http://127.0.0.1:8000/api/health", http_proc)
        wait_ready("https://127.0.0.1:8443/api/health", https_proc, verify=False)
        rows.append(probe("https://127.0.0.1:8443/api/health", verify=False))
        rows.append(probe("https://127.0.0.1:8443/", verify=False))
        rows.append(probe("http://127.0.0.1:8000/api/health", verify=True))
    finally:
        stop_server(https_proc)
        stop_server(http_proc)
        cleanup["port_8000_open_after_stop"] = port_open(8000)
        cleanup["port_8443_open_after_stop"] = port_open(8443)
    passed = write_report(run_dir, rows, cleanup)
    print(run_dir)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
