"""
M1a-3: DB snapshot 복원 스크립트

목적:
  - snapshot_db.py 가 생성한 db_snapshot/ 에서 운영 DB를 복원한다.
  - cleanup 실패나 측정 중 데이터 손상 시 M1a baseline 측정 이전 상태로 되돌린다.

안전판 (실행 순서):
  1. WHATUDOIN_PERF_FIXTURE=allow 환경변수 확인
  2. 서버 종료 상태 전제 확인
  3. --confirm-overwrite 인자 확인 (없으면 abort)
  4. target DB 세트 존재 시 사이드카 백업 (.before-restore-<timestamp>)
  5. snapshot의 .db/.db-wal/.db-shm 세트 복원
  6. sqlite3 PRAGMA integrity_check 검증

사전 조건:
  - WHATUDOIN_PERF_FIXTURE=allow 환경변수 필수
  - WhatUdoin 서버가 종료된 상태
  - --confirm-overwrite 인자 명시 필수

환경변수:
  WHATUDOIN_PERF_FIXTURE       "allow" 로 설정 필수
  WHATUDOIN_DB_PATH            복원 대상 경로 override (기본: D:/Github/WhatUdoin/whatudoin.db)
  WHATUDOIN_PERF_BASELINE_DIR  baseline 디렉터리 override
                               (기본: <repo_root>/_workspace/perf/baseline_2026-05-09/)

사용법:
  WHATUDOIN_PERF_FIXTURE=allow python restore_db.py --confirm-overwrite
  WHATUDOIN_PERF_FIXTURE=allow python restore_db.py --confirm-overwrite --snapshot-dir /path/to/db_snapshot
  # PowerShell:
  $env:WHATUDOIN_PERF_FIXTURE="allow"; python _workspace/perf/scripts/restore_db.py --confirm-overwrite
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── 경로 상수 ──────────────────────────────────────────────────────────────────
# __file__ = <repo>/_workspace/perf/scripts/restore_db.py
# parents[0] = scripts/, [1] = perf/, [2] = _workspace/, [3] = <repo root>
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parents[2]

_DEFAULT_DB_PATH = str(_REPO_ROOT / "whatudoin.db")

_BASELINE_DATE = "2026-05-09"
_DEFAULT_BASELINE_DIR = _REPO_ROOT / "_workspace" / "perf" / f"baseline_{_BASELINE_DATE}"

_SNAPSHOT_SUBDIR = "db_snapshot"


# ── 인자 파싱 ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="M1a-3 DB snapshot 복원 스크립트"
    )
    parser.add_argument(
        "--confirm-overwrite",
        action="store_true",
        help="운영 DB 덮어쓰기를 명시적으로 허용 (없으면 ABORT)",
    )
    parser.add_argument(
        "--snapshot-dir",
        type=str,
        default=None,
        help="snapshot 디렉터리 경로 override (기본: baseline_dir/db_snapshot/)",
    )
    return parser.parse_args()


# ── 가드 ──────────────────────────────────────────────────────────────────────

def _check_guards(args: argparse.Namespace) -> tuple[str, Path]:
    """환경변수 + overwrite 인자 검사.
    통과하면 (db_path, snapshot_dir) 반환, 실패 시 sys.exit(1)."""

    # 1. 허용 환경변수 가드
    if os.environ.get("WHATUDOIN_PERF_FIXTURE") != "allow":
        print(
            "[ABORT] WHATUDOIN_PERF_FIXTURE=allow 환경변수가 설정되지 않았습니다.\n"
            "  운영 DB 오염 방지를 위해 이 스크립트는 명시적 승인 없이 실행되지 않습니다.\n"
            "  사용 예: WHATUDOIN_PERF_FIXTURE=allow python restore_db.py --confirm-overwrite",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. DB 경로 결정
    db_path = os.environ.get("WHATUDOIN_DB_PATH", _DEFAULT_DB_PATH)

    # 3. --confirm-overwrite 인자 확인
    if not args.confirm_overwrite:
        print(
            "[ABORT] --confirm-overwrite 인자가 없습니다.\n"
            "  이 스크립트는 운영 DB를 덮어씁니다. 명시적 확인이 필요합니다.\n"
            "  사용 예: WHATUDOIN_PERF_FIXTURE=allow python restore_db.py --confirm-overwrite",
            file=sys.stderr,
        )
        sys.exit(1)

    # 4. snapshot 디렉터리 결정
    if args.snapshot_dir:
        snapshot_dir = Path(args.snapshot_dir)
    else:
        baseline_dir_env = os.environ.get("WHATUDOIN_PERF_BASELINE_DIR")
        if baseline_dir_env:
            baseline_dir = Path(baseline_dir_env)
        else:
            baseline_dir = _DEFAULT_BASELINE_DIR
        snapshot_dir = baseline_dir / _SNAPSHOT_SUBDIR

    return db_path, snapshot_dir


def _db_set(path: Path) -> dict[str, Path]:
    return {
        "": path,
        "-wal": Path(str(path) + "-wal"),
        "-shm": Path(str(path) + "-shm"),
    }


# ── 복원 실행 ─────────────────────────────────────────────────────────────────

def _do_restore(db_path: str, snapshot_dir: Path) -> None:
    """snapshot_dir 에서 운영 DB 세트를 복원한다."""

    dst_db = Path(db_path)
    src_set = _db_set(snapshot_dir / dst_db.name)
    dst_set = _db_set(dst_db)
    src_db = src_set[""]
    if not src_db.exists():
        print(
            f"[ABORT] snapshot DB를 찾을 수 없습니다: {src_db}\n"
            f"  snapshot_db.py 를 먼저 실행해 snapshot을 생성하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    src_paths = {suffix: path for suffix, path in src_set.items() if path.exists()}
    target_paths = [path for path in dst_set.values() if path.exists()]

    # 사이드카 백업: target DB 세트 존재 시 복원 전 보관
    if target_paths:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        for target in target_paths:
            sidecar = target.parent / f"{target.name}.before-restore-{ts}"
            print(f"[INFO] 현재 DB 세트 파일을 사이드카로 보관: {sidecar}")
            shutil.copy2(target, sidecar)
    else:
        print(f"[INFO] target DB 없음 - 사이드카 생략: {dst_db}")

    # 복원 전 target sidecar 정리: snapshot에 없는 WAL/SHM은 삭제해야 stale WAL 재생을 막을 수 있다.
    for suffix in ("-wal", "-shm"):
        target = dst_set[suffix]
        if target.exists() and suffix not in src_paths:
            print(f"[INFO] snapshot에 없는 target sidecar 삭제: {target}")
            target.unlink()

    # 복원
    copied: list[str] = []
    for suffix, src in src_paths.items():
        dst = dst_set[suffix]
        print(f"[INFO] 복원 중: {src} -> {dst}")
        shutil.copy2(src, dst)
        copied.append(dst.name)

    # 무결성 검증
    print(f"[INFO] 무결성 검증 중: {dst_db}")
    conn = sqlite3.connect(str(dst_db))
    try:
        rows = conn.execute("PRAGMA integrity_check;").fetchall()
        result = rows[0][0] if rows else ""
        if result != "ok":
            print(
                f"[ABORT] PRAGMA integrity_check 실패: {result}\n"
                "  복원된 파일이 손상됐을 가능성이 있습니다.\n"
                "  사이드카 파일을 확인하거나 다른 snapshot을 사용하세요.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[OK]   무결성 검증 통과 (integrity_check: ok)")
    finally:
        conn.close()

    print(f"[OK]   복원 완료: {dst_db}")
    print(f"[OK]   복원 파일: {', '.join(copied)}")
    print(
        "\n다음 단계:\n"
        "  1. 서버 시작 후 정상 동작 확인\n"
        "  2. 필요 시 fixture seed_users.py 재실행 후 측정 재진행\n"
        "  상세: _workspace/perf/README.md"
    )


# ── 진입점 ────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()
    db_path, snapshot_dir = _check_guards(args)
    _do_restore(db_path, snapshot_dir)


if __name__ == "__main__":
    main()
