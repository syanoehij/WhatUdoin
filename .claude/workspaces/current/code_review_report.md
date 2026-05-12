# 코드 리뷰 — 팀 기능 그룹 B #13 (`/팀이름` 비로그인 공개 포털)

대상: `app.py`, `database.py`, `templates/team_portal.html`(신규), `tests/phase82_team_portal.py`(신규).

## 차단 결함

없음.

## 점검 항목

1. **라우트 등록 순서 (1차 메커니즘)** — ✅ `@app.get("/{team_name}")`는 `app.py` 라우트 정의 영역 맨 끝(uvicorn 부트스트랩 직전)에 등록. 모든 정적 페이지 라우트(`/`, `/calendar`, `/admin`, `/kanban`, `/gantt`, `/doc`, `/check`, `/notice`, `/trash`, `/remote`, `/avr` 등)보다 뒤 — 테스트 `test_dynamic_route_registered_last`가 소스 오프셋으로 강제 검증. `/docs` `/redoc` `/openapi.json`은 `app = FastAPI(...)`(line 153)에서 등록 → 자연히 우선 (테스트가 `GET /docs|/redoc|/openapi.json` → 200 확인).
2. **예약어 casefold 비교** — ✅ `team_name.casefold() in RESERVED_TEAM_PATHS`, `RESERVED_TEAM_PATHS`도 전부 `casefold()`로 정규화. 하드코딩 목록(계획서 섹션 4 전부) + 실제 등록 라우트 첫 세그먼트 합집합으로 누락 자동 방지(advisor 권장).
3. **정규식 검사가 핸들러 안** — ✅ `Path(pattern=...)`가 아니라 핸들러에서 `_TEAM_NAME_RE.match`. 불일치 시 422가 아닌 404 (계획서 요구 "404로 분리").
4. **대소문자 정확 일치** — ✅ `db.get_team_by_name_exact`는 `WHERE name = ?` (SQLite 기본 BINARY collation — `teams.name TEXT NOT NULL UNIQUE`, COLLATE NOCASE 없음 확인). `/ABC` 유효 시 `/abc` → None → 404. 테스트 `test_portal_case_exact_and_404s`가 확인.
5. **데이터 필터가 메뉴 설정과 독립** — ✅ `get_public_portal_data`는 `is_public`/private/히든 프로젝트 필터만 적용, `menu` 키는 별도. `get_team_menu_visibility`는 UI 진입(탭) 제어용으로만 템플릿에서 쓰임. 테스트 `test_portal_data_filtering`가 `is_public=0`·히든 프로젝트 항목 비노출 확인.
6. **히든 프로젝트 완전 차단** — ✅ `get_kanban_events(viewer=None)`의 `private_clause`(`is_private=1 OR is_hidden=1` 제외), `get_project_timeline(viewer=None)`의 `hidden_projs` skip, `get_checklists(viewer=None)`의 `public_filter` + `get_blocked_hidden_project_names(None)` — 모두 `is_public` 값 무관. `is_public=1` 히든 프로젝트 일정/체크가 비노출되는지 테스트가 명시 확인.
7. **삭제 예정 팀** — ✅ `team.get("deleted_at")`이면 `deleted=True`로 안내 페이지만, `portal` 컨텍스트 자체를 안 넘김 → 데이터·가입 버튼·탭 비노출. 테스트 `test_deleted_team_notice_only`가 `btn-primary">계정 가입`/`공개 포털 — ...`/`id="portal-tabs"` 부재 확인.
8. **로그인 사용자 200 (redirect 없음)** — ✅ 라우트가 auth 분기 안 함. 테스트 `test_logged_in_user_gets_portal_no_redirect` 확인.
9. **DB 함수 시그니처 호환성** — ✅ 신규 함수 3개 + 모듈 상수 1개만 추가. 기존 함수(`get_kanban_events`/`get_project_timeline`/`get_checklists`/`get_team_active`) 미변경.
10. **마이그레이션 phase 추가 없음** — ✅ 스키마 무변경. PHASES 리스트 손대지 않음. 신규 DB 함수는 SELECT 전용. 테스트가 `team_phase_13`/`public_portal_v1` 마커 부재 확인.
11. **surgical 변경** — ✅ `app.py`는 맨 끝에 블록 1개 추가, `database.py`는 `get_team_active` 직후 블록 1개 추가, 템플릿/테스트 신규 파일. 인접 코드 변경 없음.

## 경고 (허용)

- `team_portal.html`의 CSS 셀렉터 텍스트 `.portal-tabs`가 항상 마크업에 들어가므로 "탭 부재" 판별은 `id="portal-tabs"` 요소로 해야 함 — 테스트가 이 패턴 사용. (frontend_changes.md에도 기록.)
- `get_public_portal_data`가 `get_checklists(viewer=None)`(전 팀 조회) 후 Python에서 team_id 필터 — 팀 수·체크 수가 매우 많은 환경에서 약간 비효율. 현 규모(인트라넷)에선 무해. 필요 시 #19 이후 `get_checklists`에 team_id 인자 추가 검토.
- 캘린더 탭 데이터(`portal.calendar`)는 칸반 events 풀 재활용 — 메뉴 기본 OFF라 보통 렌더 안 됨. 정확히는 캘린더 전용 events 조회(meeting/journal 포함)와 약간 다를 수 있으나 #13 범위에선 칸반 풀로 충분(계획서 섹션 9 "같은 events 데이터").

## 결론

통과. 차단 결함 0, 경고 3(모두 허용 가능). qa 단계로 진행.
