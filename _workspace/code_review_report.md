## 코드 리뷰 보고서 — `_BrowserHTTPSRedirectMiddleware` 개선 (37차 4단계)

### 리뷰 대상 파일
- `app.py` (line 169–224)

### 변경 요약
301 → 307 + no-store, GET/HEAD 한정, 경로 제외 확장(/api·/static·/uploads·/favicon.ico), Sec-Fetch-* 현대 브라우저 감지, IPv6 Host 파싱 개선.

---

### 차단(Blocking)
없음.

---

### 경고(Warning)

- **W1. Open redirect 가능성 (Host 헤더 신뢰)** — `app.py:205`
  Host 헤더 무검증 사용 → 공격자가 `Host: evil.example.com`으로 요청 시 `https://evil.example.com:8443/`으로 리다이렉트 가능.
  - 인트라넷 폐쇄망 환경에서는 실 익스플로잇 가능성 낮음.
  - 권고: 외부 노출 검토 시 `TrustedHostMiddleware` 도입.

- **W2. `_https_available()` 매 요청 fs stat** — `_https_available` (line 222–223)
  매 HTTP 요청마다 인증서 파일 stat 2회. 인트라넷 부하에서는 OS 캐시로 무시 가능.
  - 권고: 37차 성능 최적화 후속에서 부팅 시 1회 캐시 처리.

---

### 통과

- [x] **307 + no-store**: 301 영구 캐시 문제 해결. ASGI 응답 포맷 정확.
- [x] **GET/HEAD 한정**: POST 등 비멱등 메서드 제외로 양식 제출 등 엣지 케이스 안전.
- [x] **경로 제외 완전성**: `/mcp`, `/mcp-codex`, `/api`, `/static`, `/uploads`, `/favicon.ico` 모두 `startswith(tuple)`로 커버. MCP 이중 보호(경로 + UA) 유지.
- [x] **Sec-Fetch-* 감지**: `cors`/`no-cors`/`same-origin`/`navigate` 분기 정확. `mode` 또는 `dest` 헤더 하나라도 있으면 Sec-Fetch 방식으로만 판단 → 구형/현대 브라우저 혼용 안전.
- [x] **IPv6 파싱**: `[::1]:8000` → `[::1]` 정확 분리.
- [x] **ASGI 패턴**: `_MCPBearerAuthMiddleware`와 동일 스타일. BaseHTTPMiddleware 미사용(SSE 호환).
- [x] **테스트 16/16 통과**: `tests/phase37_stage4_https_redirect.py` (TestClient + mock).

---

### 최종 판정

**통과** — QA 진행 가능.
