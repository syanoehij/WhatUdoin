"""
M1a-11 + M1a-12 통합 측정 runner (Python orchestrator)

M1a-7 runner lifecycle 패턴 재사용 (copy, not import).
운영 코드(app.py / database.py / auth.py / crypto.py / backup.py /
          llm_parser.py / mcp_server.py / templates/ / static/) 변경 0건.

Phase 0: pre-flight 점검 (포트 / locust / httpx / Node / Playwright / 인증서 / WAL)
Phase 1: run 디렉터리 생성 + 환경 메타데이터 캡처 (Playwright/Node 버전 포함)
Phase 2: DB snapshot + seed (50/50/50 검증)
Phase 3: uvicorn 서버 시작 + readiness wait
Phase 4: M1a-11 — Playwright 측정 spec (perf_m1a11.spec.js) 실행 + sanity gate
Phase 5: M1a-12 — 메인 Playwright 회귀 (lazy-load 관련 phase 한정)
Phase 6: 서버 graceful shutdown   [always via finally]
Phase 7: cleanup + 검증            [always via finally, seed_done guard]
Phase 8: summary.md 생성           [best-effort in finally]

Usage:
  python _workspace/perf/scripts/run_baseline_m1a11.py
  python _workspace/perf/scripts/run_baseline_m1a11.py --max-phase 1   # dry-run
  python _workspace/perf/scripts/run_baseline_m1a11.py --skip-m1a12    # Phase 4만
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
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
# __file__ = <repo>/_workspace/perf/scripts/run_baseline_m1a11.py
# parents[0] = scripts/, [1] = perf/, [2] = _workspace/, [3] = <repo root>
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[2]

PYTHON = sys.executable

# Windows에서 subprocess는 .cmd 확장자를 자동으로 찾지 못하므로 명시적 탐색
def _find_npx() -> str:
    """npx 실행파일 경로 반환. Windows는 npx.cmd 우선."""
    import shutil
    # shutil.which는 PATHEXT를 고려해 .cmd도 찾음
    found = shutil.which("npx")
    if found:
        return found
    # fallback
    return "npx"

NPX = _find_npx()

SEED_SCRIPT = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "seed_users.py"
CLEANUP_SCRIPT = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "cleanup.py"
SNAPSHOT_SCRIPT = _REPO_ROOT / "_workspace" / "perf" / "scripts" / "snapshot_db.py"
PLAYWRIGHT_CONFIG = _REPO_ROOT / "playwright.config.js"
DB_PATH = _REPO_ROOT / "whatudoin.db"
COOKIES_JSON = _REPO_ROOT / "_workspace" / "perf" / "fixtures" / "session_cookies.json"
BASELINE_DIR = _REPO_ROOT / "_workspace" / "perf" / "baseline_2026-05-09"
HTTPS_HOST = "https://localhost:8443"

# M1a-11 spec이 결과를 저장하는 고정 위치 (spec 내부 OUT_DIR)
M1A11_SPEC_OUT = BASELINE_DIR / "m1a11_results"

READINESS_TIMEOUT = 30   # seconds
READINESS_INTERVAL = 2   # seconds

# Playwright 타임아웃
PLAYWRIGHT_M1A11_TIMEOUT = 300   # seconds (spec 5~6분 여유)
PLAYWRIGHT_M1A12_TIMEOUT = 1200  # seconds (focused 8개 spec, 각 1~2분)

# M1a-12: lazy-load 영향 phase 한정 (frontend-dev 변경 파일 기준)
# base.html, event-modal.js, check.html, home.html, project_manage.html,
# trash.html, notice_history.html 변경 영향 + asset cache spec
M1A12_SPECS = [
    "tests/phase33_doc_linebreak.spec.js",
    "tests/phase33_toc_resizer.spec.js",
    "tests/phase33_dark_theme_codeblock.spec.js",
    "tests/phase33_pinpoint_all.spec.js",
    "tests/phase34_tiptap_migration.spec.js",
    "tests/phase37_asset_cache.spec.js",
    "tests/phase37_stage2_static_cache.spec.js",
    "tests/phase37_stage3_static_cleanup.spec.js",
    "tests/phase38_doc_image_resize.spec.js",
    "tests/phase52_check_load_perf.spec.js",
    "tests/phase53_paste_table_check_hit.spec.js",
]

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


# ── subprocess 헬퍼 (M1a-7에서 복사) ─────────────────────────────────────────
def _log_output(run_dir: Path, basename: str, stdout: str, stderr: str) -> None:
    """stdout/stderr를 run_dir에 저장. 동명 파일이 있으면 _2, _3 suffix."""
    for kind, text in (("stdout", stdout), ("stderr", stderr)):
        base = run_dir / f"{basename}_{kind}.log"
        if base.exists():
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


def _run_npx(
    *args: str,
    env_extras: dict | None = None,
    timeout: int | None = None,
    run_dir: Path | None = None,
    log_basename: str | None = None,
) -> subprocess.CompletedProcess:
    """npx 명령 실행. stdout/stderr를 run_dir에 저장."""
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "NODE_OPTIONS": "--no-warnings",
    }
    if env_extras:
        env.update(env_extras)
    cmd = [NPX] + list(args)
    result = subprocess.run(
        cmd,
        env=env,
        cwd=str(_REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if run_dir is not None and log_basename is not None:
        _log_output(run_dir, log_basename, result.stdout, result.stderr)
    return result


# ── SQLite 직접 쿼리 (M1a-7에서 복사) ────────────────────────────────────────
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


# ── Phase 0: pre-flight ───────────────────────────────────────────────────────
def phase0_preflight() -> dict:
    """pre-flight 점검. Node/Playwright 버전을 반환 (Phase 1 메타데이터용)."""
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
        _warn("Ollama 11434 포트 없음 — Playwright spec의 ai_parse 관련 기능 실패 예상")

    # locust / httpx 설치 확인
    import importlib.util
    for pkg in ("locust", "httpx"):
        if importlib.util.find_spec(pkg) is None:
            _abort(f"{pkg} 미설치. pip install {pkg}")
    _ok("locust, httpx 설치 확인")

    # Node 버전 확인
    node_r = subprocess.run(
        ["node", "--version"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    node_ver = node_r.stdout.strip() if node_r.returncode == 0 else None
    if node_ver is None:
        _abort("node 미설치 또는 PATH 미등록. Node.js를 설치하세요.")
    _ok(f"Node 버전: {node_ver}")

    # Playwright 버전 확인
    pw_r = subprocess.run(
        [NPX, "playwright", "--version"],
        capture_output=True, text=True, encoding="utf-8", check=False,
        cwd=str(_REPO_ROOT),
    )
    pw_ver = (pw_r.stdout + pw_r.stderr).strip() if pw_r.returncode == 0 else None
    if pw_ver is None:
        _abort("npx playwright --version 실패. npm install 또는 npx playwright install이 필요합니다.")
    _ok(f"Playwright 버전: {pw_ver}")

    # playwright.config.js 존재 확인
    if not PLAYWRIGHT_CONFIG.exists():
        _abort(f"playwright.config.js 없음: {PLAYWRIGHT_CONFIG}")
    _ok(f"playwright.config.js 존재 확인: {PLAYWRIGHT_CONFIG}")

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

    # M1a-11 spec 파일 확인
    spec_file = _REPO_ROOT / "tests" / "perf_m1a11.spec.js"
    if not spec_file.exists():
        _abort(f"M1a-11 spec 파일 없음: {spec_file}")
    _ok(f"M1a-11 spec 파일 확인: {spec_file}")

    # 환경변수 자체 설정
    os.environ["WHATUDOIN_PERF_FIXTURE"] = "allow"
    _ok("Phase 0 통과")

    return {"node_ver": node_ver, "pw_ver": pw_ver}


# ── Phase 1: run 디렉터리 + 환경 메타데이터 ──────────────────────────────────
def phase1_metadata(env_versions: dict) -> Path:
    _phase("Phase 1: run 디렉터리 생성 + 환경 메타데이터 캡처")

    ts = datetime.now().strftime("%H%M%S")
    run_dir = BASELINE_DIR / f"m1a11_run_{ts}"
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

    # RAM
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

    import platform
    uname = platform.uname()

    db_size_kb = round(DB_PATH.stat().st_size / 1024, 1)
    row_counts = _db_counts()

    start_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db_rows_lines = "\n".join(
        f"| {t} rows | {cnt} |" for t, cnt in row_counts.items()
    )

    meta = f"""# 환경 메타데이터 — M1a-11 baseline

측정 시작: {start_time}
run 디렉터리: {run_dir}

## 시스템

| 항목 | 값 |
|------|-----|
| OS | {uname.system} {uname.release} {uname.version[:40]} |
| CPU | {uname.processor or uname.machine} ({cpu_count} logical) |
| RAM | {ram_gb} |
| Python | {py_version} |
| Node | {env_versions.get('node_ver', 'N/A')} |
| Playwright | {env_versions.get('pw_ver', 'N/A')} |
| locust | {locust_ver} |
| httpx | {httpx_ver} |

## DB 상태 (측정 시작 전)

| 항목 | 값 |
|------|-----|
| whatudoin.db 크기 | {db_size_kb} KB |
{db_rows_lines}

## 측정 환경 정책

| 항목 | 상태 |
|------|------|
| 서버 바인드 | 0.0.0.0:8443 (TLS) — 192.168.0.18:8443 접근 포함 |
| Playwright BASE | https://192.168.0.18:8443 (IP whitelist 자동 로그인) |
| 캐시 비활성화 | CDP Network.setCacheDisabled(true) |
| CPU throttle | CDP Emulation.setCPUThrottlingRate(rate=4) |
| seed 쿠키 | session_cookies.json (50건) — IP whitelist 자동 로그인 보조 |
| M1a-7 baseline | {BASELINE_DIR}/run_181951/ |
"""
    meta_file = run_dir / "environment_metadata.md"
    meta_file.write_text(meta, encoding="utf-8")
    _ok(f"환경 메타데이터 기록: {meta_file}")

    return run_dir


# ── Phase 2: snapshot + seed (M1a-7에서 복사) ────────────────────────────────
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

    # seed 검증 — sqlite3 직접 SELECT
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


# ── Phase 3: 서버 시작 + readiness wait (M1a-7에서 복사) ─────────────────────
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


# ── Phase 4: M1a-11 Playwright 측정 spec 실행 ────────────────────────────────
def phase4_m1a11_playwright(run_dir: Path) -> dict:
    _phase("Phase 4: M1a-11 — Playwright 측정 spec 실행")

    json_out = run_dir / "m1a11_playwright.json"
    pw_test_output = run_dir / "playwright_test_output"
    pw_test_output.mkdir(exist_ok=True)

    _info("npx playwright test tests/perf_m1a11.spec.js --reporter=json 실행...")
    _info(f"timeout: {PLAYWRIGHT_M1A11_TIMEOUT}초")

    env_extras = {
        "PLAYWRIGHT_JSON_OUTPUT_NAME": str(json_out),
    }

    r = _run_npx(
        "playwright", "test", "tests/perf_m1a11.spec.js",
        "--reporter=json",
        env_extras=env_extras,
        timeout=PLAYWRIGHT_M1A11_TIMEOUT,
        run_dir=run_dir,
        log_basename="m1a11_playwright",
    )

    # stderr 전용 파일 저장 (log_basename이 이미 저장했지만 명시적으로 중복 없음)
    # _run_npx에서 이미 m1a11_playwright_stdout.log / m1a11_playwright_stderr.log 저장됨

    _info(f"playwright exit code: {r.returncode}")

    # JSON 결과 파일 파싱
    pw_result = _parse_playwright_json(json_out, run_dir)

    # spec이 OUT_DIR에 저장한 결과를 run_dir로 복사
    results_copy_dir = run_dir / "m1a11_results_copy"
    if M1A11_SPEC_OUT.exists():
        try:
            shutil.copytree(str(M1A11_SPEC_OUT), str(results_copy_dir), dirs_exist_ok=True)
            _ok(f"M1a-11 spec 결과를 run_dir로 복사: {results_copy_dir}")
        except Exception as e:
            _warn(f"spec 결과 복사 실패 (비치명): {e}")
    else:
        _warn(f"M1a-11 spec OUT_DIR 없음: {M1A11_SPEC_OUT} — spec이 결과를 저장하지 않았을 수 있음")

    # sanity gate 평가
    _check_m1a11_sanity_gate(pw_result, results_copy_dir if results_copy_dir.exists() else None)

    return pw_result


def _parse_playwright_json(json_out: Path, run_dir: Path) -> dict:
    """Playwright JSON 결과 파싱.

    PLAYWRIGHT_JSON_OUTPUT_NAME이 효과 없을 때를 대비해 fallback:
    json_out이 없으면 run_dir/<basename>_stdout.log를 JSON으로 시도.
    """
    data = None

    if json_out.exists():
        try:
            data = json.loads(json_out.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            _warn(f"Playwright JSON 파싱 실패 ({json_out.name}): {e}")

    if data is None:
        # fallback: stdout.log에서 JSON 탐색
        # _run_npx가 저장한 <log_basename>_stdout.log (예: m1a11_playwright_stdout.log)
        # json_out stem → log_basename 역추출 (m1a11_playwright → m1a11_playwright_stdout.log)
        stem = json_out.stem  # e.g. "m1a11_playwright"
        stdout_log = run_dir / f"{stem}_stdout.log" if run_dir else None
        if stdout_log and stdout_log.exists():
            raw = stdout_log.read_text(encoding="utf-8", errors="replace").strip()
            try:
                data = json.loads(raw)
                _info(f"Playwright JSON — PLAYWRIGHT_JSON_OUTPUT_NAME 없음, stdout.log에서 파싱 성공")
            except Exception:
                # stdout에 progress 출력이 섞여 있을 수 있음 — JSON 블록만 추출 시도
                import re
                m = re.search(r'(\{.*\})', raw, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group(1))
                        _info("Playwright JSON — stdout에서 JSON 블록 추출 성공")
                    except Exception as e2:
                        _warn(f"stdout JSON 추출 실패: {e2}")

    if data is None:
        _warn(f"Playwright JSON 결과를 찾지 못함: {json_out}")
        return {"found": False, "passed": 0, "failed": 0, "skipped": 0, "failed_tests": []}

    # Playwright JSON 구조: { stats: { expected, unexpected, ... }, suites: [...] }
    stats = data.get("stats", {})
    passed = stats.get("expected", 0)
    failed = stats.get("unexpected", 0)
    skipped = stats.get("skipped", 0)

    # 실패 테스트 목록 추출
    failed_tests = []

    def _walk_suites(suites):
        for suite in suites:
            for spec in suite.get("specs", []):
                for test in spec.get("tests", []):
                    for result in test.get("results", []):
                        if result.get("status") in ("failed", "timedOut"):
                            failed_tests.append({
                                "title": spec.get("title", "?"),
                                "file": suite.get("file", "?"),
                                "status": result.get("status"),
                            })
            _walk_suites(suite.get("suites", []))

    _walk_suites(data.get("suites", []))

    _info(f"Playwright 결과 — pass: {passed} / fail: {failed} / skip: {skipped}")
    return {
        "found": True,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "failed_tests": failed_tests,
        "raw": data,
    }


def _check_m1a11_sanity_gate(pw_result: dict, results_copy_dir: Path | None) -> None:
    """M1a-11 sanity gate:
    - Playwright pass 수가 0이면 abort (spec 전체 실패)
    - m1a11_asset_downloads.json에서 check-detail-first['wu-editor'] == 0이면 abort
    """
    # Gate 1: JSON 결과 없거나 pass 수 = 0 이면 abort
    # "found=False" = JSON 파일 없음 = Playwright 실행 자체 실패 → abort
    # "found=True, passed=0" = spec 전체 실패 → abort
    if not pw_result.get("found") or pw_result["passed"] == 0:
        reason = (
            "JSON 결과를 찾을 수 없음 (Playwright 실행 실패 가능성)"
            if not pw_result.get("found")
            else f"pass 수 = 0 (fail={pw_result['failed']}, skip={pw_result['skipped']})"
        )
        _abort(
            f"[SANITY GATE] M1a-11 Playwright 이상: {reason}. "
            "m1a11_playwright_stderr.log / m1a11_playwright_stdout.log를 확인하세요."
        )

    # Gate 2: 자산 다운로드 횟수 (m1a11_asset_downloads.json)
    if results_copy_dir is not None:
        downloads_json = results_copy_dir / "m1a11_asset_downloads.json"
        if downloads_json.exists():
            try:
                downloads = json.loads(downloads_json.read_text(encoding="utf-8"))
                check_first = downloads.get("check-detail-first", {})
                wu_count = check_first.get("wu-editor", None)
                if wu_count is not None and wu_count == 0:
                    _abort(
                        "[SANITY GATE] check-detail-first['wu-editor'] = 0. "
                        "모든 페이지에서 자산 다운로드 0건 — 측정 spec 결함 가능성. "
                        "네트워크 리스너 설정을 확인하세요. abort."
                    )
                _info(f"sanity gate 통과 — check-detail-first wu-editor 다운로드: {wu_count}")
            except Exception as e:
                _warn(f"m1a11_asset_downloads.json 파싱 실패 (비치명): {e}")
        else:
            _warn(f"m1a11_asset_downloads.json 없음: {downloads_json} — sanity gate Gate 2 생략")

    _ok("M1a-11 sanity gate 통과")


# ── Phase 5: M1a-12 메인 Playwright 회귀 ─────────────────────────────────────
def phase5_m1a12_regression(run_dir: Path) -> dict:
    _phase("Phase 5: M1a-12 — 메인 Playwright 회귀 (lazy-load 관련 phase 한정)")

    # 존재하는 spec만 필터
    existing_specs = [s for s in M1A12_SPECS if (_REPO_ROOT / s).exists()]
    missing_specs = [s for s in M1A12_SPECS if not (_REPO_ROOT / s).exists()]
    if missing_specs:
        _warn(f"존재하지 않는 spec 제외: {missing_specs}")

    if not existing_specs:
        _warn("실행할 spec이 없음 — Phase 5 생략")
        return {"found": False, "passed": 0, "failed": 0, "skipped": 0, "failed_tests": [], "scope": []}

    _info(f"회귀 대상 spec ({len(existing_specs)}개):")
    for s in existing_specs:
        _info(f"  {s}")
    _info(f"timeout: {PLAYWRIGHT_M1A12_TIMEOUT}초")

    json_out = run_dir / "m1a12_playwright.json"
    env_extras = {
        "PLAYWRIGHT_JSON_OUTPUT_NAME": str(json_out),
    }

    cmd_args = (
        ["playwright", "test"] +
        existing_specs +
        ["--reporter=json"]
    )
    r = _run_npx(
        *cmd_args,
        env_extras=env_extras,
        timeout=PLAYWRIGHT_M1A12_TIMEOUT,
        run_dir=run_dir,
        log_basename="m1a12_playwright",
    )

    _info(f"M1a-12 playwright exit code: {r.returncode}")

    pw_result = _parse_playwright_json(json_out, run_dir)
    pw_result["scope"] = existing_specs

    if pw_result.get("failed", 0) > 0:
        _warn(f"M1a-12 회귀 실패 {pw_result['failed']}건:")
        for ft in pw_result["failed_tests"]:
            _warn(f"  [{ft['status']}] {ft['file']} :: {ft['title']}")
    else:
        _ok(f"M1a-12 회귀 통과 — pass: {pw_result['passed']}, skip: {pw_result['skipped']}")

    return pw_result


# ── Phase 6: 서버 graceful shutdown (M1a-7 phase7에서 복사) ──────────────────
def phase6_shutdown(proc: subprocess.Popen, run_dir: Path | None) -> None:
    _phase("Phase 6: 서버 graceful shutdown")

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


# ── Phase 7: cleanup + 검증 (M1a-7 phase8에서 복사) ─────────────────────────
def phase7_cleanup(run_dir: Path | None = None) -> dict:
    _phase("Phase 7: cleanup + 검증")

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

    # 검증 SELECT 3종
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


# ── Phase 8: summary.md 생성 ─────────────────────────────────────────────────
def phase8_summary(
    run_dir: Path,
    cleanup_result: dict,
    pw_m1a11: dict,
    pw_m1a12: dict,
) -> None:
    _phase("Phase 8: summary.md 생성")

    # snapshot 해시
    snap_hash = "N/A"
    meta_file = run_dir / "environment_metadata.md"
    if meta_file.exists():
        for line in meta_file.read_text(encoding="utf-8").splitlines():
            if "snapshot SHA256" in line:
                snap_hash = line.strip()
                break

    # cleanup 결과 문자열
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

    # §5-1 4단계 측정 표 구성
    stage4_table = "결과 없음"
    results_copy = run_dir / "m1a11_results_copy"
    stage4_json_path = results_copy / "m1a11_4stage.json"
    asset_json_path = results_copy / "m1a11_asset_downloads.json"
    viewer_json_path = results_copy / "m1a11_viewer_regression.json"

    if stage4_json_path.exists():
        try:
            stage4_data = json.loads(stage4_json_path.read_text(encoding="utf-8"))
            rows = []
            for page_key, v in stage4_data.items():
                s1 = v.get("stage1_download_ms", "N/A")
                s2 = "ready" if v.get("stage2_wu_assets_ready") else "N/A"
                s3 = v.get("stage3_create_duration_ms", "N/A")
                s4 = v.get("stage4_prosemirror_visible_ms", "N/A")
                rows.append(f"| {page_key} | {s1} | {s2} | {s3} | {s4} |")
            stage4_table = (
                "| 페이지 | Stage1 다운로드(ms) | Stage2 WuAssets ready | Stage3 create(ms) | Stage4 ProseMirror(ms) |\n"
                "|--------|--------------------|-----------------------|-------------------|------------------------|\n"
                + "\n".join(rows)
            )
        except Exception as e:
            stage4_table = f"파싱 실패: {e}"

    # 자산 다운로드 표
    asset_table = "결과 없음"
    if asset_json_path.exists():
        try:
            asset_data = json.loads(asset_json_path.read_text(encoding="utf-8"))
            rows = []
            for page_key, v in asset_data.items():
                wu = v.get("wu-editor", "N/A")
                mm = v.get("mermaid", "N/A")
                tt = v.get("tiptap", "N/A")
                hl = v.get("highlight", "N/A")
                mode = v.get("mode", "")
                rows.append(f"| {page_key} ({mode}) | {wu} | {mm} | {tt} | {hl} |")
            asset_table = (
                "| 페이지 | wu-editor | mermaid | tiptap | highlight |\n"
                "|--------|-----------|---------|--------|----------|\n"
                + "\n".join(rows)
            )
        except Exception as e:
            asset_table = f"파싱 실패: {e}"

    # viewer 회귀 결과
    viewer_regression = "결과 없음"
    if viewer_json_path.exists():
        try:
            vr = json.loads(viewer_json_path.read_text(encoding="utf-8"))
            rows = [f"| {k} | {v} |" for k, v in vr.items()]
            viewer_regression = (
                "| 항목 | 결과 |\n|------|------|\n" + "\n".join(rows)
            )
        except Exception as e:
            viewer_regression = f"파싱 실패: {e}"

    # M1a-11 결과
    m1a11_pass = pw_m1a11.get("passed", 0)
    m1a11_fail = pw_m1a11.get("failed", 0)
    m1a11_skip = pw_m1a11.get("skipped", 0)
    m1a11_status = "PASS" if m1a11_fail == 0 and m1a11_pass > 0 else ("FAIL" if m1a11_fail > 0 else "UNKNOWN")

    # M1a-12 결과
    m1a12_pass = pw_m1a12.get("passed", 0)
    m1a12_fail = pw_m1a12.get("failed", 0)
    m1a12_skip = pw_m1a12.get("skipped", 0)
    m1a12_fail_list = pw_m1a12.get("failed_tests", [])
    m1a12_scope = pw_m1a12.get("scope", M1A12_SPECS)
    m1a12_fail_str = (
        "\n".join(f"  - [{ft['status']}] {ft['file']} :: {ft['title']}" for ft in m1a12_fail_list)
        if m1a12_fail_list else "  없음"
    )

    # M1a 종료 게이트 평가
    gate_stage_baseline = "N/A (M1a-7 수치와 수동 비교 필요)"
    gate_sse_sep = "N/A (M1a-7에서 측정됨 — 본 run 제외)"
    gate_4stage = "PASS" if stage4_json_path.exists() else "FAIL (4stage JSON 없음)"
    gate_viewer_reg = "PASS" if m1a11_fail == 0 and m1a11_pass > 0 else "FAIL"

    summary = f"""# M1a-11 baseline 측정 요약

생성 일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
run 디렉터리: {run_dir}
M1a-7 baseline: {BASELINE_DIR}/run_181951/ (비교 기준)

## 가드 검증 결과

| 항목 | 결과 |
|------|------|
| snapshot SHA256 | {snap_hash} |
| seed users/sessions/cookies | 50 / 50 / 50 |
| cleanup 검증 | {cleanup_ok_str} |

## §5-1 4단계 측정 표 (M1a-11)

{stage4_table}

> Stage1: 자산 다운로드 시간(ms) / Stage2: WuAssets.isReady() / Stage3: WUEditor.create() 소요(ms) / Stage4: ProseMirror 등장(ms from nav start)

## 자산 다운로드 표 (페이지별 다운로드 횟수)

{asset_table}

> 목록 모드 0건 / 상세 진입 1건 (두 번째 진입 delta=0) 검증

## viewer 회귀 결과 (M1a-11)

{viewer_regression}

## M1a-11 Playwright spec 결과

| 항목 | 값 |
|------|-----|
| pass | {m1a11_pass} |
| fail | {m1a11_fail} |
| skip | {m1a11_skip} |
| 종합 | {m1a11_status} |

## M1a-12 메인 Playwright 회귀

회귀 대상 ({len(m1a12_scope)}개 spec — lazy-load 및 viewer 관련 phase 한정):
{chr(10).join('- ' + s for s in m1a12_scope)}

| 항목 | 값 |
|------|-----|
| pass | {m1a12_pass} |
| fail | {m1a12_fail} |
| skip | {m1a12_skip} |

실패 목록:
{m1a12_fail_str}

> 전체 회귀 제외 사유: 변경 영향 범위(base.html, check.html, home.html,
> project_manage.html, trash.html, event-modal.js, notice_history.html)에
> 해당하는 viewer/doc/asset-cache phase spec 11개로 한정.

## M1a 종료 게이트 평가

| 게이트 | 상태 |
|--------|------|
| 단계별 baseline (M1a-7 대비) | {gate_stage_baseline} |
| SSE 분리 측정 | {gate_sse_sep} |
| §5-1 4단계 측정 완료 | {gate_4stage} |
| viewer 회귀 0건 | {gate_viewer_reg} |

## 연결 파일

- 환경 메타데이터: [environment_metadata.md](environment_metadata.md)
- 서버 로그: [server_stderr.log](server_stderr.log)
- M1a-11 Playwright JSON: [m1a11_playwright.json](m1a11_playwright.json)
- M1a-12 Playwright JSON: [m1a12_playwright.json](m1a12_playwright.json)
- M1a-11 spec 결과 복사본: [m1a11_results_copy/](m1a11_results_copy/)
- Playwright HTML report: playwright-report/ (repo root)
"""

    summary_file = run_dir / "summary.md"
    summary_file.write_text(summary, encoding="utf-8")
    _ok(f"summary.md 생성: {summary_file}")


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="WhatUdoin M1a-11 + M1a-12 통합 측정 runner")
    parser.add_argument(
        "--max-phase", type=int, default=8,
        help="최대 실행 phase (기본: 8). --max-phase 1 로 Phase 0/1만 dry-run 가능",
    )
    parser.add_argument(
        "--skip-m1a12", action="store_true",
        help="Phase 5 (M1a-12 회귀) 생략. Phase 4만 실행.",
    )
    args = parser.parse_args()
    max_phase = args.max_phase

    log.info("")
    log.info("=" * 60)
    log.info("  WhatUdoin M1a-11 + M1a-12 통합 측정 runner")
    log.info("  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("  max-phase: %d", max_phase)
    if args.skip_m1a12:
        log.info("  --skip-m1a12: Phase 5 생략")
    log.info("=" * 60)

    # Ctrl-C → KeyboardInterrupt → try/finally 정리
    signal.signal(signal.SIGINT, signal.default_int_handler)

    server_proc: subprocess.Popen | None = None
    run_dir: Path | None = None
    seed_done = False
    cleanup_result: dict = {}
    pw_m1a11: dict = {}
    pw_m1a12: dict = {}

    try:
        env_versions = phase0_preflight()
        if max_phase < 1:
            return

        run_dir = phase1_metadata(env_versions)
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

        pw_m1a11 = phase4_m1a11_playwright(run_dir)
        if max_phase < 5:
            return

        if not args.skip_m1a12:
            pw_m1a12 = phase5_m1a12_regression(run_dir)
        else:
            _info("--skip-m1a12 지정 — Phase 5 건너뜀")
            pw_m1a12 = {"found": False, "passed": 0, "failed": 0, "skipped": 0, "failed_tests": [], "scope": [], "skipped_reason": "--skip-m1a12"}

    finally:
        # Phase 6: 서버 종료 (항상)
        if server_proc is not None:
            phase6_shutdown(server_proc, run_dir)

        # Phase 7: cleanup (seed 완료 시만)
        if seed_done:
            cleanup_result = phase7_cleanup(run_dir)

        # Phase 8: summary (best-effort)
        if run_dir is not None and max_phase >= 8:
            try:
                phase8_summary(run_dir, cleanup_result, pw_m1a11, pw_m1a12)
            except Exception as e:
                _warn(f"Phase 8 summary 생성 실패 (비치명): {e}")

    log.info("")
    log.info("=" * 60)
    log.info("  M1a-11 + M1a-12 측정 완료")
    if run_dir:
        log.info("  결과: %s", run_dir)
    log.info("=" * 60)
    log.info("")
    if run_dir:
        log.info("다음 단계:")
        log.info("  1. %s/summary.md 검토", run_dir)
        log.info("  2. m1a11_results_copy/m1a11_4stage.json — 4단계 수치 확인")
        log.info("  3. m1a11_results_copy/m1a11_asset_downloads.json — 자산 다운로드 검증")
        log.info("  4. m1a12_playwright.json — 회귀 pass/fail 확인")


if __name__ == "__main__":
    main()
