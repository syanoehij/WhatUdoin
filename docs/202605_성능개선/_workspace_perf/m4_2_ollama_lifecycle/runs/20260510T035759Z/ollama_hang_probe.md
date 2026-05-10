# M4-2 Hang 시뮬레이션 Probe — 20260510T035759Z

## 검증 방식 선언

전체 FastAPI app spawn 없이 thread 격리 proxy 테스트 사용.
- `_call_ollama_service(timeout=1)`을 thread에서 실행 → hang mock 서버
- 별도 thread에서 CPU 작업 응답 시간 p95 < 100ms 단언
- 이는 "threadpool에서 non-LLM 라우트가 hang에 영향받지 않는다"의 proxy이며
  완전한 FastAPI end-to-end 검증은 아님.

## 결과 요약

- PASS: 4
- FAIL: 0
- 총계: 4

## 세부 결과

```
  [PASS] IPC hang → OllamaUnavailableError(reason=timeout)
  [PASS] IPC timeout 발생 시각 < 8s
  [PASS] 별도 thread p95 < 100ms
  [PASS] 샘플 수 = 20
```

## 측정값

- IPC timeout 발생 소요 시간: 6.02s (있을 경우)
- fast_task p95 latency: 0.00ms (목표: < 100ms)
- 샘플 수: 20
