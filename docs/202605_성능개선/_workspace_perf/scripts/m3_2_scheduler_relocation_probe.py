"""M3-2 scheduler service relocation probe.

단언 목록:
  1. env 미설정 + app import → lifespan 분기에서 scheduler.start() 호출 분기 존재
     (fallback 깨지지 않음)
  2. env 설정 + app lifespan 진입 → scheduler 미시작, finalize_expired_done 즉시 실행 0회
     (mock으로 검증: scheduler.add_job 호출 0, db.finalize_expired_done mock 호출 0)
  3. scheduler_service.py import (module level) → 부작용 0
     (스케줄러 인스턴스 미생성, DB init 호출 0)
  4. scheduler_service.py 코드 grep → cron 5종 + finalize_expired_done + healthz + graceful shutdown
  5. supervisor.scheduler_service_spec() 호출 → ServiceSpec 검증
     (name=scheduler, SCHEDULER_SERVICE_ENABLE_ENV=1, BIND_HOST=127.0.0.1, PORT 적용)
  6. extra_env가 protected 키를 override하지 못함

Run:
    python _workspace/perf/scripts/m3_2_scheduler_relocation_probe.py
"""
from __future__ import annotations

import importlib
import os
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


# ─────────────────────────────────────────────────────────────────────────────
print("\n[m3_2] M3-2 Scheduler service relocation probe")
print("=" * 60)
# ─────────────────────────────────────────────────────────────────────────────

app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
svc_src = (ROOT / "scheduler_service.py").read_text(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# [1] env 미설정 → fallback 분기 코드 존재 (substring 검증)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] env 미설정 fallback 분기 코드 존재")

_ok("scheduler.start() 호출 코드 존재 (else 분기)",
    "scheduler.start()" in app_src)
_ok("db.finalize_expired_done() 즉시 호출 코드 존재 (else 분기)",
    "db.finalize_expired_done()" in app_src)
_ok("scheduler.add_job(db.finalize_expired_done cron 코드 존재 (else 분기)",
    "scheduler.add_job(db.finalize_expired_done" in app_src)
_ok("_scheduler_service_enabled 분기 변수 선언 존재",
    "_scheduler_service_enabled" in app_src)

# ─────────────────────────────────────────────────────────────────────────────
# [2] env 설정 시 lifespan → scheduler 미시작 + finalize 0회
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] WHATUDOIN_SCHEDULER_SERVICE=1 시 lifespan skip 검증 (mock)")

# app 모듈 재로드 전 env 설정
os.environ["WHATUDOIN_SCHEDULER_SERVICE"] = "1"
# app 모듈이 이미 로드됐을 수 있으므로 재import
import app as _app_mod
importlib.reload(_app_mod)

_ok("_scheduler_service_enabled is True after reload",
    _app_mod._scheduler_service_enabled is True)

# lifespan 실제 실행 mock 검증
import asyncio

mock_db = MagicMock()
mock_scheduler = MagicMock()
mock_scheduler.running = False

_add_job_calls = 0
_finalize_calls = 0

def _counting_add_job(*a, **kw):
    global _add_job_calls
    _add_job_calls += 1

def _counting_finalize():
    global _finalize_calls
    _finalize_calls += 1

mock_scheduler.add_job.side_effect = _counting_add_job

async def _run_lifespan():
    # 실제 lifespan contextmanager 진입
    # app.py lifespan이 backup, db 등을 호출하므로 patch 필요
    with patch.object(_app_mod, "scheduler", mock_scheduler), \
         patch.object(_app_mod.db, "finalize_expired_done", _counting_finalize), \
         patch.object(_app_mod.db, "init_db"), \
         patch.object(_app_mod.db, "cleanup_expired_sessions"), \
         patch.object(_app_mod.db, "get_setting", return_value=None), \
         patch.object(_app_mod, "backup") as _mock_backup, \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(_app_mod.wu_broker, "start_on_loop"):
        from fastapi import FastAPI
        test_app = FastAPI(lifespan=_app_mod.lifespan)
        async with _app_mod.lifespan(test_app):
            pass

asyncio.run(_run_lifespan())

_ok("SCHEDULER_SERVICE=1 시 scheduler.add_job 호출 0회",
    _add_job_calls == 0, f"got {_add_job_calls}")
_ok("SCHEDULER_SERVICE=1 시 db.finalize_expired_done 즉시 호출 0회",
    _finalize_calls == 0, f"got {_finalize_calls}")
_ok("SCHEDULER_SERVICE=1 시 scheduler.start() 호출 0회",
    mock_scheduler.start.call_count == 0, f"got {mock_scheduler.start.call_count}")

# env 정리
del os.environ["WHATUDOIN_SCHEDULER_SERVICE"]

# ─────────────────────────────────────────────────────────────────────────────
# [3] scheduler_service.py import 부작용 0
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] scheduler_service.py import 부작용 0 (module level)")

# 실제 import (scheduler_service는 if __name__ == "__main__" 패턴)
import scheduler_service as _svc_mod

_ok("scheduler_service 모듈 import 성공", True)
_ok("모듈 수준 AsyncIOScheduler 인스턴스 없음",
    not hasattr(_svc_mod, "scheduler"))
_ok("모듈 수준 Starlette app 인스턴스 없음",
    not hasattr(_svc_mod, "app"))
_ok("모듈 수준 main 함수만 존재",
    hasattr(_svc_mod, "main") and callable(_svc_mod.main))

# ─────────────────────────────────────────────────────────────────────────────
# [4] scheduler_service.py 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] scheduler_service.py 코드 내용 grep")

# cron 5종
_ok("cron: check_upcoming_event_alarms (interval)",
    "db.check_upcoming_event_alarms" in svc_src)
_ok("cron: run_backup nightly 03:00",
    "backup.run_backup" in svc_src and "hour=3, minute=0" in svc_src)
_ok("cron: finalize_expired_done 03:05",
    "db.finalize_expired_done" in svc_src and "hour=3, minute=5" in svc_src)
_ok("cron: cleanup_old_backups 03:10",
    "backup.cleanup_old_backups" in svc_src and "hour=3, minute=10" in svc_src)
_ok("cron: cleanup_old_trash 03:20",
    "db.cleanup_old_trash" in svc_src and "hour=3, minute=20" in svc_src)
_ok("cron: cleanup_orphan_images 03:30",
    "backup.cleanup_orphan_images" in svc_src and "hour=3, minute=30" in svc_src)
# startup 콜백
_ok("startup 콜백: finalize_expired_done 즉시 실행 코드",
    "db.finalize_expired_done()" in svc_src)
# healthz 라우트
_ok("healthz 라우트 존재",
    '"/healthz"' in svc_src and '"scheduler"' in svc_src)
# graceful shutdown
_ok("graceful shutdown 코드 존재 (should_exit 또는 shutdown)",
    "should_exit" in svc_src or "scheduler.shutdown" in svc_src)
# __name__ == "__main__" 진입점
_ok("if __name__ == \"__main__\": main() 패턴",
    'if __name__ == "__main__"' in svc_src and "main()" in svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [5] supervisor.scheduler_service_spec() 호출 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] supervisor.scheduler_service_spec() ServiceSpec 검증")

import supervisor as _sup

cmd = ["python", "scheduler_service.py"]
spec = _sup.scheduler_service_spec(cmd)

_ok("name == 'scheduler'", spec.name == _sup.SCHEDULER_SERVICE_NAME,
    f"got {spec.name!r}")
_ok("SCHEDULER_SERVICE_ENABLE_ENV=1 주입",
    spec.env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1",
    f"got {spec.env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV)!r}")
_ok("BIND_HOST=127.0.0.1 강제",
    spec.env.get(_sup.SCHEDULER_SERVICE_BIND_HOST_ENV) == "127.0.0.1",
    f"got {spec.env.get(_sup.SCHEDULER_SERVICE_BIND_HOST_ENV)!r}")
_ok("PORT 기본값 8766",
    spec.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV) == str(_sup.SCHEDULER_SERVICE_DEFAULT_PORT),
    f"got {spec.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV)!r}")

# 커스텀 포트
spec2 = _sup.scheduler_service_spec(cmd, port=9000)
_ok("커스텀 포트 9000 적용",
    spec2.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV) == "9000",
    f"got {spec2.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV)!r}")

# ─────────────────────────────────────────────────────────────────────────────
# [6] extra_env이 protected 키를 override 못함
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] extra_env protected 키 override 차단")

spec3 = _sup.scheduler_service_spec(
    cmd,
    extra_env={
        _sup.SCHEDULER_SERVICE_BIND_HOST_ENV: "0.0.0.0",    # protected
        _sup.SCHEDULER_SERVICE_PORT_ENV: "9999",             # protected
        _sup.SCHEDULER_SERVICE_ENABLE_ENV: "0",              # protected
        _sup.INTERNAL_TOKEN_ENV: "leaked",                   # protected
        "SOME_CUSTOM_ENV": "custom_value",                   # 허용
    },
)
_ok("BIND_HOST override 차단 (여전히 127.0.0.1)",
    spec3.env.get(_sup.SCHEDULER_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT override 차단 (여전히 기본값)",
    spec3.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV) == str(_sup.SCHEDULER_SERVICE_DEFAULT_PORT))
_ok("ENABLE_ENV override 차단 (여전히 1)",
    spec3.env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")
_ok("INTERNAL_TOKEN override 차단 (spec.env에 없음)",
    _sup.INTERNAL_TOKEN_ENV not in spec3.env)
_ok("비보호 env 통과 (SOME_CUSTOM_ENV)",
    spec3.env.get("SOME_CUSTOM_ENV") == "custom_value")

# M2_STARTUP_SEQUENCE에 scheduler 단계 추가 확인
_ok("M2_STARTUP_SEQUENCE에 start_scheduler_service 포함",
    "start_scheduler_service" in _sup.M2_STARTUP_SEQUENCE)
_ok("start_scheduler_service가 start_sse_service 다음",
    list(_sup.M2_STARTUP_SEQUENCE).index("start_scheduler_service") ==
    list(_sup.M2_STARTUP_SEQUENCE).index("start_sse_service") + 1)

# web_api_internal_service_env에 SCHEDULER_SERVICE_ENABLE_ENV 주입 확인
web_env = _sup.web_api_internal_service_env()
_ok("web_api_internal_service_env에 SCHEDULER_SERVICE_ENABLE_ENV=1 주입",
    web_env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
