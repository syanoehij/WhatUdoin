"""Scheduler service — APScheduler 단독 프로세스.

M3-2: APScheduler 인스턴스를 Web API와 분리하여 별도 프로세스로 실행한다.
M3-3: healthz 응답 확장 + 로그 회전 정책 적용.

진입점: python scheduler_service.py
환경변수:
  WHATUDOIN_SCHEDULER_PORT (기본 8766) — healthz bind port
  WHATUDOIN_BASE_DIR / WHATUDOIN_RUN_DIR  — 경로 해석 (app.py 패턴 동일)
  WHATUDOIN_SCHEDULER_LOG_DIR — APScheduler 로그 디렉토리 (미설정 시 RUN_DIR/logs/services)

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

    # ── 로그 회전 설정 (M3-3) ───────────────────────────────────────────────
    import logging
    import logging.handlers

    _log_dir_env = os.environ.get("WHATUDOIN_SCHEDULER_LOG_DIR")
    _log_dir = Path(_log_dir_env) if _log_dir_env else (_RUN_DIR / "logs" / "services")
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_path = _log_dir / "scheduler.app.log"

    _rot_handler = logging.handlers.RotatingFileHandler(
        str(_log_path),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=14,
        encoding="utf-8",
        delay=True,  # Windows 파일 락 방지
    )
    _rot_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s"
    ))
    logging.getLogger("apscheduler").addHandler(_rot_handler)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger().addHandler(_rot_handler)

    # ── 진입 시각 기록 (uptime 계산용) ─────────────────────────────────────
    from datetime import datetime, timezone
    _process_started_at = datetime.now(timezone.utc)

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

    # 상태 컨테이너 (closure 공유)
    _state: dict = {
        "scheduler": None,
        "last_finalize_at": None,
    }

    @asynccontextmanager
    async def lifespan(app):
        sched = AsyncIOScheduler()
        _state["scheduler"] = sched

        # ── cron 5종 등록 ────────────────────────────────────────────────────
        # 1분마다 15분 후 일정 알람 체크
        sched.add_job(db.check_upcoming_event_alarms, "interval", minutes=1)
        # 매일 03:00 DB 야간 백업
        sched.add_job(
            lambda: backup.run_backup(db.DB_PATH, _RUN_DIR),
            "cron", hour=3, minute=0,
            id="daily-db-backup", replace_existing=True,
        )
        # 매일 03:05 done 7일 경과 일정 자동 완료 처리
        sched.add_job(db.finalize_expired_done, "cron", hour=3, minute=5)
        # 매일 03:10 오래된 백업 파일 정리 (90일 보관)
        sched.add_job(
            lambda: backup.cleanup_old_backups(_RUN_DIR),
            "cron", hour=3, minute=10,
            id="daily-backup-cleanup", replace_existing=True,
        )
        # 매일 03:20 휴지통 90일 초과 항목 정리
        sched.add_job(db.cleanup_old_trash, "cron", hour=3, minute=20)
        # 매일 03:30 고아 이미지 파일 정리
        sched.add_job(
            lambda: backup.cleanup_orphan_images(_RUN_DIR, db),
            "cron", hour=3, minute=30,
            id="daily-orphan-image-cleanup", replace_existing=True,
        )

        sched.start()

        # startup 콜백: finalize_expired_done 1회 즉시 실행
        # (owner 표: finalize_expired_done = scheduler 단독)
        db.finalize_expired_done()
        _state["last_finalize_at"] = datetime.now(timezone.utc)

        yield

        if sched.running:
            sched.shutdown(wait=False)

    async def healthz(request: Request) -> JSONResponse:
        try:
            sched = _state["scheduler"]
            now = datetime.now(timezone.utc)
            uptime_seconds = int((now - _process_started_at).total_seconds())

            if sched is None or not sched.running:
                return JSONResponse({
                    "status": "starting",
                    "service": "scheduler",
                    "jobs_count": 0,
                    "next_run_at": None,
                    "last_finalize_expired_done_at": (
                        _state["last_finalize_at"].isoformat()
                        if _state["last_finalize_at"] else None
                    ),
                    "uptime_seconds": uptime_seconds,
                })

            jobs = sched.get_jobs()
            jobs_count = len(jobs)

            # 가장 빠른 next_run_time 추출
            next_run_at = None
            for job in jobs:
                jrt = getattr(job, "next_run_time", None)
                if jrt is not None:
                    if next_run_at is None or jrt < next_run_at:
                        next_run_at = jrt

            return JSONResponse({
                "status": "ok",
                "service": "scheduler",
                "jobs_count": jobs_count,
                "next_run_at": next_run_at.isoformat() if next_run_at else None,
                "last_finalize_expired_done_at": (
                    _state["last_finalize_at"].isoformat()
                    if _state["last_finalize_at"] else None
                ),
                "uptime_seconds": uptime_seconds,
            })
        except Exception as exc:
            return JSONResponse({
                "status": "degraded",
                "service": "scheduler",
                "jobs_count": 0,
                "next_run_at": None,
                "last_finalize_expired_done_at": None,
                "uptime_seconds": 0,
                "error": str(exc),
            })

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
