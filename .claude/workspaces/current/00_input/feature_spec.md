# 요청

'팀 기능 구현 todo.md' 그룹 B #15-1 — 히든 프로젝트 다중 팀 전환.
상세 사양: '팀 기능 구현 계획.md' §12 (히든 프로젝트) + §8-1 (히든 프로젝트 추가 예외).
그룹 A(#1~#10) + 그룹 B #11~#15 완료됨. 이번 사이클은 **#15-1 한 항목만** 끝까지.

# 분류

백엔드 수정 (DB 헬퍼 쿼리 전환 — `users.team_id` 단일 비교 → `user_teams.status='approved'` + `projects.team_id`; 라우트 `create_hidden_project` 시그니처/fallback 정리) → **백엔드 모드** (backend-dev → code-reviewer → qa)
- 프론트엔드: 멤버 후보 드롭다운·assignee 후보는 백엔드 반환값만 렌더하므로 템플릿 변경 없음 (frontend-dev 생략). 단 backend-dev가 작업 중 `addable-members` 관련 템플릿에서 `users.team_id`/`CURRENT_USER.team_id` 분기 같은 게 발견되면 보고만.

# 배경 — 현재 코드 상태 (반드시 확인하고 시작)

그룹 A에서 이미 전환된 부분 (중복 작업 금지):
- `create_hidden_project(name, color, memo, owner_id, team_id=None)` — `team_id` 시그니처 추가됨, `(team_id, name_norm)` 팀 제한 중복 검사 적용됨 (database.py:3884 부근). 라우트 `POST /api/manage/hidden-projects` (app.py:3008)는 이미 `auth.resolve_work_team` + `auth.require_work_team_access(user, team_id)` 호출 후 `team_id=` 명시 전달. **남은 것**: DB 함수 안의 `team_id is None` 시 `owner_row["team_id"]` (users.team_id) fallback 제거.
- `_hidden_project_visible_row` (database.py:4067 부근) — 현재 `JOIN users u ... AND u.team_id IS NOT NULL AND p.team_id IS NOT NULL AND u.team_id = p.team_id`. **이게 `users.team_id` 단일 비교 잔존** — `user_teams` 기준으로 전환 대상.
- `transfer_hidden_project_owner` / `admin_change_hidden_project_owner` (database.py:4156·4188 부근) — 후보 멤버 검증 쿼리에 `u.team_id = p.team_id` 잔존 → 전환 대상.
- `add_hidden_project_member` (database.py:4108 부근) — `owner_row["team_id"] == target_row["team_id"]` 비교 (users.team_id 단일) → 전환 대상. **owner_id=NULL 케이스(admin 호출 경로)에는 owner가 없으므로 반드시 `projects.team_id` 기준으로 검증해야 함.**
- `get_hidden_project_addable_members` (database.py:4079 부근) — `owner.users.team_id` 기준 후보 조회 → `projects.team_id` 기준 + `user_teams` 승인 멤버로 전환. **owner_id=NULL이어도 빈 리스트 반환하지 말고 projects.team_id 기준 후보 반환.**
- `transfer_hidden_projects_on_removal` (database.py:6365 부근) — next_owner 후보 쿼리에 `u.team_id = p.team_id` 잔존 → 전환 대상.
- `get_hidden_project_members` (database.py:4063 부근) — `SELECT u.id, u.name, u.team_id, ...` 에서 `u.team_id`는 표시용일 뿐(권한 판단 안 함) → **그대로 유지**. 단 주석 한 줄로 "표시용, 권한 판단 아님" 명시 권장.
- `_assert_assignees_in_hidden_project` (app.py:3059 부근) — 이미 `db.get_hidden_project_members`의 member 이름만 비교 → assignee 후보는 `project_members` 기준으로 이미 제한됨, 변경 불필요. (todo §#15-1 "assignee 후보도 project_members로 제한" = 이미 만족.)
- `auth.user_team_ids`/`user_can_access_team`/`require_work_team_access` 갖춰져 있음 (auth.py). `user_teams` 실 컬럼명: `status` (값 `'pending'`|`'approved'`), `joined_at`.

# 핵심 substitution 패턴 (backend-dev: 모든 대상 쿼리에 동일 적용)

기존:
```sql
JOIN users u ON u.id = pm.user_id
JOIN projects p ON p.id = pm.project_id
WHERE ...
  AND u.is_active = 1
  AND u.team_id IS NOT NULL
  AND p.team_id IS NOT NULL
  AND u.team_id = p.team_id
  AND u.role != 'admin'
```
전환 후:
```sql
JOIN users u ON u.id = pm.user_id
JOIN projects p ON p.id = pm.project_id
WHERE ...
  AND u.is_active = 1
  AND p.team_id IS NOT NULL
  AND u.role != 'admin'
  AND EXISTS (
        SELECT 1 FROM user_teams ut
         WHERE ut.user_id = u.id
           AND ut.team_id = p.team_id
           AND ut.status = 'approved'
      )
```
(`u.team_id IS NOT NULL` 제거, `u.team_id = p.team_id` → user_teams EXISTS. `teams.deleted_at IS NULL` 추가 검증은 굳이 불필요 — projects.team_id 가 가리키는 팀이 soft-delete됐으면 그 자체로 별개 이슈이고, 이번 범위는 user_teams 전환에 한정. 단 추가하고 싶으면 `JOIN teams t ON t.id = p.team_id AND t.deleted_at IS NULL` 도 허용. **결정: 추가하지 않는다 — 기존 쿼리도 teams.deleted_at을 안 봤고, 스코프 최소화.**)

# backend-dev 담당 작업 (database.py 위주)

## 1. create_hidden_project — users.team_id fallback 제거
- `team_id is None` 일 때 `owner_row["team_id"]` 조회·fallback 코드 블록 제거.
- `team_id is None` 이면 → `ValueError("히든 프로젝트는 team_id가 필요합니다")` raise (NULL row 생성 금지). 라우트는 이미 None을 403으로 막으므로 정상 흐름엔 영향 없음. 시그니처는 `team_id: int | None = None` 유지(호환), 본문에서 None 거부.
- docstring 갱신 (users.team_id fallback 언급 제거).

## 2. _hidden_project_visible_row — user_teams 기준
- `AND u.team_id IS NOT NULL AND p.team_id IS NOT NULL AND u.team_id = p.team_id` → 위 substitution 패턴 적용 (`p.team_id IS NOT NULL` 유지, `u.team_id` 비교 → user_teams EXISTS).

## 3. get_hidden_project_addable_members — projects.team_id 기준 + user_teams
- `owner_team_id = owner_row["team_id"]` 부분 삭제 → 대신 `SELECT team_id FROM projects WHERE id = ?` 로 `project_team_id` 조회.
- `project_team_id is None` 이면 `[]` 반환 (NULL 잔존 프로젝트는 후보 없음 — 계획서 NULL 잔존 정책과 일관).
- 후보 쿼리: `SELECT u.id, u.name FROM users u WHERE u.is_active = 1 AND u.role != 'admin' AND u.id NOT IN (SELECT user_id FROM project_members WHERE project_id = ?) AND EXISTS (SELECT 1 FROM user_teams ut WHERE ut.user_id = u.id AND ut.team_id = ? AND ut.status = 'approved') ORDER BY u.name` — `?` = (project_id, project_team_id).
- **owner_id=NULL 이어도 동작** (owner를 안 봄). docstring 갱신.

## 4. add_hidden_project_member — projects.team_id 기준 검증
- 현재: `owner_row` 조회 + `owner_row["team_id"] != target_row["team_id"]` 비교.
- 전환: `proj_row = SELECT team_id FROM projects WHERE id = ?`; `proj_team_id = proj_row["team_id"]`. `proj_team_id is None` → `return False`.
- target 검증: `target_row = SELECT role, is_active FROM users WHERE id = ?` (team_id 컬럼 불필요). is_active=0 → False, role=='admin' → False.
- target이 `user_teams` 에서 `proj_team_id` 승인 멤버인지 확인: `SELECT 1 FROM user_teams WHERE user_id = ? AND team_id = ? AND status = 'approved'` → 없으면 `return False`.
- 이미 멤버면 `return None`, 아니면 INSERT 후 `return True`.
- **시그니처에서 `owner_id` 인자 제거** → `add_hidden_project_member(project_id, user_id)`. 라우트(app.py:3146)도 `proj["owner_id"]` 전달 제거. (owner_id=NULL 케이스에 owner_id를 넘기면 None이 들어와 비교 불가 — 시그니처 정리가 자연스러움.) **단 라우트 호출부 시그니처 변경은 app.py 작업에 포함.**
- docstring 갱신: "True(성공), False(팀 미승인/admin/비활성/NULL팀), None(이미 멤버)".

## 5. transfer_hidden_project_owner / admin_change_hidden_project_owner — user_teams 기준
- 두 함수 안 member 검증 쿼리에 substitution 패턴 적용. (`p.team_id IS NOT NULL` 유지, `u.team_id` 비교 → user_teams EXISTS, `u.team_id IS NOT NULL` 제거.)
- 동작·시그니처 무변경.

## 6. transfer_hidden_projects_on_removal — user_teams 기준
- next_owner 후보 쿼리에 substitution 패턴 적용.
- **시그니처·동작 무변경.** 단일 caller(`app.py:1776` admin_update_user — 레거시 단일 팀 admin 경로)가 유일하므로 다중 팀 부분 추방 시나리오는 이번 범위 밖(그 라우트 자체가 아직 레거시 `users.team_id` 모델). docstring에 "후보 조회는 user_teams + projects.team_id 기준" 한 줄.

## 7. get_hidden_project_members — 주석만
- `u.team_id` SELECT는 표시용 — "권한 판단 아님" 주석 한 줄 (실 변경 없음). 선택 사항.

## app.py 변경 (최소)
- `add_hidden_project_member_route` (app.py:3137 부근): `db.add_hidden_project_member(proj["id"], target_user_id, proj["owner_id"])` → `db.add_hidden_project_member(proj["id"], target_user_id)`.
- 그 외 라우트 무변경 (이미 `_require_hidden_can_manage`가 owner OR admin 통과 — owner_id=NULL이면 owner_id가 None이라 일반 멤버는 자동 차단, admin은 통과. `add_hidden_project_member_route` 의 result is False 메시지는 "같은 팀의 승인된 멤버만 추가할 수 있습니다."로 미세 수정 권장).

# 주의사항

- 그룹 A에서 `u.team_id = p.team_id` 가 일부 함수에 이미 들어가 있음 — 이건 그룹 A가 "projects.team_id 도입" 단계에서 임시로 깐 것이고 #15-1이 user_teams로 마저 옮기는 것. 새로 만들지 말고 기존 쿼리를 수정.
- `_hidden_project_visible_row` 는 `is_hidden_project_visible`, `_can_view_hidden_trash_project`, `_trash_item_visible_to_viewer` 가 모두 사용 — 한 곳만 고치면 전부 반영됨.
- 마이그레이션 phase 추가 없음 (스키마 무변경, SELECT 쿼리 전환만). `database.py` 의 마이그레이션 함수(`_migrate_*`)는 건드리지 않음.
- changelog 안 건드림 (명시 요청 없음).

# QA 검증 (qa: TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 다중 팀 owner 시나리오 브라우저 재현 불가)

`tests/phase85_hidden_project_multiteam.py` (네이밍 컨벤션):
1. **정적 검증**: database.py 에 `u.team_id = p.team_id` 잔존 0건 (히든 프로젝트 함수 한정 — grep), `create_hidden_project` 에 `users.team_id` fallback 없음, 위 6개 함수에 `user_teams` + `status = 'approved'` EXISTS 패턴 존재, `add_hidden_project_member` 시그니처 2-인자, app.py 라우트 호출부 갱신.
2. owner 추방→같은 팀 활성 멤버 자동 이양: 팀 A에 owner+멤버2, owner를 `transfer_hidden_projects_on_removal` → 멤버2(added_at 최선두)가 owner.
3. 후보 없으면 owner_id=NULL: 팀 A 히든에 owner만 → `transfer_hidden_projects_on_removal` → owner_id IS NULL. 그 다음 admin이 `add_hidden_project_member(pid, 새멤버)` (같은 팀 A 승인 멤버) 성공 → `admin_change_hidden_project_owner(pid, 새멤버)` 성공 → owner_id = 새멤버.
4. admin 멤버 후보 제외: 팀 A에 admin도 user_teams approved row가 (이론상) 있어도 `get_hidden_project_addable_members` 결과에 admin 없음. (실제론 admin은 user_teams row 없음 — role != 'admin' 필터로 이중 보장 확인.) assignee 후보(`get_hidden_project_members` → `_assert_assignees_in_hidden_project`)에도 admin 미포함(멤버가 아니므로 자연 제외) — assignee에 admin 이름 넣으면 422 확인.
5. 다중 팀 owner: 사용자 X가 팀 A·B 둘 다 approved. X가 owner인 히든 프로젝트 P(team_id=A). `get_hidden_project_addable_members(P)` 후보는 팀 A 승인 멤버만 (팀 B 멤버 안 보임). 팀 B 멤버를 `add_hidden_project_member(P, 팀B멤버)` 시도 → False.
6. 멀티팀 가시성: 사용자 Y가 `project_members` row 있고 `user_teams`에서 team A approved → `is_hidden_project_visible(P, Y)` True. Y를 user_teams team A에서 제거(또는 status='pending') → False (project_members row는 그대로). 다시 approved → True.
7. owner_id=NULL 복구 시에도 후보 조회: P.owner_id=NULL, P.team_id=A → `get_hidden_project_addable_members(P)` 가 빈 리스트가 아니라 팀 A 승인 멤버(현 멤버 제외) 반환.
8. **회귀**: `import app` OK. Jinja `get_template` (변경된 템플릿 없으면 생략 가능). 기존 히든 프로젝트 테스트 (phase80~84 + 히든 관련 spec 파일 찾아서 — `grep -rl hidden tests/`). `tests/test_project_rename.py` 2 FAIL은 #15에서 확인된 사전 결함(옛 픽스처 DB에 projects.team_id 없음) — #15-1 무관, 그대로면 OK.

# 서버 재시작
운영 서버 반영 시 재시작 필요 (database.py + app.py reload). 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료.
