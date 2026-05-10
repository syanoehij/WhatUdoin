"""M3-1: startup maintenance 단일 owner 표 (§11).

각 key는 실제 함수명(또는 논리적 분리 키)이며, value는 'scheduler' 또는
'web_api_lifespan' 둘 중 하나다.

- scheduler       : APScheduler service(cron/interval/startup)가 단독 실행
- web_api_lifespan: FastAPI lifespan 시작 직전 안전판으로만 실행

M3-2 분리 시 이 표를 기준으로 이관 여부를 판단한다.
단일 owner 위반(두 service가 같은 job을 동시에 실행)은 회귀로 본다.
"""

MAINTENANCE_JOB_OWNERS: dict[str, str] = {
    # done 상태로 7일 경과한 일정 자동 완료 — Scheduler 단독
    # 현재: app.py lifespan(line 84) + APScheduler cron 03:05(line 95) 동거
    # M3-2에서 lifespan 직접 호출을 Scheduler service로 이관해야 함
    "finalize_expired_done": "scheduler",

    # 휴지통 90일 초과 항목 영구 삭제 — Scheduler 단독
    "cleanup_old_trash": "scheduler",

    # 15분 후 일정 알람 체크 (1분 interval) — Scheduler 단독
    "check_upcoming_event_alarms": "scheduler",

    # 시작 직전 DB 백업 안전판 — Web API lifespan 단독
    # (첫 부팅 빈 DB 백업 방지 포함, 야간 cron과 시각이 겹치지 않으면 합법)
    "run_backup_startup_safetynet": "web_api_lifespan",

    # 야간 DB 백업 (cron 03:00) — Scheduler 단독
    "run_backup_nightly": "scheduler",

    # 오래된 백업 파일 정리 90일 (cron 03:10) — Scheduler 단독
    "cleanup_old_backups": "scheduler",

    # 고아 이미지 파일 정리 (cron 03:30, 05:00 이후 중단) — Scheduler 단독
    "cleanup_orphan_images": "scheduler",
}
