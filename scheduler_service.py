"""Scheduler service — APScheduler 단독 프로세스.

M3-2: APScheduler 인스턴스를 Web API와 분리하여 별도 프로세스로 실행한다.

진입점: python scheduler_service.py
환경변수:
  WHATUDOIN_SCHEDULER_PORT (기본 8766) — healthz bind port
  WHATUDOIN_BASE_DIR / WHATUDOIN_RUN_DIR  — 경로 해석 (app.py 패턴 동일)

모듈 import 시 부작용 0: 스케줄러 인스턴스, DB init, Starlette app 모두
if __name__ == "__main__": 내부 main()에서만 생성.
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


def main() -> None:
    # ── 경로 해석 (app.py 패턴 동일) ────────────────────────────────────────
    _BASE_DIR = Path(os.environ.get("WHATUDOIN_BASE_DIR", Path(__file__).parent))
    _RUN_DIR  = Path(os.environ.get("WHATUDOIN_RUN_DIR",  Path(__file__).parent))

    bind_host = os.environ.get("WHATUDOIN_SCHEDULER_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("WHATUDOIN_SCHEDULER_PORT", "8766"))

    # ── DB 초기화 (idempotent) ───────────────────────────────────────────────
    import database as db
    db.init_db()

    # ── Starlette healthz app (with async lifespan) ──────────────────────────
    import asyncio
    from contextlib import asynccontextmanager

    import uvicorn
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    import backup

    scheduler: AsyncIOScheduler | None = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal scheduler
        scheduler = AsyncIOScheduler()

        # ── cron 5종 등록 ────────────────────────────────────────────────────
        # 1분마다 15분 후 일정 알람 체크
        scheduler.add_job(db.check_upcoming_event_alarms, "interval", minutes=1)
        # 매일 03:00 DB 야간 백업
        scheduler.add_job(
            lambda: backup.run_backup(db.DB_PATH, _RUN_DIR),
            "cron", hour=3, minute=0,
            id="daily-db-backup", replace_existing=True,
        )
        # 매일 03:05 done 7일 경과 일정 자동 완료 처리
        scheduler.add_job(db.finalize_expired_done, "cron", hour=3, minute=5)
        # 매일 03:10 오래된 백업 파일 정리 (90일 보관)
        scheduler.add_job(
            lambda: backup.cleanup_old_backups(_RUN_DIR),
            "cron", hour=3, minute=10,
            id="daily-backup-cleanup", replace_existing=True,
        )
        # 매일 03:20 휴지통 90일 초과 항목 정리
        scheduler.add_job(db.cleanup_old_trash, "cron", hour=3, minute=20)
        # 매일 03:30 고아 이미지 파일 정리
        scheduler.add_job(
            lambda: backup.cleanup_orphan_images(_RUN_DIR, db),
            "cron", hour=3, minute=30,
            id="daily-orphan-image-cleanup", replace_existing=True,
        )

        scheduler.start()

        # startup 콜백: finalize_expired_done 1회 즉시 실행
        # (owner 표: finalize_expired_done = scheduler 단독)
        db.finalize_expired_done()

        yield

        if scheduler.running:
            scheduler.shutdown(wait=False)

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "scheduler"})

    app = Starlette(
        lifespan=lifespan,
        routes=[Route("/healthz", healthz)],
    )

    # ── graceful shutdown (SIGTERM) ──────────────────────────────────────────
    _server: uvicorn.Server | None = None

    def _handle_sigterm(signum, frame):
        if _server is not None:
            _server.should_exit = True

    if os.name != "nt":
        signal.signal(signal.SIGTERM, _handle_sigterm)

    config = uvicorn.Config(
        app,
        host=bind_host,
        port=port,
        log_level="info",
    )
    _server = uvicorn.Server(config)
    _server.run()


if __name__ == "__main__":
    main()
