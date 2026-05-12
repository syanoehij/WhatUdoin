# 요청

'팀 기능 구현 todo.md' 그룹 B #15-2 — links 기능 다중 팀 전환.
상세 사양: '팀 기능 구현 계획.md' §13 (links 테이블 정리) + §16 (권한 원칙) + §8-1 (자료별 적용 표).
그룹 A(#1~#10) + 그룹 B #11~#15-1 완료됨. 이번 사이클은 **#15-2 한 항목만** 끝까지.

# 분류

백엔드 수정 (4개 라우트 + DB 헬퍼 시그니처 전환) + 프론트엔드 (헤더 드롭다운 — 확인 결과 변경 불필요) → **백엔드 모드 + 프론트 확인** (backend-dev → frontend-dev[확인만] → code-reviewer → qa)

# 배경 — 현재 코드 상태 (반드시 확인하고 시작)

- `links` 테이블 (database.py:516): `id, title, url, description, scope('personal'|'team'), team_id INTEGER, created_by TEXT NOT NULL, created_at` — **작성자 컬럼 `created_by`(이름 문자열) 존재**. 스키마 변경 없음.
- 그룹 A #4에서 `links.team_id` 백필(`scope='team'` + NULL row 한정, `created_by` → users.name 추론) 이미 끝남 (database.py:1500 부근). **백필 중복 작업 금지.**
- 현재 `/api/links` 4개 라우트 (app.py:4060~4112):
  - `GET /api/links` → `db.get_links(user["name"], user.get("team_id"))` — **`users.team_id` 단일 비교 잔존**.
  - `POST /api/links` → `team_id = user.get("team_id") if scope=='team' else None` — **`users.team_id` 잔존**.
  - `PUT /api/links/{id}` → `db.update_link(link_id, title, url, desc, user["name"])` — **admin 분기 없음** (created_by 일치만 검사).
  - `DELETE /api/links/{id}` → `db.delete_link(link_id, user["name"], user.get("role", "editor"))` — admin 분기 이미 있음.
- DB 헬퍼 (database.py:6160~6197):
  - `get_links(user_name, team_id)`: `(scope='personal' AND created_by=?) OR (scope='team' AND team_id=?)`
  - `create_link(title, url, desc, scope, team_id, created_by)`
  - `update_link(link_id, title, url, desc, user_name)`: `WHERE id=? AND created_by=?` — **admin 경로 없음**
  - `delete_link(link_id, user_name, role)`: role=='admin' → 무조건 / else created_by 일치
- `_work_scope(request, user, explicit_id)` (app.py:2031): admin → None / 비admin → resolve_work_team 1개 set / 비소속 명시 무시·대표팀 fallback / 미배정 → set(). #10 라우트들과 동일 패턴.
- `auth.resolve_work_team(request, user, explicit_id)` / `auth.require_work_team_access(user, team_id)` (auth.py) 갖춰져 있음.
- 헤더 링크 드롭다운 (templates/base.html:1490~1641): `_loadLinks()`가 드롭다운 열 때마다 `fetch('/api/links')`. 작업 팀 전환은 #15에서 `location.reload()` 흐름 — 새 work_team_id 쿠키 적용 후 다음 드롭다운 open 시 새 컨텍스트로 자연 fetch. **base.html 변경 불필요** (확인만).
- `links` 영역 외 `users.team_id` 잔존: `app.py` 4개 라우트의 `user.get("team_id")`만. base.html 드롭다운 JS는 `link.created_by`(작성자 이름)만 비교 — `users.team_id` 참조 없음.

# 에이전트별 작업

## backend-dev

### database.py
1. `get_links(user_name, work_team_ids)` — 시그니처 `team_id` → `work_team_ids`(None=무필터(admin)/set()=팀링크 없음/{tid}=단일 팀). #10 `_work_scope` 소비자 컨벤션. 본문:
   - `work_team_ids is None` (admin): `WHERE scope='personal' AND created_by=?  UNION ALL  WHERE scope='team'` (전 팀 팀링크 + 본인 개인링크).
   - 빈 set: `WHERE scope='personal' AND created_by=?` 만.
   - {tid}: 기존과 동일하게 `(scope='personal' AND created_by=?) OR (scope='team' AND team_id=?)`.
   - 다중 set 도 일반화(`team_id IN (...)`) — 미배정/비admin은 항상 0 또는 1개지만 admin이 명시 work_team_id 줄 가능성 위해 일반화.
2. `update_link(link_id, title, url, desc, user_name, role)` — `role=='admin'` 분기 추가 (`delete_link`와 동일 패턴): admin → `WHERE id=?` / else → `WHERE id=? AND created_by=?`.
3. `create_link` — 변경 없음 (시그니처 그대로). 주석 한 줄: scope='team'이면 team_id는 호출부가 resolve_work_team으로 확정해 전달.

### app.py
1. `api_get_links(request, team_id: int = None)` — `user` 없으면 `[]` 유지. 있으면 `scope = _work_scope(request, user, team_id)`; `db.get_links(user["name"], scope)` 호출. (비로그인은 `[]` early-return — 기존 유지.)
2. `api_create_link` — `scope='team'`:
   - `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`
   - `if team_id is None: raise HTTPException(400, "작업 팀이 결정되지 않았습니다. team_id를 지정하세요.")` (manage/projects 라우트 app.py:2799-2802 미러 — admin이 work_team 없이 호출한 경우 거부)
   - `auth.require_work_team_access(user, team_id)` (admin 통과 / 비admin 비소속 → 403)
   - `scope='personal'` → `team_id = None` (기존 유지).
   - `db.create_link(..., team_id, user["name"])`.
3. `api_update_link` — `db.update_link(link_id, title, url, desc, user["name"], user.get("role", "member"))`. 실패 시 403 유지.
4. `api_delete_link` — 변경 없음 확인 (이미 role 전달). `user.get("role", "editor")` → `"member"`로 default만 손볼지 검토 (선택 — 기존 동작 무해하므로 그대로 둬도 됨).

### backend_changes.md 에 명시
- `links` 스키마 무변경 (마이그레이션 phase 추가 없음).
- **admin GET 시맨틱**: `_work_scope`가 admin에 None → DB 무필터 → admin은 헤더 드롭다운에서 전 팀의 scope='team' 링크 + 본인 개인링크를 본다. `/api/checklists`·`/api/events`·`/api/doc`와 일관 (모두 admin → 전 팀). 의도와 다르면 리뷰어가 flag.

## frontend-dev (확인만)

- `templates/base.html` 헤더 링크 드롭다운: 변경 불필요한지 재확인. `_loadLinks()`가 드롭다운 open마다 fetch → 작업 팀 전환(`location.reload()`) 후 다음 open 시 새 컨텍스트로 자연 반영. `link.created_by` 비교만 — `users.team_id` 참조 없음. 변경할 게 있으면 보고만.

## qa

- `tests/phase86_links_multiteam.py` 신규 (phase84 패턴: `_setup`/`_login`/`_join`, TestClient, temp DB `.claude/workspaces/current/test_dbs/`).
- 정적 invariant: `update_link` role 분기 / `get_links` 시그니처 `work_team_ids` / 라우트 `_work_scope`·`resolve_work_team`·`require_work_team_access` 사용 / import app OK.
- 시나리오:
  - 다중 팀 사용자 작업 팀 전환 → GET /api/links 가 새 팀의 scope='team' 링크로 (work_team_id 쿠키 기반).
  - 다른 팀 멤버 세션에선 그 팀 scope='team' 링크 안 보임.
  - personal 링크는 작성자 본인만 (작업 팀 무관).
  - admin이 work_team_id 명시(쿠키 또는 ?team_id) 후 scope='team' 링크 POST/PUT/DELETE 가능. (admin GET은 전 팀 노출.)
  - 같은 팀 멤버 B가 멤버 A의 scope='team' 링크 PUT·DELETE → 403.
  - admin이 타인 scope='team' 링크 PUT·DELETE 가능.
  - admin이 work_team 없이 (쿠키 없음 + ?team_id 없음) scope='team' POST → 400.
  - 회귀: phase80~85 + (기존 링크 전용 테스트 없음 — phase86이 첫 커버) + 기존 동작(personal CRUD 본인).
- 서버 재시작 필요 여부 보고 (코드 reload 필요 — 스키마 무변경).

# 주의사항

- `users.team_id` 컬럼 자체는 제거하지 않음 (#23 책임). 이번엔 links 라우트가 그것을 안 읽도록만 전환.
- `_require_editor`는 손대지 않음 (#16 책임) — `is_editor` 위임 그대로.
- `create_link` 시그니처는 그대로 유지 — 호출부(app.py)가 team_id를 확정해 넘긴다.
