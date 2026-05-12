# 백엔드 변경 — 팀 기능 그룹 B #13 (`/팀이름` 비로그인 공개 포털)

스키마 무변경. 마이그레이션 phase 추가 없음. 신규 DB 함수는 SELECT 전용. 기존 함수 시그니처 불변.

## database.py — 신규 헬퍼 (모두 `get_team_active` 직후에 추가)

- `get_team_by_name_exact(name) -> dict | None` — `SELECT * FROM teams WHERE name = ? LIMIT 1`.
  `teams.name`은 SQLite 기본 BINARY collation → `name = ?`는 대소문자 구분 (`/ABC` 유효 시 `/abc`는 None → 라우트 404). 삭제 예정 팀(`deleted_at IS NOT NULL`)도 그대로 반환 — 안내 페이지를 보여줘야 하므로 deleted 판정은 라우트가 함.
- `_PORTAL_MENU_DEFAULTS` 모듈 상수 — 계획서 섹션 9 기본값: `{kanban: True, gantt: True, doc: True, check: True, calendar: False}`. `team_menu_settings` 시드는 #19 책임 → 그때까지의 임시 기본값.
- `get_team_menu_visibility(team_id) -> dict` — `team_menu_settings`에서 `menu_key→enabled` 읽어 dict 빌드, 없는 키는 기본값 fallback. 의미는 "공개 포털 UI 진입(탭/링크) 차단"일 뿐 데이터 차단 아님(계획서 섹션 9).
- `get_public_portal_data(team_id) -> dict` — 공개 포털이 노출할 팀 데이터 집계. 항상 단일 팀 + `viewer=None`(공개 portal context — URL은 권한 경계 아님, 계획서 섹션 7).
  - `kanban`: `get_kanban_events(team_id, viewer=None)` 재사용 — `viewer is None & team_id` 경로가 이미 `is_public=1` + 외부 비공개(`is_private=1`)·히든(`is_hidden=1`) 프로젝트 제외 SQL 내장.
  - `gantt`: `get_project_timeline(team_id, viewer=None)` 재사용 — `viewer is None`이면 `is_public==0` 제외, `is_public is None`이면 private/미지정 프로젝트 제외, 히든 프로젝트 제외.
  - `checks`: `get_checklists(viewer=None)`로 전 팀 공개 체크를 받아 Python에서 `team_id` 필터 (`get_checklists`엔 team_id 인자 없음). `get_checklists`의 `public_filter` + `get_blocked_hidden_project_names(None)`이 이미 `is_public` + private/히든 프로젝트 차단.
  - `calendar`: 칸반과 같은 events 풀 — 별도 시각화일 뿐(계획서 섹션 9). 기본 메뉴 OFF라 프론트가 탭을 안 그릴 수 있지만 데이터는 채워둠. `list(kanban)`.
  - `docs`: 팀 공개 팀-문서만 — `meetings WHERE deleted_at IS NULL AND team_id = ? AND is_public = 1 AND is_team_doc = 1` (개인 문서 `is_team_doc=0`은 팀 자료가 아니므로 포털 비노출). `author_name` JOIN.
  - `menu`: `get_team_menu_visibility(team_id)` 결과.

## app.py — 라우트 + 예약어 (uvicorn 부트스트랩 직전 = 모든 라우트 정의 영역 맨 끝)

- `_TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")` — 팀 이름 규칙(계획서 섹션 4).
- `_RESERVED_TEAM_PATHS_BASE` frozenset — 계획서 섹션 4 예약어 전부 (`api, admin, doc, check, kanban, gantt, calendar, mcp, mcp-codex, uploads, static, settings, changelog, register, project-manage, ai-import, alarm-setup, notice, trash, remote, avr, favicon.ico, docs, redoc, openapi.json, healthz`).
- `_build_reserved_team_paths()` — 하드코딩 목록 + 실제 등록된 라우트(`app.routes`)의 첫 경로 세그먼트(파라미터 세그먼트 제외)를 casefold하여 합집합 → `RESERVED_TEAM_PATHS` (등록 직후 1회 계산). 누락 자동 방지(advisor 권장).
- `@app.get("/{team_name}", response_class=HTMLResponse) def team_public_portal(request, team_name)`:
  1. 정규식 불일치 **또는** `team_name.casefold() in RESERVED_TEAM_PATHS` → `HTTPException(404, "Not Found")` (정규식 검사는 핸들러 안에서 — `Path(pattern=...)`는 422를 내므로 안 씀).
  2. `db.get_team_by_name_exact(team_name)`이 None → 404.
  3. `team["deleted_at"]` 있음 → `team_portal.html` 렌더(`deleted=True`) — 가입 버튼·팀 신청·공개 데이터 모두 비노출.
  4. 그 외 → `db.get_public_portal_data(team["id"])` → `team_portal.html`(`deleted=False`, `portal=...`).
  - 로그인 사용자·admin이 와도 동일한 200 공개 포털을 주되 redirect 안 함(#13 범위). "팀 신청 / 가입 대기 중" 등 로그인 사용자 UI 분기는 #14.
  - **라우트 등록 위치가 1차 메커니즘**: `/{team_name}`은 단일 세그먼트 catch-all이므로 모든 정적 페이지 라우트(`/`, `/calendar`, `/admin`, `/kanban`, ... `/trash`, `/remote`, `/avr` 등)보다 *뒤*에 등록해야 한다. FastAPI/Starlette는 등록 순서대로 매칭, 첫 매치 승리 → app.py 맨 끝에 배치. `/docs` `/redoc` `/openapi.json`은 `app = FastAPI(...)`(line 153) 시점 등록이라 자연히 우선. 하드코딩 예약어 목록은 defense-in-depth.

## 검증

- `import app` OK (실제 Python 3.12로 확인). `/{team_name}` 단일 라우트 등록 확인. `RESERVED_TEAM_PATHS` 26개+ (하드코딩 + 실제 라우트 첫 세그먼트 합집합).
- 단위 검증은 qa(TestClient + 임시 DB) 참조.

## 서버 재시작

운영 서버 반영 시 **재시작 필요** (app.py / database.py 코드 reload). 스키마 무변경 → 마이그레이션 불필요. 단 본 사이클 단위 검증은 TestClient(임시 DB)로 완료.
