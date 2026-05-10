# M3-4 Live Scheduler Integration Probe

**실행 시각**: 20260510T021035Z
**Python**: D:\Program Files\Python\Python312\python.exe
**Scheduler port**: 51676
**run_dir**: D:\Github\WhatUdoin\_workspace\perf\m3_4_live\runs\20260510T021035Z\supervisor_run

## 시나리오 결과

| # | 항목 | 결과 | 상세 |
|---|---|---|---|
| 1 | ensure_internal_token: path exists | PASS | D:\Github\WhatUdoin\_workspace\perf\m3_4_live\runs\20260510T021035Z\supervisor_run\internal_token |
| 2 | scheduler_service.py exists | PASS | D:\Github\WhatUdoin\scheduler_service.py |
| 3 | scheduler_service_spec: name=scheduler | PASS |  |
| 4 | scheduler_service_spec: port env set | PASS |  |
| 5 | scheduler_service_spec: SCHEDULER_SERVICE=1 in env | PASS |  |
| 6 | start_service: status=running | PASS | status=running, pid=26376 |
| 7 | healthz: 200 + status in {ok, starting} | PASS | status=ok error= |
| 8 | healthz: jobs_count >= 6 | PASS | jobs_count=6 |
| 9 | healthz: next_run_at 비어있지 않음 | PASS | next_run_at=2026-05-10T11:11:35.787457+09:00 |
| 10 | healthz: last_finalize_expired_done_at 채워짐 | PASS | last_finalize_expired_done_at=2026-05-10T02:10:35.794460+00:00 |
| 11 | healthz: uptime_seconds >= 0 | PASS | uptime_seconds=1 |
| 12 | stop_all: 5초 내 완료 | PASS | elapsed=0.50s |
| 13 | stop_all: service status=stopped | PASS | status=stopped |
| 14 | stop_all: process terminated | PASS | poll=1 |

## healthz JSON 스냅샷

```json
{
  "status": "ok",
  "service": "scheduler",
  "jobs_count": 6,
  "next_run_at": "2026-05-10T11:11:35.787457+09:00",
  "last_finalize_expired_done_at": "2026-05-10T02:10:35.794460+00:00",
  "uptime_seconds": 1
}
```

## 총계

**14/14 PASS**

## Windows note

stop_all()은 TerminateProcess(hard kill)이므로 SIGTERM graceful shutdown은 보장 안 됨.
5초 내 프로세스 종료를 shutdown 완료 기준으로 사용.