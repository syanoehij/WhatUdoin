# M2-20 50 SSE 부하 시뮬레이션 Probe

**실행 시각**: 20260509T184932Z
**SSE port**: 50680
**목표 연결 수**: 50
**경과 시간**: 31.5s

## 측정값 요약

| 항목 | 측정값 | SLA | 판정 |
|---|---|---|---|
| SSE 연결 성공 | 50/50 | ≥90% | PASS |
| event 수신 도달 | 50/50 | ≥90% | PASS |
| publish→수신 p95 | 0.0ms | <2000ms | PASS |
| publish→수신 avg | 0.0ms | — | — |
| subscribers=0 복귀 | 0 | ==0 | PASS |
| /healthz p95 (SSE svc) | 16.0ms | <500ms | PASS |
| /healthz avg | 2.5ms | — | — |

## 시나리오 결과

| # | 항목 | 결과 | 상세 |
|---|---|---|---|
| 1 | sse_service spawn | PASS | status=running pid=24704 |
| 2 | sse_service /healthz ok | PASS | status=ok error= |
| 3 | 50 SSE initial connect: 50/50 | PASS | 50/50 in 219ms |
| 4 | publish → 200 | PASS | got 200 |
| 5 | publish→수신: 50/50 연결 도달 | PASS | 50/50 |
| 6 | publish→수신 p95 < 2000.0ms | PASS | p95=0.0ms avg=0.0ms n=50 |
| 7 | subscribers → 0 after all disconnect | PASS | subscribers=0 |
| 8 | /healthz 동시 50회: 50/50 200 OK | PASS | 50/50 |
| 9 | /healthz p95 < 500.0ms | PASS | p95=16.0ms avg=2.5ms |
| 10 | stop_all: status=stopped | PASS |  |

## 총계

**10/10 PASS**, 경과=31.5s

## 범위 제한 (선언)

- **Web API /healthz 라이브 spawn 제외**: 60초 cap + SQLite WAL lock 위험 + DB init 부하로 SSE service만 spawn.
  SSE service /healthz 50회 동시 요청으로 JSON GET API 회귀 대역 측정함.
- **EventSource 인증 세션 제외**: SSE service 단독 spawn이라 쿠키 기반 인증 없음.
  라이브 브라우저 인증 SSE는 M2-0 gate probe에서 별도 측정됨.