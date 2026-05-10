"""
M2-4 HTTP 8000 fallback write-policy probe.

The probe creates a temporary IP-whitelisted editor, starts the HTTP server on
127.0.0.1:8000, exercises unsafe write endpoints without cookies, and cleans up
all rows/files created under its marker prefix.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
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
BASE_URL = "http://127.0.0.1:8000"
ORIGIN = BASE_URL


@dataclass
class ProbeRow:
    name: str
    method: str
    path: str
    status_code: int
    outcome: str
    body: str
    elapsed_ms: float


def log(message: str) -> None:
    print(f"[m2-4] {message}", flush=True)


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
    cur = conn.execute("INSERT INTO teams (name) VALUES (?)", ("M2-4 Probe Team",))
    return int(cur.lastrowid)


def create_fixture(marker: str) -> tuple[int, int, int]:
    ensure_schema()
    with get_conn() as conn:
        cleanup_rows(conn, marker)
        team_id = ensure_team(conn)
        cur = conn.execute(
            "INSERT INTO users (name, password, role, team_id, is_active) VALUES (?, ?, 'editor', ?, 1)",
            (marker, f"{marker}-pw", team_id),
        )
        user_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '127.0.0.1', 'whitelist')",
            (user_id,),
        )
        doc = conn.execute(
            "INSERT INTO meetings (title, content, team_id, created_by, is_team_doc, is_public, team_share) "
            "VALUES (?, ?, ?, ?, 1, 0, 0)",
            (f"{marker} doc", "m2-4 seed", team_id, user_id),
        )
        conn.commit()
        return user_id, team_id, int(doc.lastrowid)


def cleanup_rows(conn: sqlite3.Connection, marker: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    user_rows = conn.execute("SELECT id FROM users WHERE name LIKE ?", (f"{marker}%",)).fetchall()
    user_ids = [int(row["id"]) for row in user_rows]
    doc_rows = conn.execute("SELECT id FROM meetings WHERE title LIKE ?", (f"{marker}%",)).fetchall()
    doc_ids = [int(row["id"]) for row in doc_rows]
    checklist_rows = conn.execute("SELECT id FROM checklists WHERE title LIKE ?", (f"{marker}%",)).fetchall()
    checklist_ids = [int(row["id"]) for row in checklist_rows]

    counts["events"] = conn.execute("SELECT COUNT(*) FROM events WHERE title LIKE ?", (f"{marker}%",)).fetchone()[0]
    conn.execute("DELETE FROM events WHERE title LIKE ?", (f"{marker}%",))

    if checklist_ids:
        placeholders = ",".join("?" for _ in checklist_ids)
        counts["checklist_histories"] = conn.execute(
            f"SELECT COUNT(*) FROM checklist_histories WHERE checklist_id IN ({placeholders})",
            checklist_ids,
        ).fetchone()[0]
        conn.execute(f"DELETE FROM checklist_histories WHERE checklist_id IN ({placeholders})", checklist_ids)
        counts["checklists"] = len(checklist_ids)
        conn.execute(f"DELETE FROM checklists WHERE id IN ({placeholders})", checklist_ids)
    else:
        counts["checklist_histories"] = 0
        counts["checklists"] = 0

    if doc_ids:
        placeholders = ",".join("?" for _ in doc_ids)
        counts["meeting_histories"] = conn.execute(
            f"SELECT COUNT(*) FROM meeting_histories WHERE meeting_id IN ({placeholders})",
            doc_ids,
        ).fetchone()[0]
        conn.execute(f"DELETE FROM meeting_histories WHERE meeting_id IN ({placeholders})", doc_ids)
        counts["meeting_locks"] = conn.execute(
            f"SELECT COUNT(*) FROM meeting_locks WHERE meeting_id IN ({placeholders})",
            doc_ids,
        ).fetchone()[0]
        conn.execute(f"DELETE FROM meeting_locks WHERE meeting_id IN ({placeholders})", doc_ids)
        counts["meetings"] = len(doc_ids)
        conn.execute(f"DELETE FROM meetings WHERE id IN ({placeholders})", doc_ids)
    else:
        counts["meeting_histories"] = 0
        counts["meeting_locks"] = 0
        counts["meetings"] = 0

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
        counts["users"] = len(user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
    else:
        counts["sessions"] = 0
        counts["user_ips"] = 0
        counts["users"] = 0

    conn.commit()
    return counts


def cleanup_files(urls: list[str]) -> list[str]:
    removed: list[str] = []
    for url in urls:
        if not url.startswith("/uploads/meetings/"):
            continue
        rel = url.replace("/uploads/", "", 1).replace("/", os.sep)
        path = REPO_ROOT / rel
        if path.exists():
            path.unlink()
            removed.append(str(path.relative_to(REPO_ROOT)))
    return removed


def make_png() -> bytes:
    from PIL import Image  # noqa: PLC0415

    buffer = io.BytesIO()
    Image.new("RGBA", (1, 1), (255, 255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def start_server(run_dir: Path) -> subprocess.Popen:
    if port_open(8000):
        raise RuntimeError("port 8000 is already in use")
    stdout = open(run_dir / "server_stdout.log", "w", encoding="utf-8", errors="replace")
    stderr = open(run_dir / "server_stderr.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [
            PYTHON,
            "-m",
            "uvicorn",
            "app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ],
        cwd=str(REPO_ROOT),
        stdout=stdout,
        stderr=stderr,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    (run_dir / "server.pid").write_text(str(proc.pid), encoding="utf-8")
    log(f"server pid={proc.pid}")
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


def wait_ready(proc: subprocess.Popen, timeout_s: float = 30.0) -> None:
    deadline = time.perf_counter() + timeout_s
    with httpx.Client(timeout=2.0) as client:
        while time.perf_counter() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early: {proc.returncode}")
            try:
                resp = client.get(f"{BASE_URL}/api/events")
                if resp.status_code < 500:
                    return
            except Exception:
                time.sleep(0.5)
    raise RuntimeError("server readiness timeout")


def snippet(resp: httpx.Response) -> str:
    text = resp.text.replace("\n", " ").strip()
    if len(text) > 180:
        return text[:177] + "..."
    return text


def classify(status_code: int) -> str:
    if status_code in {403, 405}:
        return "blocked"
    if 200 <= status_code < 300:
        return "allowed"
    return "other"


def request_json(client: httpx.Client, method: str, path: str, payload: dict[str, Any]) -> ProbeRow:
    started = time.perf_counter()
    resp = client.request(method, f"{BASE_URL}{path}", json=payload, headers={"Origin": ORIGIN})
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    return ProbeRow(path, method, path, resp.status_code, classify(resp.status_code), snippet(resp), elapsed)


def request_file(client: httpx.Client, name: str, path: str, files: dict[str, Any]) -> tuple[ProbeRow, str]:
    started = time.perf_counter()
    resp = client.post(f"{BASE_URL}{path}", files=files, headers={"Origin": ORIGIN})
    elapsed = round((time.perf_counter() - started) * 1000, 1)
    uploaded_url = ""
    try:
        data = resp.json()
        uploaded_url = str(data.get("url") or "")
    except Exception:
        uploaded_url = ""
    return ProbeRow(name, "POST", path, resp.status_code, classify(resp.status_code), snippet(resp), elapsed), uploaded_url


def run_probes(marker: str, doc_id: int) -> tuple[list[ProbeRow], list[str]]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    headers = {"Origin": ORIGIN}
    uploaded_urls: list[str] = []
    rows: list[ProbeRow] = []
    with httpx.Client(timeout=15.0, follow_redirects=False, headers=headers) as client:
        rows.append(request_json(client, "POST", "/api/events", {
            "title": f"{marker} event",
            "assignee": marker,
            "start_datetime": f"{today}T09:00:00",
            "end_datetime": f"{today}T10:00:00",
            "event_type": "schedule",
            "source": "manual",
        }))
        rows.append(request_json(client, "PUT", f"/api/doc/{doc_id}", {
            "title": f"{marker} doc updated",
            "content": "m2-4 update",
            "is_team_doc": True,
            "is_public": False,
            "team_share": False,
            "attachments": [],
        }))
        rows.append(request_json(client, "POST", "/api/checklists", {
            "title": f"{marker} checklist",
            "content": "m2-4 checklist",
            "is_public": False,
            "attachments": [],
        }))
        row, url = request_file(
            client,
            "POST /api/upload/image",
            "/api/upload/image",
            {"file": ("m2_4.png", make_png(), "image/png")},
        )
        rows.append(row)
        if url:
            uploaded_urls.append(url)
        row, url = request_file(
            client,
            "POST /api/upload/attachment",
            "/api/upload/attachment",
            {"file": ("m2_4.txt", b"m2-4 attachment", "text/plain")},
        )
        rows.append(row)
        if url:
            uploaded_urls.append(url)
        started = time.perf_counter()
        resp = client.get(f"{BASE_URL}/api/events")
        rows.append(ProbeRow("GET /api/events", "GET", "/api/events", resp.status_code, classify(resp.status_code), snippet(resp), round((time.perf_counter() - started) * 1000, 1)))
    return rows, uploaded_urls


def write_report(run_dir: Path, label: str, marker: str, rows: list[ProbeRow], cleanup: dict[str, Any], expect: str) -> bool:
    required = [r for r in rows if r.method in {"POST", "PUT", "PATCH", "DELETE"}]
    if expect == "blocked":
        passed = all(r.outcome == "blocked" for r in required)
    elif expect == "allowed":
        passed = all(r.outcome == "allowed" for r in required)
    else:
        passed = True
    safe_get_ok = any(r.method == "GET" and r.status_code == 200 for r in rows)
    if expect == "blocked":
        passed = passed and safe_get_ok

    data = {
        "label": label,
        "marker": marker,
        "expect": expect,
        "passed": passed,
        "rows": [asdict(row) for row in rows],
        "cleanup": cleanup,
    }
    (run_dir / "m2_4_http_fallback_probe.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# M2-4 HTTP fallback probe: {label}",
        "",
        f"- expect: {expect}",
        f"- verdict: {'PASS' if passed else 'FAIL'}",
        f"- marker: `{marker}`",
        "",
        "| endpoint | method | status | outcome | elapsed_ms | body |",
        "|---|---:|---:|---|---:|---|",
    ]
    for row in rows:
        body = row.body.replace("|", "\\|")
        lines.append(f"| `{row.path}` | {row.method} | {row.status_code} | {row.outcome} | {row.elapsed_ms} | `{body}` |")
    lines.extend([
        "",
        "## Cleanup",
        "",
        "```json",
        json.dumps(cleanup, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    (run_dir / "m2_4_http_fallback_probe.md").write_text("\n".join(lines), encoding="utf-8")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--expect", choices=["allowed", "blocked", "observe"], default="observe")
    args = parser.parse_args()

    run_dir = BASELINE_DIR / f"m2_4_{datetime.now().strftime('%H%M%S')}_{args.label}"
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = f"m2_http_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    proc: subprocess.Popen | None = None
    cleanup: dict[str, Any] = {}
    rows: list[ProbeRow] = []
    uploaded_urls: list[str] = []
    try:
        user_id, _team_id, doc_id = create_fixture(marker)
        log(f"fixture user_id={user_id} doc_id={doc_id}")
        proc = start_server(run_dir)
        wait_ready(proc)
        rows, uploaded_urls = run_probes(marker, doc_id)
    finally:
        stop_server(proc)
        with get_conn() as conn:
            cleanup["rows"] = cleanup_rows(conn, marker)
        cleanup["files"] = cleanup_files(uploaded_urls)
        cleanup["port_8000_open_after_stop"] = port_open(8000)
    passed = write_report(run_dir, args.label, marker, rows, cleanup, args.expect)
    print(run_dir)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
