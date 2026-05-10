"""
M2-6 AVR policy probe.

Verifies current direct HTTP AVR behavior before carrying AVR through the M2
Front Router design as an in-scope compatibility requirement.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
DB_PATH = REPO_ROOT / "whatudoin.db"
BASELINE_DIR = REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"
HTTPS_CERT = REPO_ROOT / "whatudoin-cert.pem"
HTTPS_KEY = REPO_ROOT / "whatudoin-key.pem"


@dataclass
class ProbeRow:
    name: str
    url: str
    status_code: int
    passed: bool
    evidence: str


def log(message: str) -> None:
    print(f"[m2-6] {message}", flush=True)


def port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    finally:
        sock.close()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    sys.path.insert(0, str(REPO_ROOT))
    import database as db  # noqa: PLC0415

    db.init_db()


def ensure_team(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM teams ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row["id"])
    cur = conn.execute("INSERT INTO teams (name) VALUES (?)", ("M2-6 Probe Team",))
    return int(cur.lastrowid)


def create_fixture(marker: str) -> tuple[int, str]:
    ensure_schema()
    sys.path.insert(0, str(REPO_ROOT))
    import database as db  # noqa: PLC0415

    with get_conn() as conn:
        cleanup_rows(conn, marker)
        team_id = ensure_team(conn)
        cur = conn.execute(
            "INSERT INTO users (name, password, role, team_id, is_active, avr_enabled) "
            "VALUES (?, ?, 'editor', ?, 1, 1)",
            (marker, f"{marker}-pw", team_id),
        )
        user_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '127.0.0.1', 'whitelist')",
            (user_id,),
        )
        conn.commit()
    session_id = db.create_session(user_id, "editor")
    return user_id, session_id


def cleanup_rows(conn: sqlite3.Connection, marker: str) -> dict[str, int]:
    user_rows = conn.execute("SELECT id FROM users WHERE name LIKE ?", (f"{marker}%",)).fetchall()
    user_ids = [int(row["id"]) for row in user_rows]
    counts = {"users": len(user_ids), "user_ips": 0, "sessions": 0}
    if user_ids:
        placeholders = ",".join("?" for _ in user_ids)
        counts["sessions"] = conn.execute(
            f"SELECT COUNT(*) FROM sessions WHERE user_id IN ({placeholders})",
            user_ids,
        ).fetchone()[0]
        conn.execute(f"DELETE FROM sessions WHERE user_id IN ({placeholders})", user_ids)
        counts["user_ips"] = conn.execute(
            f"SELECT COUNT(*) FROM user_ips WHERE user_id IN ({placeholders})",
            user_ids,
        ).fetchone()[0]
        conn.execute(f"DELETE FROM user_ips WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
    conn.commit()
    return counts


def save_settings() -> dict[str, str | None]:
    sys.path.insert(0, str(REPO_ROOT))
    import database as db  # noqa: PLC0415

    return {
        "avr_url_enc": db.get_setting("avr_url_enc"),
        "avr_secret_enc": db.get_setting("avr_secret_enc"),
    }


def set_avr_settings(wudeskop_url: str) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    import crypto  # noqa: PLC0415
    import database as db  # noqa: PLC0415

    db.set_setting("avr_url_enc", crypto.encrypt(wudeskop_url))
    db.set_setting("avr_secret_enc", crypto.encrypt("m2-6-secret"))


def restore_settings(saved: dict[str, str | None]) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    import database as db  # noqa: PLC0415

    for key, value in saved.items():
        if value is None:
            db.delete_setting(key)
        else:
            db.set_setting(key, value)


class FakeWUDeskopHandler(BaseHTTPRequestHandler):
    token = "m2-6-token"

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/issue-token":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("content-length") or "0")
        if length:
            self.rfile.read(length)
        body = json.dumps({"token": self.token}).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def start_fake_wudeskop() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeWUDeskopHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def start_app_server(run_dir: Path, port: int, https: bool) -> subprocess.Popen:
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


def stop_proc(proc: subprocess.Popen | None) -> None:
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
    with httpx.Client(timeout=2.0, verify=verify, follow_redirects=False) as client:
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


def run_probes(session_id: str, wudeskop_url: str) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    with httpx.Client(timeout=10.0, follow_redirects=False, verify=False) as client:
        resp = client.get("http://127.0.0.1:8000/remote")
        rows.append(ProbeRow(
            "remote redirect",
            "http://127.0.0.1:8000/remote",
            resp.status_code,
            resp.status_code == 307 and resp.headers.get("location") == "/avr",
            f"location={resp.headers.get('location', '')}",
        ))

        resp = client.get(
            "http://127.0.0.1:8000/avr",
            headers={"Cookie": f"session_id={session_id}"},
        )
        rows.append(ProbeRow(
            "session login denied",
            "http://127.0.0.1:8000/avr",
            resp.status_code,
            resp.status_code == 403,
            resp.text[:160].replace("\n", " "),
        ))

        resp = client.get("http://127.0.0.1:8000/avr")
        csp = resp.headers.get("content-security-policy", "")
        body = resp.text
        expected_viewer = f"{wudeskop_url}/viewer?token={FakeWUDeskopHandler.token}"
        rows.append(ProbeRow(
            "ip whitelist avr page",
            "http://127.0.0.1:8000/avr",
            resp.status_code,
            resp.status_code == 200 and expected_viewer in body,
            f"viewer_url_present={expected_viewer in body}",
        ))
        rows.append(ProbeRow(
            "frame-src csp",
            "http://127.0.0.1:8000/avr",
            resp.status_code,
            f"frame-src {wudeskop_url}" in csp,
            csp,
        ))

        resp = client.get("https://127.0.0.1:8443/avr")
        rows.append(ProbeRow(
            "https plain-http avr redirect",
            "https://127.0.0.1:8443/avr",
            resp.status_code,
            resp.status_code == 307 and resp.headers.get("location") == "http://127.0.0.1:8000/avr",
            f"location={resp.headers.get('location', '')}",
        ))
    return rows


def write_report(run_dir: Path, rows: list[ProbeRow], cleanup: dict[str, Any]) -> bool:
    passed = all(row.passed for row in rows)
    data = {
        "passed": passed,
        "rows": [asdict(row) for row in rows],
        "cleanup": cleanup,
    }
    (run_dir / "m2_6_avr_policy_probe.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# M2-6 AVR policy probe",
        "",
        f"- verdict: {'PASS' if passed else 'FAIL'}",
        "",
        "| check | url | status | pass | evidence |",
        "|---|---|---:|---|---|",
    ]
    for row in rows:
        evidence = row.evidence.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {row.name} | `{row.url}` | {row.status_code} | {row.passed} | `{evidence}` |")
    lines.extend([
        "",
        "## Cleanup",
        "",
        "```json",
        json.dumps(cleanup, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    (run_dir / "m2_6_avr_policy_probe.md").write_text("\n".join(lines), encoding="utf-8")
    return passed


def main() -> int:
    run_dir = BASELINE_DIR / f"m2_6_{datetime.now().strftime('%H%M%S')}_avr"
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = f"m2_avr_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    http_proc: subprocess.Popen | None = None
    https_proc: subprocess.Popen | None = None
    fake_server: ThreadingHTTPServer | None = None
    saved_settings: dict[str, str | None] | None = None
    rows: list[ProbeRow] = []
    cleanup: dict[str, Any] = {}
    try:
        fake_server, _thread, wudeskop_url = start_fake_wudeskop()
        saved_settings = save_settings()
        set_avr_settings(wudeskop_url)
        _user_id, session_id = create_fixture(marker)
        http_proc = start_app_server(run_dir, 8000, https=False)
        https_proc = start_app_server(run_dir, 8443, https=True)
        wait_ready("http://127.0.0.1:8000/api/health", http_proc)
        wait_ready("https://127.0.0.1:8443/api/health", https_proc, verify=False)
        rows = run_probes(session_id, wudeskop_url)
    finally:
        stop_proc(https_proc)
        stop_proc(http_proc)
        if fake_server is not None:
            fake_server.shutdown()
            fake_server.server_close()
        if saved_settings is not None:
            restore_settings(saved_settings)
        with get_conn() as conn:
            cleanup["rows"] = cleanup_rows(conn, marker)
        cleanup["port_8000_open_after_stop"] = port_open(8000)
        cleanup["port_8443_open_after_stop"] = port_open(8443)
    passed = write_report(run_dir, rows, cleanup)
    print(run_dir)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
