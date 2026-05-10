# M5-3 C: 소유권 증거 3종 + 외부 직접 호출 차단

- **UTC**: 20260510T051250Z

## 결과: 18/18 PASS, 0 FAIL, 1 SKIP

| 항목 | 결과 | 비고 |
|------|------|------|
| media_service.py DB write 0건 (db./database./sqlite3/cursor 0) | PASS |  |
| upload_image: _require_editor 호출 | PASS |  |
| upload_image: _require_editor 존재 | PASS | 함수 본문 내 존재 여부 |
| upload_attachment: _require_editor 존재 | PASS | 함수 본문 내 존재 여부 |
| upload_image: staging → MEETINGS_DIR rename 또는 db write | PASS | rename/db. 존재 여부 |
| upload_attachment: staging → rename 또는 db write | PASS | rename/db. 존재 여부 |
| Web API: _sse_publish 함수 존재 | PASS | app.py 전역 |
| Web API: SSE publish 후처리 경로 존재 (from publisher import _sse_publish) | PASS | app.py 전역 — 업로드 핸들러는 URL 반환 전용이므로 SSE publish 없음 (설계 정상) |
| Web API: /api/upload/image endpoint 보유 | PASS |  |
| Web API: /api/upload/attachment endpoint 보유 | PASS |  |
| Web API: _call_media_service 헬퍼 존재 | PASS |  |
| 1. STAGING_ROOT/../etc/passwd → None (path 거부) | PASS | got None |
| 2. 절대 경로 (밖) → None (path 거부) | PASS | got None |
| 3. sub/../../../escape.txt → None (path 거부) | PASS | got None |
| 4. symlink (STAGING 내 → 외부 타겟) | SKIP | SKIP: Windows symlink 생성 권한 없음 (개발자 모드 또는 관리자 권한 필요) |
| 5. STAGING_ROOT/valid.png → Path (staging 통과) | PASS | got C:\Users\Lumen\AppData\Local\Temp\m5_3_staging_boundary_el49c55g\valid_test.png |
| 외부 IP (192.0.2.1) → /internal/process → 403 | PASS | got 403: {'ok': False, 'reason': 'forbidden'} |
| 외부 IP 응답: reason=forbidden | PASS | reason=forbidden |
| loopback (127.0.0.1) → /internal/process → 200 | PASS | got 200: {'ok': True} |