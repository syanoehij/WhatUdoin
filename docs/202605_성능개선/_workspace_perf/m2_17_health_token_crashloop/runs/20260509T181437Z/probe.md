# M2-17 probe — 20260509T181437Z

## 결과 요약

| 항목 | 결과 |
|------|------|
| A1 헤더 없음 → 401 | PASS |
| A2 잘못된 토큰 → 401 | PASS |
| A3 올바른 토큰 → 200 | PASS |
| A4 env 미설정+헤더 없음 → 401 | PASS |
| A5 secrets.compare_digest 사용 | PASS |
| B1 SSE /healthz → 200 | PASS |
| B2 SSE status=ok | PASS |
| B3 SSE service=sse | PASS |
| B4 SSE subscribers key | PASS |
| B5 WebAPI /healthz → 200 | PASS |
| B6 WebAPI status=ok | PASS |
| B7 WebAPI service=web-api | PASS |
| C1 3회 누적 → crash-loop 감지 | PASS |
| C2 start_service → degraded | PASS |
| C3 crash-loop blocked message | PASS |
| C4 pid None (spawn 없음) | PASS |
| C5 reset 후 status=stopped | PASS |
| C6 reset 후 crash_history 비어있음 | PASS |
| C7 reset 후 start → running | PASS |
| D1 probe 200+ok → True | PASS |
| D2 probe 연결 불가 → False | PASS |
| D3 probe 404 → False | PASS |

**TOTAL 22 / PASS 22 / FAIL 0**

## 상수
- CRASH_LOOP_WINDOW_SECONDS = 300
- CRASH_LOOP_MAX_FAILURES = 3

## /healthz 응답 구조
- SSE service: `{"status": "ok", "subscribers": <int>, "service": "sse"}`
- Web API: `{"status": "ok", "service": "web-api"}`