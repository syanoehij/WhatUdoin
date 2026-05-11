"""QA: 마이그레이션 dedup phase ordering 버그 수정 검증.

`_run_phase_migrations()`가 `_PRE_PREFLIGHT_PHASES`(=team_phase_5a)를 preflight 앞에서
실행하는지, 그래서 안전 dedup → preflight → #5 인덱스 생성 순서가 보장되는지 확인한다.

합성 임시 DB만 사용한다 (실서버 X). 각 케이스는:
  - "phase 1·2 적용 후" 상태의 합성 스키마(projects: team_id, name_norm 컬럼 포함)를 만들고,
  - phase 5a / 5 *외* 모든 phase 마커를 미리 set (그 phase 본문이 합성 DB에서 안 돌도록),
  - database.DB_PATH / _RUN_DIR 를 임시 경로로 monkeypatch,
  - database._run_phase_migrations() 직접 호출,
  - settings 마커 / 인덱스 / team_migration_warnings / 예외 여부를 검증.

케이스:
  1. safe-only 충돌: 같은 (team_id, name_norm)에 참조 0건 중복 row 2개
     → 5a가 1개 삭제(MIN id 보존) → preflight 통과 → #5 인덱스 생성.
     기대: 5a 마커 set, 5 마커 set, idx_projects_team_name 존재, dedup_projects_auto warning, 예외 없음.
  2. unsafe 충돌 (discriminator): 같은 그룹 2 row 모두 events.project_id가 참조
     → 5a는 안전 정리할 게 없어 cleanly return(둘 다 보존) → preflight가 2건 충돌 잡음 → RuntimeError.
     기대: 5a 마커 set (돌긴 돌았음), 5 마커 *미*set, preflight_projects_team_name warning, RuntimeError 발생.
     ※ 5a 마커가 미set이면 러너 버그(pre-preflight가 preflight 실패로 롤백된 것).
  3. 충돌 0건 회귀: 중복 없음 → 5a 노옵 → preflight 통과 → #5 인덱스 생성. 예외 없음.
  4. 재호출 skip: 케이스 3 DB로 _run_phase_migrations() 재호출 → _pending_phases() 빈 리스트
     → 즉시 반환, 백업·preflight·phase 본문 전부 skip. 마커·데이터 불변, 예외 없음.

실행:
  "D:\\Program Files\\Python\\Python312\\python.exe" .claude/workspaces/current/scripts/verify_dedup_phase_ordering.py
"""
import gc
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

import database as db  # noqa: E402

_PASS = []
_FAIL = []


@contextmanager
def _tmpdir():
    """Windows에서 backup.py가 백업 파일 핸들을 GC까지 안 닫으므로,
    cleanup 전에 gc.collect() + rmtree(ignore_errors=True)."""
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        gc.collect()
        shutil.rmtree(d, ignore_errors=True)


def _assert(cond, msg):
    if cond:
        _PASS.append(msg)
        print(f"  PASS: {msg}")
    else:
        _FAIL.append(msg)
        print(f"  FAIL: {msg}")


# ── 합성 스키마 (phase 1·2 적용 후 상태의 최소 부분집합) ──────────────────
_MARKER_PREFIX = "migration_phase:"

# 5a / 5 외에 PHASES에 등록된 모든 phase 이름. 케이스 1~3에서 미리 마커를 찍어
# 합성 DB에서 그 본문이 안 돌게 한다 (합성 스키마엔 그 phase가 기대하는 테이블이 없을 수 있음).
def _other_phase_names():
    keep = {"team_phase_5a_projects_dedup_safe_v1", "team_phase_5_projects_unique_v1"}
    return [n for n, _ in db.PHASES if n not in keep]


def _build_synthetic_db(path, project_rows, event_project_ids=()):
    """project_rows: list of (id, name, team_id, name_norm). event_project_ids: events.project_id 값들.
    settings에 5a/5 외 모든 phase 마커 + 빈 team_migration_warnings 시드."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "CREATE TABLE projects ("
        " id INTEGER PRIMARY KEY, name TEXT, team_id INTEGER, name_norm TEXT, "
        " color TEXT, is_active INTEGER DEFAULT 1)"
    )
    conn.execute(
        "CREATE TABLE events ("
        " id INTEGER PRIMARY KEY, title TEXT, project_id INTEGER, deleted_at TEXT)"
    )
    for (pid, name, team_id, name_norm) in project_rows:
        conn.execute(
            "INSERT INTO projects (id, name, team_id, name_norm) VALUES (?,?,?,?)",
            (pid, name, team_id, name_norm),
        )
    for i, pid in enumerate(event_project_ids, start=1):
        conn.execute(
            "INSERT INTO events (id, title, project_id) VALUES (?,?,?)",
            (i, f"ev{i}", pid),
        )
    for n in _other_phase_names():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            (_MARKER_PREFIX + n, "2026-01-01T00:00:00+00:00"),
        )
    conn.commit()
    conn.close()


def _run_migrations_on(path):
    """database.DB_PATH / _RUN_DIR 를 임시로 바꿔치고 _run_phase_migrations() 호출.
    반환: 발생한 예외 (없으면 None)."""
    old_db_path = db.DB_PATH
    old_run_dir = db._RUN_DIR
    backup_dir = Path(path).parent
    try:
        db.DB_PATH = str(path)
        db._RUN_DIR = backup_dir
        try:
            db._run_phase_migrations()
            return None
        except Exception as exc:  # noqa: BLE001
            return exc
    finally:
        db.DB_PATH = old_db_path
        db._RUN_DIR = old_run_dir


def _markers(path):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT key FROM settings WHERE key LIKE ?", (_MARKER_PREFIX + "%",)
        ).fetchall()
        return {r[0][len(_MARKER_PREFIX):] for r in rows}
    finally:
        conn.close()


def _warnings_categories(path):
    import json
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'team_migration_warnings'"
        ).fetchone()
        if not row or not row[0]:
            return []
        try:
            data = json.loads(row[0])
        except Exception:  # noqa: BLE001
            return []
        # 형태가 list[dict{category,...}] 또는 dict{category: [...]} 둘 다 대응
        if isinstance(data, list):
            return [d.get("category") for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            return list(data.keys())
        return []
    finally:
        conn.close()


def _has_index(path, idx_name):
    conn = sqlite3.connect(path)
    try:
        return bool(conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (idx_name,)
        ).fetchone())
    finally:
        conn.close()


def _project_ids(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute("SELECT id FROM projects").fetchall())
    finally:
        conn.close()


# ── 케이스 1: safe-only 충돌 ──────────────────────────────────────────────
def case_1():
    print("\n[case 1] safe-only 충돌 — 5a가 정리 후 #5 인덱스 생성")
    with _tmpdir() as tmp:
        path = os.path.join(tmp, "syn1.db")
        _build_synthetic_db(
            path,
            project_rows=[
                (101, "Alpha", 7, "alpha"),
                (102, "Alpha", 7, "alpha"),  # 중복, 참조 0건
                (103, "Beta", 7, "beta"),    # 충돌 없음
            ],
            event_project_ids=[103],  # Beta만 참조 (Alpha 그룹은 참조 0건)
        )
        exc = _run_migrations_on(path)
        _assert(exc is None, f"case1: 예외 없이 완료 (실제 exc={exc!r})")
        m = _markers(path)
        _assert("team_phase_5a_projects_dedup_safe_v1" in m, "case1: 5a 마커 set")
        _assert("team_phase_5_projects_unique_v1" in m, "case1: #5 마커 set")
        _assert(_has_index(path, "idx_projects_team_name"), "case1: idx_projects_team_name 생성됨")
        _assert(_project_ids(path) == [101, 103], f"case1: 중복 102 삭제·101/103 보존 (실제={_project_ids(path)})")
        cats = _warnings_categories(path)
        _assert("dedup_projects_auto" in cats, f"case1: dedup_projects_auto warning 기록 (실제 cats={cats})")
        _assert("preflight_projects_team_name" not in cats, "case1: preflight 충돌 warning 없음")


# ── 케이스 2: unsafe 충돌 (discriminator) ─────────────────────────────────
def case_2():
    print("\n[case 2] unsafe 충돌 — 5a 노옵·preflight가 RuntimeError (discriminator)")
    with _tmpdir() as tmp:
        path = os.path.join(tmp, "syn2.db")
        _build_synthetic_db(
            path,
            project_rows=[
                (201, "Gamma", 9, "gamma"),
                (202, "Gamma", 9, "gamma"),  # 중복
            ],
            event_project_ids=[201, 202],  # 두 row 모두 참조 → unsafe (delete 없음 → 5a 노옵)
        )
        exc = _run_migrations_on(path)
        _assert(isinstance(exc, RuntimeError), f"case2: RuntimeError 발생 (실제 exc={exc!r})")
        m = _markers(path)
        # ★ 핵심: 5a는 돌긴 돌았으므로 마커 set이어야 함. 미set이면 pre-preflight가 preflight 실패로 롤백된 것 = 러너 버그.
        _assert("team_phase_5a_projects_dedup_safe_v1" in m,
                "case2: 5a 마커 set (preflight 실패에도 pre-preflight 마커는 커밋됨) — discriminator")
        _assert("team_phase_5_projects_unique_v1" not in m, "case2: #5 마커 미set (preflight가 막음)")
        _assert(_project_ids(path) == [201, 202], f"case2: unsafe 그룹 두 row 모두 보존 (실제={_project_ids(path)})")
        _assert(not _has_index(path, "idx_projects_team_name"), "case2: idx_projects_team_name 미생성")
        cats = _warnings_categories(path)
        _assert("preflight_projects_team_name" in cats, f"case2: preflight_projects_team_name warning 기록 (실제 cats={cats})")


# ── 케이스 3: 충돌 0건 회귀 ───────────────────────────────────────────────
def _setup_case_3_db(tmp):
    path = os.path.join(tmp, "syn3.db")
    _build_synthetic_db(
        path,
        project_rows=[
            (301, "Delta", 5, "delta"),
            (302, "Epsilon", 5, "epsilon"),
        ],
        event_project_ids=[301],
    )
    return path


def case_3():
    print("\n[case 3] 충돌 0건 — 5a 노옵·preflight 통과·#5 인덱스 생성")
    with _tmpdir() as tmp:
        path = _setup_case_3_db(tmp)
        exc = _run_migrations_on(path)
        _assert(exc is None, f"case3: 예외 없이 완료 (실제 exc={exc!r})")
        m = _markers(path)
        _assert("team_phase_5a_projects_dedup_safe_v1" in m, "case3: 5a 마커 set")
        _assert("team_phase_5_projects_unique_v1" in m, "case3: #5 마커 set")
        _assert(_has_index(path, "idx_projects_team_name"), "case3: idx_projects_team_name 생성됨")
        _assert(_project_ids(path) == [301, 302], f"case3: 모든 row 보존 (실제={_project_ids(path)})")
        cats = _warnings_categories(path)
        _assert("dedup_projects_auto" not in cats, "case3: dedup warning 없음 (정리할 게 없었음)")
        _assert("preflight_projects_team_name" not in cats, "case3: preflight 충돌 없음")


# ── 케이스 4: 재호출 skip ─────────────────────────────────────────────────
def case_4():
    print("\n[case 4] 재호출 — _pending_phases() 빈 리스트 → 즉시 반환·전부 skip")
    with _tmpdir() as tmp:
        path = _setup_case_3_db(tmp)
        first_exc = _run_migrations_on(path)  # 먼저 정상 완료시켜 모든 마커 set
        _assert(first_exc is None, f"case4: 1차 init 예외 없음 (실제 exc={first_exc!r})")
        markers_before = _markers(path)
        ids_before = _project_ids(path)
        # 백업 디렉토리에 마이그레이션 백업 파일이 새로 생기는지 추적
        backup_dir = Path(tmp) / "backupDB"
        files_before = set(os.listdir(backup_dir)) if backup_dir.exists() else set()
        exc = _run_migrations_on(path)
        _assert(exc is None, f"case4: 재호출 예외 없음 (실제 exc={exc!r})")
        _assert(_markers(path) == markers_before, "case4: 마커 불변")
        _assert(_project_ids(path) == ids_before, "case4: projects 데이터 불변")
        files_after = set(os.listdir(backup_dir)) if backup_dir.exists() else set()
        new_files = files_after - files_before
        # 백업이 한 번 더 떴으면 _pending_phases가 빈 리스트가 아니었던 것 → 버그.
        _assert(not new_files, f"case4: 새 백업 파일 없음 (pending=0이라 백업 skip) — 실제 new={new_files}")


def main():
    print("=== verify_dedup_phase_ordering ===")
    case_1()
    case_2()
    case_3()
    case_4()
    print("\n=== 결과 ===")
    print(f"PASS {len(_PASS)} / FAIL {len(_FAIL)}")
    if _FAIL:
        for m in _FAIL:
            print(f"  FAIL: {m}")
        sys.exit(1)
    print("ALL PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
