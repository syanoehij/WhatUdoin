# #15 백엔드 변경 — work_team_id 쿠키 발급/검증 + /api/me/work-team

스키마 무변경 (마이그레이션 phase 추가 없음 — 신규 DB 헬퍼는 전부 SELECT 전용).

## auth.py

- `WORK_TEAM_COOKIE = "work_team_id"` (불변, 주석만 갱신).
- `_team_is_active(team_id) -> bool` 신규 — `db.get_team_active(team_id) is not None` (쿠키/명시 값 검증용, 예외 시 False).
- `_work_team_default(user)` 신규 — 쿠키 없거나 무효일 때 대표 작업 팀:
  - admin → `db.first_active_team_id()` (첫 비삭제 팀 id 최소)
  - 비admin → `db.primary_team_id_for_user(uid)` (joined_at 가장 이른 approved+비삭제 팀) → 없으면 legacy `users.team_id` (비삭제일 때만) → None
- `resolve_work_team(request, user, explicit_id=None)` 재작성:
  - explicit_id → int 파싱되면 무조건 신뢰 (호출부 `_work_scope`/`require_work_team_access` 가 검증) — #10 동작 불변
  - work_team_id 쿠키 → int 파싱 + `user_can_access_team(user, ctid)` AND `_team_is_active(ctid)` 통과 시에만 사용. 무효면 무시
  - 그 외 → `_work_team_default(user)`
  - **변경점 vs 이전**: ① 쿠키 값 검증 추가(이전엔 무조건 신뢰), ② admin no-cookie/무효-cookie fallback = `None` → 첫 비삭제 팀, ③ 비admin 대표 팀 = `min(team_ids)` → joined_at 기준 (`primary_team_id_for_user`)

## database.py (get_team_by_name_exact 뒤에 추가)

- `first_active_team_id() -> int | None` — `SELECT id FROM teams WHERE deleted_at IS NULL ORDER BY id ASC LIMIT 1`
- `primary_team_id_for_user(user_id) -> int | None` — `user_teams` approved + `teams.deleted_at IS NULL` JOIN, `ORDER BY ut.joined_at ASC, ut.team_id ASC LIMIT 1` (joined_at 컬럼 실재 확인됨: database.py:458 CREATE)
- `user_work_teams(user_id) -> list[dict]` — 본인 approved+비삭제 소속 팀 `[{id, name}]` joined_at 순 (드롭다운/POST 검증용; admin은 호출 안 함)
- `get_team_active(team_id)` 는 이미 존재 — `team_active` 용도로 재사용

## app.py

- `_set_work_team_cookie(response, team_id)` — `set_cookie(WORK_TEAM_COOKIE, str(tid), max_age=86400*365, samesite="lax", httponly=False, path="/")`
- `_ensure_work_team_cookie(request, response, user)` — SSR 응답 보정: user None 또는 미배정이면 noop / `resolve_work_team` 결과(None이면 noop)와 현재 쿠키가 다르면 Set-Cookie
- `GET /api/me/work-team` — `_require_login`. `{current: <tid|None>, teams: [{id,name}], is_admin}`. 비admin=`db.user_work_teams`, admin=`db.get_visible_teams()`
- `POST /api/me/work-team` — `_check_csrf` + 로그인 필수(401) + `team_id` int 파싱(400) + `db.get_team_active`(없으면 404) + `auth.require_work_team_access`(비admin 비소속 403, admin 통과) → `_set_work_team_cookie` → `{ok, team_id, team_name}`
- `_ctx(request, **kwargs)` — `work_team_id`/`work_team_name` 키 추가 (user None 또는 미배정이면 둘 다 None; 그 외 `resolve_work_team` + `get_team_active().name`)
- SSR 페이지 라우트에 `_ensure_work_team_cookie` 호출 추가: `/`(index), `/calendar`, `/admin`(admin인 경우만 — admin_login 응답엔 안 붙음, user None이라 noop), `/kanban`, `/gantt`, `/project-manage`, `/doc`(docs_page), `/doc/new`, `/doc/{id}`(doc_detail_page), `/doc/{id}/history`, `/check`, `/trash` — 총 12개. `calendar_page` 는 `user` 변수 추출하도록 미세 리팩터(동작 동일).

## 회귀 주의

- `_work_scope`(app.py) **무변경** — admin `None` 반환·explicit team_id 우선·비소속 무시 fallback·미배정 빈 set 그대로. 쿠키 검증은 `resolve_work_team` 내부로 흡수돼 `_work_scope`의 line 1948-1950 재검증과 중복이지만 무해 (그대로 둠 — surgical).
- admin write 경로(`resolve_work_team` 호출 — app.py:2153 event create 등)는 이제 admin에게 실제 team_id(쿠키 or 첫 비삭제 팀) 반환 — 계획서 §7 의도. 아카이브 `verify_team10.py` 는 read-focused로 admin write `team_id IS NULL` 단언 없음 (확인 완료).
- 쿠키 없이 명시 `team_id`만 보내던 #10 검증 테스트 = explicit_id 경로 그대로 → 영향 없음.

## import 검증

`python -c "import app"` → OK.

## 서버 재시작

스키마 무변경 → 운영 DB 마이그레이션 불필요. 코드 reload 위해 **서버 재시작 필요** (auth.py / database.py / app.py 변경).
