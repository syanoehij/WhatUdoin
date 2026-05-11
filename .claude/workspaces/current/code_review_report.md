## 코드 리뷰 보고서 — #9 IP 자동 로그인 관리

### 리뷰 대상 파일
- `database.py` — IP Management 섹션(신규 helper + `IPWhitelistConflict`), phase `team_phase_4b_user_ips_whitelist_unique_v1`, preflight `_check_user_ips_whitelist_unique`
- `app.py` — `/api/me/ip-whitelist` GET/POST/DELETE, `/api/admin/users/{user_id}/ips` POST, `/api/admin/ips/{ip_id}` DELETE, `PUT /api/admin/ips/{ip_id}/whitelist` 409 처리, `_require_login` 헬퍼
- `templates/base.html` — 설정 패널 자동 로그인 섹션 + JS
- `templates/admin.html` — IP 모달 확장 + JS

### 백엔드 체크리스트
- [x] 새 라우트 권한 체크: `GET /api/me/ip-whitelist` → `_require_login`(401), `POST/DELETE /api/me/ip-whitelist` → `_require_editor` + POST는 admin 403 추가, `POST /api/admin/users/{id}/ips` / `DELETE /api/admin/ips/{id}` → `_require_admin`. ✅
- [x] 페이지 라우트 `_ctx()`: 본 사이클 신규 페이지 라우트 없음(JSON API만). N/A
- [x] DB 스키마 변경: 별도 migration 파일 없음. `PHASES.append` 인라인 패턴 + `_table_exists`/`_column_set` 가드. `CREATE UNIQUE INDEX IF NOT EXISTS` idempotent. ✅
- [x] 새 컬럼 추가 없음 — `user_ips` 기존 테이블에 인덱스만 추가. try/except 중복 방지 N/A
- [x] 하위호환: 기존 `get_user_by_whitelist_ip`/`record_ip`/`get_user_ips` 시그니처·동작 유지. `toggle_ip_whitelist`는 시그니처 동일, enable=True 충돌 시 예외만 추가(동작 보강). ✅
- [x] 파일 경로: 본 사이클 경로 상수 사용 없음. N/A
- [x] Ollama: 무관. N/A
- [x] `get_conn()` contextmanager: 모든 신규 helper가 `with get_conn() as conn:` 사용. `_set_whitelist_ip_locked`/`_whitelist_owner_id`는 conn 인자 받는 내부 함수(트랜잭션 공유). ✅
- [x] SQL 파라미터화: 모든 신규 쿼리 `?` 바인딩. f-string 직접 삽입 없음. ✅
- [x] 충돌 검사 + INSERT가 한 `get_conn()` 트랜잭션 안 — race 시 IntegrityError(부분 인덱스)도 `IPWhitelistConflict`로 변환. ✅
- [x] preflight: `_phase_5_projects_unique` 패턴 따름. 자동 dedup phase 만들지 않고 abort + `team_migration_warnings`(`preflight_user_ips_whitelist`) — 명세대로. ✅
- [x] phase 등록 순서: phase 3(admin whitelist→history) 이후 phase 4b 실행 → admin row가 인덱스 충돌을 일으키지 않음. ✅

### 프론트엔드 체크리스트
- [x] `fetch()` `response.ok` 체크 후 `.json()`: base.html `loadIpAutologinStatus`/`onIpAutologinToggle`, admin.html `toggleWhitelist`/`adminAddIp`/`deleteIpRow` 모두 `res.ok` 분기. ✅
- [x] 오류 시 `wuToast.error/warning` 사용자 피드백. ✅
- [x] `{% extends "base.html" %}`: admin.html 유지. base.html은 레이아웃 자체. ✅
- [x] 라이브러리 조건부 초기화: `wuDialog`/`wuToast`는 base.html이 `wu-dialog.js`로 항상 로드 — 조건부 가드 불필요(기존 관례 일치). ✅
- [x] 서버사이드 권한: 설정 패널 섹션은 JS로 숨기되 admin 차단은 `POST /api/me/ip-whitelist` 서버 403이 최종 방어. admin IP 등록/삭제는 `_require_admin` 서버 강제. ✅
- [x] XSS: admin.html `refreshIpList`의 `${ip.ip_address}` / `${ip.last_seen}`는 서버 DB값(접속 IP·타임스탬프). 사용자 자유입력 문자열 아님(접속 IP는 `request.client.host` 또는 admin이 입력한 IP — 후자는 innerHTML에 들어가나 admin 권한자 자신이 입력한 값). base.html `conflict_user`는 사용자 이름인데 `textContent`로 삽입(escape됨). ⚠️ 아래 경고 참조.
- [x] UI 패턴 일관성: 기존 `switch switch-sm`, `btn-danger-outline`, `wuDialog.confirm`, settings 패널 섹션 구조 재사용. ✅

### 경고(Warning) ⚠️
- `templates/admin.html` `refreshIpList()` — `${ip.ip_address}`를 `innerHTML` 템플릿 문자열에 직접 삽입. 값의 출처는 (1) `request.client.host`(서버가 채운 peer IP — 안전) 또는 (2) admin이 `POST /api/admin/users/{id}/ips`로 직접 입력한 문자열. (2)는 admin 권한자 본인이 자기 화면에서만 보게 되는 self-XSS 수준이고, 기존 `openIPs`도 동일하게 했던 패턴이라 회귀 아님. 운영 규모(<10명, 인트라넷)·권한 경계상 차단 아님. 향후 정리 시 `textContent` 기반 DOM 조립으로 바꾸면 좋음.
- `app.py` `POST /api/admin/users/{user_id}/ips` — IP 형식 검증이 "빈 문자열·공백만 거부" 수준(엄격한 IPv4/v6 파싱 없음). 사양서가 "임의의 IP 문자열을 직접 입력"이라 명시했고(미리 부여 시나리오), `get_client_ip`도 형식 검증을 하지 않으므로 일관. 차단 아님.

### 통과 ✅
- DB 스키마: phase/preflight 인프라(`PHASES`/`_PREFLIGHT_CHECKS`) 올바르게 사용, idempotent 가드 완비
- SQL 파라미터화 전 쿼리 확인
- 권한 체크: 모든 신규 쓰기 라우트 `_require_editor`/`_require_admin` + admin POST 403
- 트랜잭션 원자성: 충돌 검사+쓰기 단일 conn, IntegrityError fallback
- import-time 검증 PASS (`python -c "import database; import app"`), Jinja2 parse PASS

### 회귀 검토
- 이전 사이클(#8) 리포트 대비 회귀 없음. `auth.get_current_user`/`record_ip`/`get_user_by_whitelist_ip` 미변경. `toggle_ip_whitelist` 시그니처 유지.

### 최종 판정
- **통과** (차단 결함 없음, 경고 2건은 운영 규모·권한 경계상 수용. QA 진행 허용)
