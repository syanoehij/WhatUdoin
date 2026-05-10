# SSE PoC 결과 (placeholder)

이 파일은 M1a-6 산출물 구조 정의용 placeholder입니다.
실 실행은 사용자 승인 후 별도 사이클에서 진행합니다.

실행 방법:
  python _workspace/perf/scripts/sse_poc.py --n-list 10,30,50 --host https://192.168.0.18:8443

---

## 결과 스키마

실 실행 시 아래 형식으로 N=10/30/50 각각 결과가 기록됩니다.

## N=10 결과
- 시작 연결 수: 10
- 연결 성공: X / 10
- 60s 이내 끊김: X건
- timeout 발생: X건
- 수신 메시지/ping 합계: X건

### (a) 연결 유지 성공률
- 성공률: X/10 = X%
- 끊김 연결: X건

### (b) publish -> 수신 지연 (inter-arrival 상대 측정)
  [한계] broker.py가 server-side timestamp를 부여하지 않으므로
  publish 시점을 클라이언트에서 알 수 없다. 서버는 25초마다 ping을
  보내므로 대부분의 inter-arrival은 ~25000ms 구간에 분포한다.
  M1c-10 이후 broker QueueFull 카운터 도입 시 서버 측 측정으로 대체한다.
  - inter-arrival 평균: X ms
  - inter-arrival 중앙값: X ms
  - inter-arrival p95: X ms

### (c) QueueFull 발생 수
  - 클라이언트 측 추정값: 0건
  [한계] broker.py QueueFull은 서버가 조용히 무시(silent drop).
  sequence id 미부여로 클라이언트에서 누락 감지 불가.
  M1c-10 단계에서 서버 QueueFull 카운터 도입 후 정확 측정 예정.

---

## N=30 결과
(동일 스키마)

---

## N=50 결과
(동일 스키마)

---

## RAM 추정 (50 SSE 연결)

클라이언트 측 (httpx asyncio):
  - asyncio task + read buffer + SSL context: ~100~300 KB/연결
  - 50 연결 합계: ~5~15 MB

서버 측 (broker.py asyncio.Queue):
  - asyncio.Queue(maxsize=100) 오브젝트: ~4~8 KB/큐
  - 50 구독자: ~0.2~0.4 MB
  - 합계 추정: 클라이언트+서버 ~5~15 MB (main app 100~160MB 대비 무시 가능)
