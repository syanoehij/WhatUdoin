"""M3-1 owner 표 probe.

maintenance_owners.MAINTENANCE_JOB_OWNERS 가 §11 권장 표와 정확히 매핑되는지 단언.

단언 목록:
  1. MAINTENANCE_JOB_OWNERS 의 7개 키 모두 존재
  2. owner 값이 'web_api_lifespan' 또는 'scheduler' 둘 중 하나
  3. 코드에서 실제 함수 정의(def <name>)가 존재하는지 단언
  4. finalize_expired_done 이 lifespan + APScheduler 양쪽에 있음 (동거 상태 확인)
  5. run_backup 이 lifespan(safetynet) + APScheduler(nightly) 양쪽에 있음 (합법 분리 확인)

Run:
    python _workspace/perf/scripts/m3_1_owner_table_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

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


# run_backup_startup_safetynet 과 run_backup_nightly 는 동일 함수 run_backup 을 가리킴
KEY_TO_FUNC: dict[str, str] = {
    "run_backup_startup_safetynet": "run_backup",
    "run_backup_nightly": "run_backup",
}

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


# ─────────────────────────────────────────────────────────────────────────────
# [1] import 및 키 존재 확인
# ─────────────────────────────────────────────────────────────────────────────

print("\n[1] MAINTENANCE_JOB_OWNERS import 및 키 확인")
import maintenance_owners  # noqa: E402

owners = maintenance_owners.MAINTENANCE_JOB_OWNERS

_ok("dict 타입", isinstance(owners, dict), f"got {type(owners)}")

for key in EXPECTED_KEYS:
    _ok(f"key '{key}' 존재", key in owners)

_ok("여분 키 없음 (정확히 7개)", len(owners) == len(EXPECTED_KEYS),
    f"got {len(owners)}: {list(owners.keys())}")


# ─────────────────────────────────────────────────────────────────────────────
# [2] owner 값 유효성
# ─────────────────────────────────────────────────────────────────────────────

print("\n[2] owner 값 유효성 (scheduler|web_api_lifespan)")
for key, val in owners.items():
    _ok(f"owner['{key}'] = '{val}' 유효", val in VALID_OWNERS,
        f"허용값: {VALID_OWNERS}")

# 정책 확인: startup_safetynet 만 web_api_lifespan
web_keys = [k for k, v in owners.items() if v == "web_api_lifespan"]
_ok("web_api_lifespan owner 는 run_backup_startup_safetynet 단 1개",
    web_keys == ["run_backup_startup_safetynet"],
    f"got {web_keys}")


# ─────────────────────────────────────────────────────────────────────────────
# [3] 실제 함수 정의 존재
# ─────────────────────────────────────────────────────────────────────────────

print("\n[3] 실제 함수 정의 존재 (def <name>)")
db_src = (ROOT / "database.py").read_text(encoding="utf-8", errors="replace")
backup_src = (ROOT / "backup.py").read_text(encoding="utf-8", errors="replace")
combined = db_src + backup_src

for key in EXPECTED_KEYS:
    func_name = KEY_TO_FUNC.get(key, key)
    found = f"def {func_name}" in combined
    _ok(f"def {func_name} 소스 존재", found)


# ─────────────────────────────────────────────────────────────────────────────
# [4] app.py 동거 상태 확인
# ─────────────────────────────────────────────────────────────────────────────

print("\n[4] app.py 동거 상태 확인")
app_src = (ROOT / "app.py").read_text(encoding="utf-8", errors="replace")

# finalize_expired_done: lifespan 직접 호출 + APScheduler cron 양쪽 있어야 함
fed_lifespan = "db.finalize_expired_done()" in app_src
fed_scheduler = "scheduler.add_job(db.finalize_expired_done" in app_src
_ok("finalize_expired_done: lifespan 직접 호출 존재 (M3-2 이관 전)", fed_lifespan)
_ok("finalize_expired_done: APScheduler cron 등록 존재", fed_scheduler)

# run_backup: lifespan(safetynet) + APScheduler(nightly) 양쪽 있어야 함
rb_lifespan = "backup.run_backup" in app_src and "lifespan" not in app_src.split("backup.run_backup")[0].rsplit("\n", 20)[-1]
# 더 직접적인 체크: await run_in_threadpool(backup.run_backup 존재
rb_safetynet = "run_in_threadpool(backup.run_backup" in app_src
rb_nightly = "lambda: backup.run_backup" in app_src
_ok("run_backup: lifespan safetynet 호출 존재", rb_safetynet)
_ok("run_backup: APScheduler nightly lambda 존재", rb_nightly)

# owner 표 주석 박혔는지 확인
_ok("app.py에 M3-1 owner 표 주석 존재", "M3-1 startup maintenance 단일 owner 표" in app_src)
_ok("app.py에 maintenance_owners 참조 주석 존재", "maintenance_owners.MAINTENANCE_JOB_OWNERS" in app_src)


# ─────────────────────────────────────────────────────────────────────────────
# [5] maintenance_owners.py 순수 데이터 모듈 확인 (import 없음)
# ─────────────────────────────────────────────────────────────────────────────

print("\n[5] maintenance_owners.py 순수 데이터 모듈 (외부 import 0)")
mo_src = (ROOT / "maintenance_owners.py").read_text(encoding="utf-8", errors="replace")
import_lines = [
    ln.strip() for ln in mo_src.splitlines()
    if ln.strip().startswith("import ") or ln.strip().startswith("from ")
]
_ok("외부 import 0건", len(import_lines) == 0, f"found: {import_lines}")


# ─────────────────────────────────────────────────────────────────────────────
# 결과
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n결과: {_pass} PASS, {_fail} FAIL")
sys.exit(0 if _fail == 0 else 1)
