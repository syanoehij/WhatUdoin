# M4-4 Ollama hang 중 일반 API p95 보강 Probe

**실행 시각**: 20260510T041450Z
**방식**: in-process ASGI (httpx.ASGITransport)
**hang mock**: sleep=10s, port=49811

## 검증 방식 선언

- ASGI in-process 방식: full FastAPI app spawn 없이 `httpx.ASGITransport` 사용
- AI hang: `_call_ollama_service(timeout=1)` × 3 threads → hang mock(10s sleep)에 연결
- 일반 API: GET /api/health 50회 동시 호출 (asyncio.gather)
- /api/health는 LLM IPC 호출 없음 → threadpool 잠식 0

## 측정 결과

| 지표 | 값 |
|------|----|
| p50 latency | 16.0ms |
| p95 latency | 31.0ms |
| 목표 | < 500.0ms |
| 결과 | PASS |

## 항목별 결과

| # | 항목 | 결과 | 상세 |
|---|------|------|------|
| 1 | app.py import 성공 | PASS |  |
| 2 | app.app ASGI 객체 존재 | PASS |  |
| 3 | llm_parser._ollama_service_url 패치 | PASS |  |
| 4 | AI in-flight threads 시작 | PASS |  |
| 5 | 50회 GET 완료 | PASS | count=50 |
| 6 | 일반 API p95 < 500.0ms (AI hang 중) | PASS | p95=31.0ms, p50=16.0ms |
| 7 | AI threads 종료 완료 (3개) | PASS | done=3/3 |
| 8 | AI OllamaUnavailableError 발생 (timeout 예상) | PASS | exceptions=['OllamaUnavailableError', 'OllamaUnavailableError', 'OllamaUnavailableError'] |

## 총계

**8/8 PASS**