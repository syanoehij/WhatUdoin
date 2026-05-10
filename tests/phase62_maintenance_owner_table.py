"""M3-1 maintenance owner 표 단일 소유자 정책 검증 (standalone runner).

검증 항목:
  1. maintenance_owners.MAINTENANCE_JOB_OWNERS 7개 키 모두 존재
  2. owner 값이 'scheduler' 또는 'web_api_lifespan' 둘 중 하나
  3. 실제 함수 정의(def <name>)가 database.py 또는 backup.py에 존재
  4. app.py에 owner 표 주석 블록이 박혔는지
  5. finalize_expired_done 동거 상태 (M3-2 이관 전까지 lifespan + APScheduler 공존)
  6. run_backup 합법 분리 (lifespan safetynet + APScheduler nightly 양쪽 존재)

Run:
    python tests/phase62_maintenance_owner_table.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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


EXPECTED_KEYS = [
    "finalize_expired_done",
    "cleanup_old_trash",
    "check_upcoming_event_alarms",
    "run_backup_startup_safetynet",
    "run_backup_nightly",
    "cleanup_old_backups",
    "cleanup_orphan_images",
]

VALID_OWNERS = {"scheduler", "web_api_lifespan"}

KEY_TO_FUNC: dict[str, str] = {
    "run_backup_startup_safetynet": "run_backup",
    "run_backup_nightly": "run_backup",
}


# ─────────────────────────────────────────────────────────────────────────────
print("\n[phase62] M3-1 maintenance owner 표 검증")
print("=" * 60)
# ─────────────────────────────────────────────────────────────────────────────

# 소스 로드
db_src = (ROOT / "database.py").read_text(encoding="utf-8", errors="replace")
backup_src = (ROOT / "backup.py").read_text(encoding="utf-8", errors="replace")
app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")
combined_funcs = db_src + backup_src

# ─────────────────────────────────────────────────────────────────────────────
# [1] import & 키 확인
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] maintenance_owners 모듈 로드 및 키 확인")

import maintenance_owners  # noqa: E402

owners = maintenance_owners.MAINTENANCE_JOB_OWNERS
_ok("MAINTENANCE_JOB_OWNERS 는 dict", isinstance(owners, dict))
_ok(f"키 개수 정확히 {len(EXPECTED_KEYS)}개", len(owners) == len(EXPECTED_KEYS),
    f"got {len(owners)}")

for key in EXPECTED_KEYS:
    _ok(f"key '{key}' 존재", key in owners)


# ─────────────────────────────────────────────────────────────────────────────
# [2] owner 값 유효성
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] owner 값 유효성")

for key, val in owners.items():
    _ok(f"owners['{key}'] 유효 ('{val}')", val in VALID_OWNERS)

web_keys = [k for k, v in owners.items() if v == "web_api_lifespan"]
_ok("web_api_lifespan 은 run_backup_startup_safetynet 단 1개",
    web_keys == ["run_backup_startup_safetynet"], f"got {web_keys}")


# ─────────────────────────────────────────────────────────────────────────────
# [3] 실제 함수 존재
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] 실제 함수 정의 존재")

for key in EXPECTED_KEYS:
    func_name = KEY_TO_FUNC.get(key, key)
    _ok(f"def {func_name} 존재", f"def {func_name}" in combined_funcs)


# ─────────────────────────────────────────────────────────────────────────────
# [4] app.py 주석 박힘 확인
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] app.py owner 표 주석 확인")

_ok("M3-1 owner 표 주석 존재", "M3-1 startup maintenance 단일 owner 표" in app_src)
_ok("maintenance_owners.MAINTENANCE_JOB_OWNERS 참조 주석 존재",
    "maintenance_owners.MAINTENANCE_JOB_OWNERS" in app_src)


# ─────────────────────────────────────────────────────────────────────────────
# [5] finalize_expired_done 동거 상태 (M3-2 이관 전)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] finalize_expired_done 동거 상태 확인 (M3-2 이관 전)")

_ok("lifespan 직접 호출 존재", "db.finalize_expired_done()" in app_src)
_ok("APScheduler cron 등록 존재", "scheduler.add_job(db.finalize_expired_done" in app_src)


# ─────────────────────────────────────────────────────────────────────────────
# [6] run_backup 합법 분리 확인
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] run_backup 합법 분리 확인")

_ok("lifespan safetynet 존재 (run_in_threadpool)",
    "run_in_threadpool(backup.run_backup" in app_src)
_ok("APScheduler nightly lambda 존재",
    "lambda: backup.run_backup" in app_src)


# ─────────────────────────────────────────────────────────────────────────────
# 결과
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
