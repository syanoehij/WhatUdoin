# 백엔드 변경 — #9 IP 자동 로그인 관리

## database.py
- 새 예외 `IPWhitelistConflict(Exception)` — 라우트가 409 매핑.
- 새 helper (IP Management 섹션):
  - `_whitelist_owner_id(conn, ip)` — 트랜잭션 내부용, 해당 IP의 whitelist 소유 user_id.
  - `find_whitelist_owner(ip) -> int|None`
  - `get_whitelist_status_for_ip(user_id, ip) -> {enabled, conflict, conflict_user, ip}`
  - `_set_whitelist_ip_locked(conn, user_id, ip)` — 한 트랜잭션 안에서 충돌 검사 + history→whitelist 승격 또는 INSERT. IntegrityError(부분 인덱스 race)도 IPWhitelistConflict로 변환.
  - `set_user_whitelist_ip(user_id, ip)` — 본인 등록.
  - `admin_set_whitelist_ip(target_user_id, ip)` — admin 임의 등록 (접속 이력 없는 IP도 새 row).
  - `remove_user_whitelist_ip(user_id, ip)` — 본인 해제, type='history' 강등(row 삭제 X).
  - `delete_ip_row(ip_id)` — admin row 삭제.
  - `toggle_ip_whitelist(ip_id, enable)` 수정 — enable=True 시 그 IP가 이미 다른 사용자 whitelist면 IPWhitelistConflict. enable=False는 항상 허용.
- 새 마이그레이션 phase `team_phase_4b_user_ips_whitelist_unique_v1` (등록 위치: `team_phase_4_data_backfill_v1` 직후):
  - `_phase_4b_user_ips_whitelist_unique(conn)` — `CREATE UNIQUE INDEX IF NOT EXISTS idx_user_ips_whitelist_unique ON user_ips(ip_address) WHERE type='whitelist'`. `_table_exists`/`_column_set` 가드.
- 새 preflight `_check_user_ips_whitelist_unique(conn)` — 같은 ip_address가 2명 이상에게 whitelist면 `preflight_user_ips_whitelist` 카테고리로 보고 → 러너가 `team_migration_warnings`에 누적 + 서버 시작 거부 (자동 정리 없음 — phase 5a 같은 자동 dedup을 만들지 않음). `_PREFLIGHT_CHECKS.append`.

## app.py
- 상수 `_IP_WHITELIST_CONFLICT_MSG` (사양서 §6 문구).
- 헬퍼 `_require_login(request)` — CSRF 검사 없는 로그인 필요(GET용). (이미 동일 패턴이 흩어져 있었으나 명시 헬퍼로 추출.)
- `GET /api/me/ip-whitelist` — `_require_login`. 현재 클라이언트 IP 기준 `get_whitelist_status_for_ip` + `admin` 플래그. admin이면 항상 enabled=false, admin=true.
- `POST /api/me/ip-whitelist` — `_require_editor`. **admin이면 403**. `auth.get_client_ip` IP를 본인 whitelist 등록. 충돌 409 + `_IP_WHITELIST_CONFLICT_MSG`. `{ok, ip}`.
- `DELETE /api/me/ip-whitelist` — `_require_editor`. 현재 IP whitelist 해제(강등). `{ok, ip}`.
- `PUT /api/admin/ips/{ip_id}/whitelist` 수정 — `db.toggle_ip_whitelist` 호출을 try/except로 감싸 IPWhitelistConflict → 409.
- `POST /api/admin/users/{user_id}/ips` — `_require_admin`. body `{ip_address}`. 빈 문자열 400. 충돌 409. `db.admin_set_whitelist_ip`. `{ok, ip}`.
- `DELETE /api/admin/ips/{ip_id}` — `_require_admin`. `db.delete_ip_row`. `{ok}`.

## 건드리지 않은 것
- `record_ip` (history 중복 허용 — 부분 인덱스가 `WHERE type='whitelist'`만 걸리므로 무관).
- `auth.get_current_user` / `get_client_ip` (이미 admin whitelist 무시 + IP 추출 동작).
- 그룹 B/C 후속 (#15 쿠키, #18 멤버 관리).

## QA 중 수정
- `_set_whitelist_ip_locked` — history row 전체 일괄 승격 → MIN(id) 1개만 승격으로 변경 (같은 IP history 중복 시 IntegrityError 방지). `scripts/verify_team_a_009.py` 재검증 59 PASS.

## 검증
- `python -c "import database; import app"` PASS (import-time).
- `scripts/verify_team_a_009.py` 59 PASS / 0 FAIL.
- 서버 재시작 필요 (phase 4b 인덱스 적용 + 새 라우트 활성화).
