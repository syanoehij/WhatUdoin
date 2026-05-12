# #15 코드 리뷰 — work_team_id 쿠키 + 프로필 "팀 변경" UI

대상: `auth.py`, `database.py`, `app.py`, `templates/base.html`, `templates/kanban.html`, `templates/project.html`, `templates/calendar.html`, `templates/doc_list.html`, `static/css/style.css`, `tests/phase84_work_team_cookie.py`.

## 결론: 차단 결함 0건. 경고 3건 (현 범위 허용).

## 점검 항목

- **마이그레이션 phase 추가 없음** ✔ — 신규 DB 헬퍼(`first_active_team_id`/`primary_team_id_for_user`/`user_work_teams`) 전부 SELECT 전용. `PHASES`/`_PREFLIGHT_CHECKS` 미변경. 스키마 무변경 → 운영 DB 마이그레이션 불필요.
- **#10 회귀 안전** ✔ — `_work_scope`(app.py) 무변경: admin `None` 반환·explicit team_id 우선·비소속 무시 fallback·미배정 빈 set 그대로. 쿠키 검증을 `resolve_work_team` 내부로 흡수했지만 `_work_scope`의 line 1948-1950 재검증은 중복일 뿐 무해(그대로 둠 — surgical). phase84 test_j(명시 team_id 우선) PASS.
- **admin 동작** ✔ — `resolve_work_team(admin, no/invalid cookie)` = 첫 비삭제 팀(`first_active_team_id`) — 계획서 §7 의도. `_work_scope(admin)` = `None` 유지 → READ 슈퍼유저 무필터 (phase84 test_l PASS). admin write 경로(`resolve_work_team` 호출 — app.py:2153 event create 등)가 이제 admin에게 실제 team_id 반환 — 계획서 §7 의도. 아카이브 `verify_team10.py` read-focused로 admin write `team_id IS NULL` 단언 없음 (확인 완료).
- **쿠키 검증** ✔ — `resolve_work_team` 쿠키 경로: int 파싱 + `user_can_access_team(user, ctid)` AND `_team_is_active(ctid)` 통과 시에만 사용. 무효(삭제 예정/소속 빠짐)면 무시 → `_work_team_default` → 호출부 `_ensure_work_team_cookie` 가 Set-Cookie 갱신 (phase84 test_d/test_e PASS).
- **Set-Cookie 발급** ✔ — `_set_work_team_cookie`(max_age 1년, samesite=lax, httponly=False, path=/). `_ensure_work_team_cookie`: user None/미배정이면 noop, `resolve_work_team` 결과(None이면 noop)와 현재 쿠키 다르면 Set-Cookie. SSR 페이지 12개에 호출 추가. 미배정 SSR `GET /` → Set-Cookie 없음 (phase84 test_k PASS).
- **`POST /api/me/work-team`** ✔ — `_check_csrf` + 로그인 필수(401) + int 파싱(400) + `db.get_team_active`(없으면 404) + `auth.require_work_team_access`(비admin 비소속 403). phase84 test_f/g/h/n PASS.
- **`GET /api/me/work-team`** ✔ — `_require_login`. 비admin=`user_work_teams`, admin=`get_visible_teams()`. phase84 test_m PASS.
- **프론트 XSS** ✔ — `loadWorkTeams` 가 팀 이름을 `textContent` 로 주입(HTML interpolation 아님). 버튼 onclick 은 int `t.id` 만.
- **프론트 surgical** ✔ — kanban/project 화면별 팀 드롭다운 + `_applyInitialTeamFilter` + localStorage 키(`kanban_team_filter`/`proj_team_filter`) 제거, `loadKanban`/`loadData` URL 단순화. member-chip·my-only·project-filter 등 나머지 그대로. `CURRENT_USER.team_id` → `work_team_id` 4곳(kanban/calendar 멤버칩 + calendar 편집 게이팅 힌트 + doc_list weekly-team 기본값) 교체, legacy 잔존 없음.
- **import** ✔ — `python -c "import app"` OK. Jinja `get_template` base/kanban/project/calendar/doc_list OK.

## 경고 (허용)

1. `_ctx` 가 모든 SSR 페이지에서 `resolve_work_team` 호출 → 페이지당 DB 쿼리 +1~2회 (`user_can_access_team`/`primary_team_id_for_user`/`get_team_active`). 호출 빈도 낮고 #12에서도 `_ctx` +1 쿼리 허용한 전례 — 허용. 향후 캐싱 후보.
2. `_ensure_work_team_cookie` 내부에서도 `resolve_work_team` 한 번 더 호출 (`_ctx`와 합쳐 페이지당 2회). 마찬가지로 허용.
3. `templates/kanban.html`/`project.html` 라우트가 여전히 `teams=db.get_all_teams()` 넘기지만 두 템플릿에서 더 이상 사용 안 함 (다른 템플릿 공유라 시그니처 미변경). 사소한 데드 인자 — 허용 (#16 정리 후보).
4. `weekly-team` 모달 드롭다운 / `calendar.html` 의 `CURRENT_USER.role === 'editor'` 리터럴 — #15 범위 밖, 미변경.

## 회귀 테스트

- `tests/phase84_work_team_cookie.py` 19/19 PASS.
- `tests/phase80_landing_page.py`(#11) 5 / `phase81_unassigned_user.py`(#12) 8 / `phase82_team_portal.py`(#13) 8 / `phase83_team_portal_loggedin.py`(#14) 9 — 30/30 PASS.
- `tests/test_project_rename.py` 2 FAIL은 사전 결함(`git stash` 후 동일 — 옛 픽스처 DB에 `projects.team_id` 없음, master HEAD `04006ba` 동일, #15 무관).
