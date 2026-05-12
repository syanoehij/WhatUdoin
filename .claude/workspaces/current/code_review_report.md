## 코드 리뷰 보고서 — #15-3 team_notices 팀별 공지 전환

### 리뷰 대상 파일
- `database.py` — `get_notice_latest_for_team`(신규/rename), `save_notice`(시그니처 변경 + 팀별 자동정리), `get_notice_history`(시그니처 변경), `create_notification_for_team`(신규)
- `app.py` — `_notice_work_team`(신규 헬퍼), `GET /notice`·`GET /notice/history`(SSR 쿠키 hook 추가 + 팀 기준), `GET /api/notice`·`POST /api/notice`·`POST /api/notice/notify`(work_team 기준 전환)
- `templates/notice.html` / `notice_history.html` / `base.html` — 변경 없음(확인만)

### 차단(Blocking) ❌
- 없음.

### 경고(Warning) ⚠️
- [ ] `app.py` — `notice_page`·`notice_history_page` 가 `_ctx()` 내부에서 한 번(`work_team_id` 라벨 계산용 `auth.resolve_work_team`) + `_notice_work_team` 에서 또 한 번 `resolve_work_team` 을 호출 → 페이지당 동일 쿼리 1~2회 중복. #15에서 이미 동일 양상으로 기록된 경고와 같은 성질 — 현 규모 허용, #16 정리 후보. (`_ctx` 가 반환하는 `work_team_id` 를 재사용하도록 미세 리팩터 가능하나 동작 동일·범위 최소화 차원에서 미적용.)
- [ ] `app.py:api_notify_notice` — `data = await request.json()` 가 비-dict(list 등)를 반환할 경우 `(data or {}).get(...)` 가 falsy 가 아닌 list 면 `.get` AttributeError. 단 기존 `api_save_notice` 도 동일하게 가드 없음(`data.get(...)` 직접) — 사전 관례와 일치, 신규 결함 아님. (notify는 원래 body 미독이었으나 빈 body 시 `request.json()` 가 raise → try/except 로 `{}` 폴백 추가했고, 정상 케이스는 영향 없음.)

### 통과 ✅
- [x] **권한 체크**: `POST /api/notice`·`POST /api/notice/notify` 둘 다 `_require_editor`(CSRF + is_member) 유지 + `auth.require_work_team_access(user, team_id)` 추가 (비admin 비소속 → 403, admin 통과). `GET /api/notice`·SSR 페이지는 읽기라 무인증 허용(비로그인/미배정 → `{}` / 빈 리스트) — 기존 정책 보존.
- [x] **work_team 결정 패턴**: `POST` 는 `auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))` → None 이면 400 → `require_work_team_access` — `/api/links` POST(#15-2)·`manage/projects` 패턴과 동일. `GET` 은 `_notice_work_team`(비admin 비소속 explicit_id 무시·대표 팀 fallback / admin explicit 신뢰) — `_work_scope` GET 시맨틱과 일관(읽기는 silent ignore, 쓰기는 403).
- [x] **팀 공유 모델**: `POST /api/notice`·`/notify` 에 `created_by == user["name"]` 게이트 없음 — 같은 팀 승인 멤버 누구나 작성·갱신·발송 (계획서 §8-1 — links 의 "작성자/admin만"과 의도적으로 다름). 기존 코드도 작성자 게이트 없었음 — 유지.
- [x] **자동 정리 팀별 적용**: `save_notice` 의 30일/100개 삭제 두 쿼리 모두 `WHERE team_id = ?`. 100개 캡 서브쿼리도 `WHERE team_id = ? ORDER BY id DESC LIMIT 100` 으로 동일 팀 한정 — 다른 팀 공지 영향 없음. NULL 잔존 row 는 `team_id = ?` 매칭 안 되어 자동 정리 제외(계획서 §13 — 운영자 사후 정리). docstring 명시.
- [x] **NULL 잔존 row 가시성**: `get_notice_latest_for_team(team_id)` 는 `WHERE team_id = ?` 라 NULL row 미반환 → `GET /api/notice` 에 안 나옴. `get_notice_history(team_id, include_null=auth.is_admin(user))` → admin 만 `team_id = ? OR team_id IS NULL` 로 NULL orphan 노출. 계획서 "NULL 잔존 row 는 admin 슈퍼유저에게만" 충족.
- [x] **알림 팬아웃 팀 한정**: 신규 `create_notification_for_team` 가 `users u JOIN user_teams ut ON ut.user_id = u.id WHERE ut.team_id = ? AND ut.status = 'approved' AND u.is_active = 1` — pending 멤버·다른 팀·비활성 제외. 글로벌 admin(user_teams row 없음)은 자연 미수신 — 계획서 §13 "같은 팀 승인 멤버에게만"과 일치. docstring 명시. `team_id is None` → no-op.
- [x] **SSR 쿠키 hook**: `GET /notice`·`GET /notice/history` 에 `_ensure_work_team_cookie(request, resp, user)` 추가 (이전엔 없었음 — #15 SSR 12페이지 적용 시 누락분 보완). `_ctx(request, ...)` 사용 — `_ctx` 누락 없음.
- [x] **SQL 파라미터화**: 모든 신규/변경 쿼리 `?` 플레이스홀더 — f-string SQL 삽입 없음.
- [x] **DB 경로**: 신규 코드는 `get_conn()` 만 사용 — `_BASE_DIR`/`_RUN_DIR` 직접 참조 없음.
- [x] **시그니처 전환 호출부**: `get_latest_notice`/`save_notice`(2-인자)/`get_notice_history`(0-인자) 옛 시그니처 호출부 — grep 결과 app.py 3곳 외 없음(mcp_server.py 에 notice 도구 없음). 전부 새 시그니처로 전환. `import app` OK.
- [x] **템플릿**: `notice.html`/`notice_history.html`/`base.html` 변경 없음 — SSR `notice`/`histories` 가 작업 팀 기준으로 바뀌어 자연 반영. NULL orphan row 도 `created_by`(문자열)·`created_at` 만 렌더 — XSS/표시 깨짐 없음(Jinja 자동 이스케이프). 작업 팀 전환 → base.html `selectWorkTeam`→`location.reload()` 흐름(#15)으로 새 팀 공지로 갱신.
- [x] **범위 준수**: `_require_editor`/`is_editor` 역할 정리(#16)·`users.team_id` 컬럼 제거(#23)·`IS_EDITOR`/`user.role in (...)` 리터럴 — 모두 미변경. 스키마/마이그레이션 추가 없음.

### 최종 판정
- **통과** — 차단 결함 0, 경고 2(현 규모 허용·신규 결함 아님). QA 진행 허용.
