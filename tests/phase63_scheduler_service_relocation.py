"""phase63 — M3-2 Scheduler service relocation 검증 (standalone runner).

검증 항목:
  1. env 미설정 → fallback 분기 코드 (scheduler.start / finalize / add_job) 존재
  2. WHATUDOIN_SCHEDULER_SERVICE=1 → lifespan 진입 시 scheduler 미시작, finalize 0회
  3. scheduler_service.py import 부작용 0 (module level scheduler/app 인스턴스 없음)
  4. scheduler_service.py 코드: cron 5종 + startup 콜백 + healthz + graceful shutdown
  5. supervisor.scheduler_service_spec() → ServiceSpec name/env 검증
  6. extra_env protected 키 override 차단

Run:
    python tests/phase63_scheduler_service_relocation.py
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
print("\n[phase63] M3-2 Scheduler service relocation 검증")
print("=" * 60)
# ─────────────────────────────────────────────────────────────────────────────

app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
svc_src = (ROOT / "scheduler_service.py").read_text(encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────────────────────────────────────
# [1] fallback 분기 코드 존재 (substring)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] env 미설정 fallback 분기 코드 존재")

_ok("_scheduler_service_enabled 변수 선언",
    "_scheduler_service_enabled" in app_src)
_ok("WHATUDOIN_SCHEDULER_SERVICE env 참조",
    "WHATUDOIN_SCHEDULER_SERVICE" in app_src)
_ok("else 분기 scheduler.start() 코드",
    "scheduler.start()" in app_src)
_ok("else 분기 db.finalize_expired_done() 즉시 호출",
    "db.finalize_expired_done()" in app_src)
_ok("else 분기 scheduler.add_job(db.finalize_expired_done cron",
    "scheduler.add_job(db.finalize_expired_done" in app_src)
_ok("run_backup safetynet 유지 (분기 영향 받지 않음)",
    "run_in_threadpool(backup.run_backup" in app_src)
_ok("cleanup_expired_sessions 유지 (분기 영향 받지 않음)",
    "db.cleanup_expired_sessions()" in app_src)

# ─────────────────────────────────────────────────────────────────────────────
# [2] WHATUDOIN_SCHEDULER_SERVICE=1 → lifespan skip 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] WHATUDOIN_SCHEDULER_SERVICE=1 → lifespan skip")

os.environ["WHATUDOIN_SCHEDULER_SERVICE"] = "1"
import app as _app_mod
importlib.reload(_app_mod)

_ok("_scheduler_service_enabled True", _app_mod._scheduler_service_enabled is True)

import asyncio

_add_job_calls = 0
_finalize_calls = 0

mock_scheduler = MagicMock()
mock_scheduler.running = False

def _count_add_job(*a, **kw):
    global _add_job_calls
    _add_job_calls += 1

def _count_finalize():
    global _finalize_calls
    _finalize_calls += 1

mock_scheduler.add_job.side_effect = _count_add_job


async def _run_lifespan():
    with patch.object(_app_mod, "scheduler", mock_scheduler), \
         patch.object(_app_mod.db, "finalize_expired_done", _count_finalize), \
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

_ok("scheduler.add_job 호출 0회",
    _add_job_calls == 0, f"got {_add_job_calls}")
_ok("db.finalize_expired_done 즉시 호출 0회",
    _finalize_calls == 0, f"got {_finalize_calls}")
_ok("scheduler.start() 호출 0회",
    mock_scheduler.start.call_count == 0, f"got {mock_scheduler.start.call_count}")

del os.environ["WHATUDOIN_SCHEDULER_SERVICE"]

# ─────────────────────────────────────────────────────────────────────────────
# [3] scheduler_service.py import 부작용 0
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] scheduler_service.py import 부작용 0")

import scheduler_service as _svc

_ok("import 성공", True)
_ok("module-level scheduler 인스턴스 없음",
    not hasattr(_svc, "scheduler"))
_ok("module-level app 인스턴스 없음",
    not hasattr(_svc, "app"))
_ok("main 함수 존재 및 callable",
    hasattr(_svc, "main") and callable(_svc.main))

# ─────────────────────────────────────────────────────────────────────────────
# [4] scheduler_service.py 코드 grep
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] scheduler_service.py 코드 내용 검증")

_ok("cron: check_upcoming_event_alarms interval",
    "db.check_upcoming_event_alarms" in svc_src and '"interval"' in svc_src)
_ok("cron: run_backup 03:00",
    "backup.run_backup" in svc_src and "hour=3, minute=0" in svc_src)
_ok("cron: finalize_expired_done 03:05",
    "db.finalize_expired_done" in svc_src and "hour=3, minute=5" in svc_src)
_ok("cron: cleanup_old_backups 03:10",
    "backup.cleanup_old_backups" in svc_src and "hour=3, minute=10" in svc_src)
_ok("cron: cleanup_old_trash 03:20",
    "db.cleanup_old_trash" in svc_src and "hour=3, minute=20" in svc_src)
_ok("cron: cleanup_orphan_images 03:30",
    "backup.cleanup_orphan_images" in svc_src and "hour=3, minute=30" in svc_src)
_ok("startup 콜백: db.finalize_expired_done() 즉시 실행",
    "db.finalize_expired_done()" in svc_src)
_ok("healthz 라우트 존재",
    '"/healthz"' in svc_src and '"scheduler"' in svc_src)
_ok("graceful shutdown (should_exit 또는 scheduler.shutdown)",
    "should_exit" in svc_src or "scheduler.shutdown" in svc_src)
_ok('if __name__ == "__main__": main()',
    'if __name__ == "__main__"' in svc_src and "main()" in svc_src)

# ─────────────────────────────────────────────────────────────────────────────
# [5] supervisor.scheduler_service_spec() 검증
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] supervisor.scheduler_service_spec() ServiceSpec 검증")

import supervisor as _sup

cmd = ["python", "scheduler_service.py"]
spec = _sup.scheduler_service_spec(cmd)

_ok("name == SCHEDULER_SERVICE_NAME",
    spec.name == _sup.SCHEDULER_SERVICE_NAME, f"got {spec.name!r}")
_ok("SCHEDULER_SERVICE_ENABLE_ENV=1",
    spec.env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")
_ok("BIND_HOST=127.0.0.1",
    spec.env.get(_sup.SCHEDULER_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT=8766 (기본값)",
    spec.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV) == "8766")

spec_custom = _sup.scheduler_service_spec(cmd, port=9001)
_ok("커스텀 포트 9001",
    spec_custom.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV) == "9001")

# ─────────────────────────────────────────────────────────────────────────────
# [6] extra_env protected 키 override 차단
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] extra_env protected 키 override 차단")

spec_bad = _sup.scheduler_service_spec(
    cmd,
    extra_env={
        _sup.SCHEDULER_SERVICE_BIND_HOST_ENV: "0.0.0.0",
        _sup.SCHEDULER_SERVICE_PORT_ENV: "9999",
        _sup.SCHEDULER_SERVICE_ENABLE_ENV: "0",
        _sup.INTERNAL_TOKEN_ENV: "leaked",
        "SAFE_KEY": "safe_value",
    },
)
_ok("BIND_HOST override 차단",
    spec_bad.env.get(_sup.SCHEDULER_SERVICE_BIND_HOST_ENV) == "127.0.0.1")
_ok("PORT override 차단",
    spec_bad.env.get(_sup.SCHEDULER_SERVICE_PORT_ENV) == "8766")
_ok("ENABLE_ENV override 차단 (여전히 1)",
    spec_bad.env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")
_ok("INTERNAL_TOKEN override 차단",
    _sup.INTERNAL_TOKEN_ENV not in spec_bad.env)
_ok("비보호 키 SAFE_KEY 통과",
    spec_bad.env.get("SAFE_KEY") == "safe_value")

# M2_STARTUP_SEQUENCE 검증
_ok("M2_STARTUP_SEQUENCE에 start_scheduler_service 포함",
    "start_scheduler_service" in _sup.M2_STARTUP_SEQUENCE)
seq = list(_sup.M2_STARTUP_SEQUENCE)
_ok("start_scheduler_service가 start_sse_service 다음",
    seq.index("start_scheduler_service") == seq.index("start_sse_service") + 1)

# web_api_internal_service_env에 inject 확인
web_env = _sup.web_api_internal_service_env()
_ok("web_api_internal_service_env SCHEDULER_SERVICE_ENABLE_ENV=1 주입",
    web_env.get(_sup.SCHEDULER_SERVICE_ENABLE_ENV) == "1")

# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
