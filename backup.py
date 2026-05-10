import sqlite3
import time
from datetime import datetime
from pathlib import Path

_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".txt", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".zip", ".7z"}
_CLEANUP_STOP_HOUR = 5  # 05:00에 중단

BACKUP_RETENTION_DAYS = 90


def _backup_dir(run_dir: Path) -> Path:
    d = run_dir / "backupDB"
    d.mkdir(exist_ok=True)
    return d


def run_backup(db_path: str, run_dir: Path) -> Path:
    bdir = _backup_dir(run_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = bdir / f"whatudoin-{ts}.db"
    with sqlite3.connect(db_path) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)
    return target


def run_migration_backup(db_path: str, run_dir: Path) -> Path:
    """마이그레이션 직전 1회용 백업.

    파일명: whatudoin-migrate-{YYYYMMDDTHHMMSSffffff}.db
    timestamp suffix(콜론 없는 압축형, microsecond 포함) — Windows 파일명 호환 +
    같은 초에 두 번 실행되어도 충돌 없음. `cleanup_old_backups`의 glob
    `whatudoin-*.db`에 매칭되어 90일 retention에 자연 포함된다.
    """
    bdir = _backup_dir(run_dir)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    target = bdir / f"whatudoin-migrate-{ts}.db"
    with sqlite3.connect(db_path) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)
    return target


def cleanup_old_backups(run_dir: Path):
    """90일 초과 백업 파일 삭제 (APScheduler에서 호출)"""
    bdir = _backup_dir(run_dir)
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    for f in bdir.glob("whatudoin-*.db"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def cleanup_orphan_images(run_dir: Path, db) -> None:
    """고아 이미지 파일을 하나씩 확인해 삭제. 05:00 이후 중단하고 다음날 이어서 처리."""
    meetings_dir = run_dir / "meetings"
    if not meetings_dir.exists():
        return

    all_files = sorted(
        f for f in meetings_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in _UPLOAD_EXTS
    )
    if not all_files:
        return

    cursor = db.get_setting("image_cleanup_cursor") or ""
    start_idx = 0
    if cursor:
        for i, f in enumerate(all_files):
            if f.relative_to(meetings_dir).as_posix() == cursor:
                start_idx = i + 1
                break

    stop_time = datetime.now().replace(hour=_CLEANUP_STOP_HOUR, minute=0, second=0, microsecond=0)

    for f in all_files[start_idx:]:
        if datetime.now() >= stop_time:
            return  # cursor는 마지막 처리 완료 파일로 이미 저장됨

        rel = f.relative_to(meetings_dir).as_posix()
        url = f"/uploads/meetings/{rel}"
        if not db.is_image_url_referenced(url):
            try:
                f.unlink()
            except OSError:
                pass

        db.set_setting("image_cleanup_cursor", rel)

    # 전체 스캔 완료 → 다음 실행 시 처음부터
    db.delete_setting("image_cleanup_cursor")
