"""
M1a-7 baseline 측정 runner (Python orchestrator)

PS runner (run_baseline_m1a7.ps1) 대체.
PS 5.1 quirk 3종(BOM, heredoc, NativeCommandError) 누적으로 pivot.

Phase 0: pre-flight 점검
Phase 1: run 디렉터리 + 환경 메타데이터 캡처
Phase 2: snapshot + seed
Phase 3: 서버 시작 + readiness wait
Phase 4: sanity run (1 VU x 30s)
Phase 5: 본 측정 5단계 (1/5/10/25/50 VU)
Phase 6: SSE keep-alive (50 VU 단계와 병렬)
Phase 7: 서버 graceful shutdown  [always via finally]
Phase 8: cleanup + 검증           [always via finally, seed_done guard]
Phase 9: 결과 요약 생성            [best-effort in finally]

Usage:
  python _workspace/perf/scripts/run_baseline_m1a7.py
  python _workspace/perf/scripts/run_baseline_m1a7.py --max-phase 1   # dry-run
"""

import argparse
import csv
import hashlib
import importlib.util
import json
import logging
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 콘솔 한글 출력 보장 ───────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 경로 상수 ─────────────────────────────────────────────────────────────────
# __file__ = <repo>/_workspace/perf/scripts/run_baseline_m1a7.py
# parents[0] = scripts/, [1] = perf/, [2] = _workspace/, [3] = <repo root>
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[2]

PYTHON = sys.executable
LOCUSTFILE = _REPO_ROOT / "_workspace" / "perf" / "locust" / "locustfile.py"
SEED_SCRIPT = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "seed_users.py"
CLEANUP_SCRIPT = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "cleanup.py"
SNAPSHOT_SCRIPT = _REPO_ROOT / "_workspace" / "perf" / "scripts" / "snapshot_db.py"
SSE_KEEPALIVE = _REPO_ROOT / "_workspace" / "perf" / "scripts" / "sse_keepalive.py"
DB_PATH = _REPO_ROOT / "whatudoin.db"
COOKIES_JSON = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "session_cookies.json"
BASELINE_DIR = _REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"
HTTPS_HOST = "https://localhost:8443"

READINESS_TIMEOUT = 30   # seconds
READINESS_INTERVAL = 2   # seconds
LOCUST_STAGE_TIMEOUT = 120  # seconds (60s run + 60s buffer)
SANITY_TIMEOUT = 60      # seconds (30s run + 30s buffer)

# ── 로깅 설정 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def _ok(msg: str) -> None:
    log.info("[OK]   %s", msg)


def _warn(msg: str) -> None:
    log.warning("[WARN] %s", msg)


def _info(msg: str) -> None:
    log.info("[INFO] %s", msg)


def _phase(msg: str) -> None:
    log.info("")
    log.info("=" * 50)
    log.info("  %s", msg)
    log.info("=" * 50)


def _abort(msg: str) -> None:
    """abort — RuntimeError를 raise해 finally가 정리하도록 한다."""
    log.error("[ABORT] %s", msg)
    raise RuntimeError(f"ABORT: {msg}")


# ── subprocess 헬퍼 ───────────────────────────────────────────────────────────
def _log_output(run_dir: Path, basename: str, stdout: str, stderr: str) -> None:
    """stdout/stderr를 run_dir에 저장. 동명 파일이 있으면 _2, _3 suffix."""
    for kind, text in (("stdout", stdout), ("stderr", stderr)):
        base = run_dir / f"{basename}_{kind}.log"
        if base.exists():
            # sequence suffix
            idx = 2
            while True:
                candidate = run_dir / f"{basename}_{kind}_{idx}.log"
                if not candidate.exists():
                    base = candidate
                    break
                idx += 1
        base.write_text(text or "", encoding="utf-8", errors="replace")


def _run_py(
    script: Path,
    *args: str,
    env_extras: dict | None = None,
    timeout: int | None = None,
    run_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """자식 Python 프로세스 실행.

    - text=True, encoding='utf-8', capture_output=True
    - PYTHONIOENCODING=utf-8 강제 (Windows cp949 UnicodeDecodeError 방지)
    - run_dir 지정 시 stdout/stderr를 <script_basename>_stdout/stderr.log에 저장
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if env_extras:
        env.update(env_extras)
    cmd = [PYTHON, str(script)] + list(args)
    result = subprocess.run(
        cmd,
        env=env,
        cwd=str(_REPO_ROOT),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if run_dir is not None:
        _log_output(run_dir, script.stem, result.stdout, result.stderr)
    return result


# ── SQLite 직접 쿼리 ──────────────────────────────────────────────────────────
def _db_select(query: str, params: tuple = ()) -> int:
    """DB에서 단일 COUNT 값을 반환한다."""
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return conn.execute(query, params).fetchone()[0]
    finally:
        conn.close()


def _db_counts() -> dict:
    """environment_metadata용 테이블별 row 수."""
    conn = sqlite3.connect(str(DB_PATH))
    counts = {}
    try:
        for table in ("users", "events", "checklists", "notifications"):
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                counts[table] = "N/A"
    finally:
        conn.close()
    return counts


# ── Locust CSV 파싱 ───────────────────────────────────────────────────────────
def _parse_locust_stats(csv_prefix: str) -> dict:
    """stats csv에서 Aggregated 행의 p95/p99/실패율/RPS를 반환."""
    stats_file = Path(f"{csv_prefix}_stats.csv")
    empty = {"found": False, "p95": "N/A", "p99": "N/A", "fail_rate": "N/A", "rps": "N/A"}
    if not stats_file.exists():
        return empty

    with open(stats_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if agg is None and rows:
        agg = rows[-1]
    if agg is None:
        return empty

    req = float(agg.get("Request Count", 0) or 0)
    fail = float(agg.get("Failure Count", 0) or 0)
    fail_rate = f"{fail / req * 100:.1f}%" if req > 0 else "N/A"

    return {
        "found": True,
        "p95": agg.get("95%", "N/A"),
        "p99": agg.get("99%", "N/A"),
        "fail_rate": fail_rate,
        "rps": agg.get("Requests/s", "N/A"),
    }


# ── Phase 0: pre-flight ───────────────────────────────────────────────────────
def phase0_preflight() -> None:
    _phase("Phase 0: pre-flight 점검")

    # 포트 8443/8000 미사용 확인
    for port in (8443, 8000):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        in_use = sock.connect_ex(("localhost", port)) == 0
        sock.close()
        if in_use:
            _abort(f"포트 {port} 이미 사용 중. 실행 중인 서버를 먼저 종료하세요.")
    _ok("8443/8000 포트 미사용 확인")

    # Ollama 11434 (경고만)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    ollama_up = sock.connect_ex(("localhost", 11434)) == 0
    sock.close()
    if ollama_up:
        _ok("Ollama 11434 포트 listen 확인")
    else:
        _warn("Ollama 11434 포트 없음. ai_parse task 실패 예상 — 다른 phase는 계속 진행")

    # locust / httpx 설치 확인
    for pkg in ("locust", "httpx"):
        if importlib.util.find_spec(pkg) is None:
            _abort(f"{pkg} 미설치. pip install {pkg}")
    _ok("locust, httpx 설치 확인")

    # 필수 파일 확인
    required = [
        _REPO_ROOT / "whatudoin-cert.pem",
        _REPO_ROOT / "whatudoin-key.pem",
        _REPO_ROOT / "credentials.json",
        DB_PATH,
    ]
    for f in required:
        if not f.exists():
            _abort(f"필수 파일 없음: {f}")
    _ok("필수 파일 존재 확인 (cert/key/credentials/db)")

    # WAL/SHM 부재 확인
    for ext in ("-wal", "-shm"):
        p = Path(str(DB_PATH) + ext)
        if p.exists():
            _abort(f"WAL/SHM 파일 존재: {p} — WhatUdoin 서버가 실행 중입니다. 종료 후 재실행.")
    _ok("WAL/SHM 파일 없음 (서버 종료 확인)")

    # 환경변수 자체 설정
    os.environ["WHATUDOIN_PERF_FIXTURE"] = "allow"
    _ok("Phase 0 통과")


# ── Phase 1: run 디렉터리 + 환경 메타데이터 ──────────────────────────────────
def phase1_metadata() -> Path:
    _phase("Phase 1: run 디렉터리 생성 + 환경 메타데이터 캡처")

    ts = datetime.now().strftime("%H%M%S")
    run_dir = BASELINE_DIR / f"run_{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _ok(f"run 디렉터리 생성: {run_dir}")

    # Python / locust / httpx 버전
    py_version = sys.version.split()[0]

    locust_ver = "N/A"
    r = subprocess.run(
        [PYTHON, "-m", "locust", "--version"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode == 0:
        locust_ver = (r.stdout + r.stderr).strip()

    httpx_ver = "N/A"
    r2 = subprocess.run(
        [PYTHON, "-c", "import httpx; print(httpx.__version__)"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r2.returncode == 0:
        httpx_ver = r2.stdout.strip()

    # RAM (psutil 우선, 없으면 wmic)
    ram_gb = "N/A"
    try:
        import psutil  # type: ignore
        ram_gb = f"{psutil.virtual_memory().total / 1024**3:.1f} GB"
        cpu_count = psutil.cpu_count(logical=True)
    except ImportError:
        cpu_count = os.cpu_count()
        r3 = subprocess.run(
            ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"],
            capture_output=True, text=True, encoding="utf-8",
        )
        for line in r3.stdout.splitlines():
            if line.startswith("TotalPhysicalMemory="):
                val = line.split("=", 1)[1].strip()
                if val.isdigit():
                    ram_gb = f"{int(val) / 1024**3:.1f} GB"
                break

    # OS/CPU
    import platform
    uname = platform.uname()

    # DB 상태
    db_size_kb = round(DB_PATH.stat().st_size / 1024, 1)
    row_counts = _db_counts()

    # meetings/ 사용량
    meetings_dir = _REPO_ROOT / "meetings"
    meetings_files = 0
    meetings_kb = 0.0
    if meetings_dir.exists():
        all_files = [p for p in meetings_dir.rglob("*") if p.is_file()]
        meetings_files = len(all_files)
        meetings_kb = round(sum(p.stat().st_size for p in all_files) / 1024, 1)

    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db_rows_lines = "\n".join(
        f"| {t} rows | {cnt} |" for t, cnt in row_counts.items()
    )

    meta = f"""# 환경 메타데이터 — M1a-7 baseline

측정 시작: {start_time}
run 디렉터리: {run_dir}

## 시스템

| 항목 | 값 |
|------|-----|
| OS | {uname.system} {uname.release} {uname.version[:40]} |
| CPU | {uname.processor or uname.machine} ({cpu_count} logical) |
| RAM | {ram_gb} |
| Python | {py_version} |
| locust | {locust_ver} |
| httpx | {httpx_ver} |

## DB 상태 (측정 시작 전)

| 항목 | 값 |
|------|-----|
| whatudoin.db 크기 | {db_size_kb} KB |
{db_rows_lines}

## meetings/ 사용량

| 항목 | 값 |
|------|-----|
| 파일 수 | {meetings_files} |
| 사용량 | {meetings_kb} KB |

## 측정 환경 정책

| 항목 | 상태 |
|------|------|
| server-locust 동거 | 동일 호스트 (localhost). 서버 CPU/메모리 경합 포함됨 |
| locust host | {HTTPS_HOST} (자체 서명 TLS) |
| SSE 분리 측정 | Phase 6에서 50 VU와 병렬 (sse_keepalive.py) |
| sanity run | 1 VU × 30s, WU_PERF_RESTRICT_HEAVY=true |
| 본 측정 단계 | 1/5/10 VU (RESTRICT_HEAVY=true) → 25/50 VU (해제) |
"""
    meta_file = run_dir / "environment_metadata.md"
    meta_file.write_text(meta, encoding="utf-8")
    _ok(f"환경 메타데이터 기록: {meta_file}")

    return run_dir


# ── Phase 2: snapshot + seed ──────────────────────────────────────────────────
def phase2_snapshot_seed(run_dir: Path) -> None:
    _phase("Phase 2: DB snapshot + seed")

    base_env = {
        "WHATUDOIN_PERF_FIXTURE": "allow",
        "WHATUDOIN_DB_PATH": str(DB_PATH),
    }

    # snapshot
    r = _run_py(
        SNAPSHOT_SCRIPT,
        env_extras={**base_env, "WHATUDOIN_PERF_BASELINE_DIR": str(run_dir)},
        run_dir=run_dir,
    )
    _info(r.stdout.strip())
    if r.stderr.strip():
        _info(r.stderr.strip())
    if r.returncode != 0:
        _abort(f"snapshot_db.py 실패 (exit {r.returncode})")
    _ok("snapshot 완료")

    # snapshot SHA256 검증
    snap_dirs = sorted(run_dir.glob("db_snapshot*"))
    snap_hash = "N/A"
    if snap_dirs:
        snap_db = snap_dirs[0] / "whatudoin.db"
        if snap_db.exists():
            src_hash = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
            dst_hash = hashlib.sha256(snap_db.read_bytes()).hexdigest()
            if src_hash == dst_hash:
                snap_hash = dst_hash
                _ok(f"snapshot SHA256 일치: {snap_hash[:16]}...")
            else:
                _warn(f"snapshot 해시 불일치 (src: {src_hash[:16]}... / dst: {dst_hash[:16]}...)")
                snap_hash = f"MISMATCH src={src_hash[:16]} dst={dst_hash[:16]}"
            # 메타데이터에 해시 추가
            meta_file = run_dir / "environment_metadata.md"
            with open(meta_file, "a", encoding="utf-8") as f:
                f.write(f"\n| snapshot SHA256 | {snap_hash} |\n")
    else:
        _warn("snapshot 디렉터리 없음. 해시 검증 생략.")

    # seed
    r = _run_py(SEED_SCRIPT, env_extras=base_env, run_dir=run_dir)
    _info(r.stdout.strip())
    if r.stderr.strip():
        _info(r.stderr.strip())
    if r.returncode != 0:
        _abort(f"seed_users.py 실패 (exit {r.returncode})")

    # seed 검증 — sqlite3 직접 SELECT (temp .py 불필요)
    u_count = _db_select(
        "SELECT COUNT(*) FROM users WHERE name LIKE 'test_perf_%'"
    )
    s_count = _db_select(
        "SELECT COUNT(*) FROM sessions WHERE user_id IN "
        "(SELECT id FROM users WHERE name LIKE 'test_perf_%')"
    )
    ck_count = 0
    if COOKIES_JSON.exists():
        with open(COOKIES_JSON, encoding="utf-8") as f:
            ck_count = len(json.load(f))
    _info(f"seed 검증 — users: {u_count} / sessions: {s_count} / cookies.json: {ck_count}")

    if u_count != 50:
        _abort(f"seed 검증 실패 — test_perf_ users = {u_count} (기대값 50)")
    if s_count != 50:
        _abort(f"seed 검증 실패 — sessions = {s_count} (기대값 50)")
    if ck_count != 50:
        _abort(f"seed 검증 실패 — cookies.json keys = {ck_count} (기대값 50)")
    _ok("seed 검증 통과 (users=50, sessions=50, cookies=50)")


# ── Phase 3: 서버 시작 + readiness wait ──────────────────────────────────────
def phase3_start_server(run_dir: Path) -> subprocess.Popen:
    _phase("Phase 3: uvicorn 서버 시작 + readiness wait")

    stdout_log = open(run_dir / "server_stdout.log", "w", encoding="utf-8", errors="replace")
    stderr_log = open(run_dir / "server_stderr.log", "w", encoding="utf-8", errors="replace")

    cmd = [
        PYTHON, "-m", "uvicorn", "app:app",
        "--host", "0.0.0.0",
        "--port", "8443",
        "--ssl-certfile", "whatudoin-cert.pem",
        "--ssl-keyfile", "whatudoin-key.pem",
        "--log-level", "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(_REPO_ROOT),
        stdout=stdout_log,
        stderr=stderr_log,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    _info(f"uvicorn PID: {proc.pid}")

    # readiness 폴링 (httpx 직접 사용)
    # proc을 로컬에서 종료한 뒤 re-raise — main() server_proc 할당 전에 실패해도 uvicorn 누수 없음
    import httpx
    import warnings
    warnings.filterwarnings("ignore")

    _info(f"readiness wait 시작 (최대 {READINESS_TIMEOUT}초)...")
    try:
        deadline = time.monotonic() + READINESS_TIMEOUT
        ready = False
        while time.monotonic() < deadline:
            time.sleep(READINESS_INTERVAL)
            if proc.poll() is not None:
                raise RuntimeError(
                    f"uvicorn 프로세스가 예기치 않게 종료됨 (exit {proc.returncode}). "
                    f"server_stderr.log 확인: {run_dir / 'server_stderr.log'}"
                )
            try:
                r = httpx.get(
                    f"{HTTPS_HOST}/api/notifications/count",
                    verify=False, timeout=2.0,
                )
                if r.status_code == 200:
                    ready = True
                    break
            except Exception:
                pass

        if not ready:
            raise RuntimeError(
                f"서버 readiness timeout ({READINESS_TIMEOUT}초). "
                f"server_stderr.log 확인: {run_dir / 'server_stderr.log'}"
            )
    except BaseException:
        # readiness 실패 시 uvicorn을 즉시 종료하고 re-raise
        # (main()의 server_proc 할당 전이므로 finally에서 처리 불가)
        _warn("readiness 실패 — uvicorn 종료 후 abort")
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
        raise

    _ok("서버 준비 완료")
    return proc


# ── Phase 4: sanity run ───────────────────────────────────────────────────────
def phase4_sanity(run_dir: Path) -> None:
    _phase("Phase 4: sanity run (1 VU x 30s)")

    csv_prefix = str(run_dir / "sanity_locust")
    locust_log = str(run_dir / "sanity_locust_locust.log")
    env_extras = {"WU_PERF_RESTRICT_HEAVY": "true"}
    cmd = [
        PYTHON, "-m", "locust",
        "-f", str(LOCUSTFILE),
        "--host", HTTPS_HOST,
        "--headless",
        "--users", "1",
        "--spawn-rate", "1",
        "-t", "30s",
        "--csv", csv_prefix,
        "--csv-full-history",
        "--loglevel", "INFO",
        "--logfile", locust_log,
    ]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **env_extras}
    r = subprocess.run(
        cmd, cwd=str(_REPO_ROOT),
        env=env,
        text=True, encoding="utf-8", capture_output=True,
        check=False, timeout=SANITY_TIMEOUT,
    )
    _log_output(run_dir, "sanity_locust", r.stdout, r.stderr)
    if r.returncode != 0:
        _warn(f"locust sanity 비정상 종료 (exit {r.returncode}). CSV 결과로 판정 시도...")

    _check_sanity_gate(csv_prefix, run_dir)
    _ok("Phase 4 sanity 통과 — 본 측정 진입")


def _check_sanity_gate(csv_prefix: str, run_dir: Path) -> None:
    """sanity run 통과 기준 검사.

    Aggregated Request Count == 0  → 즉시 hard fail (locust가 요청을 한 건도 안 보냄)
    view_pages 그룹 Request Count == 0 또는 실패율 >= 50% → hard fail
    event_crud 그룹 Request Count == 0 또는 실패율 >= 50% → hard fail
    upload_file: 100% 실패 허용 (로그만)
    ai_parse: 별도 평가 (로그만)
    server_stderr: connection refused / SSL 오류 10건 초과 시 abort
    """
    stats_file = Path(f"{csv_prefix}_stats.csv")
    if not stats_file.exists():
        _abort(f"sanity CSV 없음: {stats_file}")

    with open(stats_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # Aggregated 행 전체 요청 수 먼저 확인
    agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if agg is None and rows:
        agg = rows[-1]
    if agg is not None:
        agg_req = float(agg.get("Request Count", 0) or 0)
        if agg_req == 0:
            _abort(
                "sanity 실패 — Aggregated Request Count=0. "
                "locust가 요청을 한 건도 전송하지 않았습니다. "
                f"sanity_locust_locust.log / sanity_locust_stderr.log 확인: {run_dir}"
            )

    view_names = {"/", "/check", "/project-manage", "/trash",
                  "/api/events", "/api/kanban", "/calendar", "/api/checklists"}

    def _group_fail_rate(group_rows: list, name: str) -> float:
        total_req = sum(float(r.get("Request Count", 0) or 0) for r in group_rows)
        total_fail = sum(float(r.get("Failure Count", 0) or 0) for r in group_rows)
        if total_req == 0:
            _abort(
                f"sanity 실패 — {name} 그룹 Request Count=0. "
                "locust user가 해당 task를 한 번도 실행하지 않았습니다. "
                f"sanity_locust_locust.log 확인: {run_dir}"
            )
        rate = total_fail / total_req * 100
        _info(f"{name} 실패율: {rate:.1f}% (요청 {total_req:.0f} / 실패 {total_fail:.0f})")
        return rate

    vp_rows = [r for r in rows if r.get("Name") in view_names]
    ec_rows = [r for r in rows if "/api/events" in r.get("Name", "") and "[" in r.get("Name", "")]
    up_rows = [r for r in rows if "upload" in r.get("Name", "").lower()]
    ai_rows = [r for r in rows if "ai/parse" in r.get("Name", "")]

    vp_rate = _group_fail_rate(vp_rows, "view_pages")
    ec_rate = _group_fail_rate(ec_rows, "event_crud")

    # upload_file: 실패 허용
    if up_rows:
        up_req = sum(float(r.get("Request Count", 0) or 0) for r in up_rows)
        up_fail = sum(float(r.get("Failure Count", 0) or 0) for r in up_rows)
        if up_req > 0:
            _info(f"upload_file 실패율: {up_fail/up_req*100:.1f}% (PIL.verify 한계로 100% 예상)")

    # ai_parse: 별도 평가
    if ai_rows:
        ai_req = sum(float(r.get("Request Count", 0) or 0) for r in ai_rows)
        ai_fail = sum(float(r.get("Failure Count", 0) or 0) for r in ai_rows)
        if ai_req > 0:
            _info(f"ai_parse 실패율: {ai_fail/ai_req*100:.1f}% (Ollama 응답 시간 가변)")

    # server_stderr SSL/연결 오류 확인
    stderr_log = run_dir / "server_stderr.log"
    if stderr_log.exists():
        content = stderr_log.read_text(encoding="utf-8", errors="replace")
        ssl_count = sum(
            content.lower().count(pat)
            for pat in ("connection refused", "ssl handshake", "sslerror")
        )
        if ssl_count > 10:
            _abort(f"sanity 실패 — server_stderr SSL/연결 오류 {ssl_count}건 감지")

    if vp_rate >= 50:
        _abort(f"sanity 실패 — view_pages 실패율 {vp_rate:.1f}% >= 50%")
    if ec_rate >= 50:
        _abort(f"sanity 실패 — event_crud 실패율 {ec_rate:.1f}% >= 50%")


# ── Phase 5+6: 본 측정 5단계 + SSE keep-alive ────────────────────────────────
def phase5_6_measurement(run_dir: Path) -> subprocess.Popen | None:
    _phase("Phase 5: 본 측정 5단계 (1/5/10/25/50 VU)")

    stages = [
        {"vu": 1,  "rate": 1,  "heavy": True,  "label": "vu1"},
        {"vu": 5,  "rate": 5,  "heavy": True,  "label": "vu5"},
        {"vu": 10, "rate": 10, "heavy": True,  "label": "vu10"},
        {"vu": 25, "rate": 25, "heavy": False, "label": "vu25"},
        {"vu": 50, "rate": 50, "heavy": False, "label": "vu50"},
    ]

    sse_proc: subprocess.Popen | None = None

    for i, stage in enumerate(stages):
        is_last = stage["label"] == "vu50"
        # 명시적으로 환경변수 설정/해제 — os.environ 상속값도 덮어써야 함
        env_extras = {}
        if stage["heavy"]:
            env_extras["WU_PERF_RESTRICT_HEAVY"] = "true"
        else:
            env_extras["WU_PERF_RESTRICT_HEAVY"] = ""  # 빈 문자열로 덮어써 부모 셸 값 무력화

        csv_prefix = str(run_dir / f"locust_{stage['label']}")
        _info(
            f"단계 {stage['label']}: {stage['vu']} VU, "
            f"spawn-rate {stage['rate']}, 60s, "
            f"RESTRICT_HEAVY={stage['heavy']}"
        )

        if is_last:
            # Phase 6: SSE keep-alive 병렬 시작 (65s = 60s locust + 5s 버퍼)
            _phase("Phase 6: SSE keep-alive 병렬 시작 (50 VU와 동시)")
            sse_stdout = open(run_dir / "sse_stdout.log", "w", encoding="utf-8", errors="replace")
            sse_stderr = open(run_dir / "sse_stderr.log", "w", encoding="utf-8", errors="replace")
            sse_proc = subprocess.Popen(
                [
                    PYTHON, str(SSE_KEEPALIVE),
                    "--n", "50",
                    "--host", HTTPS_HOST,
                    "--duration", "65",
                    "--output-dir", str(run_dir),
                ],
                cwd=str(_REPO_ROOT),
                stdout=sse_stdout,
                stderr=sse_stderr,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            _info(f"SSE keep-alive PID: {sse_proc.pid}")

        try:
            stage_locust_log = str(run_dir / f"locust_{stage['label']}_locust.log")
            cmd = [
                PYTHON, "-m", "locust",
                "-f", str(LOCUSTFILE),
                "--host", HTTPS_HOST,
                "--headless",
                "--users", str(stage["vu"]),
                "--spawn-rate", str(stage["rate"]),
                "-t", "60s",
                "--csv", csv_prefix,
                "--csv-full-history",
                "--loglevel", "INFO",
                "--logfile", stage_locust_log,
            ]
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", **env_extras}
            r = subprocess.run(
                cmd, cwd=str(_REPO_ROOT),
                env=env,
                text=True, encoding="utf-8", capture_output=True,
                check=False, timeout=LOCUST_STAGE_TIMEOUT,
            )
            _log_output(run_dir, f"locust_{stage['label']}", r.stdout, r.stderr)
            if r.returncode != 0:
                _warn(f"locust {stage['label']} 비정상 종료 (exit {r.returncode}). 계속 진행.")
        except BaseException:
            # locust 실패 시 sse_proc 즉시 정리 후 re-raise
            if sse_proc is not None and sse_proc.poll() is None:
                _warn("locust 예외 → SSE 프로세스 조기 종료")
                sse_proc.terminate()
                try:
                    sse_proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    sse_proc.kill()
                    sse_proc.wait(timeout=3)
            raise

        st = _parse_locust_stats(csv_prefix)
        _info(
            f"  [{stage['label']}] p95={st['p95']}ms / p99={st['p99']}ms / "
            f"실패율={st['fail_rate']} / RPS={st['rps']}"
        )

        if not is_last:
            _info("단계 간 안정화 5초 대기...")
            time.sleep(5)

    # SSE 프로세스 종료 대기 (vu50 locust 종료 후)
    if sse_proc is not None and sse_proc.poll() is None:
        _info("SSE 프로세스 종료 대기 (최대 90초)...")
        try:
            sse_proc.wait(timeout=90)
            _ok("SSE keep-alive 정상 종료")
        except subprocess.TimeoutExpired:
            _warn("SSE 프로세스 90초 초과 → terminate")
            sse_proc.terminate()
            try:
                sse_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sse_proc.kill()
                sse_proc.wait(timeout=3)

    return sse_proc


# ── Phase 7: 서버 graceful shutdown ──────────────────────────────────────────
def phase7_shutdown(proc: subprocess.Popen, run_dir: Path | None) -> None:
    _phase("Phase 7: 서버 graceful shutdown")

    if proc is None or proc.poll() is not None:
        _info("서버 프로세스가 이미 종료됨")
        return

    _info(f"uvicorn 종료 시도 (PID: {proc.pid})...")
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        _warn("3초 후에도 살아있음 → kill")
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass

    time.sleep(1)

    # WAL/SHM 잔존 확인
    wal = Path(str(DB_PATH) + "-wal")
    if wal.exists():
        _warn("whatudoin.db-wal 잔존. 5초 추가 대기...")
        time.sleep(5)
        if wal.exists():
            _warn("WAL 파일 여전히 존재. DB 정합성을 확인하세요.")
        else:
            _ok("WAL 파일 소멸 확인")
    else:
        _ok("WAL 파일 없음 — 서버 정상 종료")

    # server_stderr.log 마지막 20줄 출력
    if run_dir:
        stderr_log = run_dir / "server_stderr.log"
        if stderr_log.exists():
            lines = stderr_log.read_text(encoding="utf-8", errors="replace").splitlines()
            _info("server_stderr.log (마지막 20줄):")
            for line in lines[-20:]:
                _info(f"  {line}")


# ── Phase 8: cleanup + 검증 ──────────────────────────────────────────────────
def phase8_cleanup(run_dir: Path | None = None) -> dict:
    _phase("Phase 8: cleanup + 검증")

    r = _run_py(
        CLEANUP_SCRIPT,
        env_extras={
            "WHATUDOIN_PERF_FIXTURE": "allow",
            "WHATUDOIN_DB_PATH": str(DB_PATH),
        },
        run_dir=run_dir,
    )
    _info(r.stdout.strip())
    if r.stderr.strip():
        _info(r.stderr.strip())
    if r.returncode != 0:
        _warn(f"cleanup.py 비정상 종료 (exit {r.returncode})")
        _warn("복원 절차: python _workspace/perf/scripts/restore_db.py --confirm-overwrite")

    # 검증 SELECT 3종 — sqlite3 직접
    u_count = _db_select("SELECT COUNT(*) FROM users WHERE name LIKE 'test_perf_%'")
    s_count = _db_select(
        "SELECT COUNT(*) FROM sessions WHERE user_id IN "
        "(SELECT id FROM users WHERE name LIKE 'test_perf_%')"
    )
    ev_count = _db_select("SELECT COUNT(*) FROM events WHERE title LIKE 'test_perf_evt_%'")

    _info(f"cleanup 검증 — users: {u_count} / sessions: {s_count} / events: {ev_count}")

    result = {"users": u_count, "sessions": s_count, "events": ev_count, "ok": False}
    if u_count == 0 and s_count == 0 and ev_count == 0:
        _ok("cleanup 검증 3종 통과 (모두 0)")
        result["ok"] = True
    else:
        _warn(
            f"cleanup 잔존 데이터 발견 "
            f"(users={u_count}, sessions={s_count}, events={ev_count})"
        )
        _warn("복원 절차: python _workspace/perf/scripts/restore_db.py --confirm-overwrite")

    return result


# ── Phase 9: 결과 요약 생성 ───────────────────────────────────────────────────
def phase9_summary(run_dir: Path, cleanup_result: dict) -> None:
    _phase("Phase 9: 결과 요약 생성")

    stage_labels = ["vu1", "vu5", "vu10", "vu25", "vu50"]
    stage_rows = []
    for lbl in stage_labels:
        st = _parse_locust_stats(str(run_dir / f"locust_{lbl}"))
        stage_rows.append(
            f"| {lbl} | {st['p95']} | {st['p99']} | {st['fail_rate']} | {st['rps']} |"
        )

    sanity_st = _parse_locust_stats(str(run_dir / "sanity_locust"))

    # snapshot 해시
    snap_hash = "N/A"
    meta_file = run_dir / "environment_metadata.md"
    if meta_file.exists():
        for line in meta_file.read_text(encoding="utf-8").splitlines():
            if "snapshot SHA256" in line:
                snap_hash = line.strip()
                break

    # SSE 지표
    sse_csv_files = sorted(run_dir.glob("sse_keepalive_*.csv"))
    sse_summary = "N/A (측정 데이터 없음)"
    if sse_csv_files:
        sse_csv = sse_csv_files[-1]
        with open(sse_csv, newline="", encoding="utf-8") as f:
            sse_rows = list(csv.DictReader(f))
        total_sse = len(sse_rows)
        ok_sse = sum(
            1 for r in sse_rows
            if r.get("connected", "").lower() == "true"
            and r.get("disconnected_early", "").lower() == "false"
        )
        early_disc = sum(
            1 for r in sse_rows if r.get("disconnected_early", "").lower() == "true"
        )
        ia_vals = [
            float(r["ia_p95_ms"])
            for r in sse_rows
            if r.get("ia_p95_ms") and r["ia_p95_ms"] not in ("", "0", "0.0")
        ]
        ia_p95_max = max(ia_vals) if ia_vals else 0
        sse_summary = (
            f"연결 성공 {ok_sse}/{total_sse} | "
            f"조기 끊김 {early_disc} | "
            f"inter-arrival p95 {ia_p95_max:.0f} ms"
        )

    if not cleanup_result:
        cleanup_ok_str = "skipped (seed 미완료)"
    elif cleanup_result.get("ok"):
        cleanup_ok_str = "통과 (모두 0)"
    else:
        cleanup_ok_str = (
            f"잔존 — users={cleanup_result.get('users', '?')} "
            f"sessions={cleanup_result.get('sessions', '?')} "
            f"events={cleanup_result.get('events', '?')}"
        )

    summary = f"""# M1a-7 baseline 측정 요약

생성 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
run 디렉터리: {run_dir}

## 가드 검증 결과

| 항목 | 결과 |
|------|------|
| snapshot SHA256 | {snap_hash} |
| seed users/sessions | 50 / 50 |
| cleanup 검증 | {cleanup_ok_str} |
| sanity 통과 | p95={sanity_st['p95']}ms / 실패율={sanity_st['fail_rate']} |

## 단계별 지표

| 단계 | p95 (ms) | p99 (ms) | 실패율 | RPS |
|------|---------|---------|--------|-----|
{chr(10).join(stage_rows)}

## SSE 지표 3종 (Phase 6)

{sse_summary}

> [한계] broker.py server-side timestamp 없음. inter-arrival 값의 대부분은 ~25s(ping 주기).
> 실제 이벤트 latency는 M1c-10 QueueFull 카운터 도입 후 정확 측정 가능.

## 연결 파일

- 환경 메타데이터: [environment_metadata.md](environment_metadata.md)
- 서버 로그: [server_stderr.log](server_stderr.log)
- sanity CSV: sanity_locust_stats.csv

## 비고

- upload_file 실패율: PIL.verify 한계로 높을 수 있음 (예상된 실패)
- ai_parse 실패율: Ollama 응답 시간에 따라 가변
- server-locust 동거 환경 → p95에 측정 서버 자체 부하 포함
"""

    summary_file = run_dir / "summary.md"
    summary_file.write_text(summary, encoding="utf-8")
    _ok(f"결과 요약 생성: {summary_file}")


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="WhatUdoin M1a-7 baseline 측정 runner")
    parser.add_argument(
        "--max-phase", type=int, default=9,
        help="최대 실행 phase (기본: 9). --max-phase 1 로 Phase 0/1만 dry-run 가능",
    )
    args = parser.parse_args()
    max_phase = args.max_phase

    log.info("")
    log.info("=" * 60)
    log.info("  WhatUdoin M1a-7 baseline 측정 runner")
    log.info("  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("  max-phase: %d", max_phase)
    log.info("=" * 60)

    # Ctrl-C → KeyboardInterrupt → try/finally 정리
    signal.signal(signal.SIGINT, signal.default_int_handler)

    server_proc: subprocess.Popen | None = None
    run_dir: Path | None = None
    seed_done = False
    cleanup_result: dict = {}

    try:
        phase0_preflight()
        if max_phase < 1:
            return

        run_dir = phase1_metadata()
        if max_phase < 2:
            _ok("--max-phase 1 dry-run 완료. Phase 0/1 검증 성공.")
            return

        phase2_snapshot_seed(run_dir)
        seed_done = True
        if max_phase < 3:
            return

        server_proc = phase3_start_server(run_dir)
        if max_phase < 4:
            return

        phase4_sanity(run_dir)
        if max_phase < 5:
            return

        phase5_6_measurement(run_dir)

    finally:
        # Phase 7: 서버 종료 (항상)
        if server_proc is not None:
            phase7_shutdown(server_proc, run_dir)

        # Phase 8: cleanup (seed 완료 시만)
        if seed_done:
            cleanup_result = phase8_cleanup(run_dir)

        # Phase 9: 결과 요약 (best-effort)
        if run_dir is not None and max_phase >= 9:
            try:
                phase9_summary(run_dir, cleanup_result)
            except Exception as e:
                _warn(f"Phase 9 요약 생성 실패 (비치명): {e}")

    log.info("")
    log.info("=" * 60)
    log.info("  M1a-7 baseline 측정 완료")
    if run_dir:
        log.info("  결과: %s", run_dir)
    log.info("=" * 60)
    log.info("")
    if run_dir:
        log.info("다음 단계:")
        log.info("  1. %s/summary.md 검토", run_dir)
        log.info("  2. 단계별 locust_vu*.csv p95/실패율 분석")
        log.info("  3. sse_keepalive_*.csv SSE 지표 3종 확인")


if __name__ == "__main__":
    main()
