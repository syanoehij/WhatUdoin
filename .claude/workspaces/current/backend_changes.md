# Backend 변경 (그룹 D catchup — 비로그인 진입 재설계)

## app.py

### `_ctx()` (line ~668-)
`portal_team` (dict|None), `portal_menu` (dict|None) 컨텍스트 추가.
호출부가 `_ctx(request, ... portal_team=team, portal_menu=menu_vis)` 로 set 하지 않으면 None 기본값.

### 신규 라우트 (line 5518 직전, 정적 라우트 뒤)
4개 개별 라우트 등록:
- `GET /{team_name}/칸반` → `_render_team_menu(request, team_name, "kanban")`
- `GET /{team_name}/간트` → `_render_team_menu(request, team_name, "gantt")`
- `GET /{team_name}/문서` → `_render_team_menu(request, team_name, "doc")`
- `GET /{team_name}/체크` → `_render_team_menu(request, team_name, "check")`

신규 헬퍼 `_render_team_menu(request, team_name, menu_key)`:
- `_TEAM_NAME_RE` + `RESERVED_TEAM_PATHS` 검증 → 실패 시 404 (기존 동작)
- 팀 없으면 404. 삭제 예정 팀은 안내 페이지 (menu_key 명시되면 404).
- `menu_key=None` (기본 `/{team_name}` 진입): `_PORTAL_MENU_ORDER` 순서로 첫 켜진 메뉴를 `active_menu` 로. 없으면 None.
- `menu_key` 명시: 해당 메뉴가 외부공개 ON 이어야 200, 아니면 404.
- 템플릿에 `active_menu`, `portal_team=team`, `portal_menu=menu_vis` 전달.

### `team_public_portal` 수정
기존 함수 본문을 `_render_team_menu(request, team_name, None)` 호출로 위임.
별도 탭 영역 제거 — 단일 active_menu 패널만 렌더.

## database.py
변경 없음.

## 라우트 순서 invariant 유지
- 신규 4개 라우트는 모두 `/{team_name}` 라우트 위(소스상 앞)에 등록.
- 둘 다 정적 라우트(`/`, `/admin`, `/doc`, `/check`, `/kanban`, `/gantt`, ...) 뒤에 위치 (phase82 invariant 유지).
- `_build_reserved_team_paths` 는 `/{team_name}/...` 의 segment 1 인 `{team_name}` (중괄호 포함) 을 skip 하므로 칸반/간트/문서/체크가 RESERVED 에 들어가지 않음.
