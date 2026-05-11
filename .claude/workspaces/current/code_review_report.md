## 코드 리뷰 보고서 — 팀 기능 #10 가시성 누수 패치 (`/api/project-timeline`)

### 리뷰 대상 파일
- `app.py` (라인 2631-2647, `/api/project-timeline` 라우트 본문)
- `database.py` — 변경 없음

### 차단(Blocking) ❌
- 없음.

### 경고(Warning) ⚠️
- 없음. (참고: `/api/project-timeline` 의 admin 무필터 시 `team_id=None` 으로 `db.get_project_timeline` 호출 → 전 팀 데이터 반환은 기존·의도된 동작이라 유지. 간트 by-project-range 의 name 기반 조회 전환은 별개 알려진 한계로 본 사이클 범위 밖.)

### 통과 ✅
- [x] **권한 체크**: `if not auth.is_admin(viewer)` 가드 → 비admin(비로그인 포함) `_work_scope` 산출 → 빈 set 이면 `[]`. `/api/kanban` (app.py:2320) 과 동일 골격 — 검증된 패턴 재사용.
- [x] **None-안전성**: 비로그인 경로(`viewer=None`) 가 `_work_scope` → `resolve_work_team(request, None)` → `user_team_ids(None)` 까지 모두 None-가드 보유 (auth.py 확인). 크래시 없음, `set()` → `[]` 반환.
- [x] **회귀 영향 없음**: admin 분기는 `is_admin` True → 가드 통과 안 함 → `team_id` 그대로(보통 None) → `db.get_project_timeline(None, viewer=admin)` 무필터, 기존 동작 그대로. 로그인 비admin 은 기존 `if viewer and not auth.is_admin(viewer)` 분기와 동등(작업 팀 1개 set).
- [x] **surgical**: `/api/project-timeline` 라우트 본문 13줄만 변경. `proj_colors = db.get_project_colors()` 를 early-return 이후로 이동(불필요 DB hit 제거, `/api/kanban` 대칭). 인접 코드·포맷 손대지 않음.
- [x] **SQL 파라미터화**: 해당 없음 (raw SQL 추가/변경 없음).
- [x] **DB 경로 / `_ctx` / 템플릿**: 해당 없음 (JSON API, DB·템플릿 변경 없음).
- [x] **스키마 변경**: 없음 → `_migrate` 불필요.
- [x] **비로그인 GET 라우트 일괄 점검** (backend_changes.md §2): events·checklists·projects·doc·kanban·conflicts·meetings·milestones 류 GET 라우트 점검 결과 `/api/project-timeline` 외 추가 누수 없음. 다른 라우트는 (a) `_work_scope` 무조건 호출 → 비로그인 빈 set → public 만, (b) DB 함수가 `viewer is None` 시 public/private 필터 적용, (c) `_can_read_*` 헬퍼가 비로그인 시 public 외 False, (d) `not user` 단락, (e) `_require_editor` 401/403 — 중 하나로 차단됨. 근거 표는 backend_changes.md 참조.

### 최종 판정
- **통과**. QA 진행 가능.
