# #15-1 백엔드 변경 내역 — 히든 프로젝트 다중 팀 전환

## 핵심: `users.team_id` 단일 비교 → `user_teams.status='approved'` + `projects.team_id`

substitution 패턴 (모든 대상 쿼리에 적용):
```
- AND u.team_id IS NOT NULL
- AND p.team_id IS NOT NULL       (← 유지)
- AND u.team_id = p.team_id
+ AND p.team_id IS NOT NULL       (유지)
+ AND EXISTS (SELECT 1 FROM user_teams ut
+              WHERE ut.user_id = u.id AND ut.team_id = p.team_id AND ut.status = 'approved')
```

## database.py

1. **`create_hidden_project(name, color, memo, owner_id, team_id=None)`** — `team_id is None` 시 owner의 `users.team_id` fallback 코드 제거. None이면 `ValueError("히든 프로젝트는 team_id가 필요합니다")` raise (NULL row 생성 금지). 시그니처는 호환을 위해 `team_id: int | None = None` 유지, 본문에서 None 거부. docstring 갱신.

2. **`_hidden_project_visible_row(conn, project_id, user)`** — 가시성 쿼리 `u.team_id IS NOT NULL ... u.team_id = p.team_id` → substitution 패턴. 이 함수가 `is_hidden_project_visible`, `_can_view_hidden_trash_project`, `_trash_item_visible_to_viewer` 의 단일 진입점이므로 전부 반영됨.

3. **`get_hidden_project_members(project_id)`** — 변경 없음. docstring에 "`u.team_id`는 표시용 legacy 값, 권한 판단 안 함" 한 줄 추가.

4. **`get_hidden_project_addable_members(project_id)`** — owner의 `users.team_id` 참조 제거. `SELECT team_id FROM projects WHERE id = ?` 로 `project_team_id` 조회 → `None`이면 `[]`. 후보 쿼리: `is_active=1 AND role != 'admin' AND id NOT IN (project_members) AND EXISTS(user_teams approved on project_team_id)`. **owner_id=NULL 이어도 동작** (owner를 안 봄). docstring 갱신.

5. **`add_hidden_project_member(project_id, user_id)`** — **시그니처에서 `owner_id` 인자 제거** (3-인자 → 2-인자). owner 참조 대신 `SELECT team_id FROM projects WHERE id = ?` 로 `proj_team_id` 조회 → `None`이면 `False`. target은 `SELECT role, is_active FROM users` (team_id 컬럼 불필요), is_active=0 또는 role=='admin' → `False`. `SELECT 1 FROM user_teams WHERE user_id=? AND team_id=proj_team_id AND status='approved'` 없으면 `False`. 이미 멤버면 `None`, 아니면 INSERT 후 `True`. owner_id=NULL 복구 시 admin이 호출해도 동작. docstring 갱신.

6. **`transfer_hidden_project_owner(project_id, new_owner_id, requester_id)`** — member 검증 쿼리에 substitution 패턴. 시그니처·동작 무변경.

7. **`admin_change_hidden_project_owner(project_id, new_owner_id)`** — member 검증 쿼리에 substitution 패턴. 시그니처·동작 무변경. docstring 갱신.

8. **`transfer_hidden_projects_on_removal(user_id, hidden_projects)`** — next_owner 후보 쿼리에 substitution 패턴. 시그니처·동작 무변경. docstring에 "후보 조회는 user_teams + projects.team_id 기준" 추가. (단일 caller `app.py:1776 admin_update_user` 는 레거시 단일 팀 admin 경로 — 다중 팀 부분 추방 시나리오는 이번 범위 밖.)

## app.py

- `add_hidden_project_member_route` (line 3146): `db.add_hidden_project_member(proj["id"], target_user_id, proj["owner_id"])` → `db.add_hidden_project_member(proj["id"], target_user_id)`. 403 메시지 "같은 팀 사용자만 멤버로 추가할 수 있습니다." → "해당 팀의 승인된 멤버만 멤버로 추가할 수 있습니다."
- `POST /api/manage/hidden-projects` (line 3008~) — 변경 없음 (이미 `resolve_work_team` + `require_work_team_access` 적용됨).
- `_assert_assignees_in_hidden_project` (line 3059~) — 변경 없음 (이미 `get_hidden_project_members` 이름만 비교 → assignee 후보가 `project_members` 기준으로 이미 제한됨; admin은 멤버가 아니라 자연 제외).

## 변경 안 한 것 (스코프 밖)
- 마이그레이션 phase 추가 없음 (스키마 무변경 — SELECT 쿼리 전환만).
- changelog 미수정.
- `_hidden_project_visible_row` 등에 `teams.deleted_at IS NULL` 추가 검증은 의도적으로 추가 안 함 (기존 쿼리도 안 봤고 스코프 최소화 — soft-delete된 팀의 히든 프로젝트는 별개 이슈).
- 프론트엔드 변경 없음 (멤버 후보 드롭다운·assignee 후보는 백엔드 반환값만 렌더).

## 검증
- `import app` OK.
- `add_hidden_project_member` / `create_hidden_project` 외부 caller 전수: app.py 라우트 2곳뿐 (둘 다 갱신/무변경 확인). 테스트는 신규 phase85 + 기존 phase46(Playwright)·phase80~84.

## 변경 파일
- `database.py`
- `app.py`
