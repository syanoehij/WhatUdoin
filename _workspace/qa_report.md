# QA 보고서 — 37차 4단계 HTTPS 리다이렉트 미들웨어

## 테스트 방법
- `tests/phase37_stage4_https_redirect.py` (TestClient + `unittest.mock.patch`)
- `_https_available()` 를 True 로 패치해 서버 재시작 없이 미들웨어만 검증

## 실행 명령
```
"D:\Program Files\Python\Python312\python.exe" -X utf8 tests/phase37_stage4_https_redirect.py
```

## 결과: 16/16 통과

| # | 테스트 케이스 | 결과 |
|---|-------------|------|
| 1 | 현대 브라우저 GET / → 307 | PASS |
| 2 | Location에 8443 포함 | PASS |
| 3 | Location 스킴 https | PASS |
| 4 | Cache-Control: no-store | PASS |
| 5 | 쿼리스트링 보존 | PASS |
| 6 | 구형 브라우저 fallback (Mozilla/ + text/html) → 307 | PASS |
| 7 | /mcp/ 리다이렉트 없음 (401) | PASS |
| 8 | /mcp-codex/ 리다이렉트 없음 (401) | PASS |
| 9 | /api/me/mcp-token 리다이렉트 없음 (401) | PASS |
| 10 | /static/js/app.js 리다이렉트 없음 (404) | PASS |
| 11 | /favicon.ico 리다이렉트 없음 (200) | PASS |
| 12 | POST / 리다이렉트 없음 (405) | PASS |
| 13 | curl UA GET / 리다이렉트 없음 | PASS |
| 14 | AJAX Sec-Fetch-Mode: cors 리다이렉트 없음 | PASS |
| 15 | SSE Accept: text/event-stream 리다이렉트 없음 | PASS |
| 16 | _https_available=False 시 리다이렉트 없음 | PASS |

## 서버 재시작 필요
- 미들웨어 코드 변경이 반영되려면 서버 재시작 필요
- 재시작 후 `http://192.168.0.18:8000/` 접속 시 `https://192.168.0.18:8443/`로 307 리다이렉트 확인 권장
