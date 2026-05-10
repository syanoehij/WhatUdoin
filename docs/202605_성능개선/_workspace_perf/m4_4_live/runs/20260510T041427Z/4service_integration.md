# M4-4 Live 4 Service Integration Probe

**실행 시각**: 20260510T041427Z
**Python**: D:\Program Files\Python\Python312\python.exe
**STOP_ORDER**: ['ollama', 'sse', 'scheduler', 'web-api']

## 서비스 포트 할당

| service | port | 모드 |
|---------|------|------|
| ollama | 63165 | LIVE |
| sse | 63166 | LIVE |
| scheduler | 63167 | LIVE |
| web-api | mock | LIVE |

## 결과

| # | 항목 | 결과 | 상세 |
|---|------|------|------|
| 1 | ensure_internal_token: path exists | PASS |  |
| 2 | ollama_service_spec: name=ollama | PASS |  |
| 3 | sse_service_spec: name=sse | PASS |  |
| 4 | scheduler_service_spec: name=scheduler | PASS |  |
| 5 | web_api_service_spec: name=web-api | PASS |  |
| 6 | ollama spawn: status not failed | PASS | status=running |
| 7 | sse spawn: status not failed | PASS | status=running |
| 8 | sched spawn: status not failed | PASS | status=running |
| 9 | web_api mock spawn: status not failed | PASS | status=running |
| 10 | ollama 포트 open 대기 | PASS | port=63165 |
| 11 | ollama probe_healthz PASS | PASS | status=ok error= |
| 12 | sse 포트 open 대기 | PASS | port=63166 |
| 13 | sse probe_healthz PASS | PASS | status=ok error= |
| 14 | sched 포트 open 대기 | PASS | port=63167 |
| 15 | sched probe_healthz PASS | PASS | status=ok error= |
| 16 | web_api mock: process alive | PASS | pid=14508 |
| 17 | stop_all: 10초 이내 완료 | PASS | elapsed=2.03s |
| 18 | ollama: status=stopped | PASS | status=stopped |
| 19 | sse: status=stopped | PASS | status=stopped |
| 20 | scheduler: status=stopped | PASS | status=stopped |
| 21 | web-api: status=stopped | PASS | status=stopped |

## 종료 시간

- stop_all() elapsed: 2.03s
- cap: 10.0s

## 총계

**21/21 PASS**