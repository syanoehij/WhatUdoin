# 백엔드 변경 — 팀 기능 #10 가시성 누수 패치

## 1. `/api/project-timeline` 누수 수정 — app.py:2631

### 이전 (버그)
```python
@app.get("/api/project-timeline")
def project_timeline(request: Request, team_id: int = None):
    viewer = auth.get_current_user(request)
    proj_colors = db.get_project_colors()
    if viewer and not auth.is_admin(viewer):
        scope = _work_scope(request, viewer, team_id)
        if not scope: return []
        team_id = next(iter(scope))
        teams = db.get_project_timeline(team_id, viewer=viewer)
    else:
        teams = db.get_project_timeline(team_id, viewer=viewer)
    ...
```
`viewer=None` → `if viewer and ...` False → else 분기 → `db.get_project_timeline(team_id=None, viewer=None)` → DB 함수 `is_scoped=False` → 무scope 전체 조회 (전 팀 public 일정·프로젝트가 비로그인 누구에게나 노출).

### 이후 (수정)
```python
@app.get("/api/project-timeline")
def project_timeline(request: Request, team_id: int = None):
    viewer = auth.get_current_user(request)
    # 팀 기능 그룹 A #10: 간트는 현재 작업 팀 기준 (/api/kanban 과 동일 골격).
    # 비admin(비로그인 포함)이 작업 팀을 결정할 수 없거나 비소속이면 빈 목록 — 다른 팀 자료 누출 방지.
    # admin 은 무필터(team_id 그대로, 보통 None) — 전 팀 슈퍼유저, 의도된 동작.
    if not auth.is_admin(viewer):
        scope = _work_scope(request, viewer, team_id)
        if not scope:
            return []
        team_id = next(iter(scope))
    proj_colors = db.get_project_colors()
    teams = db.get_project_timeline(team_id, viewer=viewer)
    ...
```
- `/api/kanban` (app.py:2320) 과 동일 골격.
- admin: `is_admin` True → scope 산출 안 함 → `team_id` 그대로(보통 None) → `db.get_project_timeline(None, viewer=admin)` 무필터. **기존 동작 유지.**
- 로그인 비admin: `_work_scope` → 작업 팀 1개 set → 그 팀만. 팀 미배정 → 빈 set → `[]`.
- 비로그인: `is_admin(None)=False` → `_work_scope(request, None, team_id)`:
  - `is_admin(None)` False → explicit_id 가 있어도 `user_can_access_team(None, …)` False → explicit 버려짐 → `resolve_work_team(request, None)`: 쿠키 없음 → admin 아님 → `user_team_ids(None)=set()` → legacy `None` → `None` 반환 → `_work_scope` 가 `set()` 반환 → 라우트가 `[]` 반환. (크래시 없음 — auth.py 전 함수 None-safe 확인.)
- `proj_colors` 는 early-return 이후로 이동 (`/api/kanban` 대칭, 불필요한 DB hit 제거).

## 2. 비로그인(`viewer=None`) GET 라우트 일괄 점검 — 결과: 추가 누수 없음

기준: "비로그인이 다른 팀의 비공개/팀 전용 자료(team_id 있는 row, is_private/is_hidden 프로젝트)를 보게 되는가". admin 무필터·작성자 본인 NULL row·is_public row 노출은 의도된 동작 — 제외.

| 라우트 | 비로그인 처리 | 판정 |
|---|---|---|
| `/api/project-timeline` (2631) | **위에서 수정** — 빈 set → `[]` | 수정 완료 |
| `/api/kanban` (2320) | `if not auth.is_admin(viewer): scope=_work_scope; if not scope: return []` → 비로그인 `[]` | OK (기존) |
| `/api/events` (1954) | `_filter_events_by_visibility(events, None, _work_scope(req,None,team_id))` → scope=빈 set → is_public row 만 통과 | OK (회귀 확인 대상) |
| `/api/events/by-project-range` (2032) | 동일 — `_filter_events_by_visibility(..., None, _work_scope(...))` → is_public 만 | OK |
| `/api/events/search-parent` (2039) | 본문이 conn 직접 raw 쿼리지만 마지막에 `_filter_events_by_visibility(events, None, _work_scope(...))` 통과 → 비로그인 빈 set → is_public row 만, team_id/is_public/created_by 컬럼은 응답 전 pop | OK |
| `/api/events/{id}/subtasks` (2070) | `_filter_events_by_visibility(subtasks, None, _work_scope(...))` → is_public 만 | OK |
| `/api/events/{id}` (2078) | 동일 — 안 보이면 404 | OK |
| `/api/checklists` (995) | `db.get_checklists(viewer=None, work_team_ids=None)` → `public_filter` 적용 (is_public=1 또는 프로젝트 연동, private/hidden 제외) | OK |
| `/api/checklists/{id}` (1031) | `_can_read_checklist(None, item)` — 비로그인은 is_public/프로젝트연동(non-private)만 True, else 404 | OK |
| `/api/checklists/{id}/histories` (1201) | `_can_read_checklist(None, …)` 동일 → 안 되면 403 | OK |
| `/api/projects` (2603) | `db.get_unified_project_list(viewer=None)` 히든 제외 + 라우트가 `is_private` 추가 제외 | OK |
| `/api/projects-meta` (2612) | 동일 + `is_hidden` 추가 제외 | OK |
| `/api/project-list` (2651) | `_require_editor(request)` → 비로그인 401/403 | OK |
| `/api/manage/projects` (2672) | `_require_editor(request)` → 401/403 | OK |
| `/api/conflicts` (2592) | `if not user: return {"conflicts": []}` | OK |
| `/api/doc` (4008) | `db.get_all_meetings(viewer=None, work_team_ids=None)` → SQL `AND m.is_public = 1` | OK |
| `/api/doc/calendar` (4166) | `if user is None: return []` | OK |
| `/api/doc/{id}/events` (4458) | `_can_read_doc(None, doc)` — 비로그인은 is_public 문서만 True, else 404 | OK |
| `/api/my-meetings` (2333) | `if not user: raise 403` | OK |
| `/api/project-milestones/calendar` (2342) | `if not user: return []` | OK |

→ **`/api/project-timeline` 만 유일한 누수였음.** 다른 모든 라우트는 (a) `_work_scope` 무조건 호출 → 비로그인 빈 set → public 만, (b) DB 함수가 `viewer is None`일 때 public/private 필터 적용, (c) `_can_read_*` 헬퍼가 비로그인 시 public 외 False, (d) `not user` 단락, (e) `_require_editor` 401/403 — 중 하나로 막혀 있음.

## 3. 작업 중 에러
없음. `python -c "import ast; ast.parse(...)"` 로 app.py syntax OK 확인.

## 4. 패턴 grep 확인 + 알려진 후속(범위 밖)
- `grep "if .*(viewer|user) and not auth.is_admin" app.py` → `/api/project-timeline` 외 hit 없음. 같은 idiom(비로그인 short-circuit → scope 가드 우회)이 남아 있지 않음 확인.
- `app.py:2970 _filter_visible_events` 의 `if user and auth.is_admin(user): return events` 는 극성 반대(admin 만 무필터, 비로그인은 아래 히든 필터로 떨어짐) — 누수 아님.
- 프론트 참조: `templates/project.html:604` 가 `/api/project-timeline` 호출. `/gantt` 페이지 라우트(`app.py:730 project_page`)에는 `_require_editor` 가드가 없음 → 비로그인도 페이지 자체는 로드 가능. 본 패치 이후 비로그인은 빈 간트를 보게 됨(이전엔 전 팀 public 일정). **이건 의도된 보안 동작이고, 비로그인 화면 UX(로그인 안내 등)는 #11/#13 책임이라 본 사이클에서 `/gantt` 에 로그인 게이트를 추가하지 않음** — 알려진 후속으로만 기록.

## 변경 파일
- `app.py` — `/api/project-timeline` 라우트 본문만 (13줄 영역).
- `database.py` — 변경 없음 (라우트 레이어에서 차단).
- 스키마 변경 없음 → migration 불필요. **코드 변경이므로 끝나면 서버 재시작 필요.**
