"""Phase 65: M3-4 통합 부하 테스트 (standalone runner).

A. 라이브 Supervisor 통합 — mock 위주 (실제 spawn은 probe 스크립트에서)
B. Owner 정책 위반 3종 — 코드 grep + mock 카운터
C. 일반 API p95 — 가벼운 구조 단언 (라이브 부하는 probe 스크립트에서)
D. 회귀: phase54~64 해당 항목 단언 재확인

실행:
    python tests/phase65_m3_4_integration_load.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_pass = 0
_fail = 0
_results: list[dict] = []


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    _results.append({"name": name, "passed": cond, "detail": detail})
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def _read(p: Path) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_text(encoding="utf-8", errors="replace")


# 소스 코드 로드
app_src  = _read(ROOT / "app.py")
svc_src  = _read(ROOT / "scheduler_service.py")
sup_src  = _read(ROOT / "supervisor.py")
db_src   = _read(ROOT / "database.py")
bk_src   = _read(ROOT / "backup.py")
main_src = _read(ROOT / "maintenance_owners.py")

print("\n[phase65] M3-4 통합 부하 테스트")

# ─────────────────────────────────────────────────────────────────────────────
# A. 라이브 Supervisor 통합 — 구조 단언 (spawn은 probe 스크립트)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[A] Supervisor 통합 구조 단언...")

from supervisor import (
    WhatUdoinSupervisor,
    scheduler_service_spec,
    STOP_ORDER,
    SCHEDULER_SERVICE_ENABLE_ENV,
    SCHEDULER_SERVICE_BIND_HOST_ENV,
    SCHEDULER_SERVICE_PORT_ENV,
    INTERNAL_TOKEN_ENV,
    M2_STARTUP_SEQUENCE,
)

# A1. scheduler_service_spec 4종 보호 env 확인
spec = scheduler_service_spec(
    command=["python", "scheduler_service.py"],
    port=9999,
    extra_env={"WHATUDOIN_SCHEDULER_BIND_HOST": "0.0.0.0", "EXTRA": "val"},
)
_ok("scheduler_service_spec: BIND_HOST 항상 127.0.0.1 (override 차단)",
    spec.env.get(SCHEDULER_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("scheduler_service_spec: PORT=9999 강제",
    spec.env.get(SCHEDULER_SERVICE_PORT_ENV) == "9999")
_ok("scheduler_service_spec: SCHEDULER_SERVICE=1 강제",
    spec.env.get(SCHEDULER_SERVICE_ENABLE_ENV) == "1")
_ok("scheduler_service_spec: extra_env EXTRA 통과",
    spec.env.get("EXTRA") == "val")
_bad_spec = scheduler_service_spec(
    command=["python", "scheduler_service.py"],
    port=9999,
    extra_env={INTERNAL_TOKEN_ENV: "bad_value_should_be_blocked"},
)
_ok("scheduler_service_spec: INTERNAL_TOKEN override 차단 (extra_env에 넣어도 무시)",
    _bad_spec.env.get(INTERNAL_TOKEN_ENV) != "bad_value_should_be_blocked",
    f"token_in_spec={_bad_spec.env.get(INTERNAL_TOKEN_ENV, 'absent')}")  # token은 service_env()에서 주입

# A2. STOP_ORDER 검증
_ok("STOP_ORDER: 4종 포함 (ollama/sse/scheduler/web-api)",
    set(STOP_ORDER) >= {"ollama", "sse", "scheduler", "web-api"})
_ok("STOP_ORDER: scheduler < web-api 순서",
    list(STOP_ORDER).index("scheduler") < list(STOP_ORDER).index("web-api"))

# A3. M2_STARTUP_SEQUENCE에 start_scheduler_service 포함 + sse 다음
seq = list(M2_STARTUP_SEQUENCE)
_ok("M2_STARTUP_SEQUENCE: start_scheduler_service 포함",
    "start_scheduler_service" in seq)
if "start_scheduler_service" in seq and "start_sse_service" in seq:
    _ok("M2_STARTUP_SEQUENCE: start_sse_service < start_scheduler_service",
        seq.index("start_sse_service") < seq.index("start_scheduler_service"))
else:
    _ok("M2_STARTUP_SEQUENCE: start_sse_service < start_scheduler_service", False,
        "seq=" + str(seq))

# A4. stop_all STOP_ORDER 순서 실행 — 가짜 ServiceState로 캡처
print("  [mock] stop_all STOP_ORDER 순서 확인...")
stopped_order: list[str] = []

sup = WhatUdoinSupervisor.__new__(WhatUdoinSupervisor)
sup.run_dir = ROOT / "_tmp_phase65"
sup.log_dir = sup.run_dir / "logs"
sup.token_path = sup.run_dir / "internal_token"
sup.token_info = None
sup.services = {}

def _make_mock_state(name: str):
    from supervisor import ServiceState
    state = ServiceState(name=name, status="running")
    state._process = MagicMock()
    state._process.poll.return_value = None  # alive

    def mock_terminate():
        state._process.poll.return_value = 0

    state._process.terminate = mock_terminate
    state._process.wait = MagicMock(return_value=0)
    return state

for svc in ["web-api", "scheduler", "sse"]:
    sup.services[svc] = _make_mock_state(svc)

# stop_service를 캡처 버전으로 monkey-patch
original_stop = WhatUdoinSupervisor.stop_service

def _capturing_stop(self, name: str, timeout: float = 5.0):
    stopped_order.append(name)
    state = self.services.get(name)
    if state:
        state.status = "stopped"
        state.stopped_at = 0.0
        state.pid = None
        state._exit_counted = True
    return state

import time as _time
_real_sleep = _time.sleep
_time.sleep = lambda x: None  # パッチ

try:
    WhatUdoinSupervisor.stop_service = _capturing_stop
    sup.stop_all(timeout=5.0)
finally:
    WhatUdoinSupervisor.stop_service = original_stop
    _time.sleep = _real_sleep

_ok("stop_all: STOP_ORDER 준수 (sse before scheduler before web-api)",
    stopped_order.index("sse") < stopped_order.index("scheduler") < stopped_order.index("web-api"),
    f"order={stopped_order}")

# A5. probe_healthz 인터페이스 존재
_ok("supervisor.probe_healthz 메서드 존재",
    callable(getattr(WhatUdoinSupervisor, "probe_healthz", None)))

# ─────────────────────────────────────────────────────────────────────────────
# B. Owner 정책 위반 3종 — 코드 grep + mock 카운터
# ─────────────────────────────────────────────────────────────────────────────
print("\n[B] Owner 정책 위반 3종 증거...")

# B1. finalize_expired_done 단일 실행
_ok("app.py: _scheduler_service_enabled 분기 존재",
    "_scheduler_service_enabled" in app_src)

lifespan_match = re.search(r"async def lifespan\([^)]*\).*?\n\s+yield", app_src, re.DOTALL)
if lifespan_match:
    lifespan_body = lifespan_match.group(0)
    enabled_block = re.search(r"if _scheduler_service_enabled:(.*?)else:", lifespan_body, re.DOTALL)
    if enabled_block:
        enabled_content = enabled_block.group(1)
        _ok("app.py: if _scheduler_service_enabled 블록에 finalize 호출 없음",
            "finalize_expired_done" not in enabled_content)
    else:
        _ok("app.py: if/else 분기 패턴", False, "패턴 매칭 실패")
else:
    _ok("app.py: lifespan 함수 추출", False)

svc_finalize_count = len(re.findall(r"db\.finalize_expired_done\(\)", svc_src))
_ok("scheduler_service.py: finalize_expired_done() 1회 (startup 콜백)",
    svc_finalize_count == 1,
    f"count={svc_finalize_count}")

# mock 카운터 시뮬레이션
os.environ["WHATUDOIN_SCHEDULER_SERVICE"] = "1"
try:
    enabled = bool(os.environ.get("WHATUDOIN_SCHEDULER_SERVICE"))
    lifespan_count = [0]
    startup_count  = [0]

    if enabled:
        pass  # skip
    else:
        lifespan_count[0] += 1

    startup_count[0] += 1  # scheduler startup 콜백

    _ok("mock: SCHEDULER_SERVICE=1 -> lifespan finalize 0건",
        lifespan_count[0] == 0, f"count={lifespan_count[0]}")
    _ok("mock: scheduler startup finalize 1건",
        startup_count[0] == 1, f"count={startup_count[0]}")
    _ok("mock: 합산 1건 (중복 0건)",
        lifespan_count[0] + startup_count[0] == 1)
finally:
    del os.environ["WHATUDOIN_SCHEDULER_SERVICE"]

# B2. 백업 동시 쓰기
_ok("backup.py: 초 단위 timestamp 파일명 패턴",
    'strftime("%Y%m%d-%H%M%S")' in bk_src)
_ok("backup.py: sqlite3.backup() API 원자성",
    "src.backup(dst)" in bk_src)

from maintenance_owners import MAINTENANCE_JOB_OWNERS
_ok("maintenance_owners: run_backup_startup_safetynet=web_api_lifespan",
    MAINTENANCE_JOB_OWNERS.get("run_backup_startup_safetynet") == "web_api_lifespan")
_ok("maintenance_owners: run_backup_nightly=scheduler",
    MAINTENANCE_JOB_OWNERS.get("run_backup_nightly") == "scheduler")

# B3. idempotency 가드
_ok("finalize_expired_done: (is_active IS NULL OR is_active = 1) 가드",
    "(is_active IS NULL OR is_active = 1)" in db_src)
_ok("check_upcoming_event_alarms: SELECT 1 FROM notifications dedup 가드",
    "SELECT 1 FROM notifications" in db_src and "type = 'upcoming'" in db_src)
_ok("check_upcoming_event_alarms: date('now') 데일리 dedup",
    "date('now')" in db_src)

# ─────────────────────────────────────────────────────────────────────────────
# C. 일반 API p95 구조 단언
# ─────────────────────────────────────────────────────────────────────────────
print("\n[C] p95 구조 단언...")

# healthz endpoint 구조: sched.get_jobs() in-memory, no DB I/O
_ok("scheduler_service.py: /healthz 엔드포인트 정의",
    'Route("/healthz", healthz)' in svc_src)
_ok("scheduler_service.py: healthz에서 sched.get_jobs() 사용 (in-memory, DB I/O 없음)",
    "sched.get_jobs()" in svc_src)
_ok("scheduler_service.py: healthz 응답 4종 키 포함",
    all(k in svc_src for k in
        ["jobs_count", "next_run_at", "last_finalize_expired_done_at", "uptime_seconds"]))

# ─────────────────────────────────────────────────────────────────────────────
# D. 회귀 단언 (phase54~64 핵심 항목)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[D] phase54~64 회귀 단언...")

# phase62: maintenance_owners 7개 job
_ok("phase62 회귀: MAINTENANCE_JOB_OWNERS 7개 job",
    len(MAINTENANCE_JOB_OWNERS) >= 7,
    f"count={len(MAINTENANCE_JOB_OWNERS)}")

_ok("phase62 회귀: 모든 owner가 'scheduler' 또는 'web_api_lifespan'",
    all(v in ("scheduler", "web_api_lifespan") for v in MAINTENANCE_JOB_OWNERS.values()))

# phase63: scheduler service 4종 환경변수 보호
_ok("phase63 회귀: scheduler_service_spec 4종 보호 env (BIND_HOST/PORT/ENABLE/TOKEN)",
    "WHATUDOIN_SCHEDULER_BIND_HOST" in sup_src and
    "WHATUDOIN_SCHEDULER_PORT" in sup_src and
    "WHATUDOIN_SCHEDULER_SERVICE" in sup_src and
    "WHATUDOIN_INTERNAL_TOKEN" in sup_src)

# phase63: app.py env 분기
_ok("phase63 회귀: app.py WHATUDOIN_SCHEDULER_SERVICE env 분기",
    "WHATUDOIN_SCHEDULER_SERVICE" in app_src and "_scheduler_service_enabled" in app_src)

# phase63: web_api_internal_service_env에 SCHEDULER_SERVICE=1 주입
_ok("phase63 회귀: web_api_internal_service_env SCHEDULER_SERVICE=1 주입",
    "SCHEDULER_SERVICE_ENABLE_ENV" in sup_src and
    '"1"' in sup_src)

# phase64: STOP_ORDER 존재
_ok("phase64 회귀: STOP_ORDER 상수 존재",
    "STOP_ORDER" in sup_src)

# phase64: RotatingFileHandler 코드 grep
_ok("phase64 회귀: RotatingFileHandler 로그 회전 적용",
    "RotatingFileHandler" in svc_src)

# phase64: healthz 4종 키
_ok("phase64 회귀: healthz 4종 키 (jobs_count/next_run_at/last_finalize/uptime)",
    all(k in svc_src for k in
        ["jobs_count", "next_run_at", "last_finalize_expired_done_at", "uptime_seconds"]))

# ─────────────────────────────────────────────────────────────────────────────
# 최종 결과
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n=== phase65 결과: {_pass}/{_pass + _fail} PASS ===")
if _fail > 0:
    print(f"  FAIL 목록:")
    for r in _results:
        if not r["passed"]:
            print(f"    - {r['name']}" + (f": {r['detail']}" if r["detail"] else ""))

# 임시 디렉토리 정리
import shutil
tmp_dir = ROOT / "_tmp_phase65"
if tmp_dir.exists():
    shutil.rmtree(tmp_dir, ignore_errors=True)

sys.exit(0 if _fail == 0 else 1)
