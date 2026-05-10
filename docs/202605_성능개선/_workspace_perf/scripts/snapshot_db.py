"""
M1a-3: 측정 전 DB snapshot 백업 스크립트

목적:
  - WhatUdoin 서버 종료 상태에서 whatudoin.db를 부하 측정용 baseline 디렉터리에 복사
  - 측정 도중 데이터 손상 또는 cleanup 실패 시 이 snapshot으로 복원 가능
  - M1b WAL 적용 전 별도 백업(backup.py)과는 다른 안전판 — 아래 "snapshot vs backup" 참조

안전판 (실행 순서):
  1. WHATUDOIN_PERF_FIXTURE=allow 환경변수 확인
  2. 서버 종료 상태 전제 확인 (WAL/SHM 파일이 있으면 세트로 복사)
  3. target 디렉터리 충돌 처리 (timestamp suffix 자동 생성)
  4. shutil.copy2() 로 whatudoin.db / whatudoin.db-wal / whatudoin.db-shm 세트 복사
  5. sqlite3 PRAGMA integrity_check 검증

snapshot vs backup:
  - 이 스크립트: 오프라인 shutil.copy2 — 서버 종료 상태에서만 실행, WAL sidecar가
    남아 있으면 .db/.db-wal/.db-shm 세트로 함께 복사.
    M1a baseline 측정의 안전판 (fixture seed/cleanup 실패 대비).
  - backup.py run_backup(): sqlite3 backup API (온라인 가능) — M1b WAL 모드 활성화
    직전 별도 가드이며 WAL 파일 포함 일관된 복사를 보장하는 다른 메커니즘.

사전 조건:
  - WHATUDOIN_PERF_FIXTURE=allow 환경변수 필수
  - WhatUdoin 서버가 종료된 상태

환경변수:
  WHATUDOIN_PERF_FIXTURE       "allow" 로 설정 필수
  WHATUDOIN_DB_PATH            DB 파일 경로 override (기본: D:/Github/WhatUdoin/whatudoin.db)
  WHATUDOIN_PERF_BASELINE_DIR  baseline 디렉터리 override
                               (기본: <repo_root>/_workspace/perf/baseline_2026-05-09/)

사용법:
  WHATUDOIN_PERF_FIXTURE=allow python snapshot_db.py
  WHATUDOIN_PERF_FIXTURE=allow WHATUDOIN_DB_PATH=D:/path/to/whatudoin.db python snapshot_db.py
  # PowerShell:
  $env:WHATUDOIN_PERF_FIXTURE="allow"; python _workspace/perf/scripts/snapshot_db.py
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── 경로 상수 ──────────────────────────────────────────────────────────────────
# __file__ = <repo>/_workspace/perf/scripts/snapshot_db.py
# parents[0] = scripts/, [1] = perf/, [2] = _workspace/, [3] = <repo root>
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parents[2]

_DEFAULT_DB_PATH = str(_REPO_ROOT / "whatudoin.db")

# baseline 날짜 고정: step 시작 시점 2026-05-09
_BASELINE_DATE = "2026-05-09"
_DEFAULT_BASELINE_DIR = _REPO_ROOT / "_workspace" / "perf" / f"baseline_{_BASELINE_DATE}"

_SNAPSHOT_SUBDIR_BASE = "db_snapshot"


# ── 가드 ──────────────────────────────────────────────────────────────────────

def _check_guards() -> tuple[str, Path]:
    """환경변수 + 경로 검사.
    통과하면 (db_path, baseline_dir) 반환, 실패 시 sys.exit(1)."""

    # 1. 허용 환경변수 가드
    if os.environ.get("WHATUDOIN_PERF_FIXTURE") != "allow":
        print(
            "[ABORT] WHATUDOIN_PERF_FIXTURE=allow 환경변수가 설정되지 않았습니다.\n"
            "  운영 DB 오염 방지를 위해 이 스크립트는 명시적 승인 없이 실행되지 않습니다.\n"
            "  사용 예: WHATUDOIN_PERF_FIXTURE=allow python snapshot_db.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. DB 경로 결정
    db_path = os.environ.get("WHATUDOIN_DB_PATH", _DEFAULT_DB_PATH)

    # 3. baseline 디렉터리 결정 (env override 허용)
    baseline_dir_env = os.environ.get("WHATUDOIN_PERF_BASELINE_DIR")
    if baseline_dir_env:
        baseline_dir = Path(baseline_dir_env)
    else:
        baseline_dir = _DEFAULT_BASELINE_DIR

    return db_path, baseline_dir


def _db_set_paths(db_path: str) -> list[Path]:
    base = Path(db_path)
    paths = [base] if base.exists() else []
    for ext in ("-wal", "-shm"):
        p = Path(db_path + ext)
        if p.exists():
            paths.append(p)
    return paths


def _signature(paths: list[Path]) -> dict[str, tuple[int, int]]:
    return {
        p.name: (p.stat().st_size, p.stat().st_mtime_ns)
        for p in paths
    }


# ── snapshot 디렉터리 결정 ────────────────────────────────────────────────────

def _resolve_snapshot_dir(baseline_dir: Path) -> Path:
    """target snapshot 디렉터리 경로를 결정한다.

    - 기본 이름: baseline_dir/db_snapshot/
    - 이미 존재하면 timestamp suffix 자동 생성: db_snapshot_<HHMMSS>/
    - 무조건 덮어쓰기는 하지 않는다.
    """
    primary = baseline_dir / _SNAPSHOT_SUBDIR_BASE
    if not primary.exists():
        return primary

    # 이미 있으면 timestamp suffix
    ts = datetime.now(timezone.utc).strftime("%H%M%S")
    alt = baseline_dir / f"{_SNAPSHOT_SUBDIR_BASE}_{ts}"
    print(
        f"[INFO] '{primary}' 이 이미 존재합니다.\n"
        f"  새 snapshot 디렉터리: '{alt}'",
        file=sys.stderr,
    )
    return alt


# ── snapshot 실행 ─────────────────────────────────────────────────────────────

def _do_snapshot(db_path: str, snapshot_dir: Path) -> None:
    """DB 파일 세트를 snapshot_dir 에 복사하고 무결성을 검증한다."""

    src_db = Path(db_path)
    if not src_db.exists():
        print(
            f"[ABORT] DB 파일을 찾을 수 없습니다: {src_db}",
            file=sys.stderr,
        )
        sys.exit(1)

    source_paths = _db_set_paths(db_path)
    before = _signature(source_paths)
    sidecars = [p.name for p in source_paths if p != src_db]
    if sidecars:
        print(f"[INFO] WAL sidecar 세트 복사 대상: {sidecars}")
    else:
        print("[INFO] WAL sidecar 없음: whatudoin.db 단일 파일 복사")

    # 디렉터리 생성
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] snapshot 디렉터리: {snapshot_dir}")

    # .db/.db-wal/.db-shm 파일 세트 복사
    copied: list[str] = []
    for src in source_paths:
        dst = snapshot_dir / src.name
        print(f"[INFO] 복사 중: {src} -> {dst}")
        shutil.copy2(src, dst)
        copied.append(dst.name)

    after_paths = _db_set_paths(db_path)
    after = _signature(after_paths)
    if before != after:
        print(
            "[ABORT] snapshot 중 원본 DB 세트가 변경됐습니다.\n"
            "  서버가 실행 중이거나 다른 프로세스가 DB를 썼을 가능성이 있습니다.\n"
            "  서버 종료 상태를 확인한 뒤 다시 실행하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    # 무결성 검증: snapshot 본체를 열면 SQLite가 WAL을 checkpoint할 수 있으므로
    # 별도 검증 복사본에서만 integrity_check를 수행한다.
    verify_dir = snapshot_dir / ".integrity_check"
    if verify_dir.exists():
        shutil.rmtree(verify_dir)
    verify_dir.mkdir()
    for name in copied:
        shutil.copy2(snapshot_dir / name, verify_dir / name)
    verify_db = verify_dir / src_db.name
    print(f"[INFO] 무결성 검증 중: {verify_db}")
    conn = sqlite3.connect(str(verify_db))
    try:
        rows = conn.execute("PRAGMA integrity_check;").fetchall()
        result = rows[0][0] if rows else ""
        if result != "ok":
            print(
                f"[ABORT] PRAGMA integrity_check 실패: {result}\n"
                f"  snapshot 디렉터리는 분석용으로 보존됩니다: {snapshot_dir}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[OK]   무결성 검증 통과 (integrity_check: ok)")
    finally:
        conn.close()
        shutil.rmtree(verify_dir, ignore_errors=True)

    print(f"[OK]   snapshot 완료: {snapshot_dir}")
    print(f"[OK]   복사 파일: {', '.join(copied)}")
    print(
        "\n복원 절차:\n"
        f"  1. 서버 종료\n"
        f"  2. python _workspace/perf/scripts/restore_db.py --confirm-overwrite\n"
        f"     (기본 snapshot 경로: {snapshot_dir})\n"
        f"  3. 복원 후 서버 시작\n"
        f"  상세: _workspace/perf/README.md"
    )


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    db_path, baseline_dir = _check_guards()
    snapshot_dir = _resolve_snapshot_dir(baseline_dir)
    _do_snapshot(db_path, snapshot_dir)


if __name__ == "__main__":
    main()
