# 요청
'팀 기능 구현 todo.md' 그룹 A의 #8 (계정 가입과 팀 신청 분리) 구현.

# 분류
백엔드 수정 (라우트/DB 함수 변경 중심) + 소량 프론트 (register.html 안내 문구·자동 로그인). 실행 모드: 팀 모드(backend → frontend → reviewer → qa). 프론트는 최소(register.html의 success 동작·문구만, 팀 신청 UI는 #12·#14·#18 책임).

# 컨텍스트 (탐색 결과)
- `passwords.py`: `hash_password`, `verify_password`, `is_valid_user_name`(`^[A-Za-z0-9가-힣]+$`), `is_valid_password_policy`(영문+숫자 동시), `DUMMY_HASH`.
- `database.py`:
  - `normalize_name(s)` — NFC+casefold 정규화 (line 771).
  - `user_teams` 컬럼: `id, user_id, team_id, role, status, joined_at` (created_at 없음). `idx_user_teams_user_team` UNIQUE on `(user_id, team_id)` (phase 4).
  - `users` 컬럼: name, name_norm, password, password_hash, role, team_id, is_active, ... — password NOT NULL DEFAULT ''.
  - `check_register_duplicate`(line 4539), `create_pending_user`(4557), `get_pending_users`(4566), `approve_pending_user`(4574), `reject_pending_user`(4591) — pending_users 테이블 기반. **이 사이클은 register 경로에서 pending 쓰기만 제거**, admin 승인 UI/라우트는 그대로 둠 (Phase 5/후속에서 정리).
  - `create_session(user_id, role="editor")`, `record_ip(user_id, ip)`.
- `app.py`:
  - `/api/register` (line 1446): 현재 `check_register_duplicate` → `create_pending_user`. 교체 대상.
  - `/api/login` (1399 근처): 이미 이름+비밀번호+정규식+name_norm 매칭 (#7).
  - `/api/me/change-password` (1415).
  - admin pending 라우트 (1486~1509): `/api/admin/pending`, `.../approve`, `.../reject` — 그대로 둠.
  - `auth.SESSION_COOKIE`, `auth.get_client_ip`, `auth.get_current_user`, `auth.is_admin`, `auth.is_team_admin`.
- `templates/register.html`: 가입 폼(이름·비밀번호·메모) + success-box("관리자 승인 후 사용 가능"). #8 후엔 즉시 가입 완료 + 자동 로그인 + `/` 이동으로 바꿔야 함.
- `templates/base.html`: line 521 `<a href="/register" class="login-link">가입 신청</a>` — 문구 "계정 가입"으로 변경 검토(선택).

# 에이전트별 작업

## backend-dev
### A. /api/register 신규 흐름 (app.py + database.py)
1. `database.py`에 신규 함수 `create_user_account(name, password) -> dict | None`:
   - `name_norm = normalize_name(name)`.
   - 트랜잭션 안에서 `users(name, name_norm, password, password_hash, role, team_id, is_active)` INSERT. `password=''`(NOT NULL deviation, #7과 동일), `password_hash=passwords.hash_password(password)`, `role='member'`, `team_id=NULL`, `is_active=1`.
   - `users.name_norm` 전역 UNIQUE 인덱스(#7)와 충돌 시 `sqlite3.IntegrityError` → None 반환 (라우트가 409 매핑) 또는 사전 SELECT로 중복 검사 후 메시지 반환. **권장: 사전 SELECT + IntegrityError 둘 다 가드** (race 안전).
   - 반환: `{"id": ..., "name": ..., "role": "member", "team_id": None}`.
2. `app.py` `/api/register` 교체:
   - body: `{name, password}` (memo는 더 이상 받지 않거나 무시).
   - `name`/`password` 빈 값 → 400.
   - `passwords.is_valid_user_name(name)` 위반 → 400 "이름은 영문·숫자·한글만 사용할 수 있습니다."
   - **예약 사용자명 차단**: `name.casefold()` 또는 `name.lower()` ∈ {`admin`, `system`, `root`, `guest`, `anonymous`} → 400 "사용할 수 없는 이름입니다." (모듈 상수 `RESERVED_USERNAMES` 정의).
   - `passwords.is_valid_password_policy(password)` 위반 → 400 "비밀번호는 영문과 숫자를 모두 포함해야 합니다."
   - `db.create_user_account(name, password)` → None이면 409 "이미 사용 중인 이름입니다."
   - 성공 시 `session_id = db.create_session(user["id"], role="member")` (또는 role 인자 — `create_session`의 admin 분기만 5분, 그 외 30일이므로 "member" 무방), `db.record_ip(user["id"], auth.get_client_ip(request))`, `response.set_cookie(auth.SESSION_COOKIE, session_id, httponly, samesite="lax", secure=(scheme=="https"), max_age=86400*30)`.
   - 응답: `{"ok": True, "name": user["name"], "role": user["role"], "team_id": user["team_id"]}` (프론트가 `/`로 리다이렉트).
   - `@limiter.limit("5/minute")` 유지.
   - **주의**: `register`는 `response: Response` 파라미터가 필요 (현재 시그니처에 없음 — `/api/login`·`/api/admin/login` 패턴 참고하여 추가).
3. `db.check_register_duplicate` / `db.create_pending_user` 호출부는 `/api/register`에서만 사용 → 호출 제거. **함수 자체는 삭제하지 않음**(다른 곳에서 안 쓰이면 데드코드로 남겨두되 mention만). grep으로 다른 참조 없는지 확인.

### B. 팀 신청 라우트 (app.py + database.py)
1. `database.py` 신규 함수:
   - `apply_to_team(user_id, team_id) -> str`: 반환값 `"created"` | `"updated"` | `"blocked"`.
     - 트랜잭션 안에서: `SELECT id, status FROM user_teams WHERE user_id=? AND team_id=?`.
       - 없으면 `INSERT INTO user_teams(user_id, team_id, role, status) VALUES(?,?,'member','pending')` → "created".
       - 있고 `status='pending'` → "blocked" (이미 신청 중).
       - 있고 `status='approved'` → "blocked" (이미 멤버) — 라우트가 다른 메시지로 분기 가능하게 status도 반환 고려. 단순화 위해 `apply_to_team`이 `(result, current_status)` 튜플 반환해도 됨.
       - 있고 `status='rejected'` → `UPDATE user_teams SET status='pending', role='member' WHERE id=?` (joined_at은 건드리지 않음) → "updated".
     - **추가 가드**: `SELECT 1 FROM user_teams WHERE user_id=? AND status='pending'` (다른 팀에도 pending이 있으면 차단? — 명세는 "pending row가 1개라도 있으면 추가 신청 차단" → **임의 팀에 대한 pending이 있으면 신규 신청 자체 차단**. rejected→pending 갱신은 같은 팀에 한해 허용). 구현: 신규 INSERT 직전에 user의 다른 pending 존재 여부 확인 → 있으면 "blocked".
     - team_id가 존재하는 팀인지(`teams` 에 있고 `deleted_at IS NULL`) 검증은 라우트에서. (`teams.deleted_at` 컬럼은 #2에서 추가됨.)
   - `decide_team_application(user_id, team_id, decision: 'approved'|'rejected') -> bool`: 대상 row가 `status='pending'`일 때만 처리. `approved` → `UPDATE ... SET status='approved', joined_at=CURRENT_TIMESTAMP`. `rejected` → `UPDATE ... SET status='rejected'`. 대상 없거나 pending 아니면 False.
   - (선택) `list_team_applications(team_id) -> list[dict]`: `status='pending'` row + user name join — admin/팀관리자 조회용. 본 사이클 최소 UI 안 만들면 라우트만 노출하고 UI는 #18.
2. `app.py` 신규 라우트:
   - `POST /api/me/team-applications`: body `{team_id}`. `user = auth.get_current_user(request)`; 없으면 401. `auth.is_admin(user)`면 400 "관리자는 팀 신청 대상이 아닙니다." team_id 유효성(존재 + deleted_at IS NULL) 검사 → 없으면 404. `db.apply_to_team(...)` 결과: "created"/"updated" → 200 `{"ok": True, "status": "pending"}`; "blocked" → 409 (현재 상태에 따라 "이미 가입 신청 중입니다." / "이미 해당 팀 멤버입니다." / "다른 팀 신청이 처리 대기 중입니다." 중 하나 — `apply_to_team`이 이유를 반환하도록).
   - `GET /api/admin/teams/{team_id}/applications` (또는 `/api/teams/{team_id}/applications`): `user`가 `auth.is_admin(user)` 또는 `auth.is_team_admin(user, team_id)`여야 함 → 아니면 403. `db.list_team_applications(team_id)` 반환.
   - `POST /api/admin/teams/{team_id}/applications/{user_id}/decide`: body `{decision}` ∈ {"approve"/"approved", "reject"/"rejected"}. 권한: admin 또는 해당 팀 관리자. `db.decide_team_application(...)` → False면 404/409. 200 `{"ok": True}`.
   - (라우트 경로명은 기존 컨벤션 보고 결정. `/api/admin/...`은 admin 전용 느낌이라 팀관리자도 쓰는 라우트는 `/api/teams/{team_id}/applications` 류가 더 적절할 수 있음 — backend-dev가 기존 라우트 네이밍 보고 판단.)
3. `backend_changes.md`에 변경 내용·신규 함수 시그니처·라우트 목록 기록.

### 주의
- `users` INSERT 시 다른 NOT NULL/DEFAULT 컬럼 확인 (현 `approve_pending_user`는 `name, password, role, team_id, is_active`만 지정 → 나머지 DEFAULT로 충분). `name_norm`, `password_hash`만 추가하면 됨.
- `create_session` role 인자: admin이 아닌 한 만료 30일. 'member' 넘기면 OK.
- 마이그레이션 phase 본문 추가 불필요 (#8은 런타임 라우트만).

## frontend-dev (backend 완료 후)
- `templates/register.html`:
  - 폼에서 memo textarea 제거(또는 placeholder만 유지 — backend가 memo를 무시하므로 제거 권장). h2/desc 문구를 "계정 가입" / "이름과 비밀번호로 가입하면 바로 시작할 수 있습니다."로 변경.
  - submit JS: `/api/register` 응답 `res.ok` 시 → `window.location.href = '/'` (현재의 success-box 표시 대신). 에러 시 기존처럼 `err.detail` 표시.
  - success-box 블록은 제거하거나 안 쓰이게 처리.
- `templates/base.html` line 521: `가입 신청` → `계정 가입` (선택, 문구 일관성).
- 팀 신청 버튼/모달은 만들지 않음 (#12·#14·#18 책임).
- `frontend_changes.md` 기록.

## code-reviewer
- backend_changes.md + frontend_changes.md 읽고 변경 파일 정적 리뷰.
- 중점: (1) `/api/register`에 `response: Response` 누락 여부, set_cookie 옵션 일관성, (2) 예약어 비교 시 casefold 처리(`Admin`, `ADMIN`), (3) `create_user_account`의 race-safe 처리(IntegrityError 가드), (4) `apply_to_team`의 pending 차단 로직 정확성(다른 팀 pending 존재 시 차단 / 같은 팀 rejected→pending 갱신 / approved 중복 방지), (5) 권한 체크 — team-applications decide 라우트가 admin OR team_admin 둘 다 허용하는지, 외부인 차단, (6) pending_users 쓰기 경로가 register에서 완전히 빠졌는지, (7) rate limit 유지, (8) UNIQUE 인덱스(`idx_user_teams_user_team`)와 INSERT 충돌 가능성.

## qa
- import-time + 합성 DB 검증 (서버 재시작 불가).
- 시나리오:
  1. 예약 사용자명 차단: `admin`, `Admin`, `ADMIN`, `system`, `ROOT` 등 → 400.
  2. 정규식 위반(`hong gildong` 공백, `a_b` 밑줄, `유저@1`) → 400. 정책 위반(`abcdef` 숫자없음, `123456` 영문없음) → 400.
  3. 정상 가입 → `users` row 생성(role='member', team_id NULL, name_norm 정규화됨, password_hash 채워짐, password=''), 세션 생성됨, 응답에 ok/name/role/team_id, Set-Cookie 헤더 존재.
  4. 같은 이름 재가입(대소문자 변형 포함, name_norm 충돌) → 409.
  5. 가입한 사용자가 `/api/me/team-applications`로 팀 신청 → user_teams pending row 1건. 같은 사용자가 다른 팀에 또 신청 → 409 (pending 차단). 같은 팀에 또 신청 → 409.
  6. admin이 `decide`로 approve → status='approved', joined_at 채워짐. reject 시나리오: 새 신청 → reject → status='rejected'. 같은 팀 재신청 → row 갱신되어 pending(row 추가 X — count 동일).
  7. 권한: 외부 사용자가 decide 라우트 호출 → 403. 팀 관리자(`user_teams` role='admin' or is_team_admin 헬퍼 충족)가 자기 팀 decide → 200.
  8. pending_users 테이블에 register로 인한 신규 row가 생기지 않음을 확인.
- 서버 재시작 필요 없음(런타임 라우트, 마이그레이션 변경 없음). 실서버 E2E는 사용자 재시작 후 후속 가능 — qa_report에 명시.

# 산출물
`.claude/workspaces/current/{backend_changes.md, code_review_report.md, qa_report.md}` + `scripts/`(qa 검증 스크립트).
