# M3-4 General API p95 + Database Locked 0건 Probe

**실행 시각**: 20260510T021043Z
**Scheduler port**: 51682
**총 소요 시간**: 2.7s

## 부하 설정

- 대상: Scheduler service /healthz (in-memory, DB I/O 없음)
- 동시 연결: 50개
- SLA: p95 < 500.0ms

## 측정 결과

| 지표 | 값 |
|---|---|
| p50 | 6.1ms |
| p95 | 12.9ms |
| p99 | 16.0ms |
| 성공 | 50/50 |
| 오류 | 0 |
| database is locked | 0건 |

## 시나리오 결과

| # | 항목 | 결과 | 상세 |
|---|---|---|---|
| 1 | start_service: status=running | PASS | status=running, pid=44024 |
| 2 | healthz: status=ok (startup 콜백 완료) | PASS | status=ok jobs_count=6 |
| 3 | p95 < 500.0ms | PASS | p95=12.9ms |
| 4 | p99 측정값 | PASS | p99=16.0ms |
| 5 | 동시 50회 오류 0건 | PASS | errors=0 |
| 6 | 'database is locked' 발생 0건 | PASS | count=0 checked=['D:\\Github\\WhatUdoin\\_workspace\\perf\\m3_4_live\\runs\\20260510T021043Z\\supervisor_run\\logs\\services\\scheduler.stderr.log', 'D:\\Github\\WhatUdoin\\_workspace\\perf\\m3_4_live\\runs\\20260510T021043Z\\supervisor_run\\logs\\services\\scheduler.stdout.log', 'D:\\Github\\WhatUdoin\\logs\\services\\scheduler.app.log'] |
| 7 | stop_all: service stopped | PASS | status=stopped |
| 8 | 전체 실행 60s 이내 완료 | PASS | elapsed=2.7s |

## 총계

**8/8 PASS**