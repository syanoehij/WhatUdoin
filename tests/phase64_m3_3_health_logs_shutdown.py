"""
M3-3 healthcheck 확장 + 로그 회전 + graceful shutdown 순서 테스트 (standalone runner).

시나리오:
  1. scheduler_service.py healthz 응답에 4개 키 존재 (소스 grep)
  2. status: starting / degraded 분기 코드 존재
  3. RotatingFileHandler 등록 코드 존재 (delay=True 포함)
  4. APScheduler 로거 + root logger에 핸들러 추가 코드 존재
  5. WHATUDOIN_SCHEDULER_LOG_DIR env 참조 존재
  6. supervisor.STOP_ORDER 상수 존재 + ("ollama","sse","scheduler","web-api") 포함
  7. stop_all이 STOP_ORDER 기준으로 sse → scheduler → web-api 순서 실행
  8. M2_STARTUP_SEQUENCE에 start_scheduler_service 포함 + start_sse_service 다음 위치

Run:
    python tests/phase64_m3_3_health_logs_shutdown.py
"""
from __future__ import annotations

import sys
import time as _time
import tempfile
from pathlib import Path
from unittest.mock import patch

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


def _ok(name: str, cond: bool, detail: str = "") -> None:
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  [PASS] {name}")
    else:
        _fail += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


# ─────────────────────────────────────────────────────────────────────────────
# 소스 로드
# ─────────────────────────────────────────────────────────────────────────────
svc_src = (ROOT / "scheduler_service.py").read_text(encoding="utf-8", errors="replace")
sup_src = (ROOT / "supervisor.py").read_text(encoding="utf-8", errors="replace")


def test_healthz_keys():
    print("\n[1-2] healthz 응답 키 및 분기 코드 검증")
    _ok("[1a] jobs_count 키 존재", '"jobs_count"' in svc_src)
    _ok("[1b] next_run_at 키 존재", '"next_run_at"' in svc_src)
    _ok("[1c] last_finalize_expired_done_at 키 존재",
        '"last_finalize_expired_done_at"' in svc_src)
    _ok("[1d] uptime_seconds 키 존재", '"uptime_seconds"' in svc_src)
    _ok("[2a] status: starting 분기 존재", '"starting"' in svc_src)
    _ok("[2b] status: degraded 분기 존재", '"degraded"' in svc_src)
    _ok("[2c] get_jobs() 호출 코드 존재", "get_jobs()" in svc_src)
    _ok("[2d] next_run_time 추출 코드 존재", "next_run_time" in svc_src)
    _ok("[2e] uptime 계산 started_at 기록", "_process_started_at" in svc_src)


def test_log_rotation():
    print("\n[3-5] 로그 회전 핸들러 코드 검증")
    _ok("[3a] RotatingFileHandler import 존재", "RotatingFileHandler" in svc_src)
    _ok("[3b] RotatingFileHandler 인스턴스 생성", "RotatingFileHandler(" in svc_src)
    _ok("[3c] maxBytes 설정 존재", "maxBytes" in svc_src)
    _ok("[3d] backupCount 설정 존재", "backupCount" in svc_src)
    _ok("[3e] delay=True (Windows 락 방지)", "delay=True" in svc_src)
    _ok("[3f] scheduler.app.log 경로", "scheduler.app.log" in svc_src)
    _ok("[4a] getLogger('apscheduler') 핸들러 등록",
        'getLogger("apscheduler").addHandler' in svc_src
        or "getLogger('apscheduler').addHandler" in svc_src)
    _ok("[4b] root logger 핸들러 등록", 'getLogger().addHandler' in svc_src)
    _ok("[5a] WHATUDOIN_SCHEDULER_LOG_DIR env 참조",
        "WHATUDOIN_SCHEDULER_LOG_DIR" in svc_src)
    _ok("[5b] 로그 디렉토리 mkdir 코드", ".mkdir(" in svc_src)


def test_stop_order():
    print("\n[6-7] supervisor STOP_ORDER + stop_all 순서 검증")
    import supervisor as _sup

    _ok("[6a] STOP_ORDER 상수 존재", hasattr(_sup, "STOP_ORDER"))
    _ok("[6b] ollama 포함", "ollama" in _sup.STOP_ORDER)
    _ok("[6c] sse 포함", "sse" in _sup.STOP_ORDER)
    _ok("[6d] scheduler 포함", "scheduler" in _sup.STOP_ORDER)
    _ok("[6e] web-api 포함", "web-api" in _sup.STOP_ORDER)

    stop_calls: list[str] = []

    sup = _sup.WhatUdoinSupervisor(run_dir=tempfile.mkdtemp())
    for svc_name in ("web-api", "scheduler", "sse"):  # 역순 등록
        st = _sup.ServiceState(name=svc_name)
        st.status = "running"
        sup.services[svc_name] = st

    original_stop = sup.stop_service

    def _capturing_stop(name: str, timeout: float = 5.0):
        stop_calls.append(name)
        return original_stop(name, timeout=timeout)

    sup.stop_service = _capturing_stop  # type: ignore[method-assign]

    with patch("supervisor.time") as mock_time:
        mock_time.sleep = lambda _: None
        mock_time.time = _time.time
        sup.stop_all(timeout=1.0)

    _ok("[7a] sse가 scheduler보다 먼저 종료",
        stop_calls.index("sse") < stop_calls.index("scheduler"),
        str(stop_calls))
    _ok("[7b] scheduler가 web-api보다 먼저 종료",
        stop_calls.index("scheduler") < stop_calls.index("web-api"),
        str(stop_calls))
    _ok("[7c] 3종 모두 종료 완료",
        set(stop_calls) == {"web-api", "scheduler", "sse"},
        str(stop_calls))


def test_startup_sequence():
    print("\n[8] M2_STARTUP_SEQUENCE 검증")
    import supervisor as _sup

    seq = list(_sup.M2_STARTUP_SEQUENCE)
    _ok("[8a] start_scheduler_service 포함",
        "start_scheduler_service" in seq, str(seq))
    _ok("[8b] start_scheduler_service가 start_sse_service 다음",
        seq.index("start_scheduler_service") == seq.index("start_sse_service") + 1,
        str(seq))
    _ok("[8c] 총 9개 항목 (M4-1: start_ollama_service 추가)",
        len(seq) == 9, f"got {len(seq)}")


def test_no_secret_logging():
    """§B 보안 가드 — scheduler_service.py 로거 호출에 토큰/비밀 0건."""
    import re
    print("\n[9] 토큰/비밀 로그 유출 부재 확인")
    # logging 호출(debug/info/warning/error/critical) 라인에서 토큰류 키워드 검색
    secret_pattern = re.compile(
        r"\b(INTERNAL_TOKEN|secret|password|passwd|token|credential)\b",
        re.IGNORECASE,
    )
    log_call_pattern = re.compile(
        r"\b(log\.|logging\.|logger\.)(debug|info|warning|error|critical)\(",
        re.IGNORECASE,
    )
    hits: list[str] = []
    for line in svc_src.splitlines():
        if log_call_pattern.search(line) and secret_pattern.search(line):
            hits.append(line.strip())
    _ok("[9a] 로거 호출에 토큰/비밀 키워드 0건",
        len(hits) == 0, f"위반 라인: {hits}")


# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("phase64 - M3-3 healthz + logs + graceful shutdown")
    print("=" * 65)
    test_healthz_keys()
    test_log_rotation()
    test_stop_order()
    test_startup_sequence()
    test_no_secret_logging()
    print("\n" + "=" * 65)
    print(f"TOTAL: {_pass + _fail}  PASS: {_pass}  FAIL: {_fail}")
    print("=" * 65)
    if _fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
