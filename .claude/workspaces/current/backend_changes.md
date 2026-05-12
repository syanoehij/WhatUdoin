# #11 백엔드 변경

## database.py
- `get_visible_teams()` 신규 추가: `SELECT * FROM teams WHERE deleted_at IS NULL ORDER BY name`. 공개 화면(`/`)용. `get_all_teams()`는 그대로 유지.

## app.py
- `index()` (`GET /`): 비로그인 시 `RedirectResponse("/kanban")` 분기 **제거**. 비로그인/로그인 모두 `home.html` 렌더. `teams`는 `db.get_visible_teams()` (삭제 예정 팀 제외).

## 범위 밖 (미변경)
- `/kanban`, `/calendar`, `/gantt`, `/doc`, `/admin` 등 다른 라우트의 `get_all_teams()` 호출 그대로.
- `/팀이름` 동적 라우트 없음 (#13). 비로그인 사용자가 팀 카드 클릭 시 현재로선 404 — 의도된 상태.

## API 스펙 (프론트가 알아야 할 것)
- `home.html`에 `teams` 컨텍스트 변수: `[{id, name, deleted_at(=None), ...}]`. 비삭제 팀만.
