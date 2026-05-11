# 코드 리뷰 — 팀 기능 그룹 A #8 (계정 가입과 팀 신청 분리)

대상: `app.py`, `database.py`, `templates/register.html`, `templates/base.html`.
방식: 정적 리뷰 + import-time 검증 + 합성 DB E2E(verify_team_a_008.py 72 PASS).

## 결론: 통과 (차단 결함 0, 경고 2)

## 확인 항목

1. `/api/register` 시그니처에 `response: Response` 추가됨 — 자동 로그인 set_cookie 가능. ✔
   - set_cookie 옵션(`httponly`, `samesite="lax"`, `secure=(scheme=="https")`, `max_age=86400*30`)이 `/api/login` 과 일치. ✔
   - `@limiter.limit("5/minute")` 유지. ✔
2. 예약 사용자명 비교: `name.casefold() in RESERVED_USERNAMES` — `Admin`/`ADMIN`/`Guest` 등 대소문자 변형 모두 차단됨(QA 7케이스 PASS). 한글·터키어 등 비ASCII 예약어는 없으므로 `casefold` 로 충분(과한 정규화 회피). ✔
3. `create_user_account` race-safe: 사전 `SELECT name_norm` + `sqlite3.IntegrityError` 캐치 이중 가드. `users.name_norm` 전역 UNIQUE 인덱스(#7)와 정합. ✔
   - INSERT 컬럼: `name, name_norm, password='', password_hash, role='member', team_id=NULL, is_active=1`. 나머지 컬럼은 DEFAULT(`created_at` 등). `approve_pending_user` 의 기존 INSERT 패턴과 동일 수준. ✔
   - `password=''`(빈 문자열) — `users.password NOT NULL DEFAULT ''` 제약 회피. #7의 `reset_user_password` deviation 과 동일. ✔
4. `apply_to_team` pending 차단 로직:
   - "임의 팀 pending 1개라도 있으면 신규 신청 차단" — `other_pending` 조회 후, 이 팀에 row 없음 OR (rejected row를 pending으로 갱신하려는데) 다른 팀 pending 존재 시 `pending_other` 차단. ✔
   - 이 팀 `approved` → `already_member` 차단(중복 가입 방지). ✔
   - 이 팀 `pending` → `pending_here` 차단(중복 신청 방지). ✔
   - 이 팀 `rejected`/기타 + 다른 팀 pending 없음 → 동일 `(user_id, team_id)` row UPDATE(`status='pending', role='member'`, joined_at 미변경) — row 추가 X, `idx_user_teams_user_team` UNIQUE(#2)와 정합. ✔
   - 멀티팀 멤버(팀 B approved + 팀 A rejected)가 팀 A 재신청 시 `other_pending` 비어 있어 갱신 허용. ✔ (QA "rejected→pending 시 다른 팀 pending 있으면 차단" + Part1 추가 케이스 PASS)
   - 모두 단일 `with get_conn()` 트랜잭션 안에서 처리 — TOCTOU 최소화. ✔
5. 권한 게이트:
   - `POST /api/me/team-applications`: 로그인 필요(401), `auth.is_admin` → 400. ✔
   - `GET /api/teams/{team_id}/applications`, `POST .../decide`: `_require_team_admin(request, team_id)` → `_check_csrf` + 로그인(401) + `auth.is_team_admin(user, team_id)`(글로벌 admin 또는 `user_teams.role='admin' AND status='approved'`, auth.py:99 확인). 외부인 403, 다른 팀 관리자 403. ✔ (QA: 외부인 403, 팀관리자 자기팀 200/다른팀 403 PASS)
   - decide 라우트는 path의 team_id 로만 권한 체크 + `decide_team_application` 도 `(user_id, team_id)` 로만 동작 → cross-team 누출 없음. ✔
6. `decide_team_application` — `decision` 화이트리스트(`approved`/`rejected`)만 처리, 대상 row가 `status='pending'` 일 때만 갱신. 이미 처리됐거나 없는 신청 → False → 라우트 404. ✔
   - 라우트에서 `decision` 입력을 `str(...).strip().lower()` + `approve/approved/reject/rejected` 매핑 → 비문자 입력에도 500 안 남. ✔
7. pending_users 쓰기 경로: `/api/register` 에서 `create_pending_user` 호출 제거됨. grep 결과 다른 호출처 없음. QA에서 register 흐름 후 `pending_users` 0건 확인. ✔
8. `_check_csrf` 사용: 신규 unsafe 라우트(`/api/me/team-applications`, decide) 모두 `_check_csrf` 통과. ✔ (`/api/register` 는 기존에도 csrf 미적용 — 비로그인 진입점이라 기존 정책 유지.)
9. 프론트:
   - `register.html`: memo 입력 제거, `/api/register` 응답 `res.ok` 시 `window.location.href='/'`. `res.json().catch(()=>({}))` 로 빈 바디 방어. ✔
   - `base.html`: 로그인 모달 링크 문구 "가입 신청"→"계정 가입" — 단순 텍스트 변경. ✔
   - 팀 신청 UI는 만들지 않음(#12·#14·#18 책임) — 명세 부합. ✔
10. import-time: `python -c "import app"` OK. 신규 라우트 4건(`/api/register`[POST], `/api/me/team-applications`[POST], `/api/teams/{team_id}/applications`[GET], `/api/teams/{team_id}/applications/{user_id}/decide`[POST]) 등록 확인. ✔

## 경고 (비차단)

- W1. `database.check_register_duplicate` / `create_pending_user` 는 이제 데드코드(다른 호출처 없음). 의도적으로 보존 — pending_users 테이블 자체가 Phase 5에서 drop 검토 대상이라 그때 함께 정리하는 게 자연스러움. 본 사이클에서 제거하지 않음(범위 밖 surgical-change 원칙).
- W2. `decide_team_application` 의 `decision` 인자가 라우트 외부(테스트/내부 호출)에서 잘못 들어와도 조용히 `False` 반환 — 라우트는 매핑 후 화이트리스트만 넘기므로 실사용 경로엔 문제 없음. 향후 다른 호출처가 생기면 명시적 예외가 나을 수 있으나 현 단계 과설계 회피.

## QA 결과
`scripts/verify_team_a_008.py`: 72 PASS / 0 FAIL.
