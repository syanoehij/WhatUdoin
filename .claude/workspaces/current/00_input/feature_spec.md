# 요청

'팀 기능 구현 todo.md' 그룹 A의 #10 — 문서·체크 팀 경계 완성 + 편집·삭제 권한 모델.
작업 중 발생하는 에러(import, 라우트 충돌, 마이그레이션 깨짐 등)는 같은 흐름에서 수정까지.

# 분류

백엔드 수정 (라우트/DB 쿼리/권한 헬퍼 전환) → 팀 모드 아님. backend → reviewer → qa.
프론트 변경은 원칙적으로 없음 (#15에서 쿠키/UI 통합). 단 #10 시점에 프론트가 명시 `team_id`를 보낼 필요는 없음 — 서버가 `resolve_work_team` 대표 팀 fallback으로 동작하면 됨. 프론트 변경 불필요.

# 권위 명세

- `팀 기능 구현 todo.md` `### #10` 섹션 (line 274~313). 인용 박스의 #10/#15 분리 정책 준수: **#10은 백엔드 가시성 쿼리 전환만**. `work_team_id` 쿠키 발급/검증/Set-Cookie, "팀 변경" UI, 화면별 팀 드롭다운 제거는 #15. #10 시점엔 라우트가 `team_id` 파라미터를 명시적으로 받을 수 있고, 없으면 `resolve_work_team` 대표 팀 fallback.
- `팀 기능 구현 계획.md` 섹션 8 (화면별 팀 기준, 개인 문서 가시성), 섹션 8-1 (데이터 소유·편집 권한 모델).

# 핵심 구분 — 절대 혼동 금지

## events / checklists / projects / team_notices = "순수 팀 공유" 모델
- `team_id` 일치만 검사. `is_team_doc`·`team_share` **존재하지 않는 컬럼**. 이 4종 테이블에는 적용 안 함.
- `created_by`(events·checklists는 TEXT 이름, projects는 `owner_id` INTEGER)는 표시·로그용. **편집·삭제 권한 판단에 쓰지 않는다** — 같은 팀 승인 멤버 누구나 편집·삭제 가능.
- todo의 "개인 문서 가시성 규칙(작성자 본인 / team_share=1 / NULL)" 불릿은 **meetings(문서)에만 해당**. `_filter_events_by_visibility`에 `team_share` 로직을 절대 넣지 말 것.

## meetings (문서) = 혼합 모델 (`is_team_doc` 분기)
- `is_team_doc=1` → 팀 공유 모델 (team_id 일치 → 같은 팀 승인 멤버 누구나 편집·삭제)
- `is_team_doc=0` (개인 문서):
  - 가시성: 작성자 본인 항상 / `team_share=1`이면 같은 팀 멤버 + **현재 작업 팀이 그 team_id일 때** 읽기만 / `team_id IS NULL`이면 작성자 본인만 (`team_share` 무관)
  - 편집·삭제: **작성자 본인만**. `team_share=1`이라도 다른 멤버는 읽기만, 편집·삭제 시도 시 403.
- `meetings.created_by`는 INTEGER (user id) — 작성자 비교는 `doc["created_by"] == user["id"]`.
- 이미 `permissions._can_read_doc` / `app._can_write_doc`가 존재하나 **단일팀 모델(`doc.team_id == user.team_id`)** — 현재 작업 팀(`resolve_work_team`) 기준으로 교체 필요.

# 작성자 컬럼 (검증된 사실 — 가정 금지)
| 테이블 | 작성자 컬럼 | 타입 | 본인 판정 |
|---|---|---|---|
| events | `created_by` | TEXT (이름) | `row["created_by"] == user["name"]` |
| checklists | `created_by` | TEXT (이름) | `row["created_by"] == user["name"]` |
| meetings | `created_by` | INTEGER (user id) | `row["created_by"] == user["id"]` |
| projects | `owner_id` | INTEGER (user id) | `row["owner_id"] == user["id"]` |

`teams.deleted_at`이 NULL이 아니면 그 팀은 삭제 예정 — 가시성·편집 양쪽에서 제외해야 함. `auth.user_team_ids`는 `status='approved'`만 반환하지만 `teams.deleted_at`은 안 봄 — **확인 필요**: `user_team_ids` 쿼리에 `JOIN teams ... AND teams.deleted_at IS NULL` 추가하거나, 호출부에서 별도 필터. 가장 깔끔한 건 `auth.user_team_ids`를 `JOIN teams t ON t.id = ut.team_id WHERE ut.status='approved' AND t.deleted_at IS NULL`로 보강 (legacy fallback도 동일하게 deleted 체크). 이렇게 하면 `user_can_access_team`, `resolve_work_team` 등 전부 자동 정합.

# backend-dev 담당 작업

## A. 권한 헬퍼 계층 (auth.py)
1. `user_team_ids(user)`에 `teams.deleted_at IS NULL` 조인 추가. legacy fallback도 deleted 팀이면 빈 집합.
2. **새 헬퍼 `can_edit_meeting(user, doc, work_team_id)`** (auth.py 또는 permissions.py — 순환 import 주의. `_can_write_doc`이 app.py에 있으니 거기서 처리하거나 permissions.py로 이관 후 양쪽 재사용 권장):
   - admin → True (단 `work_team_id` 명시 필수 정책은 #16 — 본 사이클은 admin은 그냥 True로 두되 주석으로 #16 표시. 기존 `_can_write_doc`도 admin은 무조건 True였으니 회귀 아님)
   - `is_team_doc=1`: `doc.team_id` is None → True (NULL 잔존 팀 문서, 작성자 백필 실패분 — 기존 동작 유지) / 아니면 `user_can_access_team(user, doc.team_id)`
   - `is_team_doc=0`: `doc.created_by == user["id"]`만 True. (team_share 무관)
3. `_can_write_doc` (app.py)을 위 헬퍼로 교체 (혹은 헬퍼 내용을 직접 반영). 단일팀 비교 `doc_team == user.get("team_id")` 제거.
4. `can_edit_event`, `can_edit_checklist`, `can_edit_project` (auth.py): 이미 `user_can_access_team`에 위임 → **순수 팀 공유 모델로 이미 정합**. 변경 불필요. 단 #1에서 `user_team_ids`에 deleted 조인 들어가면 추방·삭제팀 자동 차단도 자동 정합. (확인만)

## B. 가시성 쿼리 — `_filter_events_by_visibility` 시그니처 전환 (app.py)
1. 시그니처를 `_filter_events_by_visibility(events, user, scope_team_ids=None)`로 변경:
   - `scope_team_ids=None` AND admin → 무필터 (전 팀)
   - `scope_team_ids=None` AND 비admin → "현재 작업 팀 미상" — 호출부에서 항상 set을 넘기도록 하는 게 원칙. 안전 fallback: `user_team_ids(user)` (= 통합 동작). 단 호출부별로 의도에 맞게 명시.
   - `scope_team_ids`가 set이면 그 안의 team_id만 통과 (+ NULL row는 작성자 본인만)
2. 필터 규칙 (events):
   - 히든 프로젝트 차단 (기존 `blocked_hidden` 유지)
   - `team_id`가 set에 있으면: `is_public` 값과 무관하게 통과 (같은 팀이니 비공개도 봄 — 기존 의도). admin 무필터.
   - `team_id IS NULL`이면: `user`가 있고 `row["created_by"] == user["name"]`일 때만 통과. (작성자 본인 한정 — todo line 136)
   - `team_id`가 set에 없고 NULL도 아니면: `is_public=1`인 경우만 통과? — **주의**: `_filter_events_by_visibility`는 내부 API(/api/events 등 로그인 화면)용. 공개 포털은 #13 별도. 현재 코드는 `is_public=1`이면 비로그인도 통과시킴 — 이건 캘린더가 공개 일정도 보여주는 동작. **#10 범위에서 이 동작을 깨지 말 것**: `is_public=1`은 팀 무관 통과 유지 (단 히든 프로젝트 차단은 그대로). 비로그인(`user=None`)이면 `is_public=1`만.
   - 정리: `if admin: 통과 / elif team in scope: 통과 / elif team is None: (user and created_by==name) ? 통과 : skip / elif is_public==1: 통과 / else: skip`. `user=None`이면 `is_public==1`만.
3. 호출부 audit — 각 호출에 맞는 scope를 넘긴다:
   - `/api/events` (캘린더, line ~1910): 현재 작업 팀 → `team_id: int = None` 쿼리 파라미터 추가, `tid = resolve_work_team(request, user, team_id)`, `scope = {tid} if tid else set()` (비admin & tid None이면 빈 셋 → 아무 팀 자료 안 보임. 단 대표 팀 fallback이 거의 항상 채워줌). admin이고 tid None이면 `scope=None`.
   - `/api/events/by-project-range` (line ~1988): project.html 간트용. 현재 작업 팀. 동일 패턴 (`team_id` 파라미터 추가).
   - `/api/events/search-parent` (line ~1995): 하위 업무 오토컴플릿. 현재 작업 팀. SQL where에 `team_id` 조건 추가하거나 결과를 `_filter_events_by_visibility`로 후처리. **현재 팀 경계 없음 → 회귀 위험**. 작업 팀 scope 적용.
   - `/api/events/{id}/subtasks` (line ~2025): 부모 일정과 같은 팀. 현재 작업 팀 scope.
   - `/api/events/{id}` (line ~2033): 상세. 현재 작업 팀 scope (단 NULL row 작성자 본인 통과는 위 규칙대로).
   - "내 스케줄" 계열 (line ~4543, ~4607 — `_filter_events_by_visibility` 호출): `scope = user_team_ids(user)` (현재 작업 팀 종속 X). 이게 todo의 "모든 소속 팀 통합 라우트".
4. `_filter_visible_events` (line ~777 doc_detail_page에서 사용 — 이름 다름, 별개 함수일 수 있음): 확인 후 동일 정책 적용 (문서 상세에 딸린 events. 그 문서가 보이는 사람이면 그 팀이니 작업 팀 = 문서 팀으로 scope).

## C. checklists 가시성 (database.py `get_checklists` + app.py `/api/checklists` + `permissions._can_read_checklist`)
- `get_checklists(project, viewer, ...)`: 현재 `viewer`로 히든만 거른다. **팀 경계 없음 → 회귀 위험**. `work_team_ids` 인자 추가 (set | None). admin/None이면 무필터, set이면 `team_id IN scope OR (team_id IS NULL AND created_by == viewer_name)` 필터. `is_public=1` checklists는? checklists엔 공개 포털 노출 개념이 약함 — 기존 `_can_read_checklist`는 로그인 사용자면 (히든 아닌 한) 다 통과. **#10 범위**: 로그인 사용자에게 작업 팀 + NULL(본인) checklists만. `is_public`은 비로그인 공개 포털(#13)에서 다룸. 비로그인(`viewer=None`)이면 기존 `_can_read_checklist` 동작 유지 (히든·private 제외).
- `/api/checklists` (line ~1005): `team_id` 쿼리 파라미터 추가 → `resolve_work_team` → `get_checklists(..., work_team_ids={tid} or set())`.
- `_can_read_checklist(user, cl)` (permissions.py): 단건 상세·MCP에서 호출. **작업 팀 인자 없음** — 시그니처에 `work_team_ids=None` 추가하거나, 호출부에서 작업 팀을 알 수 없는 경우(MCP 등)는 `user_team_ids(user)`로 fallback. 규칙: admin → True / 히든 프로젝트면 멤버십 / `cl.team_id`가 작업팀/소속팀 집합에 있으면 True / `cl.team_id IS NULL`이면 `cl.created_by == user["name"]`만 / 비로그인은 기존 동작. **추방 시 본인도 안 보임** (todo line 288: 체크는 작업 팀 컨텍스트 의존) — 그래서 NULL이 아닌 이상 작성자 본인이라도 그 팀 소속 아니면 못 봄. NULL row만 작성자 본인 예외.
- `_can_read_checklist` 호출부 (app.py line ~994, 1044, 1214, 1305 등): 작업 팀을 알 수 있으면 넘기고, 아니면 `user_team_ids` fallback (= 소속 팀 어디든이면 OK라는 약한 정책. 엄밀히는 작업 팀이어야 하지만 #15 쿠키 도입 전이므로 허용). **reviewer 판단 필요**.

## D. meetings 가시성 (database.py `get_all_meetings` / `get_all_meetings_summary` + app.py `/api/doc`, `/doc` 페이지 + `permissions._can_read_doc`)
- `_can_read_doc(user, doc, work_team_ids=None)`로 시그니처 확장:
  - admin → True / `is_public=1` → True (공개 문서) / `created_by == user["id"]` → True (작성자 본인 항상) / 비로그인이고 위 아니면 False
  - `is_team_doc=1`: `doc.team_id`가 작업팀/소속팀 집합에 있으면 True (NULL이면? 작성자 백필 실패 NULL 팀 문서 — 작성자 본인만 위에서 통과, 다른 사람은 False)
  - `is_team_doc=0`: `team_share=1` AND `doc.team_id`가 집합에 있으면 읽기 True. `team_id IS NULL`이면 작성자 본인만(위에서 처리됨), 다른 사람 False.
  - `work_team_ids=None` & 비admin: `user_team_ids(user)` fallback.
- `get_all_meetings(viewer, work_team_ids=None)`: SQL/후처리로 위 규칙 반영. 가장 안전: 기존처럼 전부 가져와 `_can_read_doc`로 후처리 (메뉴 규모 작음). 단 작업 팀 인자 전달.
- `/api/doc` (line ~3948), `/doc` 페이지 (line ~752): `team_id` 쿼리 파라미터 추가 → `resolve_work_team` → 전달.
- `/doc/{id}` 상세 (line ~771), `/api/doc/{id}/...` 등: `_can_read_doc`에 작업 팀 또는 `user_team_ids` fallback 전달.

## E. projects 가시성 (database.py `get_unified_project_list` / `get_all_projects_with_events` / `get_all_projects_meta` / `get_active_projects` 등 + app.py `/api/projects`, `/api/projects-meta`, `/api/manage/projects/...`)
- 현재 이들은 `viewer`로 히든만 거른다. **NULL이 아닌 다른 팀 프로젝트 노출 위험 + NULL 잔존 projects 노출 위험** (todo line 149).
- 규칙 (todo line 149): `team_id IS NULL` 프로젝트는 `owner_id` 본인 + admin만. 그 외엔 `team_id`가 작업팀/소속팀 집합에 있으면. 히든은 기존 `project_members` 멤버십 추가 검사 유지.
- `/api/projects` (line ~2550), `/api/projects-meta` (line ~2559): `team_id` 쿼리 파라미터 → `resolve_work_team` → DB 함수에 `work_team_ids` 전달.
- `/api/manage/projects` (line ~2612) 및 상세/검색: 동일.
- DB 함수들(`get_unified_project_list`, `get_all_projects_with_events`, `get_all_projects_meta`, 그리고 `get_kanban_events`/`get_project_timeline`도 프로젝트 메타를 만지니 확인): `work_team_ids` 인자(set | None) 추가. None/admin이면 무필터. set이면 `team_id IN scope OR (team_id IS NULL AND owner_id == viewer_id)`. 비로그인은 기존(히든·private 제외)에 더해 NULL 팀 프로젝트 제외 추가.
- `db.get_project(name)` / `db.get_project_by_name(name)` — 이름 기반 단건 조회는 `can_edit_*` 헬퍼와 hidden 체크에서 쓰임. **이름은 (team_id, name_norm) UNIQUE이므로 팀 간 동명 충돌 가능** — 하지만 #10 범위에서 이걸 다 고치는 건 과하고 회귀 위험 큼. 일단 그대로 두되, `can_edit_project`는 이미 `project.team_id`로 판단하니 권한은 안전. reviewer가 위험 표시만.

## F. /api/kanban (이미 SQL 팀 필터 있음 — 회귀 방지만)
- `get_kanban_events(team_id, viewer)` (database.py line ~2955): 이미 `WHERE e.team_id = ?` 필터링. `team_id=None`이면? 확인 — 비admin이 team_id 없이 호출하면 전체가 나오면 안 됨. **`team_id=None`이고 비admin이면 `resolve_work_team`으로 대표 팀을 채워야 함.** `/api/kanban` 라우트 (line ~2274)에서 `team_id = resolve_work_team(request, viewer, team_id)` 적용. admin이고 None이면 기존 동작(전체) 유지.
- 칸반의 NULL team events는? `WHERE e.team_id = ?`에 NULL은 안 걸림 → 칸반에 안 보임. 정상 (칸반은 작업 팀 의존). 작성자 본인 NULL row 노출은 칸반에선 불필요 (todo는 캘린더/목록 중심).

## G. MCP (`mcp_server.py`) — `list_events` / `list_checklists` / `list_projects` / `search_events` 등
- 이들은 `_mcp_user`로 사용자를 얻고 `db.*_mcp(..., viewer=user)`를 호출. **MCP엔 작업 팀 컨텍스트(쿠키)가 없음** → `user_team_ids(user)`를 작업 팀 집합으로 사용 (= 소속 모든 팀 통합. todo line 309에서 MCP `list_*`/`search_*`도 NULL row 노출 안 되는지 검증 요구하므로 최소한 NULL·다른팀 차단은 필수).
- `db.get_all_events`(MCP `list_events`가 쓰는 함수 확인 — line ~112 mcp_server), `db.search_events_mcp`, checklists/projects MCP 함수: `viewer` 외에 `work_team_ids` 인자 추가하거나, MCP 호출 직전에 `_filter_events_by_visibility(rows, user, user_team_ids(user))` 후처리. 후자가 변경 최소.
- `get_event_detail`/`get_checklist_detail` MCP 단건: `_can_read_*`에 `user_team_ids` fallback 전달.

## H. 쓰기(create) 경로 — team_id 부여
- `create_checklist` (line ~1012): `team_id=user.get("team_id")` → `resolve_work_team(request, user)`로 교체 권장 (대표 팀). 단 팀 미배정 사용자(#12)는 NULL — `resolve_work_team`이 None 반환하면 NULL 저장. **이건 #12 책임이긴 하나 `user.get("team_id")` legacy를 쓰면 다중팀에서 틀림**. 최소 변경: `resolve_work_team(request, user)` 사용.
- `create_doc` (line ~3954): `user.get("team_id")` → `resolve_work_team(request, user)`.
- 이벤트 create (line ~2056): 확인 후 동일.
- _sse_publish의 `team_id=user.get("team_id")` 같은 부분: SSE 페이로드는 클라이언트 필터링용이라 정확도 낮아도 치명적 아님. 가능하면 작업 팀으로. reviewer 판단.

# 주의사항 / 가드레일
- **#10 범위만**. `work_team_id` 쿠키 발급/Set-Cookie/검증, "팀 변경" UI, 화면별 팀 드롭다운 제거 = #15. 하지 말 것. 라우트가 `team_id` 쿼리 파라미터를 받을 수 있게만 하고, 없으면 `resolve_work_team` fallback.
- **일정·체크·프로젝트 작성 폼에 "내 일정/팀 일정" 토글 추가 금지** (개인/팀 구분은 문서에만).
- **순환 import 주의**: `database.py`는 `permissions.py`/`auth.py`를 import하지 않음 (역방향만). DB 함수에 `work_team_ids` set을 인자로 받게 하고 권한 헬퍼 호출은 app.py/mcp_server.py 쪽에서.
- **회귀 방지가 최우선**: 기존 단일팀 동작(team_id NULL fallback, 대표 팀 fallback)이 깨지면 안 됨. `resolve_work_team`이 거의 항상 대표 팀을 채워주므로 단일팀 사용자는 영향 없어야 함.
- 마이그레이션 phase 추가는 **불필요** (스키마 변경 없음, 쿼리/라우트만). phase 추가하지 말 것. 서버 재시작도 코드 reload 차원에서만 필요(스키마 무관).
- 변경 후 `python -c "import app"` import-time 검증 필수 (라우트 데코레이터·시그니처 깨짐 조기 발견).
- 작업 완료 후 `.claude/workspaces/current/backend_changes.md`에 변경 파일·함수·시그니처 변경·새 헬퍼 목록 기록.
