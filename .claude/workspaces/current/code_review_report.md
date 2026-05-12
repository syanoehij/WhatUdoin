## 코드 리뷰 보고서 — #15-1 히든 프로젝트 다중 팀 전환

### 리뷰 대상 파일
- `database.py` — `create_hidden_project`, `_hidden_project_visible_row`, `get_hidden_project_members`(주석), `get_hidden_project_addable_members`, `add_hidden_project_member`, `transfer_hidden_project_owner`, `admin_change_hidden_project_owner`, `transfer_hidden_projects_on_removal`
- `app.py` — `add_hidden_project_member_route` (line ~3146)

### 차단(Blocking) ❌
- 없음.

### 경고(Warning) ⚠️
- `database.py:get_hidden_project_addable_members` / `transfer_hidden_project_owner` / `admin_change_hidden_project_owner` / `transfer_hidden_projects_on_removal` — `EXISTS(SELECT 1 FROM user_teams ut WHERE ut.user_id = u.id ...)` 상관 서브쿼리가 함수마다 4벌 복제됨. 현 규모에서 허용이나, `_approved_member_clause()` 같은 SQL 조각 헬퍼로 묶을 여지(향후 정리 후보, #16 정리 시점). 동작상 문제 없음.
- `database.py:create_hidden_project` — `team_id is None` 시 기존 `None` 반환(중복) 대신 `ValueError` raise로 동작 변경. 라우트(`POST /api/manage/hidden-projects`)는 이미 `resolve_work_team` None을 403으로 막으므로 정상 흐름 영향 없음. 단 다른 내부 caller가 추후 None으로 호출하면 500이 됨 — 현재 caller는 app.py 1곳뿐이라 OK. (의도된 변경, feature_spec §1과 일치.)

### 통과 ✅
- [x] **권한 체크**: 변경된 라우트(`add_hidden_project_member_route`)는 기존 `_require_editor` + `_get_hidden_proj_or_404` + `_require_hidden_can_manage` 그대로 유지. 신규 라우트 없음.
- [x] **SQL 파라미터화**: 모든 신규/수정 쿼리가 `?` 바인딩 사용. 문자열 포매팅 삽입 없음 (`status = 'approved'` 는 리터럴 상수).
- [x] **`u.team_id = p.team_id` 잔존 0건**: 히든 프로젝트 관련 함수 전수 확인 — 모두 `user_teams` EXISTS로 전환됨. 남은 `users.team_id` 참조는 마이그레이션 코드/주석뿐.
- [x] **owner_id=NULL 케이스 대응**: `get_hidden_project_addable_members` / `add_hidden_project_member` 가 owner를 참조하지 않고 `projects.team_id` 기준 → owner 부재 복구 흐름에서 admin이 멤버 추가 가능. feature_spec §3·§4 충족.
- [x] **admin 자동 제외**: 후보 쿼리에 `u.role != 'admin'` 유지 + admin은 `user_teams` row 없음(이중 보장). assignee 후보(`get_hidden_project_members` → `_assert_assignees_in_hidden_project`)는 멤버만 → admin 자연 제외.
- [x] **`_hidden_project_visible_row` 단일 진입점**: `is_hidden_project_visible`, `_can_view_hidden_trash_project`, `_trash_item_visible_to_viewer` 모두 자동 반영.
- [x] **시그니처 변경 호출부 일치**: `add_hidden_project_member` 3-인자 → 2-인자, app.py 라우트 호출부 갱신 확인. `create_hidden_project` 시그니처 무변경(team_id=None 본문 거부만), app.py 호출부는 항상 `team_id=` 명시 → 영향 없음.
- [x] **DB 경로 / `_ctx` / 마이그레이션 패턴**: 해당 변경 없음 (SELECT 쿼리 전환 + 시그니처 정리만, 스키마 무변경).
- [x] **`import app` OK** (backend 단계에서 확인).
- [x] **SSE 노출**: `_sse_publish("projects.changed", {"name": None, ...})` — 기존대로 히든 프로젝트 이름 미노출 유지.
- [x] **외부 caller 전수**: `add_hidden_project_member` / `create_hidden_project` 호출은 app.py 라우트 2곳뿐 (둘 다 처리됨).

### 최종 판정
- **통과** — 차단 결함 없음. 경고 2건은 모두 의도된 설계·향후 리팩터 후보. QA 진행 가능.
