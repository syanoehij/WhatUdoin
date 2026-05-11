"""팀 기능 그룹 A #1 마무리 — DB 마이그레이션 인프라 검증.

검증 3건 (todo.md "### #1. DB 마이그레이션 인프라 구축" → "검증" 항목):
  case 1: 빈 DB 첫 init_db() → 등록된 모든 phase 마커가 settings 에 기록 + preflight 통과
  case 2: 같은 DB로 init_db() 재호출 → 모든 phase is_phase_done True (재실행 없음)
  case 3: phase 마커 강제 삭제 + 위험 합성 데이터 심기 후 init_db() 재호출
          → 단계 내부 WHERE 가드 덕에 데이터 무결성 유지:
            - 이미 hash 변환된 user(password 평문 잔존)를 다시 hash() 입력으로 안 넘김
            - 이미 team_id 채운 event를 덮어쓰지 않음
            - 이미 project_id 채운 event를 덮어쓰지 않음
            - 'AdminTeam'으로 rename된 팀을 다시 건드리지 않음 (관리팀 lookup no-op)

실서버를 띄우지 않는다 — tempfile 임시 DB로만. WHATUDOIN_RUN_DIR 을 임시 디렉토리로
설정한 뒤 database 모듈을 import 한다 (DB_PATH 가 import time 에 고정되므로).

사용:
  "D:\\Program Files\\Python\\Python312\\python.exe" verify_team_a_001_close.py
"""
import os
import sys
import sqlite3
import tempfile
import shutil
import importlib
from pathlib import Path

# Windows 콘솔(cp949)에서도 em-dash 등 출력 가능하도록 UTF-8 강제.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

# ── 결과 집계 ──────────────────────────────────────────────
_results = []  # list[(ok: bool, label: str, detail: str)]


def _check(ok: bool, label: str, detail: str = "") -> None:
    _results.append((bool(ok), label, detail))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f"  — {detail}"
    print(line)


# ── 임시 작업 디렉토리 + 모듈 import ──────────────────────────
_tmp_root = tempfile.mkdtemp(prefix="wu_verify_a001_")
os.environ["WHATUDOIN_RUN_DIR"] = _tmp_root
# 백업 디렉토리도 임시 안에 만들어지도록 (run_migration_backup 이 _RUN_DIR/backupDB 사용).
# 프로젝트 루트(database.py 가 있는 곳)를 sys.path 에 올린다.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_PROJECT_ROOT))

# database 가 이미 import 됐을 수 있으니 (재실행 안전) 강제 reimport.
for _m in ("database", "backup", "passwords"):
    if _m in sys.modules:
        del sys.modules[_m]
database = importlib.import_module("database")

DB_PATH = database.DB_PATH
PHASE_KEYS = [name for name, _body in database.PHASES]
MARKER_PREFIX = database._PHASE_MARKER_KEY_PREFIX

print(f"임시 작업 디렉토리: {_tmp_root}")
print(f"DB_PATH: {DB_PATH}")
print(f"등록된 PHASES ({len(PHASE_KEYS)}): {PHASE_KEYS}")
print()


def _markers_in_db() -> set:
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT key FROM settings WHERE key LIKE ?",
            (MARKER_PREFIX + "%",),
        ).fetchall()
        return {r[0][len(MARKER_PREFIX):] for r in rows}
    finally:
        con.close()


def _exec(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur
    finally:
        con.close()


def _query_one(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    try:
        con.row_factory = sqlite3.Row
        return con.execute(sql, params).fetchone()
    finally:
        con.close()


# ════════════════════════════════════════════════════════════
# case 1: 빈 DB 첫 init_db()
# ════════════════════════════════════════════════════════════
print("=== case 1: 빈 DB 첫 init_db() → 모든 phase 마커 기록 + preflight 통과 ===")
try:
    database.init_db()
    _check(True, "init_db() 예외 없이 완료 (preflight 충돌 없음)")
except Exception as exc:  # noqa: BLE001
    _check(False, "init_db() 예외 없이 완료", repr(exc))

_after1 = _markers_in_db()
_missing = [k for k in PHASE_KEYS if k not in _after1]
_check(not _missing, "등록된 모든 phase 마커가 settings 에 존재", f"누락={_missing}" if _missing else f"{len(_after1)}개 기록")
print()

# ════════════════════════════════════════════════════════════
# case 2: 같은 DB로 init_db() 재호출 → 모든 phase skip
# ════════════════════════════════════════════════════════════
print("=== case 2: 재호출 → 모든 phase is_phase_done True (재실행 없음) ===")
# 재호출 전 데이터 스냅샷.
_users_before = _query_one("SELECT COUNT(*) AS c FROM users")["c"]
_teams_before = _query_one("SELECT COUNT(*) AS c FROM teams")["c"]
try:
    database.init_db()
    _check(True, "init_db() 재호출 예외 없이 완료")
except Exception as exc:  # noqa: BLE001
    _check(False, "init_db() 재호출 예외 없이 완료", repr(exc))

con = sqlite3.connect(DB_PATH)
try:
    _all_done = all(database._is_phase_done(con, k) for k in PHASE_KEYS)
finally:
    con.close()
_check(_all_done, "모든 phase is_phase_done() == True")
_users_after = _query_one("SELECT COUNT(*) AS c FROM users")["c"]
_teams_after = _query_one("SELECT COUNT(*) AS c FROM teams")["c"]
_check(_users_before == _users_after and _teams_before == _teams_after,
       "재호출이 users/teams row 수를 바꾸지 않음",
       f"users {_users_before}->{_users_after}, teams {_teams_before}->{_teams_after}")
# _pending_phases() 가 빈 리스트여야 한다 (백업·preflight 모두 skip 경로).
_pend = database._pending_phases()
_check(len(_pend) == 0, "_pending_phases() == [] (백업·preflight skip 경로)", f"pending={[n for n,_ in _pend]}")
print()

# ════════════════════════════════════════════════════════════
# case 3: 마커 강제 삭제 + 위험 합성 데이터 → 재호출 시 가드 동작
# ════════════════════════════════════════════════════════════
print("=== case 3: phase 마커 강제 삭제 + 위험 합성 데이터 심기 → 재호출 시 데이터 무결성 ===")

# ── 위험 합성 데이터 심기 (마이그레이션-후 상태를 흉내) ──
# (a) 평문 password 가 잔존하지만 password_hash 가 이미 있는 user
#     — phase 7 의 WHERE password_hash IS NULL 가드가 이 row 를 다시 hash() 에 안 넘겨야 함.
_existing_hash = "$2b$12$ABCDEFGHIJKLMNOPQRSTUVabcdefghijklmnopqrstuvwxyz0123"  # 가짜지만 형태만 닮은 값
# users 에 name UNIQUE / name_norm UNIQUE 이므로 새 이름 사용.
_exec(
    "INSERT INTO users (name, name_norm, password, password_hash, role, team_id, is_active) "
    "VALUES (?,?,?,?,?,?,1)",
    ("guard_user_pw", database.normalize_name("guard_user_pw"), "PLAINTEXT_LEFTOVER", _existing_hash, "member", None),
)
_uid_pw = _query_one("SELECT id FROM users WHERE name = 'guard_user_pw'")["id"]

# (b) team_id 이미 채워진 event — phase 4 data backfill 의 WHERE team_id IS NULL 가드.
#     team 하나 만들고 그 id 를 사용.
_exec("INSERT INTO teams (name, name_norm) VALUES (?,?)",
      ("GuardTeam", database.normalize_name("GuardTeam")))
_tid_guard = _query_one("SELECT id FROM teams WHERE name = 'GuardTeam'")["id"]
# events 컬럼 셋 확인 후 최소 컬럼만 채워 INSERT.
con = sqlite3.connect(DB_PATH)
try:
    _ev_cols = {r[1] for r in con.execute("PRAGMA table_info(events)")}
finally:
    con.close()
# 필수: title, start_datetime, created_by 정도. project_id 도 함께 채워 phase 6 가드도 검증.
_exec(
    "INSERT INTO events (title, start_datetime, created_by, team_id, project) "
    "VALUES (?,?,?,?,?)",
    ("guard event", "2026-01-01 09:00:00", str(_uid_pw), _tid_guard, "guard project"),
)
_ev_id = _query_one("SELECT id FROM events WHERE title = 'guard event'")["id"]
# project_id 도 직접 채워둔다 (이미 백필된 상태 흉내).
# 해당 team 의 project 를 하나 만들어 그 id 를 박는다.
_exec("INSERT INTO projects (team_id, name, name_norm, is_active) VALUES (?,?,?,1)",
      (_tid_guard, "guard project", database.normalize_name("guard project")))
_pj_id = _query_one("SELECT id FROM projects WHERE team_id = ? AND name = 'guard project'", (_tid_guard,))["id"]
_exec("UPDATE events SET project_id = ? WHERE id = ?", (_pj_id, _ev_id))

# (c) 'AdminTeam' 으로 이미 rename 된 팀 — phase 3 의 WHERE name='관리팀' lookup 이 no-op 여야.
#     teams.name UNIQUE 이므로 'AdminTeam' 이 이미 있으면 skip.
if not _query_one("SELECT 1 FROM teams WHERE name = 'AdminTeam'"):
    _exec("INSERT INTO teams (name, name_norm) VALUES (?,?)",
          ("AdminTeam", database.normalize_name("AdminTeam")))
_admin_team_row = _query_one("SELECT id, name FROM teams WHERE name = 'AdminTeam'")

# ── 변경 전 스냅샷 ──
_snap_pw_hash = _query_one("SELECT password_hash, password FROM users WHERE id = ?", (_uid_pw,))
_snap_ev = _query_one("SELECT team_id, project_id FROM events WHERE id = ?", (_ev_id,))
_snap_admin_name = _admin_team_row["name"]
_snap_admin_id = _admin_team_row["id"]
_admin_like_count_before = _query_one(
    "SELECT COUNT(*) AS c FROM teams WHERE name LIKE 'AdminTeam%' OR name = '관리팀'"
)["c"]

# ── 마커 강제 삭제 ──
_exec("DELETE FROM settings WHERE key LIKE ?", (MARKER_PREFIX + "%",))
_remaining = _markers_in_db()
_check(len(_remaining) == 0, "phase 마커 전부 삭제됨", f"남은={_remaining}" if _remaining else "")

# ── 재호출 ── (모든 phase 가 다시 돌지만 본문 WHERE 가드로 무해해야 함)
try:
    database.init_db()
    _check(True, "마커 삭제 후 init_db() 재호출 예외 없이 완료 (preflight 충돌 없음)")
except Exception as exc:  # noqa: BLE001
    _check(False, "마커 삭제 후 init_db() 재호출 예외 없이 완료", repr(exc))

# ── 마커 재기록 확인 ──
_after3 = _markers_in_db()
_missing3 = [k for k in PHASE_KEYS if k not in _after3]
_check(not _missing3, "재호출 후 모든 phase 마커 재기록", f"누락={_missing3}" if _missing3 else "")

# ── 데이터 무결성 검증 ──
_post_pw = _query_one("SELECT password_hash, password FROM users WHERE id = ?", (_uid_pw,))
_check(_post_pw["password_hash"] == _snap_pw_hash["password_hash"],
       "phase 7 가드: 이미 hash 보유 row 의 password_hash 불변 (재-hash 안 됨)",
       f"{_snap_pw_hash['password_hash'][:12]}... → {_post_pw['password_hash'][:12]}...")
# password 평문은 phase 7 가드 (WHERE password_hash IS NULL) 에 안 걸리므로 그대로 잔존이 정상.
_check(_post_pw["password"] == _snap_pw_hash["password"],
       "phase 7 가드: 평문 password 잔존 row 는 가드에 안 걸려 손대지 않음 (의도된 동작)",
       f"password={_post_pw['password']!r}")

_post_ev = _query_one("SELECT team_id, project_id FROM events WHERE id = ?", (_ev_id,))
_check(_post_ev["team_id"] == _snap_ev["team_id"],
       "phase 4-data 가드: 이미 team_id 채운 event 덮어쓰지 않음",
       f"team_id {_snap_ev['team_id']} → {_post_ev['team_id']}")
_check(_post_ev["project_id"] == _snap_ev["project_id"],
       "phase 6 가드: 이미 project_id 채운 event 덮어쓰지 않음",
       f"project_id {_snap_ev['project_id']} → {_post_ev['project_id']}")

_post_admin = _query_one("SELECT name FROM teams WHERE id = ?", (_snap_admin_id,))
_check(_post_admin is not None and _post_admin["name"] == _snap_admin_name,
       "phase 3 가드: 'AdminTeam' 팀 이름 불변 (관리팀 lookup no-op, legacy 이름 안 생김)",
       f"name={_post_admin['name'] if _post_admin else None!r}")
_admin_like_count_after = _query_one(
    "SELECT COUNT(*) AS c FROM teams WHERE name LIKE 'AdminTeam%' OR name = '관리팀'"
)["c"]
_check(_admin_like_count_before == _admin_like_count_after,
       "phase 3 가드: AdminTeam/관리팀 류 팀 수 불변 (중복 rename 없음)",
       f"{_admin_like_count_before} → {_admin_like_count_after}")
print()

# ════════════════════════════════════════════════════════════
# 정리 + 결과 출력
# ════════════════════════════════════════════════════════════
try:
    shutil.rmtree(_tmp_root, ignore_errors=True)
except Exception:  # noqa: BLE001
    pass

_passed = sum(1 for ok, _, _ in _results if ok)
_failed = sum(1 for ok, _, _ in _results if not ok)
print("=" * 60)
print(f"결과: {_passed} PASS / {_failed} FAIL  (총 {_passed + _failed})")
if _failed:
    print("실패 항목:")
    for ok, label, detail in _results:
        if not ok:
            print(f"  - {label}: {detail}")
sys.exit(1 if _failed else 0)
