## QA 보고서 — #9 IP 자동 로그인 관리

검증 방식: 합성 임시 DB + FastAPI TestClient + 마이그레이션 phase 직접 호출.
실서버 브라우저 E2E는 서버 재시작 필요(VSCode 디버깅 모드) → 후속.

스크립트: `.claude/workspaces/current/scripts/verify_team_a_009.py`
실행: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python .claude/workspaces/current/scripts/verify_team_a_009.py`
결과: **59 PASS / 0 FAIL**

### 통과 ✅
- Part 1 (DB helper 직접): 부분 UNIQUE 인덱스 생성·마커 기록, `set_user_whitelist_ip` 신규/멱등/충돌(IPWhitelistConflict), `find_whitelist_owner`, `get_whitelist_status_for_ip`(owner/타인-conflict/미등록), `remove_user_whitelist_ip`(history 강등 row 보존), 강등 후 타 사용자 등록 + history row 승격(row 1개 유지), `admin_set_whitelist_ip`(임의 IP·충돌), `delete_ip_row`, `toggle_ip_whitelist`(enable 충돌→예외, disable 항상 허용), 부분 인덱스(history 중복 OK / whitelist 중복 IntegrityError), `get_user_by_whitelist_ip` admin row 반환(필터는 auth 레이어).
- Part 2 (라우트): `GET/POST/DELETE /api/me/ip-whitelist` 정상 흐름 + DB 정합, 타 사용자 같은 IP → 409 + 타인 GET conflict_user 표시, 해제 후 타 사용자 등록 → 200, admin POST /api/me/ip-whitelist → 403 + admin GET admin=true, `POST /api/admin/users/{id}/ips`(임의 IP 200 / 빈 IP 400 / 충돌 409), `GET /api/admin/users/{id}/ips` 목록, `PUT /api/admin/ips/{id}/whitelist` enable 충돌 → 409, `DELETE /api/admin/ips/{id}` → 200 + row 삭제, 일반 사용자 admin IP API → 403, 비로그인 /api/me/ip-whitelist → 401.
- Part 3 (마이그레이션 preflight): 충돌 whitelist 2건 상태 → 마이그레이션 RuntimeError(abort) + `team_migration_warnings`에 `preflight_user_ips_whitelist` 기록 + 인덱스 미생성, 충돌 해소 후 재실행 → 인덱스 생성.
- Part 4 (smoke): 빈 DB 첫 init_db → 4b 인덱스 생성, init_db 재호출 멱등, `import database; import app` OK, Jinja2 parse OK(base.html/admin.html).

### 실패 ❌
없음.

### 검증 중 발견·수정한 결함 (같은 흐름에서 처리)
- `_set_whitelist_ip_locked`가 `(user_id, ip)`의 모든 history row를 한 번에 `whitelist`로 승격 → 같은 IP의 history row가 2개 이상이면(로그인 시 `record_ip`가 중복 history 생성) 두 번째 row에서 부분 UNIQUE 인덱스 IntegrityError → 부당하게 IPWhitelistConflict(409). **수정**: history row가 있으면 그 중 MIN(id) 1개만 승격, 없으면 INSERT. 재검증 후 59 PASS.

### 회귀 확인
- `auth.get_current_user`/`get_client_ip`/`record_ip`/`get_user_by_whitelist_ip` 미변경 — Part 1에서 `get_user_by_whitelist_ip` 동작 회귀 확인 PASS.
- `toggle_ip_whitelist` 시그니처 유지(enable=True 충돌 시 예외 추가만) — 기존 admin.html 호출부는 `toggleWhitelist`가 not-ok 분기 추가로 호환.
- 이전 마이그레이션 phase 순서 영향 없음 — phase 3(admin whitelist→history) 이후 4b 실행 확인.

### 서버 재시작 필요
**예.** 새 마이그레이션 phase `team_phase_4b_user_ips_whitelist_unique_v1` (부분 UNIQUE 인덱스)가 서버 기동 시점에 적용되며, 새 라우트도 재기동 후 활성화된다. 운영 DB에 일반 사용자 간 whitelist 충돌이 있으면 preflight가 기동을 거부하고 `settings.team_migration_warnings`에 기록한다 — `tools/migration_doctor.py` 또는 수동 SQL로 정리 후 재시작.
