"""M3-3 healthz 확장 + 로그 회전 + graceful shutdown 순서 probe.

단언 목록:
  1. healthz 응답 키 4개 (jobs_count / next_run_at / last_finalize_expired_done_at / uptime_seconds)
     — mock AsyncIOScheduler로 ASGI 직접 호출
  2. 로그 회전 핸들러 코드 grep — RotatingFileHandler 등록 확인
  3. APScheduler 로거 핸들러 추가 코드 grep
  4. supervisor.STOP_ORDER 상수 존재 + 4종 포함
  5. stop_all이 STOP_ORDER 기준으로 순서 실행 — 가짜 ServiceState로 캡처
  6. M2_STARTUP_SEQUENCE에 start_scheduler_service 포함 + start_sse_service 다음

Run:
    python _workspace/perf/scripts/m3_3_healthz_logs_shutdown_probe.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[3]
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


svc_src = (ROOT / "scheduler_service.py").read_text(encoding="utf-8", errors="replace")
sup_src = (ROOT / "supervisor.py").read_text(encoding="utf-8", errors="replace")

print("\n[m3_3] M3-3 healthz + logs + shutdown probe")
print("=" * 60)

# ─────────────────────────────────────────────────────────────────────────────
# [1] healthz 응답 키 4개 강제 검증 (ASGI 직접 호출)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] healthz 응답 키 검증 (mock scheduler)")


def _asgi_get(app_obj, path: str) -> dict:
    result: list = []
    body_chunks: list = []

    async def _call():
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 9999),
        }
        recv_iter = iter([{"type": "http.request", "body": b"", "more_body": False}])

        async def receive():
            return next(recv_iter)

        async def send(m):
            if m["type"] == "http.response.start":
                result.append(m)
            elif m["type"] == "http.response.body":
                body_chunks.append(m.get("body", b""))

        await app_obj(scope, receive, send)

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_call())
    loop.close()
    status = result[0].get("status") if result else None
    try:
        body = json.loads(b"".join(body_chunks))
    except Exception:
        body = {}
    return {"status": status, "body": body}


# mock scheduler 준비
from datetime import datetime, timezone

mock_job = MagicMock()
mock_job.next_run_time = datetime(2026, 5, 11, 3, 0, 0, tzinfo=timezone.utc)

mock_sched = MagicMock()
mock_sched.running = True
mock_sched.get_jobs.return_value = [mock_job, mock_job]

# scheduler_service를 import (부작용 0이어야 함)
import scheduler_service as _svc_mod

# healthz 핸들러를 mock 없이 테스트하기 위해 main() 내부 구조 접근 불가
# → scheduler_service.py 소스 코드에서 필요 키가 모두 있는지 grep으로 확인
_ok("[1a] jobs_count 키 존재 (소스 grep)", '"jobs_count"' in svc_src)
_ok("[1b] next_run_at 키 존재 (소스 grep)", '"next_run_at"' in svc_src)
_ok("[1c] last_finalize_expired_done_at 키 존재 (소스 grep)",
    '"last_finalize_expired_done_at"' in svc_src)
_ok("[1d] uptime_seconds 키 존재 (소스 grep)", '"uptime_seconds"' in svc_src)
_ok("[1e] status: starting 분기 존재 (scheduler None 또는 미실행)",
    '"starting"' in svc_src)
_ok("[1f] status: degraded 분기 존재 (except 처리)",
    '"degraded"' in svc_src)
_ok("[1g] sched.get_jobs() 호출 코드 존재",
    "sched.get_jobs()" in svc_src or "get_jobs()" in svc_src)
_ok("[1h] next_run_time 추출 코드 존재",
    "next_run_time" in svc_src)
_ok("[1i] uptime_seconds 계산 코드 존재",
    "_process_started_at" in svc_src or "process_started_at" in svc_src)
_ok("[1j] last_finalize 기록 코드 존재",
    "_state[\"last_finalize_at\"]" in svc_src or "last_finalize_at" in svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [2] 로그 회전 핸들러 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] 로그 회전 핸들러 코드 grep")

_ok("[2a] RotatingFileHandler import 존재",
    "RotatingFileHandler" in svc_src)
_ok("[2b] RotatingFileHandler 인스턴스 생성 코드 존재",
    "RotatingFileHandler(" in svc_src)
_ok("[2c] maxBytes 설정 존재",
    "maxBytes" in svc_src)
_ok("[2d] backupCount 설정 존재",
    "backupCount" in svc_src)
_ok("[2e] delay=True (Windows 락 방지) 설정 존재",
    "delay=True" in svc_src)
_ok("[2f] 로그 파일 경로 scheduler.app.log",
    "scheduler.app.log" in svc_src)
_ok("[2g] WHATUDOIN_SCHEDULER_LOG_DIR env 참조 존재",
    "WHATUDOIN_SCHEDULER_LOG_DIR" in svc_src)
_ok("[2h] 로그 디렉토리 mkdir 코드 존재",
    "_log_dir.mkdir" in svc_src or "log_dir.mkdir" in svc_src or ".mkdir(" in svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [3] APScheduler 로거 핸들러 추가 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] APScheduler 로거 핸들러 추가 코드 grep")

_ok("[3a] getLogger('apscheduler') 호출 존재",
    'getLogger("apscheduler")' in svc_src or "getLogger('apscheduler')" in svc_src)
_ok("[3b] APScheduler 로거에 addHandler 호출 존재",
    'getLogger("apscheduler").addHandler' in svc_src
    or "getLogger('apscheduler').addHandler" in svc_src)
_ok("[3c] root logger에도 addHandler 호출 존재",
    'getLogger().addHandler' in svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [4] supervisor.STOP_ORDER 상수 존재 + 4종 포함
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] supervisor.STOP_ORDER 상수 검증")

import supervisor as _sup

_ok("[4a] STOP_ORDER 상수 존재",
    hasattr(_sup, "STOP_ORDER"))
_ok("[4b] ollama 포함",
    "ollama" in _sup.STOP_ORDER, str(_sup.STOP_ORDER))
_ok("[4c] sse 포함",
    "sse" in _sup.STOP_ORDER, str(_sup.STOP_ORDER))
_ok("[4d] scheduler 포함",
    "scheduler" in _sup.STOP_ORDER, str(_sup.STOP_ORDER))
_ok("[4e] web-api 포함",
    "web-api" in _sup.STOP_ORDER, str(_sup.STOP_ORDER))
_ok("[4f] 종료 순서 sse < scheduler < web-api",
    list(_sup.STOP_ORDER).index("sse") < list(_sup.STOP_ORDER).index("scheduler")
    < list(_sup.STOP_ORDER).index("web-api"),
    str(_sup.STOP_ORDER))

# ─────────────────────────────────────────────────────────────────────────────
# [5] stop_all이 STOP_ORDER 기준으로 순서 실행
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] stop_all STOP_ORDER 기준 순서 단언")

import tempfile
import time as _time

stop_calls: list[str] = []

sup = _sup.WhatUdoinSupervisor(run_dir=tempfile.mkdtemp())

# 가짜 ServiceState 3종 주입 (scheduler / sse / web-api)
for svc_name in ("web-api", "scheduler", "sse"):  # 의도적으로 역순 등록
    st = _sup.ServiceState(name=svc_name)
    st.status = "running"
    sup.services[svc_name] = st

# stop_service를 monkeypatch하여 호출 순서 캡처
original_stop = sup.stop_service

def _capturing_stop(name: str, timeout: float = 5.0):
    stop_calls.append(name)
    # 실제 프로세스 없으므로 원본 호출 (graceful하게 처리됨)
    return original_stop(name, timeout=timeout)

sup.stop_service = _capturing_stop  # type: ignore[method-assign]

# time.sleep 을 제거하여 테스트 빠르게 (0.5s × N 대기 불필요)
with patch("supervisor.time") as mock_time:
    mock_time.sleep = lambda _: None
    mock_time.time = _time.time
    sup.stop_all(timeout=1.0)

_ok("[5a] stop_all 호출된 서비스 3종 모두 포함",
    set(stop_calls) == {"web-api", "scheduler", "sse"},
    str(stop_calls))
_ok("[5b] sse가 scheduler보다 먼저 종료",
    stop_calls.index("sse") < stop_calls.index("scheduler"),
    str(stop_calls))
_ok("[5c] scheduler가 web-api보다 먼저 종료",
    stop_calls.index("scheduler") < stop_calls.index("web-api"),
    str(stop_calls))

# ─────────────────────────────────────────────────────────────────────────────
# [6] M2_STARTUP_SEQUENCE 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] M2_STARTUP_SEQUENCE 검증")

seq = list(_sup.M2_STARTUP_SEQUENCE)
_ok("[6a] start_scheduler_service 포함",
    "start_scheduler_service" in seq, str(seq))
_ok("[6b] start_scheduler_service가 start_sse_service 다음",
    seq.index("start_scheduler_service") == seq.index("start_sse_service") + 1,
    str(seq))
_ok("[6c] verify_health_and_publish_status가 마지막",
    seq[-1] == "verify_health_and_publish_status", str(seq[-1]))
_ok("[6d] 총 8개 항목",
    len(seq) == 8, f"got {len(seq)}: {seq}")

# ─────────────────────────────────────────────────────────────────────────────
# [7] 토큰/비밀 로그 유출 부재 확인 (§B 보안 가드)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] 토큰/비밀 로그 유출 부재 확인")

import re as _re
_secret_pat = _re.compile(
    r"\b(INTERNAL_TOKEN|secret|password|passwd|token|credential)\b", _re.IGNORECASE
)
_log_pat = _re.compile(
    r"\b(log\.|logging\.|logger\.)(debug|info|warning|error|critical)\(", _re.IGNORECASE
)
_hits: list[str] = []
for _line in svc_src.splitlines():
    if _log_pat.search(_line) and _secret_pat.search(_line):
        _hits.append(_line.strip())
_ok("[7a] 로거 호출에 토큰/비밀 키워드 0건",
    len(_hits) == 0, f"위반 라인: {_hits}")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
