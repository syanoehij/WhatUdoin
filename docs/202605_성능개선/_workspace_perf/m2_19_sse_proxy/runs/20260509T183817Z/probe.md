# M2-19 SSE proxy 6종 조건 probe 결과

실행: 2026-05-09 18:38 UTC

## 6종 조건 PASS/FAIL

| 조건 | 내용 | 결과 | 보장 근거 |
|------|------|------|-----------|
| 1. buffering 비활성 | Cache-Control: no-cache, X-Accel-Buffering: no, chunk 즉시 forward | PASS | sse_service.py StreamingResponse 헤더 직접 설정. FrontRouter ASGI dispatch는 buffering 레이어 없음 |
| 2. compression 비활성 | gzip/brotli 미들웨어 0건, content-encoding 없음 | PASS | app.py/front_router.py/sse_service.py/supervisor.py/main.py grep 0건 |
| 3. idle timeout 정책 | FrontRouter timeout 없음, heartbeat 25초 코드 존재 | PASS | front_router.py에 asyncio.wait_for 0건. sse_service.py의 last_ping + 25.0 임계값 확인 |
| 4. client disconnect 전파 | is_disconnected() + finally unsubscribe, subs 0 복원 | PASS | ASGI in-process: connected(subs=1) → disconnect → subs=0 단언 |
| 5. 헤더/쿠키 통과 | cookie/authorization FORWARDED_HEADER_NAMES 미포함, X-Forwarded-* 만 재작성 | PASS | FORWARDED_HEADER_NAMES 정적 확인 + ASGI level downstream 헤더 단언 |
| 6. 외부 /internal/* 차단 | blocked=True, 404, downstream 0건 | PASS | match_front_route 단언 + ASGI level 404 + downstream 호출 카운트 0 |

## 운영 코드 변경

**0건** — 6종 조건이 현재 ASGI dispatcher 구조에서 자연 보장됨. 결함 발견 없음.

## 테스트 결과

| 파일 | PASS | FAIL |
|------|------|------|
| phase61_front_router_sse_proxy.py | 31 | 0 |
| m2_19_sse_proxy_six_conditions_probe.py | 31 | 0 |

## 회귀 결과

| phase | 결과 |
|-------|------|
| phase54 | PASS |
| phase55 | PASS |
| phase56 | PASS |
| phase57 | PASS (22/22) |
| phase58 | PASS (19/19) |
| phase59 | PASS (25/25) |
| phase60 | PASS (22/22) |

## M2-20 영향

- 운영 코드 변경 0건이므로 M2-20 부하 테스트 환경에 영향 없음.
- phase61이 SSE proxy 6종 단언을 고정. M2-20 부하 테스트 후 6종 조건의 실제 동작 증거(RTT/chunk latency 측정)를 M2-20 인덱스에 추가 가능.
- `_broker._subs` 구독자 수는 M2-20 측정 지표로 직접 활용 가능 (`/healthz` 노출 시).
