import sqlite3
import time
from datetime import datetime
from pathlib import Path

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
    _cleanup(bdir)
    return target


def _cleanup(bdir: Path):
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 86400
    for f in bdir.glob("whatudoin-*.db"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
