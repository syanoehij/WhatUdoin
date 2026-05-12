# #15-3 백엔드 변경 — team_notices 팀별 공지 전환

## database.py

### `get_latest_notice()` → `get_notice_latest_for_team(team_id)` (rename + 팀 필터)
- `SELECT * FROM team_notices WHERE team_id = ? ORDER BY id DESC LIMIT 1`. `team_id is None` → `None`.
- NULL 잔존 row(백필 누락분)는 반환하지 않음 — admin 이력 화면(`get_notice_history(..., include_null=True)`)에서만 노출. docstring 명시.

### `save_notice(content, created_by)` → `save_notice(content, team_id, created_by)`
- `INSERT INTO team_notices (team_id, content, created_by) VALUES (?, ?, ?)`.
- 자동 정리 **팀별로**:
  - `DELETE FROM team_notices WHERE team_id = ? AND created_at < datetime('now', '-30 days')`
  - `DELETE FROM team_notices WHERE team_id = ? AND id NOT IN (SELECT id FROM team_notices WHERE team_id = ? ORDER BY id DESC LIMIT 100)`
- team_id IS NULL 잔존 row 는 `team_id = ?` 매칭 안 되어 자동 정리 대상 아님(운영자 사후 정리 — 계획서 §13). docstring 명시.

### `get_notice_history(limit=100)` → `get_notice_history(team_id, include_null=False, limit=100)`
- `include_null=False` → `WHERE team_id = ?`
- `include_null=True` (admin 이력 화면) → `WHERE team_id = ? OR team_id IS NULL`
- `ORDER BY id DESC LIMIT ?`. docstring 명시.

### 신규 `create_notification_for_team(team_id, type_, message, event_id=None, exclude_user=None)`
- `create_notification_for_all` 바로 아래 배치.
- `SELECT u.name FROM users u JOIN user_teams ut ON ut.user_id = u.id WHERE ut.team_id = ? AND ut.status = 'approved' AND u.is_active = 1` → `exclude_user` 제외하고 `INSERT INTO notifications (user_name, type, message, event_id)`.
- 글로벌 admin(users.role='admin')은 user_teams row 없어 미수신 — 계획서 §13("같은 팀 승인 멤버에게만")과 일치. docstring 명시. `team_id is None` → no-op.

## app.py

### 신규 헬퍼 `_notice_work_team(request, user, explicit_id=None) -> int | None` (line ~700, `_can_write_doc` 뒤)
- `user is None` 또는 `auth.is_unassigned(user)` → `None`.
- 비admin: 비소속 `explicit_id` 는 버림(`auth.user_can_access_team(user, _safe_int(explicit_id))` 실패 시 None) — 다른 팀 공지 임의 조회 차단.
- admin: `explicit_id` 그대로 신뢰(호출부가 `require_work_team_access` 로 검증; admin 통과).
- → `auth.resolve_work_team(request, user, explicit_id=explicit_id)` (= explicit → 쿠키(검증) → 대표 팀 / admin은 first_active_team_id).
- (`_safe_int` 은 app.py line ~2070 정의 — 호출 시점엔 이미 모듈 로드 완료라 forward-reference OK.)

### `GET /notice` (SSR, line ~957)
- `user = auth.get_current_user(request)`; `tid = _notice_work_team(request, user, None)`; `notice = db.get_notice_latest_for_team(tid) if tid is not None else None`.
- `resp = templates.TemplateResponse(...)`; `_ensure_work_team_cookie(request, resp, user)`; `return resp` — **#15 SSR 쿠키 hook 추가** (이전엔 없었음).

### `GET /notice/history` (SSR, line ~967)
- 동일하게 `user`/`tid` 계산. `histories = db.get_notice_history(tid, include_null=auth.is_admin(user)) if tid is not None else []`.
- `_ensure_work_team_cookie` 추가.

### `GET /api/notice` (line ~1387)
- 시그니처 `api_get_notice(request: Request, team_id: int = None)`.
- `user = auth.get_current_user(request)`; `tid = _notice_work_team(request, user, team_id)`; `tid is None` → `{}` (비로그인/미배정/admin 비삭제팀0); else `db.get_notice_latest_for_team(tid) or {}`.

### `POST /api/notice` (line ~1396)
- `user = _require_editor(request)` (CSRF + is_member; 역할 정리는 #16). `data = await request.json()`.
- `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`; `None` → `HTTPException(400, "현재 작업 팀이 필요합니다.")` (admin이 work_team 없이 호출 거부 — `/api/links` POST 미러).
- `auth.require_work_team_access(user, team_id)` (비admin 비소속 → 403; admin 통과).
- `db.save_notice(content, team_id, user["name"])`. **작성자 본인 게이트 없음 — 팀 공유 모델 (같은 팀 승인 멤버 누구나 작성·갱신; links 의 "작성자만"과 다름).**

### `POST /api/notice/notify` (line ~1411)
- `user = _require_editor(request)`. `data = await request.json()` (body 없으면 `{}`).
- `team_id = auth.resolve_work_team(request, user, explicit_id=(data or {}).get("team_id"))`; `None` → `HTTPException(400, ...)`.
- `auth.require_work_team_access(user, team_id)`. **resolve THEN fetch** — `notice = db.get_notice_latest_for_team(team_id)`; `not notice` → `{"ok": False, "reason": "no_notice"}` (기존 동작 보존).
- msg 생성 로직 유지. `db.create_notification_for_team(team_id, "notice", msg, exclude_user=user["name"])` (전 → 같은 팀 승인 멤버만). `{"ok": True}`.

## 변경 안 한 것 (의도)
- `templates/notice.html` / `notice_history.html` / `base.html`: SSR `notice`/`histories` 가 작업 팀 기준으로 바뀌므로 자연 반영. 작업 팀 전환 → base.html `selectWorkTeam`→`location.reload()` → 새 work_team_id 쿠키로 SSR 다시 렌더. `IS_EDITOR`/`user.role in ('editor','admin')` 리터럴 = #16 책임 — 미변경. (NULL orphan row 도 `created_by`(이름 문자열)·`created_at` 만 렌더 — 표시 깨짐 없음.)
- 스키마/마이그레이션: `team_notices.team_id` 컬럼·백필 모두 그룹 A 완료 — 추가 없음.
- `_require_editor`/`is_editor` 역할 정리: #16. `users.team_id` 컬럼 제거: #23.

## 검증
- `import app` OK. `templates.get_template('notice.html'/'notice_history.html'/'base.html')` OK.
- `get_latest_notice`/`save_notice`(2-인자)/`get_notice_history`(0-인자) 옛 시그니처 호출부: app.py 3곳 외 없음(grep — mcp_server.py 에 notice 도구 없음). 전부 새 시그니처로 전환됨.
