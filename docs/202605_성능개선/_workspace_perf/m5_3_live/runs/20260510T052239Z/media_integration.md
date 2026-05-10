# M5-3 A: Live Media Service Integration Probe

- **UTC**: 20260510T052239Z
- **Media port**: 60977
- **Staging root**: D:\Github\WhatUdoin\_workspace\perf\m5_3_live\runs\20260510T052239Z\supervisor_run\staging

## 결과: 29/29 PASS, 0 FAIL, 1 SKIP

| 항목 | 결과 | 비고 |
|------|------|------|
| ensure_internal_token: token 파일 존재 | PASS |  |
| ensure_internal_token: token 비어있지 않음 | PASS |  |
| spec.name == 'media' | PASS |  |
| spec.env에 토큰 없음 (service_env가 주입) | PASS |  |
| spec.env에 STAGING_ROOT 포함 | PASS | env keys: ['WHATUDOIN_MEDIA_BIND_HOST', 'WHATUDOIN_MEDIA_PORT', 'WHATUDOIN_STAGING_ROOT'] |
| media spawn: status not failed | PASS | status=running, last_error= |
| port 열림 (20s timeout) | PASS | port=60977 |
| probe_healthz PASS | PASS | status=ok, error= |
| /healthz 키: staging_root | PASS |  |
| /healthz 키: processed_count | PASS |  |
| /healthz 키: uptime_seconds | PASS |  |
| IPC 정상: HTTP 200 | PASS | got 200 |
| IPC 정상: ok=True | PASS | resp={'ok': True, 'kind': 'image', 'original_name': 'x.png', 'size': 90, 'sha256': 'ef504a6a99a0bcd1', 'ext': '.png', 'dimensions': {'w': 16, 'h': 16}} |
| IPC 정상: kind=image | PASS | kind=image |
| IPC 정상: sha256 존재 | PASS | resp keys=['ok', 'kind', 'original_name', 'size', 'sha256', 'ext', 'dimensions'] |
| IPC 정상: dimensions 존재 | PASS | resp keys=['ok', 'kind', 'original_name', 'size', 'sha256', 'ext', 'dimensions'] |
| IPC 정상: ext=.png | PASS | ext=.png |
| IPC 정상: size > 0 | PASS | size=90 |
| 토큰 없음 → 401 | PASS | got 401: {'ok': False, 'reason': 'unauthorized'} |
| 잘못된 토큰 → 401 | PASS | got 401: {'ok': False, 'reason': 'unauthorized'} |
| ..'우회 → ok:False (path_traversal/forbidden_path) | PASS | status=400, reason=path_traversal |
| ..'우회 → reason in {path_traversal, forbidden_path} | PASS | reason=path_traversal |
| 절대 경로 (밖) → ok:False | PASS | status=400, reason=path_traversal |
| symlink 경로 정규화 | SKIP | SKIP: Windows symlink 생성 권한 없음 (개발자 모드 또는 관리자 권한 필요) |
| stop_service → status=stopped | PASS | status=stopped |
| 종료 후 포트 닫힘 | PASS | port=60977 |
| 종료 후 IPC 호출 → ConnectionError | PASS | expected connection refused |
| 재시작: status not failed | PASS | status=running, last_error= |
| 재시작: 포트 재개 (20s timeout) | PASS | port=60977 |
| 재시작 후 probe_healthz 회복 | PASS | status=ok, error= |

## 편차 기록

- task spec은 `reason: 'forbidden_path'` at HTTP 200을 지정하나, media_service.py 구현은 `reason: 'path_traversal'` at HTTP 400을 반환. 경계는 동등하게 동작함. spec 표현 불일치로 판단, 운영 코드 수정 없음.