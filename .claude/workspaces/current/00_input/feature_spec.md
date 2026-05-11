# 요청
'팀 기능 구현 todo.md' 그룹 A의 #9 (IP 자동 로그인 관리) 진행.

# 분류
백엔드 + 프론트엔드 수정 (마이그레이션 phase 추가 포함) / backend → reviewer → qa 흐름

# 권위 명세
- todo.md "### #9. IP 자동 로그인 관리" 섹션 (L253~271)
- 계획.md 섹션 6 "IP 자동 로그인 운영 전제" + "자동 로그인 대상 IP 등록 경로" (L315~334)
- 계획.md 섹션 10 "IP 관리 범위" 표 (L574~597)

# 기존 코드 정합
- `database.py`: `user_ips(id, user_id, ip_address, type DEFAULT 'history', last_seen)` 테이블 존재. `get_user_by_whitelist_ip`, `record_login_ip`, `get_user_ips`, `toggle_ip_whitelist` 존재. Phase 3가 이미 admin whitelist→history 강등.
- `auth.py`: `get_client_ip(request)`, `get_current_user` (세션 → whitelist IP, admin이면 None 반환).
- `app.py`: `GET /api/admin/users/{user_id}/ips`, `PUT /api/admin/ips/{ip_id}/whitelist` 존재. `_require_admin`, `_require_editor`.
- `templates/base.html`: `#user-settings-panel` 사이드 패널 (메뉴 표시 / 로컬 데이터 초기화 섹션). `CURRENT_USER` JS 객체 (role 포함). `/api/me/change-password` 패턴.
- `templates/admin.html`: IP 모달(`#ip-modal`), `openIPs`/`toggleWhitelist`.
- 마이그레이션 phase 패턴: `database.py` `PHASES.append((name, fn))`, 마지막 등록 = `team_phase_4_data_backfill_v1` (L1539). preflight 충돌 검사는 `_phase_5_projects_unique` 패턴 참조 + `team_migration_warnings` 누적.

# 핵심 결정사항
- "1 IP = 1 사용자" 만 강제. "1 사용자 = 1 IP" 는 강제하지 않음 (멀티 PC 허용 — 부분 UNIQUE 인덱스는 `ip_address`에만 걸림). 설정 패널 토글은 "현재 접속 PC의 IP가 내게 whitelist 됐는가" 의미.
- admin은 자동 로그인 대상 아님 → `POST /api/me/ip-whitelist`는 admin 세션이면 403 (방어). 설정 패널 토글 자체도 admin이면 비노출.
- 충돌 검사 + INSERT는 한 `get_conn()` 트랜잭션 안에서 — race 방지. IntegrityError 잡아 409.
- 해제(off)는 row 삭제가 아니라 `type='history'` 강등 (기존 `toggle_ip_whitelist`와 일관, 접속 이력 보존).
- `record_login_ip`는 건드리지 않음 (history 중복 허용 — 부분 인덱스 `WHERE type='whitelist'` 덕분).
- 그룹 B/C 후속(#15 쿠키, #18 멤버 관리 IP 숨김)은 건드리지 않음. 새 IP UI/API를 `/settings` 패널과 `/admin` 밖에 추가하지 않는 것이 본 사이클의 #18 관련 유일 의무.

# backend-dev 작업
## database.py
1. 새 helper:
   - `find_whitelist_owner(ip) -> int|None` — 해당 IP의 현재 whitelist 소유 user_id.
   - `set_user_whitelist_ip(user_id, ip)` — 한 트랜잭션 안에서: 다른 사용자가 같은 IP whitelist면 `ValueError`(또는 전용 예외)로 409 신호. 같은 `(user_id, ip)` history row 있으면 `type='whitelist'`로 승격, 없으면 새 row INSERT (`type='whitelist'`).
   - `remove_user_whitelist_ip(user_id, ip)` — 해당 사용자의 그 IP whitelist row를 `type='history'`로 강등 (없으면 노옵).
   - `admin_set_whitelist_ip(target_user_id, ip)` — admin이 임의 IP 직접 등록. 충돌 시 동일 예외. 사용자 접속 이력 없는 새 IP도 새 row INSERT 가능.
   - `delete_ip_row(ip_id)` — admin이 IP row 삭제.
   - `get_whitelist_status_for_ip(user_id, ip) -> dict` — `{enabled: bool, conflict: bool, conflict_user: str|None}` (설정 패널 초기 상태용). conflict_user는 이름.
2. 새 마이그레이션 phase `team_phase_4b_user_ips_whitelist_unique_v1` (L1539 `team_phase_4_data_backfill_v1` 뒤에 등록):
   - preflight: `type='whitelist'`인 `ip_address` 중 2명 이상에게 걸린 IP 있으면 `team_migration_warnings`에 `user_ips_whitelist_conflict` 카테고리로 기록 + abort (자동 해소 안 함 — `migration_doctor`로 운영자가 처리). preflight 훅 위치는 `_phase_5_projects_unique` 또는 phase 5a 패턴 따를 것. (※ phase 5a처럼 자동 dedup phase를 만들지 말 것 — IP는 안전한 자동 선택 기준이 없으므로 abort + 경고만.)
   - 본문: `CREATE UNIQUE INDEX IF NOT EXISTS idx_user_ips_whitelist_unique ON user_ips(ip_address) WHERE type='whitelist'` (부분 인덱스). 빈 DB 첫 init_db()에서도 안전 (충돌 0건).
   - `_table_exists`/`_column_set` 가드.
3. `_migrate(conn, "user_ips", ...)` 같은 CREATE-흡수도 검토 — 현재 CREATE TABLE에 `WHERE type='whitelist'` 부분 인덱스는 _migrate 대상 아님(컬럼 추가만 처리). phase로 충분.

## app.py
1. `GET /api/me/ip-whitelist` — `_require_editor` (= 로그인 필요). 현재 클라이언트 IP 기준 `get_whitelist_status_for_ip` 결과 반환. (※ todo.md에 명시 없음 — 구현하되 sub-task 토글에 카운트 안 함.)
2. `POST /api/me/ip-whitelist` — `_require_editor`. **admin이면 403**. `auth.get_client_ip(request)` IP를 본인에게 whitelist 등록. 충돌 시 409 + detail "이 PC는 다른 사용자의 자동 로그인 대상으로 등록되어 있습니다. 시스템 관리자에게 문의하세요." `{ok: True, ip: "..."}` 반환.
3. `DELETE /api/me/ip-whitelist` — `_require_editor`. 현재 클라이언트 IP whitelist 해제(history 강등). 확인 모달 없음. `{ok: True}`.
4. `POST /api/admin/users/{user_id}/ips` — `_require_admin`. body `{ip_address: "..."}`. 임의 IP whitelist 등록. IP 형식 가벼운 검증(빈 문자열·공백 거부; 엄격한 IPv4/v6 파싱은 과함 — 인트라넷). 충돌 시 409. `{ok: True}`.
5. `DELETE /api/admin/ips/{ip_id}` — `_require_admin`. row 삭제. `{ok: True}`.
6. 기존 `PUT /api/admin/ips/{ip_id}/whitelist`: `enable=True`로 켤 때 그 row의 IP가 이미 다른 사용자에게 whitelist면 409 (atomic 검사). `enable=False`는 그대로.

# frontend-dev 작업
## templates/base.html
- `#user-settings-panel` `.settings-body` 안에 새 섹션 추가: "자동 로그인" — `CURRENT_USER && CURRENT_USER.role !== 'admin'` 일 때만 표시(JS로 toggle). 토글 1개 + hint.
  - 패널 open 시 `GET /api/me/ip-whitelist` 호출해 토글 초기 상태/충돌 안내 채움.
  - 토글 ON → 확인 모달 표시. 모달 본문(정확히 계획.md L326): "이 PC의 IP를 자동 로그인 대상으로 등록합니다. 등록 후에는 이 PC에서 로그인 없이 본인 계정으로 자동 진입됩니다. 공용 PC·공유 와이파이·외부 네트워크에서는 보안상 위험하니 본인 전용 PC에서만 사용하세요. 등록 후 다른 PC로 이동했다면 설정에서 즉시 해제하세요." 확인 시 `POST /api/me/ip-whitelist`. 409면 토스트로 detail 표시 + 토글 원복.
  - 토글 OFF → 모달 없이 즉시 `DELETE /api/me/ip-whitelist`.
  - 확인 모달은 기존 `wu-dialog.js` / 인라인 modal-overlay 패턴 따를 것.
## templates/admin.html
- 기존 IP 모달(`#ip-modal`) 확장:
  - 상단 또는 하단에 "임의 IP 직접 등록" input + 버튼 — `POST /api/admin/users/{userId}/ips`. 성공 시 목록 새로고침. 409면 토스트.
  - 각 IP row에 삭제 버튼 — `DELETE /api/admin/ips/{ipId}`. 확인 후 삭제 + 목록 새로고침.
  - `toggleWhitelist` 켤 때 409 응답이면 토스트 + 체크박스 원복.

# 주의사항
- 마이그레이션 phase 추가 → 서버 재시작 필요. 최종 요약에 명시.
- VSCode 디버깅 모드라 서버 자동 재시작 불가 — 실서버 브라우저 E2E는 재시작 후 후속. qa는 import-time + 합성 DB + TestClient 위주 (#8 `scripts/verify_team_a_008.py` 패턴).
- 라우트 추가 위치: 기존 `/api/admin/users/{user_id}/ips` 근처 (app.py L1756 부근) + `/api/me/...` 그룹 근처.

# qa 작업
- `scripts/verify_team_a_009.py` 작성·실행 (합성 DB + TestClient). 커버:
  1. 일반 사용자 자체 등록 200 + DB row `type='whitelist'` 확인
  2. 다른 사용자가 같은 IP 등록 시도 → 409
  3. 본인 해제(DELETE) → row `type='history'`, 다시 다른 사용자가 등록 → 200
  4. admin 세션에서 `POST /api/me/ip-whitelist` → 403
  5. admin이 임의 IP를 사용자에게 등록 → 200 (접속 이력 없는 IP)
  6. admin이 IP row 삭제 → 200 + row 사라짐
  7. `PUT /api/admin/ips/{id}/whitelist` enable로 충돌 시 → 409
  8. 부분 UNIQUE 인덱스: 같은 IP `history` row 2개는 허용, `whitelist` 2개는 IntegrityError
  9. preflight: 합성 DB에 충돌 whitelist 2건 심고 마이그레이션 → abort + `team_migration_warnings`에 `user_ips_whitelist_conflict` 기록
  10. import-time smoke (app import OK, init_db on fresh temp DB OK)
- 결함 발견 시 backend/frontend 패치 + qa 재검증까지 같은 흐름에서 종료.
