# SSE keep-alive 결과

실행 일시: 2026-05-09T12:12:52.069835+00:00
대상 호스트: https://localhost:8443
연결 수: 50
측정 시간: 65s

## 지표 3종

### (a) 연결 유지 성공률
- 성공: 50/50 = 100.0%
- 끊김: 0건
- 재연결 합계: 0건

### (b) publish -> 수신 지연 (inter-arrival 상대 측정)
  [한계] broker.py에 server-side timestamp 없음(id: 필드 미부여).
  inter-arrival 값은 대부분 ~25000ms(ping 주기) 구간에 분포.
  실제 이벤트(CRUD publish) latency는 M1c-10 이후 정확 측정 가능.
  - inter-arrival 평균: 100.1 ms
  - inter-arrival p95: 297.0 ms

### (c) QueueFull 발생 수
  - 클라이언트 추정: 0건
  - [한계] broker.py QueueFull은 silent drop. sequence id 없어 클라이언트 검출 불가.
  - M1c-10 단계에서 서버 측 카운터로 교체 예정.

## 상세 CSV
  D:\Github\WhatUdoin\_workspace\perf\baseline_2026-05-09\run_210621\sse_keepalive_211252.csv