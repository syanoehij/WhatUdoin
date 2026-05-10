# M2-16 SSE Broker Relocation Probe Result

**Run:** 20260509T175426Z  **PASS:** 19  **FAIL:** 0

| # | Scenario | Status | Detail |
|---|----------|--------|--------|
| 1 | in-process: wu_broker.subscribe 큐 메시지 수신 | PASS | received=[('test.event', {'x': 1})] |
| 2 | IPC: mock 서버에 publish 도착 | PASS | arrived=[{'event': 'ipc.test', 'data': {'val': 42}}] |
| 3 | IPC: Authorization Bearer 헤더 전달 | PASS | Authorization='Bearer testtoken123' |
| 4 | IPC unreachable: publish 예외 미발생(silent) | PASS |  |
| 5 | IPC unreachable: sse_publish_failure 카운터 1 증가 | PASS | counter=1 |
| 6 | SSE service /api/stream: HTTP 200 | PASS | 200 |
| 7 | SSE service /api/stream: text/event-stream Content-Type | PASS |  |
| 8 | SSE service /api/stream: Cache-Control: no-cache | PASS |  |
| 9 | SSE service /api/stream: X-Accel-Buffering: no | PASS |  |
| 10 | SSE service /api/stream: ': connected' 첫 메시지 수신 | PASS | first=b': connected\n\n' |
| 11 | SSE service /internal/publish: loopback → 200 | PASS | status=200 |
| 12 | SSE service /internal/publish: 응답 ok=true | PASS |  |
| 13 | SSE service /internal/publish: 잘못된 JSON → 400 | PASS | status=400 |
| 14 | SSE service /internal/publish: data 누락 → 400 | PASS | status=400 |
| 15 | SSE service /internal/publish: 외부 IP → 403 | PASS | status=403 |
| 16 | app.py /api/stream: SSE_SERVICE_URL 설정 시 503 | PASS | status=503 |
| 17 | app.py /api/stream: 미설정 시 HTTP 200 | PASS | 200 |
| 18 | app.py /api/stream: 미설정 시 text/event-stream | PASS |  |
| 19 | app.py /api/stream: 미설정 시 ': connected' 수신 | PASS |  |