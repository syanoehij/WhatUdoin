# 백엔드 변경 이력

## [2026-05-05] HTTP→HTTPS 브라우저 자동 리다이렉트 미들웨어 개선 (37차 4단계)

### 변경 파일
- `app.py`

### 변경 위치
- `_extract_host()` 헬퍼: line 169–176
- `_BrowserHTTPSRedirectMiddleware` 클래스: line 179–222
- `app.add_middleware(_BrowserHTTPSRedirectMiddleware)`: line 224

### 변경 내용

**초기 구현 대비 개선 사항:**

1. **301 → 307 Temporary Redirect + Cache-Control: no-store**
   - 301은 브라우저가 영구 캐시 → 인증서 제거 후에도 계속 HTTPS로 이동하는 문제 방지
   - `no-store`로 캐시 완전 차단

2. **GET/HEAD만 리다이렉트**
   - `scope.get("method") in {"GET", "HEAD"}` 조건 추가
   - POST/PUT/DELETE 등 비멱등 메서드는 리다이렉트 대상 아님

3. **제외 경로 확장**
   - 기존: `/mcp` 시작 경로만 제외
   - 개선: `/mcp`, `/api`, `/static`, `/uploads`, `/favicon.ico` 모두 제외
   - `str.startswith(tuple)` 사용

4. **현대 브라우저 감지 (Sec-Fetch-*)**
   - `Sec-Fetch-Mode: navigate` 또는 `Sec-Fetch-Dest: document` → 정확한 문서 탐색 감지
   - 구형 브라우저 fallback: `Mozilla/` UA + `Accept: text/html` (기존 로직 유지)
   - AJAX(`Sec-Fetch-Mode: cors`), iframe(`Sec-Fetch-Mode: no-cors`) 등 자동 제외

5. **Host 파싱 개선 (`_extract_host`)**
   - IPv6 리터럴(`[::1]`) 처리: `[` 시작 시 `]` 까지 반환
   - 유효하지 않은 Host(빈 값) → 리다이렉트 안 함

### 미들웨어 스택 순서 (ASGI는 마지막 등록이 가장 먼저 실행)
1. `_BrowserHTTPSRedirectMiddleware` (마지막 등록 → 최외곽)
2. `_StaticCacheMiddleware`
3. `_MCPBearerAuthMiddleware`
4. FastAPI 앱 코어
