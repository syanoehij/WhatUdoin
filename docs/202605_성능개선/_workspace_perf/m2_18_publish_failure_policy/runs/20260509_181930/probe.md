# M2-18 publish 실패 유실 정책 probe 결과

실행: 2026-05-09 18:19 UTC

## publisher.py 구조

- logger: `logging.getLogger("publisher")` (module-level)
- 카운터: `sse_publish_failure: int` (global)
- 메타: `_failure_meta: deque(maxlen=50)` — (timestamp, event_name, reason) 튜플
- lock: `_failure_lock = threading.Lock()` — 카운터+deque 원자적 갱신
- 헬퍼: `get_failure_snapshot()` → `{count, last_event, last_reason, last_at}`
- IPC 브랜치: try/except → `_record_failure(event, str(exc))`
- in-process 브랜치: try/except → `_record_failure(event, str(exc))`
- logger.warning extra: `{event, reason}` — token/Authorization 제외

## /healthz 새 키

- `sse_publish_failures: int`
- `sse_publish_last_event: str|None`
- `sse_publish_last_at: float|None`

## 테스트 결과

| 파일 | PASS | FAIL |
|------|------|------|
| phase60_sse_publish_failure_policy.py | 20 | 0 |
| m2_18_publish_failure_policy_probe.py | 17 | 0 |

## 회귀 결과

| phase | PASS | FAIL |
|-------|------|------|
| phase54 | PASS | - |
| phase55 | PASS | - |
| phase56 | PASS | - |
| phase57 | PASS | - |
| phase58 | 19 | 0 |
| phase59 | 25 | 0 |
