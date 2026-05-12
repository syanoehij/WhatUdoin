# 요청

'팀 기능 구현 todo.md' 그룹 B **#15-3 — team_notices 팀별 공지 전환** 한 항목만 끝까지.
상세 사양: '팀 기능 구현 계획.md' §13 (team_notices 팀별 공지 전환) + §8-1 (자료별 적용 표) + §16.
**이게 그룹 B의 마지막 항목** — 완료 후 todo.md "진행 추적 메모"의 `- [ ] 그룹 B 완료 (#11~#15-3)` 토글 + 단위 사이클 기록 표 행 추가.
그룹 A(#1~#10) + 그룹 B #11(ae7a74e)·#12(fd1003a)·#13(3884e56)·#14(04006ba)·#15(88bf9ac)·#15-1(3af48fe)·#15-2(dcc745b) 완료됨.

# 분류

백엔드 수정(라우트 3개 전환 + DB 헬퍼 3개 시그니처 전환 + 신규 per-team 알림 헬퍼 + SSR 2페이지 쿠키 hook) + 프론트엔드(notice 화면 — 확인만, 변경 불필요) → **백엔드 모드 + 프론트 확인** (backend-dev → frontend-dev[확인만] → code-reviewer → qa)

# 배경 — 현재 코드 상태 (반드시 확인하고 시작; 중복 작업 금지)

- `team_notices` 테이블 (database.py:423): `id, team_id INTEGER(NULL 허용), content TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP` — **`created_by`는 이름 문자열**. 스키마 변경 없음(`team_id` 컬럼은 그룹 A #2 Phase 1에서 이미 추가, 백필은 #4 Phase 2에서 완료 — 작성자 단일 팀이면 그 팀 / 다중 팀이면 대표 팀(joined_at 최이른) / admin·매칭실패는 NULL 유지 + `team_migration_warnings`. **백필·마이그레이션 phase 추가 금지.**).
- 현재 `/api/notice` 3개 라우트 (app.py:1361~1385):
  - `GET /api/notice` (line 1361) → `db.get_latest_notice() or {}` — **인증/팀 필터 없음 (전역 단일 공지)**. (로그인 안 한 사용자도 호출 가능 — 현재 동작 유지: 로그인 안 했으면 `{}`. 미배정 사용자도 `{}`.)
  - `POST /api/notice` (line 1366) → `_require_editor` → `db.save_notice(content, user["name"])` — **team_id 미저장**.
  - `POST /api/notice/notify` (line 1375) → `_require_editor` → `db.get_latest_notice()` → `db.create_notification_for_all("notice", msg, exclude_user=user["name"])` — **전 사용자 알림**.
- DB 헬퍼 (database.py:3492~3524):
  - `get_latest_notice() -> dict|None`: `SELECT * FROM team_notices ORDER BY id DESC LIMIT 1` — team_id 무필터.
  - `save_notice(content, created_by) -> int`: INSERT 후 (a) `DELETE ... WHERE created_at < datetime('now','-30 days')` (b) `DELETE ... WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT 100)` — **둘 다 전역 일괄 삭제** → 팀별로 전환 필요.
  - `get_notice_history(limit=100) -> list[dict]`: `SELECT * FROM team_notices ORDER BY id DESC LIMIT ?` — team_id 무필터.
  - `create_notification_for_all(type_, message, event_id=None, exclude_user=None)` (database.py:3364): 전 활성 사용자 알림. **per-team 변형 헬퍼가 없음** → 신규 작성 필요.
- `auth.resolve_work_team(request, user, explicit_id)` (auth.py:191): explicit_id 무조건 신뢰(호출부가 `_work_scope`/`require_work_team_access`로 검증) → 쿠키 검증(`user_can_access_team` + `_team_is_active`) → `_work_team_default`(admin→`first_active_team_id` / 비admin→`primary_team_id_for_user` → legacy users.team_id(비삭제일 때만) → None). **explicit None + 쿠키 무효/없음 + 미배정이면 None 반환.**
- `auth.require_work_team_access(user, team_id)` (auth.py:149): `user_can_access_team` 실패 시 403 (admin은 항상 통과).
- `app._work_scope(request, user, explicit_id)` (app.py:2031): admin→None / 비admin→`resolve_work_team` 결과 1개 set, 비소속 명시 무시·대표 팀 fallback, 미배정→`set()`. #15-2 `/api/links` GET 과 동일 패턴.
- `app._ensure_work_team_cookie(request, response, user)` (app.py:1527): SSR 페이지에서 호출 — `resolve_work_team` 결과(None이면 noop)와 현재 쿠키 다르면 Set-Cookie. **`/notice`·`/notice/history` 라우트는 현재 이 hook 미적용** (#15에서 SSR 12개 페이지에 적용했으나 notice 계열은 빠짐). → **추가 필요.**
- `app._require_editor(request)` (app.py:678): `_check_csrf` + `auth.is_editor`(=is_member: member/editor/admin) → 403. (역할 정리는 #16 책임 — 본 사이클 변경 금지.)
- `templates/notice.html`: SSR `notice`(content) + `IS_EDITOR`(=`user.role in ('editor','admin')`)로 편집기/뷰 분기. 자동 새로고침 hook(`_autoSaveAndRefresh`)·beforeunload 방어 있음. 작업 팀 전환은 base.html `selectWorkTeam`→`location.reload()` 흐름으로 새 work_team_id 쿠키 적용 후 페이지 새로 렌더 → SSR `notice`가 새 팀 공지로 자연 반영. **`templates/notice.html` 변경 불필요** (확인만; `user.role in (...)` 리터럴은 #16 책임이라 미변경 — 단 `IS_EDITOR`/role 게이팅 자체는 #15-3 범위 밖). `templates/notice_history.html`: SSR `histories` 리스트만 렌더 — `_ctx(request, histories=...)` 가 작업 팀 기준으로 바뀌면 자연 반영. **변경 불필요.**
- `mcp_server.py`에 notice 도구 없음 — `get_latest_notice`/`save_notice`/`get_notice_history` 외부 호출부는 `app.py` 3곳뿐.

# #15-3 핵심 설계 결정 (advisor 검토 반영 — backend-dev 는 이대로 구현)

## NULL 잔존 row 가시성 — 최우선 명확화
- `GET /api/notice` → **모두에게** 현재 작업 팀의 최신 공지 1건. NULL 잔존 row로 fallback하지 않음 (계약 단순화 — "work_team에 공지 없으면 빈 `{}`"). 비admin 미배정 → `{}`. admin은 `resolve_work_team`→`first_active_team_id`로 항상 work_team이 있으므로(비삭제 팀 0개일 때만 None) 그 팀 공지(없으면 `{}`).
- `get_notice_history` → 비admin: `WHERE team_id = ?` (작업 팀). admin: `WHERE team_id = ? OR team_id IS NULL` (작업 팀 row + NULL 잔존 orphan을 admin 이력 화면에 노출). **즉 NULL orphan은 GET이 아니라 admin 의 history 화면에서만 보임.**

## 라우트 전환 (#15-2 `/api/links` 패턴 미러)
- `GET /api/notice` (app.py): `team_id: int = None` 쿼리 파라미터 추가. `user = auth.get_current_user(request)`. 로그인 안 했으면 `{}` 유지(현재 동작 보존 — notice는 비로그인 노출 정책이 아니므로 빈 dict). 로그인 시: `tid = auth.resolve_work_team(request, user, explicit_id=team_id)` — 단 비소속 명시 team_id는 버려야 함(`_work_scope`가 하는 일). **간단히**: `scope = _work_scope(request, user, team_id)` 사용 → admin이면 None / 비admin이면 0~1개 set. 그런데 GET notice는 "단일 작업 팀의 최신 1건"이라 set 컨벤션이 안 맞음 → 다음처럼:
  - admin: `team_id` 명시되면 `auth.require_work_team_access`(admin은 통과)·`_team_is_active` 검증 후 그 팀 / 없으면 `auth.resolve_work_team(request, user, None)`(=쿠키 또는 first_active_team_id). `None`이면 `{}`.
  - 비admin: `tid = auth.resolve_work_team(request, user, explicit_id=(team_id if (team_id is not None and auth.user_can_access_team(user, team_id)) else None))`. (비소속 명시 → 버리고 쿠키/대표 팀 fallback.) `tid is None` → `{}` (미배정).
  - 결정된 `tid` 로 `db.get_notice_latest_for_team(tid) or {}`.
  - **권장 단순화 (backend-dev 재량 — 위와 동치면 OK)**: `_notice_work_team(request, user, explicit_id)` 작은 헬퍼를 app.py에 두고 GET/POST/notify 셋이 공유 — admin이면 `resolve_work_team(req, user, explicit_id)`(explicit 신뢰 — `require_work_team_access` 호출부에서 검증), 비admin이면 비소속 explicit 버린 뒤 `resolve_work_team`. 반환은 `int|None`.
- `POST /api/notice` (app.py): `user = _require_editor(request)` 유지. `data = await request.json()`. `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`. `team_id is None` → **400** (`detail="현재 작업 팀이 필요합니다."` — admin이 work_team 없이 호출 거부; `manage/projects`·`/api/links` POST 미러). `auth.require_work_team_access(user, team_id)` (비admin 비소속 → 403; admin 통과). `notice_id = db.save_notice(content, team_id, user["name"])`. 반환 `{"id": notice_id}`. **작성자 본인 게이트 없음 (팀 공유 모델 — 같은 팀 승인 멤버 누구나).**
- `POST /api/notice/notify` (app.py): `user = _require_editor(request)`. `data = await request.json()` (혹은 body 없으면 빈 dict — 현재 body 안 읽으므로 호환상 `data.get("team_id")`만 옵션). `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`. `team_id is None` → `{"ok": False, "reason": "no_work_team"}` (또는 400 — backend-dev 재량, 단 `manage` 패턴 따라 400 권장). `auth.require_work_team_access(user, team_id)`. `notice = db.get_notice_latest_for_team(team_id)` — `not notice` → `{"ok": False, "reason": "no_notice"}` (현재 동작 보존). preview/msg 생성 로직 유지. `db.create_notification_for_team(team_id, "notice", msg, exclude_user=user["name"])`. 반환 `{"ok": True}`. **resolve THEN fetch — `get_latest_notice()` 먼저 호출하지 말 것.**

## DB 헬퍼 (database.py — `get_links`/`update_link` 와 동일하게 surgical rename)
- `get_latest_notice()` → **`get_notice_latest_for_team(team_id: int) -> dict|None`**: `SELECT * FROM team_notices WHERE team_id = ? ORDER BY id DESC LIMIT 1`. (기존 이름 호출부 없음 — app.py 3곳 다 새 라우트에서 호출. 안전상 grep 으로 다른 caller 없음 확인됨.)
- `save_notice(content, created_by)` → **`save_notice(content: str, team_id: int, created_by: str) -> int`**: `INSERT INTO team_notices (team_id, content, created_by) VALUES (?, ?, ?)`. 자동 정리 **팀별로**:
  - `DELETE FROM team_notices WHERE team_id = ? AND created_at < datetime('now', '-30 days')`
  - `DELETE FROM team_notices WHERE team_id = ? AND id NOT IN (SELECT id FROM team_notices WHERE team_id = ? ORDER BY id DESC LIMIT 100)`
  - **NULL 잔존 row는 자동 정리 대상 아님** (team_id가 NULL이라 `team_id = ?` 매칭 안 됨 — 계획서 §13 "NULL 잔존 row는 운영자가 사후 정리"). docstring 1줄로 명시.
- `get_notice_history(limit=100)` → **`get_notice_history(team_id: int, include_null: bool = False, limit: int = 100) -> list[dict]`**: `include_null` False → `WHERE team_id = ?`; True(admin) → `WHERE team_id = ? OR team_id IS NULL`. `ORDER BY id DESC LIMIT ?`. docstring에 "include_null은 admin 이력 화면에서 백필 누락 NULL row 노출용".
- 신규: **`create_notification_for_team(team_id: int, type_: str, message: str, event_id: int | None = None, exclude_user: str | None = None)`**: `SELECT u.name FROM users u JOIN user_teams ut ON ut.user_id = u.id WHERE ut.team_id = ? AND ut.status = 'approved' AND u.is_active = 1` → `exclude_user` 제외하고 `INSERT INTO notifications (user_name, type, message, event_id) VALUES (?,?,?,?)`. **글로벌 admin(users.role='admin')은 보통 user_teams row가 없어 알림 미수신** — 계획서 §13 "같은 팀 승인 멤버에게만"과 일치. docstring 1줄로 명시. (`create_notification_for_all` 옆에 배치.)

## SSR 페이지 (app.py)
- `GET /notice` (app.py:940): `notice = db.get_latest_notice()` → 다음으로 교체. `user = auth.get_current_user(request)`. `tid = auth.resolve_work_team(request, user, None) if (user and not auth.is_unassigned(user)) else None` (= `_ctx`가 work_team_id 계산하는 방식과 동일 — 또는 `_ctx`에서 work_team_id 받아오기). `notice = db.get_notice_latest_for_team(tid) if tid is not None else None`. `resp = templates.TemplateResponse(request, "notice.html", _ctx(request, notice=notice))`. `_ensure_work_team_cookie(request, resp, user)`. `return resp`.
- `GET /notice/history` (app.py:946): 마찬가지로 `user`/`tid` 계산. `histories = db.get_notice_history(tid, include_null=auth.is_admin(user)) if tid is not None else []`. (admin은 tid가 항상 있음. 비admin 미배정이면 `[]`.) `_ctx(request, histories=histories)`. `_ensure_work_team_cookie`. `return resp`.
- **주의**: `_ctx`는 이미 `auth.resolve_work_team`을 한 번 호출함(work_team_id 라벨용). notice 라우트에서 또 호출하면 쿼리 중복 — 허용 범위(#15에서도 이미 그럼, 경고로 기록됨). 굳이 줄이려면 라우트에서 `_ctx` 반환 dict의 `work_team_id`를 재사용해도 됨 (backend-dev 재량 — 동작 동일하면 OK).

## 프론트엔드 — 확인만 (변경 불필요)
- `templates/notice.html`: SSR `notice`(content) 가 작업 팀 기준으로 바뀌므로 자연 반영. 작업 팀 전환 → `selectWorkTeam`→`location.reload()` → 새 work_team_id 쿠키로 SSR 다시 렌더. `IS_EDITOR`/`user.role in (...)` 리터럴은 #16 책임 — **건드리지 않음**. (선택적 polish: 헤더에 현재 팀 이름 pill — `work_team_name`이 `_ctx`에 이미 있음 — 그러나 #15-3 요구사항 아님. **#15-2처럼 "프론트 변경 없음" 결과 권장.** frontend-dev 는 확인만.)
- `templates/notice_history.html`: SSR `histories` 가 작업 팀 기준으로 바뀜 → 자연 반영. **변경 없음.**
- `templates/base.html`: 헤더 등에 `/notice` 링크가 있어도 페이지 자체가 작업 팀 기준 — 변경 없음.

# 에이전트별 작업

## backend-dev
1. `database.py`:
   - `get_latest_notice()` → `get_notice_latest_for_team(team_id)` (rename + WHERE team_id=? 추가).
   - `save_notice(content, created_by)` → `save_notice(content, team_id, created_by)` (INSERT에 team_id 포함; 자동 정리 2쿼리에 `WHERE team_id = ?` 추가; NULL orphan 미정리 docstring).
   - `get_notice_history(limit=100)` → `get_notice_history(team_id, include_null=False, limit=100)` (WHERE 분기).
   - 신규 `create_notification_for_team(team_id, type_, message, event_id=None, exclude_user=None)` (`create_notification_for_all` 옆; user_teams approved JOIN; admin 미수신 docstring).
2. `app.py`:
   - `GET /api/notice` (line 1361): 위 설계대로 `team_id: int = None` 파라미터 + work_team 결정 + `db.get_notice_latest_for_team(tid) or {}`. 비로그인/미배정 → `{}`. (작은 헬퍼 `_notice_work_team` 도입 권장 — GET/POST/notify 공유.)
   - `POST /api/notice` (line 1366): `_require_editor` → `data` → `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))` → None이면 400 → `auth.require_work_team_access(user, team_id)` → `db.save_notice(content, team_id, user["name"])`. 작성자 게이트 없음.
   - `POST /api/notice/notify` (line 1375): `_require_editor` → work_team 결정(없으면 400 또는 `{"ok":False,"reason":"no_work_team"}` — 400 권장) → `require_work_team_access` → `db.get_notice_latest_for_team(team_id)` (없으면 `{"ok":False,"reason":"no_notice"}`) → msg 생성 → `db.create_notification_for_team(team_id, "notice", msg, exclude_user=user["name"])` → `{"ok": True}`.
   - `GET /notice` (line 940): work_team 기준 `notice` + `_ensure_work_team_cookie`.
   - `GET /notice/history` (line 946): work_team 기준 `histories`(admin은 include_null=True) + `_ensure_work_team_cookie`.
3. 변경 대상 파일: `database.py`, `app.py`
4. 완료 후 `.claude/workspaces/current/backend_changes.md` 에 변경 내용 기록 (변경 파일·함수·시그니처·라우트별 동작).

## frontend-dev (확인만)
- `templates/notice.html`·`templates/notice_history.html`·`templates/base.html` 검토 → 작업 팀 전환 시 새 팀 공지로 자연 반영되는지(SSR `notice`/`histories`가 work_team 기준 + `location.reload()` 흐름) 확인. **코드 변경 없음 예상** — 변경 필요 사항이 있으면(없을 듯) `frontend_changes.md`에 기록, 없으면 "변경 불필요 — 사유" 만 기록.

## code-reviewer
- `backend_changes.md`(+`frontend_changes.md`) 읽고 변경 파일 정적 리뷰: 시그니처 전환 호출부 누락 없음 / NULL orphan 처리 정확(GET 제외·history admin 포함·자동정리 제외) / 팀 공유 모델(작성자 게이트 부재) 의도 / `_ensure_work_team_cookie` 두 라우트 추가 / `create_notification_for_team` approved JOIN·admin 미수신 / 관례 준수. 결과 `code_review_report.md`.

## qa
- 라이브 Playwright 대신 **TestClient + 임시 DB** (운영 서버 IP 자동 로그인 → 다중 팀/admin 시나리오 브라우저 재현 불가). `tests/phase87_team_notices_multiteam.py` 신규 — 정적 검사(시그니처/라우트 grep) + 동작 검사:
  - A 다중 팀 사용자 작업 팀 전환(쿠키)→GET /api/notice 새 팀 최신 공지로·명시 ?team_id(소속) 우선·old 팀 공지 미노출.
  - B 다른 팀 멤버 세션에선 그 팀 공지 안 보임 (명시 ?team_id로도 비소속 → 무시·대표 팀 fallback).
  - C POST /api/notice → team_id가 work_team_id로 확정 저장·명시 team_id(소속) 우선·비소속 명시 → 403·미배정 → 400·admin이 work_team 없이(쿠키X+?X) → 400·admin 쿠키/명시 후 → 200.
  - D POST /api/notice/notify → 같은 팀 approved 멤버에게만 알림 도착·다른 팀 멤버 미도착·글로벌 admin 미도착(user_teams row 없음)·exclude_user(발송자) 제외·공지 없으면 `{"ok":False,"reason":"no_notice"}`.
  - E 같은 팀 멤버 B가 멤버 A가 만든 공지 POST(갱신)·notify 가능 (팀 공유 모델 — links와 반대).
  - F 자동 정리: 팀A에 101개 공지 → save 시 팀A 최신 100개만·팀B 공지(개수 무관) 영향 없음·30일 이전 팀A row만 삭제·NULL orphan row 영향 없음.
  - G NULL 잔존 row: GET /api/notice 에 안 나옴(작업 팀 row만)·`get_notice_history(tid, include_null=False)` 에 없음·`get_notice_history(tid, include_null=True)`(admin) 에 나옴·SSR `/notice/history` admin 응답에 포함·비admin 미포함.
  - H SSR: `/notice`·`/notice/history` GET 시 work_team_id 쿠키 없으면(소속 1개) Set-Cookie 발급(`_ensure_work_team_cookie`)·미배정 사용자 → Set-Cookie 없음·`notice`/`histories` 빈 값.
  - I 회귀: phase80~86 (전 60+ PASS) + `tests/check_notice.spec.js` 가 있으면 검토(라이브 Playwright라 미실행 가능 — 서버 재시작 필요 시 명시).
- 서버 재시작 필요(코드 reload — app.py/database.py; 스키마 무변경 → 마이그레이션 불필요)면 메인에 명시. 결과 `qa_report.md`.

# 주의사항
- **스키마 변경·마이그레이션 phase 추가 금지** — `team_notices` 테이블·`team_id` 컬럼·백필 모두 그룹 A에서 완료.
- 역할 정리(`is_editor`/`_require_editor`/`user.role in (...)` 리터럴)는 **#16 책임** — 본 사이클에서 건드리지 않음.
- `users.team_id` 컬럼 자체 제거는 **#23** — 본 사이클 범위 밖.
- todo.md 기록 누락 금지: ① `### #15-3.` 섹션 6개 `[ ]` 전부 토글(미완은 사유) ② 단위 사이클 기록 표 행 추가 ③ **#15-3이 그룹 B 마지막 → "진행 추적 메모"의 `- [ ] 그룹 B 완료 (#11~#15-3) ...` 를 `- [x]` 로 토글** (단 그룹 B 내 다른 미완 sub-task 있으면 토글 말고 메인에 사유 보고).
- 사이클 종료 시 자체 판단 커밋: `feat: 팀 기능 그룹 B #15-3 — team_notices 팀별 공지 전환 + 그룹 B 마무리`. changelog 안 건드림.
