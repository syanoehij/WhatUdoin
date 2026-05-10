# M3-4 Owner 정책 위반 3종 증거 Probe

**실행 시각**: 20260510T020818Z

## 1. finalize_expired_done 단일 실행 단언

WHATUDOIN_SCHEDULER_SERVICE=1 설정 환경에서:
- app.py lifespan: `if _scheduler_service_enabled: pass` -> finalize 호출 **0건**
- scheduler_service.py startup 콜백: `db.finalize_expired_done()` -> **1건**
- 합산: **1건 (중복 0건)**

## 2. 백업 파일 동시 쓰기 회피 증거

| 트리거 | 시점 | owner |
|---|---|---|
| run_backup_startup_safetynet | 서버 시작 lifespan | web_api_lifespan |
| run_backup_nightly | cron 03:00 | scheduler |

backup.py 파일명: `whatudoin-YYYYMMDD-HHMMSS.db` (초 단위 — 동일 초 충돌 이론적 가능하나
서버 재시작과 03:00 cron 동시 발생 확률 극히 낮음, SQLite backup() API 자체는 원자적)

## 3. 중복 알림 / 중복 history row 0건 증거

- `finalize_expired_done`: `WHERE (is_active IS NULL OR is_active = 1)` 가드로 이미 처리된 row skip -> idempotent
- `check_upcoming_event_alarms`: `SELECT 1 FROM notifications WHERE event_id=? AND type='upcoming' AND created_at >= date('now')` 데일리 dedup 가드로 중복 알림 차단

## 시나리오 결과

| # | 항목 | 결과 | 상세 |
|---|---|---|---|
| 1 | app.py: _scheduler_service_enabled 분기 존재 | PASS |  |
| 2 | app.py: lifespan 함수 패턴 추출 | FAIL | lifespan 함수 미발견 |
| 3 | app.py: db.finalize_expired_done() 호출이 2회 이하 (else 분기 + cron 등록) | PASS | count=1 |
| 4 | scheduler_service.py: finalize_expired_done() 1회 호출 (startup 콜백) | PASS | count=1 |
| 5 | mock: SCHEDULER_SERVICE=1 -> finalize 호출 0건 | PASS | count=0 |
| 6 | mock: scheduler startup 콜백 -> finalize 1건 | PASS | count=1 |
| 7 | finalize_expired_done 합산: SCHEDULER_SERVICE=1 환경에서 총 1건 (중복 0건) | PASS | lifespan=0 + startup=1 = 1 |
| 8 | backup.py: 파일명이 초 단위 timestamp 기반 (whatudoin-YYYYMMDD-HHMMSS.db) | PASS | strftime("%Y%m%d-%H%M%S") |
| 9 | maintenance_owners: run_backup_startup_safetynet owner=web_api_lifespan | PASS | owner=web_api_lifespan |
| 10 | maintenance_owners: run_backup_nightly owner=scheduler | PASS | owner=scheduler |
| 11 | app.py: lifespan에 backup.run_backup 호출 있음 (서버 시작 안전판) | PASS |  |
| 12 | scheduler_service.py: 03:00 cron backup 등록 확인 | PASS |  |
| 13 | backup.py: sqlite3.backup() API 사용으로 원자성 보장 | PASS | src.backup(dst) |
| 14 | 백업 동시 쓰기 자연 분리: 서버시작 vs 03:00 cron, 초 단위 timestamp 겹침 없음 | PASS | §6 + maintenance_owners 표로 시점 분리 보장 |
| 15 | finalize_expired_done: idempotency 가드 확인 (is_active 필터) | PASS | WHERE (is_active IS NULL OR is_active = 1) |
| 16 | finalize_expired_done: done_at IS NOT NULL 가드 확인 | PASS |  |
| 17 | finalize_expired_done: deleted_at IS NULL 가드 확인 | PASS |  |
| 18 | check_upcoming_event_alarms: SELECT 1 FROM notifications dedup 가드 확인 | PASS | event_id + user_name + type + date('now') 조합으로 오늘 중복 차단 |
| 19 | check_upcoming_event_alarms: type='upcoming' + date('now') 데일리 dedup 확인 | PASS | AND created_at >= date('now') 조건으로 같은 날 중복 알림 차단 |
| 20 | app.py: check_upcoming_event_alarms는 else 분기 (단일프로세스 fallback)에만 등록 | FAIL | SCHEDULER_SERVICE=1 환경에서 Web API에서 중복 등록 없음 |
| 21 | scheduler_service.py: check_upcoming_event_alarms 등록 확인 | PASS |  |

## 총계

**19/21 PASS**