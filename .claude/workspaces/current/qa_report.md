# QA — 팀 기능 그룹 B #13 (`/팀이름` 비로그인 공개 포털)

## 방법

운영 서버는 IP 자동 로그인이라 비로그인 브라우저 재현 불가 → **TestClient + 임시 DB**로 익명 요청 검증
(TestClient 기본 클라이언트 IP `testclient`는 user_ips whitelist 미매칭 → 익명). `tests/phase82_team_portal.py` 신규.
임시 DB는 `.claude/workspaces/current/test_dbs/` 하위에 격리 생성 후 정리.

## 결과: 8/8 PASS

| 테스트 | 검증 내용 | 결과 |
|--------|----------|------|
| `test_dynamic_route_registered_last` | `/{team_name}`이 모든 정적 페이지 라우트(`/`,`/calendar`,`/admin`,`/kanban`,`/gantt`,`/doc`,`/check`,`/notice`,`/trash`,`/remote`,`/avr`)보다 뒤에 등록(소스 오프셋) + 핸들러가 `_TEAM_NAME_RE`/`casefold`/`RESERVED_TEAM_PATHS`/404/`get_team_by_name_exact`/`deleted` 사용 | PASS |
| `test_db_helpers_exist_no_new_phases` | `get_team_by_name_exact`(WHERE name = ?)·`get_team_menu_visibility`·`get_public_portal_data` 존재 + `team_phase_13`/`public_portal_v1` 마커 부재(스키마 무변경) | PASS |
| `test_team_portal_template` | `team_portal.html`: `{% if deleted %}` 분기 + `{% if not user %}`/`href="/register"` 계정 가입 조건 + `portal.menu` 사용 + `#14` 미룸 주석 | PASS |
| `test_portal_case_exact_and_404s` | `GET /ABC`(대문자 팀)→200·팀 이름·`btn-primary">계정 가입`·`공개 포털 — ...` / `GET /abc`→404(대소문자 분리) / `GET /Nonexistent`→404 / `GET /Bad-Name`(하이픈)→404 | PASS |
| `test_static_and_api_routes_not_eclipsed` | `GET /admin`→admin 로그인 페이지(포털 아님) / `GET /api/health`→200 / `GET /docs`·`/redoc`·`/openapi.json`→200 / `GET /static/<실제 파일>`→404 아님(mount 살아 있음) / 예약어 20종 각각 `GET /<예약어>`→포털 마크업 부재 | PASS |
| `test_portal_data_filtering` | `/ABC` 마크업: `PUBLIC_EVENT/CHECK/DOC` 노출, `PRIVATE_EVENT/CHECK/DOC`(is_public=0) 비노출, `PERSONAL_DOC`(is_team_doc=0) 비노출, `HIDDEN_PROJ_EVENT/CHECK`(히든 프로젝트, is_public=1이어도) 비노출, `HiddenProj` 이름 자체 비노출 | PASS |
| `test_deleted_team_notice_only` | 삭제 예정 팀 `GET /GoneTeam`→200·"삭제 예정" 노출·`btn-primary">계정 가입` 부재·`공개 포털 — ...` 부재·`id="portal-tabs"` 부재·공개 데이터 부재 | PASS |
| `test_logged_in_user_gets_portal_no_redirect` | 로그인 사용자(create_user_account+create_session 쿠키) `GET /ABC`→200 포털(redirect 아님)·`공개 포털 — ...` 노출·`btn-primary">계정 가입`(포털 본문) 부재 | PASS |

## 회귀

- `tests/phase80_landing_page.py`(#11) 5/5 PASS
- `tests/phase81_unassigned_user.py`(#12) 9/9(8 함수, 1 함수 다중 시나리오) PASS
- 나머지 `tests/` 전체: 12 PASS / 2 FAIL — 2건은 `tests/test_project_rename.py`의 사전 결함(`sqlite3.OperationalError: no such column: team_id` — 옛 픽스처 DB에 `projects.team_id` 없음. master HEAD에서도 동일, prior cycle changelog 다수 기록. #13 무관).
- `import app` OK (실제 Python 3.12).

## 자가 발견 결함

수정 중 1건: 테스트 초안에서 `"계정 가입" not in html`로 단언했으나 `base.html`의 로그인 모달(항상 렌더)에 `/register` "계정 가입" 링크(`class="login-link"`)가 항상 존재 → 포털 본문 버튼만 식별하도록 `'btn-primary">계정 가입'`로 교체. (소스 변경 아님 — 테스트 수정만.)
또 1건: `"portal-tabs" not in html`은 CSS 셀렉터 텍스트(`.portal-tabs { ... }`)에 매칭됨 → `'id="portal-tabs"'`(요소)로 교체.

## 서버 재시작

운영 서버 반영 시 **재시작 필요** (app.py / database.py 코드 reload — 스키마 무변경이라 마이그레이션 불필요). 단 본 사이클 단위 검증은 TestClient(임시 DB)로 완료.
