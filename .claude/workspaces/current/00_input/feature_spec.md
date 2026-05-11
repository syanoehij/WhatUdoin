# 요청
팀 기능 #10에서 놓친 가시성 누수 수정 — `/api/project-timeline`(간트 데이터 소스)이 비로그인(`viewer=None`) 시 필터 없이 전 팀 프로젝트·일정을 노출. 작업 중 발견되는 에러도 같은 흐름에서 수정. 추가로 비로그인 관점 GET 라우트 일괄 점검.

# 분류
백엔드 수정 / 백엔드 모드 (backend → reviewer → qa)

# 배경 / 진단 (메인 + 플래너 확인 완료)
- `app.py:2631` `/api/project-timeline`:
  ```python
  viewer = auth.get_current_user(request)
  proj_colors = db.get_project_colors()
  if viewer and not auth.is_admin(viewer):
      scope = _work_scope(request, viewer, team_id)
      if not scope: return []
      team_id = next(iter(scope))
      teams = db.get_project_timeline(team_id, viewer=viewer)
  else:
      teams = db.get_project_timeline(team_id, viewer=viewer)
  ```
  → `viewer=None`이면 `if viewer and ...` 가 False → `else` 분기 → `team_id=None`·`viewer=None` 으로 `db.get_project_timeline` 호출 → DB 함수 `is_scoped=False` → 무scope 전체 조회 (전 팀 public 일정 노출). 비로그인이 다른 팀 자료를 보게 됨. **admin(`else` 무필터)은 의도된 동작 — 유지.**
- 올바른 패턴 = `/api/kanban` (`app.py:2320`):
  ```python
  viewer = auth.get_current_user(request)
  if not auth.is_admin(viewer):
      scope = _work_scope(request, viewer, team_id)
      if not scope: return []
      team_id = next(iter(scope))
  return db.get_kanban_events(team_id, viewer=viewer)
  ```
  비로그인 → `is_admin(None)=False` → `_work_scope` 가 빈 set → `[]`.
- `_work_scope` (app.py:1923): admin → `None` 반환. 비admin(비로그인 포함) → resolve 결과 1개 set, 결정 불가/팀 미배정/비소속 → 빈 set.
- `_filter_events_by_visibility` (app.py:1878): admin → 전체 통과. `scope_team_ids=None` & 비admin → `user_team_ids(user)` fallback (비로그인은 빈 set). 빈 set → team_id ∈ scope 통과 없음, NULL team 은 작성자 본인만, is_public==1 통과.

# backend-dev 담당 작업

## 1. `/api/project-timeline` 누수 패치 (필수)
`/api/kanban` 과 동일 골격으로 교체:
```python
@app.get("/api/project-timeline")
def project_timeline(request: Request, team_id: int = None):
    viewer = auth.get_current_user(request)
    if not auth.is_admin(viewer):
        scope = _work_scope(request, viewer, team_id)
        if not scope:
            return []
        team_id = next(iter(scope))
    proj_colors = db.get_project_colors()
    teams = db.get_project_timeline(team_id, viewer=viewer)
    for team in teams:
        for project in team.get("projects", []):
            project["color"] = resolve_project_color(project.get("name"), proj_colors)
    return teams
```
- admin: `is_admin` True → scope 산출 안 함 → `team_id` 그대로(보통 None) → `db.get_project_timeline(None, viewer=admin)` 무필터. 기존 동작 유지.
- 로그인 비admin: `_work_scope` → 작업 팀 1개 set → 그 팀만. 빈 set(팀 미배정) → `[]`.
- 비로그인: `is_admin(None)=False` → `_work_scope(request, None, team_id)` → 빈 set → `[]`. (이전엔 전 팀 public 일정 노출.)

### 비로그인 기대 출력 분류표 (qa 오탐 방지 — 이 분류 기준으로 점검·검증)
| 라우트 군 | 비로그인 기대 |
|---|---|
| `/api/kanban`, `/api/project-timeline` (작업 팀 스코프) | `[]` |
| `/api/events`, `/api/events/by-project-range`, `/api/checklists`, `/api/projects`, `/api/projects-meta`, `/api/doc`, `/api/doc/calendar` | `is_public=1`(+ private/hidden 제외) row 만, 팀 무관 — **회귀 확인 대상, 누수 아님** |
| `/api/conflicts`, `/api/project-list`, `/api/manage/*`, `/api/events/search-parent` | `_require_editor` → 비로그인 401/403 — 가드 존재 확인만 |
| `/api/my-meetings`, `/api/project-milestones/calendar` | `not user` 단락(403 또는 `[]`) — 인벤토리 완결성 위해 확인만 |

## 2. 비로그인(`viewer=None`) 관점 GET 라우트 일괄 점검 (필수)
아래 라우트들이 비로그인 시 누수가 없는지 코드를 직접 확인하고, `/api/project-timeline` 과 같은 종류의 누락(비로그인이라 viewer가 falsy → 가드 통과 → 무필터 조회)이 더 있으면 동일 패턴으로 수정. 점검 결과(누수 있음/없음·근거)를 backend_changes.md 에 기록.
- `/api/events` (1954), `/api/events/by-project-range` (2032), `/api/events/search-parent` (2039), `/api/events/{id}/subtasks` (2070), `/api/events/{id}` (2078)
- `/api/checklists` (995), `/api/checklists/{id}` (1031), `/api/checklists/{id}/histories` (1201)
- `/api/projects` (2603), `/api/projects-meta` (2612), `/api/project-list` (2651), `/api/manage/projects` (2672)
- `/api/kanban` (2320), `/api/project-timeline` (2631), `/api/conflicts` (2592)
- `/api/doc` (4008), `/api/doc/calendar` (4166), `/api/doc/{id}/events` (4458)
- 기준: admin 무필터·작성자 본인 NULL row 노출·is_public 일정 노출은 **의도된 동작 — 건드리지 말 것**. 누수 판정은 "비로그인이 다른 팀의 비공개/팀 전용 자료(team_id 있는 row, is_private 프로젝트)를 보게 되는가".
- 참고: 대부분의 라우트는 DB 함수가 `viewer is None`일 때 `public_filter`/`private_clause` 를 적용하므로 DB 레이어에서 막힘. `db.get_project_timeline`은 그 패턴이 불완전(전 팀 public 노출)이라 라우트에서 막아야 함 — 이게 이번 핵심.

## 3. 작업 중 발생 에러
backend·qa 작업 중 import 오류·런타임 에러 등 발견 시 같은 흐름에서 수정.

# 변경 대상 파일
- `app.py` (`/api/project-timeline` 및 점검 결과 누수 발견 시 해당 라우트)
- (필요 시) `database.py` — 단, 가급적 라우트 레이어에서 막는 쪽 선호 (간트 by-project-range name 기반 조회 전환 같은 별개 이슈는 건드리지 말 것)

# 주의사항
- 스키마 변경 없음 (migration 불필요). 코드 변경이므로 끝나면 서버 재시작 필요.
- 공개 포털(`/팀이름`)·`/` 비로그인 화면은 #13/#11 책임 — 새로 만들지 말 것. 기존 내부 API 라우트의 누수만 막는다.
- 회귀 방지 확인: 로그인 사용자(소속 팀 1개)·다중 팀 사용자(작업 팀 전환)·admin(무필터)·팀 미배정 로그인 사용자(빈 set→[])·비로그인(빈 set→[]) 동작이 #10 명세대로.
- 본 사이클은 이 누수 패치 범위만. #11/#13 화면 작업·간트 by-project-range name 기반 전환 등은 범위 밖.
