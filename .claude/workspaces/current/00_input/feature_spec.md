# 요청

'팀 기능 구현 todo.md' 그룹 B #15 — 프로필 메뉴 "팀 변경" UI + `work_team_id` 쿠키 발급/검증/Set-Cookie + 화면별 팀 드롭다운 제거.
상세 사양: '팀 기능 구현 계획.md' §7 (현재 작업 팀 선택) + §16 (권한 원칙).
그룹 A(#1~#10) + 그룹 B #11~#14 완료됨. 이번 사이클은 **#15 한 항목만** 끝까지.

# 분류

기능 추가 (서버 라우트 신규 + SSR 쿠키 hook + 프론트 프로필 드롭다운 신규 + 기존 화면 드롭다운 제거) → **팀 모드** (backend-dev → frontend-dev → code-reviewer → qa)

# 배경 — 현재 코드 상태 (반드시 확인하고 시작)

- **`auth.resolve_work_team(request, user, explicit_id)` 는 이미 쿠키를 읽는다** (auth.py:160-195). 우선순위 explicit_id → `work_team_id` 쿠키 → admin이면 None → 대표 팀(min id) → legacy `users.team_id`.
  - 빠진 것 3가지: ① **쿠키 값 검증** (현재 쿠키 int를 무조건 신뢰 — 소속 빠짐/팀 soft-delete 시 stale id 반환), ② **Set-Cookie 발급** (전용 라우트 없음, SSR hook 없음), ③ **admin no-cookie fallback** (현재 None — 계획서 §7은 "첫 비삭제 팀").
- `_work_scope(request, user, explicit_id)` (app.py:1930-1951): admin → `None` 반환 (전 팀 무필터 READ 유지), 비admin → resolve_work_team 결과 1개 set, 명시 team_id 비소속이면 무시·대표팀 fallback, 결정 불가(미배정)면 빈 set. **#15에서 admin은 여기 변경 안 함** — admin의 work_team_id는 WRITE(이벤트 생성 team_id 등) + 프로필 표시용일 뿐, READ는 슈퍼유저 무필터 유지.
- `auth.require_work_team_access(user, team_id)` (auth.py:149-153): `user_can_access_team` 실패 시 HTTPException 403. admin은 항상 통과.
- `auth.user_team_ids` 는 이미 `JOIN teams ... deleted_at IS NULL` 필터 적용.
- 팀 컨텍스트 라우트 ~20개는 이미 `team_id` 파라미터 + `_work_scope`/`resolve_work_team`/`require_work_team_access` 골격이 #10에서 들어가 있음. **이번엔 쿠키 통합 + Set-Cookie + UI만 얹는다.**

# admin 동작 결정 (스펙으로 확정 — backend-dev 추측 금지)

§16-1 (admin_team_scope = 전 팀) 와 §7 (admin도 작업 팀 선택, 프로필 "HW팀(슈퍼유저)") 가 모순처럼 보이지만 다음으로 화해한다:

| 함수 | admin, 쿠키 없음 | admin, 유효 쿠키 | admin, 무효 쿠키 |
|------|----------------|----------------|----------------|
| `resolve_work_team` | **첫 비삭제 팀 id** (`teams WHERE deleted_at IS NULL ORDER BY id LIMIT 1`) ← **변경** | 쿠키 값 (불변) | 무시 → 첫 비삭제 팀 id ← **변경(검증 추가)** |
| `_work_scope` | `None` (불변 — 무필터 READ 유지) | `None` (불변) | `None` (불변) |

즉 admin의 `work_team_id`는 **WRITE 대상 team_id**(이벤트/문서 생성 시 `resolve_work_team` 결과) + **프로필 라벨**만 좌우. READ scoping은 admin 슈퍼유저 그대로.

회귀 확인: `tests/` + 아카이브 `verify_team10.py` 에 admin write가 `team_id IS NULL` 임을 단언하는 부분 없음 (read-focused) — 확인 완료. 따라서 admin no-cookie fallback 변경 안전. backend-dev는 그래도 `resolve_work_team` 호출부(`grep -n resolve_work_team app.py`)를 다시 훑어 admin이 None을 받던 write 경로(특히 app.py:2153 event create, 2461/2542 checklist 등, 4048 doc_team_id)가 이제 실제 팀에 들어가는 게 의도대로인지 확인.

# 에이전트별 작업

## backend-dev

### A. `auth.resolve_work_team` 쿠키 검증 + admin fallback 강화

- 쿠키 값을 int 파싱한 뒤 **`user_can_access_team(user, tid)` 로 검증** (admin은 user_can_access_team이 비삭제 팀이면 True여야 — 현재 admin은 항상 True 반환하므로 admin 유효 쿠키가 삭제 예정 팀이면? → admin도 쿠키 검증 시 `teams.deleted_at IS NULL` 체크 필요. 새 헬퍼 `_team_active(tid)` 또는 인라인 SELECT). 검증 실패면 쿠키 무시하고 fallback으로.
- admin fallback (쿠키 없음/무효): `None` 대신 **`db` 신규 헬퍼 `first_active_team_id()`** = `SELECT id FROM teams WHERE deleted_at IS NULL ORDER BY id LIMIT 1` (없으면 None). (계획서 §7 "마지막 선택 팀(별도 저장 시) 또는 첫 번째 팀" — '마지막 선택 팀 별도 저장'은 이번 범위 밖, 쿠키가 그 역할. 쿠키 없으면 첫 비삭제 팀.)
- 비admin fallback 변경 없음: 대표 팀 = `user_team_ids` 중 — **단 계획서 §7·todo §#15는 "joined_at 가장 이른 팀"**. 현재 코드는 `min(team_ids)` (id 최소). 이걸 `joined_at` 기준으로 교체: `db` 신규 헬퍼 `primary_team_id_for_user(user_id)` = `SELECT ut.team_id FROM user_teams ut JOIN teams t ON t.id=ut.team_id WHERE ut.user_id=? AND ut.status='approved' AND t.deleted_at IS NULL ORDER BY ut.joined_at ASC, ut.team_id ASC LIMIT 1`. (joined_at 컬럼명 확인 — `user_teams` 실제 컬럼이 `joined_at`인지 grep. 없으면 `created_at` 또는 rowid 순. todo §#2 비고: 실제 컬럼명이 명세와 다를 수 있음.)
- legacy `users.team_id` fallback은 그대로 마지막 단계로 유지 (마이그레이션 전 호출 방어).
- **`_work_scope` 의 쿠키 안전망**(app.py:1948-1950 `user_can_access_team(user, tid)` 재검증)은 이제 `resolve_work_team` 자체가 검증하므로 중복이지만 — 그대로 둬도 무해. 단 `resolve_work_team`이 검증을 흡수했으니 `_work_scope`는 손대지 말고(surgical), admin `None` 반환 라인도 불변.

### B. `db` 신규 헬퍼

- `first_active_team_id() -> int | None`
- `primary_team_id_for_user(user_id) -> int | None`  (joined_at 기준 대표 팀)
- `user_work_teams(user_id) -> list[dict]`  (프로필 드롭다운/`/api/me/work-team` 검증용) = 사용자의 approved + 비삭제 소속 팀 `[{id, name}]`, joined_at 순. admin은 호출하지 않음 (admin은 전체 비삭제 팀 = 기존 `get_visible_teams()` 사용).
- `team_active(team_id) -> bool` 또는 인라인 — `teams WHERE id=? AND deleted_at IS NULL` 존재 여부 (쿠키/POST 검증용).

### C. `POST /api/me/work-team` 라우트 신규

- body: `{"team_id": <int>}` (Form 또는 JSON — 기존 다른 `/api/me/*` 라우트 컨벤션 따름. #9 `/api/me/ip-whitelist`, #8 `/api/me/team-applications` 확인 — 대부분 Form/Body Pydantic).
- 검증:
  - `_check_csrf(request)` (다른 unsafe 라우트와 동일).
  - 로그인 필수 (`auth.get_current_user`; 없으면 401).
  - `team_id` int 파싱 실패 → 400.
  - **비admin**: `auth.require_work_team_access(user, team_id)` (그 팀 approved 멤버 아니면 403) + `team_active(team_id)` 거짓이면 404/400 ("삭제 예정 팀으로 전환할 수 없습니다").
  - **admin**: `team_active(team_id)` 거짓이면 404/400, 그 외 허용 (require_work_team_access는 admin이면 통과하므로 호출해도 무방).
- 성공: `JSONResponse({"ok": True, "team_id": tid, "team_name": ...})` + `response.set_cookie(WORK_TEAM_COOKIE, str(tid), max_age=31536000, samesite="lax", httponly=False, path="/")`. (secure 플래그: 다른 set_cookie 쓰는 곳 — `auth.SESSION_COOKIE` set 위치 — 의 패턴 따름. 운영은 https지만 http:8000도 살아있으니 기존 패턴 그대로.)
- 잘못된 팀이면 4xx (위 검증). detail 메시지 한글.

### D. SSR 첫 페이지 렌더 시 쿠키 읽고 검증 + Set-Cookie

- **방식: 미들웨어 신설 금지**. per-route 헬퍼 `_ensure_work_team_cookie(request, response, user) -> None`:
  - `user is None` 또는 `auth.is_unassigned(user)` → 아무것도 안 함 (미배정·비로그인은 작업 팀 개념 없음. 쿠키 있어도 건드리지 않음 — 추후 가입 시 다시 계산).
  - `intended = auth.resolve_work_team(request, user, None)` (이미 검증 포함). `intended is None` (admin인데 비삭제 팀 0개 등) → 아무것도 안 함.
  - `cookie_raw = request.cookies.get(auth.WORK_TEAM_COOKIE)`. `cookie_raw` 가 `str(intended)` 와 다르면 (없거나·무효라 fallback 됐거나·오래된 값) → `response.set_cookie(WORK_TEAM_COOKIE, str(intended), max_age=31536000, samesite="lax", httponly=False, path="/")`.
- `templates.TemplateResponse(...)` 가 Response 객체를 반환하므로, 해당 변수에 담아 `_ensure_work_team_cookie` 호출 후 `return`. 적용 SSR 페이지 라우트 (app.py 691~ 영역 + 그 아래):
  - `/` (index), `/calendar`, `/admin` (admin인 경우만 — admin_login 페이지엔 불필요하나 호출해도 user None이라 무해), `/kanban`, `/gantt`, `/project-manage`, `/doc` (docs_page), `/check`, `/doc/{id}` (문서 보기 — 라우트명 확인), `/doc/new`, `/doc/{id}/edit` (있으면), `/trash` 등 — **로그인 사용자가 보는 주요 페이지 전부**. 정확한 목록은 backend-dev가 `grep -n 'TemplateResponse' app.py` 로 추려 user 컨텍스트 있는 것만. 누락돼도 다른 페이지에서 곧 발급되므로 critical하진 않음 — 단 `/` 와 칸반·간트·캘린더·문서·체크는 필수.
  - **주의**: `_ensure_work_team_cookie` 안에서 `resolve_work_team` 을 호출하면 page 라우트마다 user_team_ids 쿼리 1회 추가 — 허용 (호출 빈도 낮음, #12에서 `_ctx`도 +1 쿼리 허용한 전례).
- 무효 쿠키(삭제 예정 팀 / 소속 빠진 팀) → `resolve_work_team` 이 무시하고 새 대표 팀(admin은 첫 비삭제 팀) 계산 → cookie_raw != str(intended) 이므로 Set-Cookie 갱신. ✔

### E. `_ctx` 에 work_team 정보 추가 (프론트 표시용)

- `_ctx` 반환 dict에 추가:
  - `"work_team_id": <resolve_work_team 결과 or None>`
  - `"work_team_name": <해당 팀 이름 or None>`  (admin이면 팀 이름 그대로 — 프로필 라벨에서 "(슈퍼유저)" 는 템플릿에서 admin 분기로 붙임)
- 이미 `_ctx`가 `auth.is_unassigned(user)` 호출하므로 user 조회는 있음. work_team_id는 `auth.resolve_work_team(request, user, None)`, 이름은 `db.get_team_active(work_team_id)` 또는 가벼운 `SELECT name FROM teams WHERE id=?`. None이면 둘 다 None.
- (선택) `current_user_payload` 가 base.html `{% set %}` 에서 만들어지므로, work_team_id/work_team_name 을 `_ctx` 컨텍스트로만 넘기면 base.html에서 `current_user_payload` 에 합칠 수 있음 — 그건 frontend-dev 작업.

### F. 변경 대상 파일

- `auth.py` — `resolve_work_team` 검증/admin fallback/joined_at 대표 팀, 필요 시 `_team_active` 헬퍼 (또는 db 위임)
- `database.py` — `first_active_team_id`, `primary_team_id_for_user`, `user_work_teams`, `team_active` 신규 (전부 SELECT 전용 — 마이그레이션 phase 추가 없음)
- `app.py` — `POST /api/me/work-team`, `_ensure_work_team_cookie` 헬퍼, SSR 라우트 다수에 호출 추가, `_ctx` work_team 정보 추가

### G. 완료 후 `.claude/workspaces/current/backend_changes.md` 에 변경 내용 기록 (변경 파일·함수·시그니처·새 라우트 스펙·SSR hook 적용 라우트 목록)

## frontend-dev

### A. 프로필 메뉴 "팀 변경" UI (`templates/base.html`)

- `profile-dropdown` 안에 새 섹션 (예: `🔑 비밀번호 변경` 위 또는 `⚙️ 설정` 아래, profile-dropdown-sep 적절히):
  - "👥 팀 변경" 항목 — 클릭 시 인라인 하위 메뉴(submenu) 또는 모달로 팀 목록 표시. **간단하게**: profile-dropdown 안에 접히는 sub-list. 옵션: 각 팀 버튼, 현재 작업 팀엔 ✔ 표시.
  - 팀 목록 데이터: 비admin = 본인 소속 팀 (서버에서 `_ctx` 로 넘긴 list 또는 `GET /api/me/work-team` GET 엔드포인트 — **GET도 추가하면 깔끔**: `GET /api/me/work-team` → `{current: tid, teams: [{id,name}]}`. backend-dev가 POST와 같이 GET도 만들면 프론트가 동적 로드. 아니면 `_ctx` 컨텍스트 변수로 `work_teams` 넘김 — backend-dev와 협의. **권장: GET 엔드포인트 추가** — 드롭다운 열 때 fetch).
  - admin = 전체 비삭제 팀 (GET 엔드포인트가 admin이면 `get_visible_teams()` 반환).
  - 팀 선택 시: `fetch('/api/me/work-team', {method:'POST', headers:{'Content-Type':...}, body: JSON.stringify({team_id})})` → 성공(`r.ok`)이면 `location.reload()`. 실패면 `showToast`(있으면)/`alert` 로 detail.
- 프로필 라벨에 현재 작업 팀 이름 표시:
  - `profile-btn` 또는 `profile-dropdown-header` 에 — 계획서 §7 예: `홍길동 · HW팀`, admin은 `admin · HW팀(슈퍼유저)`.
  - `profile-dropdown-role` 줄(현재 "관리자"/"에디터")을 활용하거나 별도 줄 추가. `work_team_name` 이 `_ctx` 로 오므로 `{{ work_team_name }}` 사용. None(미배정/팀없음)이면 표시 생략.
  - admin이면 `{{ work_team_name }}(슈퍼유저)` 또는 `(슈퍼유저)` 만.
- `current_user_payload` 에 `work_team_id`/`work_team_name` 추가 (base.html:344 `{% set current_user_payload = ... %}`) — JS에서 `CURRENT_USER.work_team_id` 로 접근 가능하게.

### B. JS `CURRENT_USER.team_id` 사용처 전환

- `base.html:344` 의 payload는 현재 `team_id: user.team_id` (legacy 단일 팀). `work_team_id` 추가 후, "지금 내가 작업 중인 팀" 의미로 `CURRENT_USER.team_id` 를 쓰던 곳을 `CURRENT_USER.work_team_id` 로 교체:
  - `templates/kanban.html:344` `_applyInitialTeamFilter` — 이 함수 자체가 드롭다운 제거로 사라짐 (아래 C). 관련 없음.
  - `templates/kanban.html:604-605` `fetch('/api/teams/members?team_id=' + CURRENT_USER.team_id)` → `CURRENT_USER.work_team_id` (작업 팀 멤버 칩 로드).
  - `templates/calendar.html:570-571` 동일 — `team_id=CURRENT_USER.team_id` → `work_team_id`.
  - `templates/calendar.html:769` 편집 권한 게이팅 `(CURRENT_USER.role === 'editor' && (p.team_id === null || p.team_id === CURRENT_USER.team_id))` → `CURRENT_USER.work_team_id` (이 일정의 team_id가 내 작업 팀과 같으면 편집 가능 — 실제 권한은 서버가 최종 판단이지만 UI 힌트는 작업 팀 기준이 맞음).
  - `templates/project.html` — `team-filter` 드롭다운 제거 시 `_applyInitialTeamFilter` 도 제거 (아래 C). 다른 `CURRENT_USER.team_id` 참조 있으면 audit.
  - **legacy 그대로 둘 곳**: 진짜 legacy 단일 팀 의미(거의 없을 것)는 audit 후 판단. 대부분 위 4곳이 전부.
- `null` 안전: `work_team_id` 가 null일 수 있음(admin인데 팀 0개, 또는 미배정 — 미배정은 base.html 알림 게이팅과 별개). members fetch는 `if (CURRENT_USER && CURRENT_USER.work_team_id)` 가드 유지 (기존 `if (CURRENT_USER && CURRENT_USER.team_id)` 패턴 그대로 키만 교체).

### C. 기존 화면별 팀 드롭다운 제거

- **`templates/kanban.html`**:
  - `<select id="team-filter" onchange="loadKanban()">...{% for team in teams %}...</select>` + 그 `<label>팀</label>` 제거 (line ~246-253).
  - `_applyInitialTeamFilter()` 함수 제거.
  - `loadKanban()`: `const teamId = document.getElementById('team-filter').value; localStorage.setItem('kanban_team_filter', teamId); const url = teamId ? '/api/kanban?team_id='+teamId : '/api/kanban';` → 단순히 `const url = '/api/kanban';` (서버가 work_team_id 쿠키로 결정). `kanban_team_filter` localStorage 키 더 이상 안 씀 (purge는 안 해도 됨 — 그냥 안 읽음).
  - `_applyInitialTeamFilter()` 호출부(DOMContentLoaded 등) 제거.
  - `project-filter`, `kbn-my-only-btn`, `kanban-team-members`, member-chip 로직은 **그대로 유지**.
  - 페이지 라우트가 `teams=db.get_all_teams()` 를 넘기는데 (app.py:733) 더 이상 칸반에서 안 쓰면 — backend-dev가 그 인자를 빼도 되지만 (surgical 차원) **다른 템플릿도 쓰므로 라우트 시그니처는 건드리지 말 것**. 단지 kanban.html 에서 `{% for team in teams %}` 만 사라짐. (backend-dev가 `kanban_page`/`project_page` 의 `teams=` 인자를 제거할지는 선택 — 깔끔하지만 위험 낮은 한도에서. 안 해도 무방.)
- **`templates/project.html`** (간트):
  - `<select id="team-filter" onchange="loadData()">...{% for team in teams %}...</select>` 제거 (line ~340-345).
  - `LS_TEAM = 'proj_team_filter'` 상수 + `_applyInitialTeamFilter()` 함수 + line ~1386 호출 제거.
  - `loadData()` 의 `const teamId = document.getElementById('team-filter').value;` 및 그걸 쓰는 URL 조립 → team_id 빼고 서버 쿠키 의존 (line ~602). 간트 데이터 소스 = `/api/project-timeline` (team_id 옵션 → 안 보냄) + 기타. backend-dev의 #10 골격이 team_id 없으면 cookie로 resolve 하므로 그대로 동작.
  - nav 버튼·접기 등 나머지 유지.
- **`templates/calendar.html`**: grep 결과 `<select id="team`  드롭다운 없음 (CURRENT_USER.team_id 참조만 4곳 — 위 B에서 처리). 캘린더는 `/api/events` (team_id 옵션 안 보냄 — 이미 안 보내거나, 보낸다면 제거). calendar.html 의 events fetch URL 확인해서 team_id 파라미터 붙이는 게 있으면 제거.
- **`templates/doc_list.html`**: team filter 있으면(grep `team_id`) 동일하게 — 단 doc_list는 grep 결과 매칭이 8개 파일 목록에만 떴고 실제 select 드롭다운인지 frontend-dev가 확인. 있으면 제거, 없으면 무시.

### D. 변경 대상 파일

- `templates/base.html` (프로필 드롭다운 "팀 변경" + 라벨 + payload)
- `templates/kanban.html` (드롭다운·`_applyInitialTeamFilter`·`loadKanban` URL·`CURRENT_USER.team_id`→work_team_id)
- `templates/project.html` (드롭다운·`LS_TEAM`·`_applyInitialTeamFilter`·`loadData` URL)
- `templates/calendar.html` (`CURRENT_USER.team_id`→work_team_id 4곳, events fetch team_id 제거 시)
- `templates/doc_list.html` (있을 경우만)

### E. backend-dev 의존

- `GET /api/me/work-team` (현재 작업 팀 + 선택 가능 팀 목록) 엔드포인트 — backend-dev에게 추가 요청 (스펙 §C "GET도 추가하면 깔끔"). 없으면 `_ctx` 의 컨텍스트 변수로 받음. frontend-dev는 `backend_changes.md` 읽고 어느 쪽인지 확인.
- `current_user_payload` 에 work_team_id/name 합류는 `_ctx` 가 그 변수를 넘겨야 — backend-dev §E 완료 전제.

### F. 완료 후 `.claude/workspaces/current/frontend_changes.md` 기록

# 검증 (qa)

운영 서버 IP 자동 로그인 → 특정 사용자/다중 팀/admin 상태 브라우저 재현 불가 → **TestClient + 임시 DB** 가 적합. TestClient는 session 쿠키 + `work_team_id` 쿠키 set 가능, Set-Cookie 응답 헤더 inspect 가능, 다중 팀 사용자·admin 임시 DB 구성 가능. 새 spec 파일 `tests/phase84_work_team_cookie.py` (네이밍 컨벤션 준수 — phase80~83 다음).

시나리오:
- A. 첫 로드(쿠키 없음), 2개 approved 팀 멤버(joined_at 순) → SSR `GET /` → `work_team_id` Set-Cookie = joined_at 가장 이른 팀, `_ctx`/마크업에 그 팀 이름.
- B. 첫 로드(쿠키 없음), admin → SSR → Set-Cookie = 가장 작은 id 비삭제 팀.
- C. 유효 쿠키 present → 그 값 사용, Set-Cookie 없음(또는 같은 값) — 쿠키 값과 다른 갱신 없음 확인.
- D. 쿠키 present 인데 그 팀이 soft-deleted → SSR → 새 대표 팀으로 Set-Cookie 갱신.
- E. 쿠키 present 인데 사용자가 그 팀 멤버 아님(추방) → SSR → 새 대표 팀으로 Set-Cookie 갱신.
- F. `POST /api/me/work-team {team_id: <소속 팀>}` → 200 + Set-Cookie 그 값.
- G. `POST /api/me/work-team {team_id: <비소속 팀>}` (비admin) → 403.
- H. `POST /api/me/work-team {team_id: <삭제 예정 팀>}` → 4xx.
- I. F 이후 후속 `/api/events` / `/api/kanban` / `/api/checklists` / `/api/doc` / `/api/project-timeline` (team_id 파라미터 안 보냄) 가 새 팀 컨텍스트로 동작 (그 팀 데이터만, 다른 팀 데이터 미노출).
- J. **#10 회귀**: 명시 `?team_id=X` (소속 팀) 이 쿠키보다 우선 — `/api/kanban?team_id=X` 가 X 팀으로 동작. 비소속 X 명시는 무시·대표팀(쿠키) fallback (기존 `_work_scope` 동작 불변).
- K. **phase80~83 회귀**: 쿠키 없는 익명 요청 = pre-#15 경로 — `tests/phase80_landing_page.py`/`phase81_unassigned_user.py`/`phase82_team_portal.py`/`phase83_team_portal_loggedin.py` 전부 PASS 유지. 미배정 사용자 SSR `GET /` 는 `_ensure_work_team_cookie` 가 early-return 하므로 Set-Cookie 없음.
- L. (admin) 프로필 표시: admin SSR 페이지 마크업에 work_team_name + "(슈퍼유저)" 노출. admin의 `_work_scope` 는 여전히 `None` (전 팀 무필터 READ) — `/api/kanban` 이 admin에겐 전 팀 노출 (기존 동작 회귀).
- M. (선택) `GET /api/me/work-team` 추가됐으면: 비admin → 본인 소속 팀 목록 + 현재 작업 팀 / admin → 전체 비삭제 팀.
- 회귀: `tests/phase75*` 등 기존 import-time/단위 테스트 PASS. `tests/test_project_rename.py` 2 FAIL은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, 본 변경 무관) — 그대로 둠.
- `import app` OK 확인.

# todo.md 기록 (qa 또는 플래너 — 사이클 종료 시 반드시)

1. `### #15.` 섹션:
   - **구현(서버)** 5개 sub-task: `/api/me/work-team` POST·SSR 쿠키 검증·일반 사용자 대표 팀(joined_at)·admin fallback(첫 비삭제 팀)·무효 쿠키 무시·모든 팀 컨텍스트 API team_id+require_work_team_access (마지막은 #10에서 골격 + 쿠키 통합으로 연결) → 완료면 `[x]`.
   - **구현(프론트엔드)** 4개: 프로필 "팀 변경" 드롭다운·팀 선택 시 POST+reload·프로필 현재 작업 팀 이름(admin "슈퍼유저")·기존 칸반/간트/캘린더 드롭다운 제거 → `[x]`.
   - **검증** 2개: 첫 로그인 쿠키 없음→대표 팀 자동+Set-Cookie / 작업 팀 전환 시 캘린더·칸반·간트·문서·체크 새 팀 컨텍스트 → `[x]`.
   - 미완 sub-task 있으면 `[ ]` + 한 줄 사유.
2. "단위 사이클 기록" 표에 한 줄 추가 — 기존 행 형식 (날짜 / 항목 / 핵심 결과 / 산출물). 형식 참조: todo.md line 718 (`#14` 행).
3. "진행 추적 메모" 의 `[ ] 그룹 B 완료` 는 **그대로 unchecked** (#15-1/-2/-3 아직 남음).

# 주의사항

- **마이그레이션 phase 추가 없음** — 신규 DB 헬퍼는 전부 SELECT 전용. 스키마 무변경 → 운영 DB는 마이그레이션 불필요, 단 코드 reload용 **서버 재시작 필요**.
- `_work_scope` 의 admin `None` 반환·explicit team_id 우선 로직은 #10 핵심 — **건드리지 말 것**. 쿠키 검증은 `resolve_work_team` 안에서만.
- 쿠키 없이 명시 `team_id` 만 보내던 #10 검증 테스트는 그대로 통과해야 함 (J 시나리오).
- 임시 산출물(스크린샷·로그·diff)은 `.claude/workspaces/current/` 하위에만. 루트에 PNG/JSON/log 직접 생성 금지.
- frontend-dev: `teams` 컨텍스트 변수는 admin.html·기타 여러 템플릿이 공유 — 라우트 시그니처에서 빼지 말 것. kanban.html/project.html 에서 `{% for team in teams %}` 만 제거.
- backend-dev: `user_teams` 의 joined_at 컬럼 실제 이름 grep으로 확인 (todo §#1 비고: 실제 컬럼이 `role`/`status`로 명세와 다름 — joined_at도 다를 수 있음). 없으면 `created_at` 또는 rowid 순으로 대체하고 backend_changes.md 에 명시.
