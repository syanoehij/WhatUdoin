# 백엔드 변경 사항

## 히든 프로젝트 A단계 구현 (2026-05-08)

### 수정 파일

- `database.py`
- `app.py`

---

## database.py 변경 사항

### 1. DB 스키마 마이그레이션 (`init_db`)

**projects 테이블 신규 컬럼** (`_migrate` 패턴, line ~248):
```sql
ALTER TABLE projects ADD COLUMN is_hidden INTEGER DEFAULT 0;
ALTER TABLE projects ADD COLUMN owner_id  INTEGER;
```
- 기존 데이터에 영향 없음 (DEFAULT 0 / NULL)

**project_members 테이블 신규 생성** (`CREATE TABLE IF NOT EXISTS`, line ~252):
```sql
CREATE TABLE IF NOT EXISTS project_members (
    project_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    added_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, user_id)
)
```

### 2. 이름 중복 검사 (`project_name_exists`)

기존 함수의 `projects` 테이블 조회는 이미 `deleted_at` 필터 없이 전체 검사 중 (변경 없음).
히든 프로젝트 생성 함수 내에서도 동일하게 `WHERE LOWER(name) = LOWER(?)` 로 휴지통 포함 전체 검사.

### 3. 신규 함수

#### `create_hidden_project(name, color, memo, owner_id) -> dict | None`
- 이름 중복(휴지통 포함) 시 `None` 반환
- `projects` 테이블에 `is_hidden=1, owner_id=owner_id`로 INSERT
- `project_members` 테이블에 `(project_id, user_id=owner_id)` INSERT (owner도 멤버에 포함)

#### `get_hidden_project_member_ids(project_id) -> list[int]`
- `project_members` 테이블에서 해당 프로젝트의 `user_id` 목록 반환

#### `is_hidden_project_visible(project_id, user) -> bool`
- `admin` → True
- `project_members`에 user_id 존재 → True
- 그 외 → False

#### `get_project_by_name(name) -> dict | None`
- 삭제 여부 무관하게 이름으로 프로젝트 조회 (can-manage 엔드포인트용)

### 4. 가시성 필터 수정 (viewer 파라미터 추가)

다음 함수들에 `viewer=None` 파라미터 추가:
- `get_unified_project_list(active_only, viewer=None)`
- `get_all_projects_with_events(viewer=None)`
- `get_all_projects_meta(viewer=None)`
- `get_project_timeline(team_id, viewer=None)` — 기존 viewer 파라미터 활용하여 히든 필터 추가

**필터 로직:**
- `viewer=None` (비로그인): is_hidden=1 프로젝트 전부 제외
- `viewer={"role": "admin"}`: 모든 히든 프로젝트 포함
- 그 외 viewer: `project_members`에 user_id가 있는 히든 프로젝트만 포함

**N+1 방지:** `project_members`를 한 번에 pre-fetch하여 Python에서 set 조회.

**orphan 재누출 방지:** `hidden_blocked` 집합으로 차단된 히든 프로젝트 이름을 추적하여
events/checklists 이름으로부터 orphan 추가 및 이벤트 분류에서 재노출 차단.

---

## app.py 변경 사항

### 1. 기존 API 수정 — viewer 파라미터 전달

| 위치 | 변경 내용 |
|------|----------|
| `GET /check` | `get_all_projects_meta(viewer=user)` |
| `GET /check/new/edit` | `get_all_projects_with_events(viewer=user)` |
| `GET /check/{id}/edit` | `get_all_projects_with_events(viewer=user)` |
| `GET /api/projects` | `get_unified_project_list(viewer=user)` |
| `GET /api/project-list` | `get_unified_project_list(viewer=user)` |
| `GET /api/manage/projects` | `get_all_projects_with_events(viewer=user)` |
| `GET /api/project-colors` | `get_unified_project_list(active_only=False, viewer=user)` |
| `GET /api/project-timeline` | 기존 viewer 전달 유지 (이미 viewer 파라미터 있음) |

### 2. 신규 엔드포인트

#### `POST /api/manage/hidden-projects`
- 요청: `{ name: str, color: str, memo: str }`
- 권한: 로그인 필수 + team_id 필수 (팀 없으면 403)
- 중복 시: 422 "생성할 수 없습니다. 다른 이름을 넣어주세요." (이유 불문, 누설 방지)
- 응답: `{ id, name, color, memo, is_hidden: 1, owner_id }`

#### `GET /api/manage/hidden-projects/{name}/can-manage`
- 권한: 로그인 필수
- 히든 프로젝트 아닌 경우: 404
- 응답: `{ can_manage: bool, is_owner: bool, is_admin: bool }`

---

## 마이그레이션 안전성

- `ALTER TABLE ADD COLUMN` → `_migrate` 패턴 사용 (기존 컬럼 중복 시 자동 스킵)
- `CREATE TABLE IF NOT EXISTS` → 재실행 안전
- 기존 데이터: `is_hidden=0` (DEFAULT), `owner_id=NULL` 으로 하위호환 유지
- 데이터 손실 없음

---

## 미구현/범위 외 (B단계 이후)

- 멤버 관리 API (`GET/POST/DELETE /api/manage/hidden-projects/{name}/members`)
- 권한 이양 API
- 히든 항목 is_public 변경 차단
- MCP 용 `get_projects_for_mcp` 히든 필터 (현재 시스템 컨텍스트로 viewer=None 사용 중)

---

## BLOCK 수정 (2026-05-08)

코드 리뷰 차단 결함 3건 수정 (`app.py`).

### B1: CSRF 보호 누락 수정 (app.py:2226-2227)

`POST /api/manage/hidden-projects`에서 `auth.get_current_user(request)` + 수동 401 체크를 `_require_editor(request)` 단일 호출로 교체. CSRF 검증 및 editor 이상 역할 체크가 동시에 적용됨. 기존 `team_id` 체크는 그대로 유지.

### B2: SSE 페이로드 히든 프로젝트 이름 누설 제거 (app.py:2242)

`wu_broker.publish("projects.changed", {"name": name, "action": "create"})` 에서 `name` 값을 `None`으로 변경. 비로그인 SSE 구독자에게 히든 프로젝트 이름이 브로드캐스트되지 않음.

### B3: can-manage 엔드포인트 enumeration 차단 (app.py:2254-2255)

`GET /api/manage/hidden-projects/{name}/can-manage`에서 프로젝트 존재 확인 후 `db.is_hidden_project_visible` 체크를 추가. 비멤버가 임의의 이름으로 호출 시 프로젝트 존재 여부와 무관하게 일관된 404 반환.

---

## B단계 변경 (2026-05-08)

### 신규 DB 함수 (database.py)

| 함수 | 위치 | 설명 |
|------|------|------|
| `get_hidden_project_members(project_id)` | line ~1694 | 멤버 목록 (owner 포함, is_owner 플래그 포함) |
| `get_hidden_project_addable_members(project_id)` | line ~1710 | 추가 가능 사용자 목록 (owner 팀 기준, 현재 비멤버) |
| `add_hidden_project_member(project_id, user_id, owner_id)` | line ~1734 | 멤버 추가. 반환: True(성공) / False(팀 불일치) / None(이미 멤버) |
| `remove_hidden_project_member(project_id, user_id)` | line ~1762 | 멤버 삭제. owner 삭제 시도 시 False 반환 |
| `transfer_hidden_project_owner(project_id, new_owner_id, requester_id)` | line ~1779 | owner 권한 이양. requester != owner이면 False |
| `admin_change_hidden_project_owner(project_id, new_owner_id)` | line ~1803 | admin 강제 owner 변경. 비멤버 대상이면 False |

### 신규 엔드포인트 (app.py)

| 메서드 | URL | 권한 | 설명 |
|--------|-----|------|------|
| `GET` | `/api/manage/hidden-projects/{name}/members` | owner 또는 admin | 멤버 목록. 응답: `{ members: [{id, name, team_id, is_owner}] }` |
| `GET` | `/api/manage/hidden-projects/{name}/addable-members` | owner 또는 admin | 추가 가능 사용자. 응답: `{ addable_members: [{id, name}] }` |
| `POST` | `/api/manage/hidden-projects/{name}/members` | owner 또는 admin | 멤버 추가. body: `{ user_id }`. 팀 불일치 → 403, 이미 멤버 → 409 |
| `DELETE` | `/api/manage/hidden-projects/{name}/members/{user_id}` | owner 또는 admin | 멤버 삭제. owner 삭제 시도 → 403 |
| `POST` | `/api/manage/hidden-projects/{name}/transfer-owner` | owner만 (admin 불가) | 권한 이양. body: `{ user_id }`. 비멤버 대상 → 400 |
| `POST` | `/api/manage/hidden-projects/{name}/change-owner` | admin만 | 강제 owner 변경. body: `{ user_id }`. 비멤버 대상 → 400 |

**공통 주의사항:**
- 모든 B단계 엔드포인트는 `_get_hidden_proj_or_404(name, user)` 경유 — 비멤버에게 프로젝트 존재 여부 누설 없음 (일관 404 반환)
- `wu_broker.publish("projects.changed", {"name": None, "action": "..."})` — 이름 누설 방지 패턴 유지

### 산하 항목 외부 공개 락 적용 위치

| 위치 | 처리 내용 |
|------|----------|
| `PATCH /api/checklists/{id}/visibility` (app.py:~876) | is_public=1 시도 시 해당 체크리스트의 project가 is_hidden이면 403 |
| `PATCH /api/events/{id}/visibility` (app.py:~3176) | is_public=1 시도 시 해당 이벤트의 project가 is_hidden이면 403 |
| `PATCH /api/events/{id}/project` (app.py:~1831) | 이동 대상 project가 is_hidden이면 is_public 강제 0, 응답에 `hidden_forced: true` 포함 |
| `PATCH /api/checklists/{id}` (app.py:~920) | project 변경 시 이동 대상이 is_hidden이면 is_public 강제 0 |

### 범위 외 / 미구현 (의도적)

- **meetings(문서) visibility 락**: meetings 테이블에 `project` 컬럼 없음 → 적용 불가. `PATCH /api/doc/{id}/visibility`는 락 미적용.
- **C단계**: 이동 확인 모달, 히든 휴지통 분리, 팀원 제외 경고 (다음 단계)

### BLOCK 수정 (2026-05-08) — B-B1: Bulk visibility 히든 락

코드 리뷰 차단 결함 B-B1 수정 (`app.py:847-850`, `865-868`).

**문제:** `PATCH /api/checklists/bulk-visibility`와 `PATCH /api/events/bulk-visibility`에 히든 프로젝트 검사가 없어, per-item 락을 우회하여 is_public 일괄 변경이 가능했음.

**수정 내용:** 두 라우트 모두 `is_public`이 1(공개)이고 `project`가 지정된 경우, `db.get_project_by_name(project)`로 해당 프로젝트의 `is_hidden` 여부를 확인한다. 히든 프로젝트이면 HTTP 403 "히든 프로젝트 항목은 외부 공개할 수 없습니다." 반환.

**적용 조건:** `is_public && project` 둘 다 truthy인 경우에만 체크 (공개→비공개 전환, 프로젝트 미지정 bulk는 기존 동작 유지).

---

## C단계 변경 (2026-05-08)

### C-1. 히든→일반 이동 확인값 요구

**수정 파일:** `app.py`

**수정 라우트:**

| 라우트 | 추가 로직 |
|--------|----------|
| `PATCH /api/events/{event_id}/project` (app.py ~1866) | 이동 전 project가 히든이고 이동 후가 히든이 아닌 경우, request body에 `confirm: true` 없으면 400 반환 |
| `PATCH /api/checklists/{checklist_id}` (app.py ~903) | `project` 필드가 request body에 있고 실제 변경되는 경우에만 위 동일 로직 적용 |

**요청/응답:**
- 확인 없이 이동 시 → `400 { requires_confirm: true, message: "히든 프로젝트 밖으로 이동합니다. 계속하시겠습니까?" }`
- `confirm: true` 포함 재요청 시 → 이동 허용 (기존 응답 형식 유지)

**범위 외:**
- `PATCH /api/meetings/{id}/project` — meetings 테이블에 `project` 컬럼 없음. 해당 없음 (B단계와 동일 사유).
- `JSONResponse`를 app.py import에 추가 (`from fastapi.responses`).

---

### C-2. 히든 프로젝트 휴지통 분리

**수정 파일:** `app.py`, `database.py`

**database.py 변경:**

| 함수 | 변경 내용 |
|------|----------|
| `get_trash_items(team_id, viewer=None)` | `viewer` 파라미터 추가. `is_hidden=1` 프로젝트는 `viewer.role=='admin'` 또는 `viewer.id==owner_id`인 경우만 groups에 포함. SELECT에 `is_hidden, owner_id` 컬럼 추가. |
| `get_trash_hidden_project(project_id)` | 신규 함수. 휴지통 프로젝트의 `is_hidden, owner_id` 반환 (복원 권한 검사용). |

**app.py 변경:**

| 라우트 | 변경 내용 |
|--------|----------|
| `GET /api/trash` | `db.get_trash_items(team_id, viewer=user)` — viewer 전달 |
| `POST /api/trash/{item_type}/{item_id}/restore` | `item_type=="project"` 시 `db.get_trash_hidden_project(item_id)`로 히든 여부 확인. 히든이면 admin 또는 owner만 복원 허용. 비권한자 → 403 |

**범위 외:**
- 수동 영구 삭제 API 없음. 자동 스케줄러(`cleanup_old_trash`, 매일 03:20 실행)가 90일 초과 항목을 삭제하며, 이 경로에는 별도 권한 검사를 추가하지 않음(스케줄러이므로 사용자 접근 없음).

---

### C-3. 팀원 제외 시 히든 프로젝트 owner 경고

**수정 파일:** `app.py`, `database.py`

**주의:** spec에 명시된 `DELETE /api/manage/teams/{team_id}/members/{user_id}` 엔드포인트는 존재하지 않음. 실제 팀원 제외는 `PUT /api/admin/users/{user_id}` (admin 전용)를 통해 처리됨.

**database.py 신규 함수:**

| 함수 | 설명 |
|------|------|
| `get_user_owned_hidden_projects(user_id)` | 해당 user가 owner인 활성 히든 프로젝트 목록(`id, name`) 반환 |
| `transfer_hidden_projects_on_removal(user_id, hidden_projects)` | 강제 제외 시 각 프로젝트의 owner를 `project_members` 기준 added_at 가장 빠른 다른 멤버로 이양. 이양 불가 시 `owner_id=NULL` |

**app.py 수정:** `PUT /api/admin/users/{user_id}` (admin_update_user)

**트리거 조건:** `old_team_id IS NOT NULL` AND (`new_team_id != old_team_id` OR `is_active=0`)
- 팀 변경(다른 팀으로 이동 포함) 또는 비활성화 시 모두 경고. 어느 경우든 해당 사용자는 기존 팀의 히든 프로젝트 접근을 잃음.

**요청/응답:**

```
# 1단계: force 없이 호출 (팀 제외/비활성화 시도)
PUT /api/admin/users/{user_id}
Body: { team_id: null, is_active: 0 }

→ 200 {
    warning: true,
    hidden_projects: ["프로젝트A", "프로젝트B"],
    message: "해당 사용자는 히든 프로젝트 관리자입니다. 계속 진행하면 관리 권한이 이양됩니다."
  }  (DB 변경 없음)

# 2단계: force: true 포함 재요청
Body: { team_id: null, is_active: 0, force: true }

→ 200 { ok: true }  (owner 이양 후 팀 제외/비활성화 실행)
```
