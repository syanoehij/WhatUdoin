# 팀 기능 그룹 A #8 — 계정 가입과 팀 신청 분리 (backend 변경)

## 요약
`/api/register` 를 "관리자 승인 대기"(pending_users) 흐름에서 "즉시 가입 + 자동 로그인" 흐름으로 교체.
팀 가입은 별도 라우트(`POST /api/me/team-applications`)로 분리. 팀 관리자/시스템 관리자가 신청을 수락/거절하는 백엔드 라우트 추가. 마이그레이션 phase 본문 추가 없음(런타임 라우트/DB 함수만).

## database.py — 신규 함수
`reject_pending_user` 바로 아래에 추가.

- `create_user_account(name, password) -> dict | None`
  - `name_norm = normalize_name(name)` 산출.
  - `users(name, name_norm, password, password_hash, role, team_id, is_active)` INSERT — `password=''`(NOT NULL deviation, #7과 동일), `password_hash=passwords.hash_password(password)`, `role='member'`, `team_id=NULL`, `is_active=1`.
  - 사전 `SELECT 1 FROM users WHERE name_norm=?` 중복 검사 + `sqlite3.IntegrityError` 캐치(`users.name_norm` 전역 UNIQUE 인덱스 — #7) → 둘 다 충돌 시 `None`.
  - 반환: 신규 row dict.
- `apply_to_team(user_id, team_id) -> tuple[str, str|None]`
  - 트랜잭션 안에서: 사용자의 임의 팀 대상 pending row 존재 여부 + 이 팀 대상 row 조회.
  - 이 팀 row가 `approved` → `("blocked","already_member")`; `pending` → `("blocked","pending_here")`; `rejected`(또는 기타) → 다른 팀에 pending 있으면 `("blocked","pending_other")`, 없으면 `UPDATE ... SET status='pending', role='member'` → `("updated",None)`.
  - 이 팀 row 없음 → 다른 팀 pending 있으면 `("blocked","pending_other")`, 없으면 `INSERT ... status='pending'` → `("created",None)`.
  - row 추가는 신규 INSERT 1경로뿐. rejected→pending은 동일 `(user_id, team_id)` row UPDATE(joined_at 미변경) — `idx_user_teams_user_team` UNIQUE(#2)와 정합.
- `list_team_applications(team_id) -> list[dict]`
  - `user_teams.status='pending'` row + `users.name`/`name_norm` JOIN. `id ASC` 정렬. 운영자 조회용.
- `decide_team_application(user_id, team_id, decision) -> bool`
  - `decision ∈ {'approved','rejected'}` 가드. 대상 row가 `status='pending'` 일 때만 처리. `approved` → `status='approved', joined_at=CURRENT_TIMESTAMP`; `rejected` → `status='rejected'`. 대상 없거나 pending 아니면 `False`.
- `get_team_active(team_id) -> dict | None`
  - `teams WHERE id=? AND deleted_at IS NULL`. team_id 유효성 검사용(#2에서 추가된 `teams.deleted_at` 사용).

### 데드코드 (삭제하지 않음, mention만)
- `check_register_duplicate(name, password)` / `create_pending_user(name, password, memo)` — 이제 `/api/register`에서 호출되지 않음. 다른 호출처 없음(grep 확인). 함수 자체는 보존(Phase 5에서 pending_users 테이블 drop 검토 시 함께 정리).

## app.py — 라우트 변경/추가

### `/api/register` (POST) — 교체
- 시그니처: `register(request: Request, response: Response)` (이전엔 `response` 없었음 — `/api/login` 패턴 차용).
- `@limiter.limit("5/minute")` 유지.
- 검증 순서: 빈 값(400) → `passwords.is_valid_user_name`(400 "이름은 영문·숫자·한글만 사용할 수 있습니다.") → 예약어(`name.casefold() ∈ {"admin","system","root","guest","anonymous"}` → 400 "사용할 수 없는 이름입니다.") → `passwords.is_valid_password_policy`(400 "비밀번호는 영문과 숫자를 모두 포함해야 합니다.").
- `db.create_user_account(name, password)` → None이면 409 "이미 사용 중인 이름입니다.".
- 성공: `db.create_session(user["id"], role=user["role"])`("member"라 만료 30일), `db.record_ip(...)`, `response.set_cookie(auth.SESSION_COOKIE, ..., httponly, samesite="lax", secure=(scheme=="https"), max_age=86400*30)`.
- 응답: `{"ok": True, "name": ..., "role": "member", "team_id": None}`.
- memo 필드는 더 이상 받지 않음.
- 모듈 상수 `RESERVED_USERNAMES = {"admin","system","root","guest","anonymous"}` 를 register 라우트 바로 위에 추가.

### `POST /api/me/team-applications` — 신규
- `@limiter.limit("10/minute")` + `_check_csrf`.
- 로그인 필요(없으면 401). `auth.is_admin(user)` → 400 "관리자는 팀 신청 대상이 아닙니다.".
- body `{team_id}` — None/비정수 → 400. `db.get_team_active(team_id)` 없음 → 404.
- `db.apply_to_team(...)` 결과 `"blocked"` 시 detail에 따라 409("이미 가입 신청 중입니다." / "다른 팀 신청이 처리 대기 중입니다." / "이미 해당 팀의 멤버입니다."). 그 외 `{"ok": True, "status": "pending"}`.

### `_require_team_admin(request, team_id)` — 헬퍼 신규
- `_check_csrf` + 로그인 검사 + `auth.is_team_admin(user, team_id)`(글로벌 admin 또는 `user_teams.role='admin' AND status='approved'`). 실패 시 401/403.

### `GET /api/teams/{team_id}/applications` — 신규
- `_require_team_admin` → `db.list_team_applications(team_id)`.

### `POST /api/teams/{team_id}/applications/{user_id}/decide` — 신규
- `_require_team_admin`. body `{decision}` — `approve|approved|reject|rejected` 매핑(소문자/공백 허용). 매핑 실패 시 400.
- `db.decide_team_application(...)` → False면 404 "처리할 신청이 없습니다.". 그 외 `{"ok": True}`.

## 영향 없는 부분 (의도적 미변경)
- admin pending 라우트(`/api/admin/pending`, `.../approve`, `.../reject`)와 `templates/admin.html` pending UI — 본 사이클 범위 밖. pending_users 테이블 자체도 유지(Phase 5에서 drop 검토).
- `/api/login`, `/api/admin/login`, `/api/me/change-password` — 변경 없음.
- 마이그레이션 phase / preflight — 변경 없음. 운영 DB 재시작 불필요(런타임 라우트만).

## 프론트엔드 동반 변경
- `templates/register.html`: memo 입력 제거, 문구 "계정 가입"/"이름과 비밀번호로 가입하면 바로 시작할 수 있습니다.", submit 성공 시 `window.location.href='/'`(success-box 제거), 에러는 기존처럼 `err.detail` 표시.
- `templates/base.html` line 521: 로그인 모달의 "가입 신청" 링크 → "계정 가입".

## import-time / 합성 DB 검증
`.claude/workspaces/current/scripts/verify_team_a_008.py` — 72 PASS / 0 FAIL.
- `python.exe -c "import app"` OK + 신규 라우트 4건 등록 확인.
