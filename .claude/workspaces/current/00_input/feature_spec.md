# 요청

'팀 기능 구현 todo.md' 그룹 B #13 (`/팀이름` 비로그인 공개 포털) 한 항목만 끝까지 진행.
상세 사양: '팀 기능 구현 계획.md' 섹션 4(URL 정책)·7(비로그인 URL 동작)·9(공개 포털 데이터 노출 정책).
그룹 A(#1~#10) + 그룹 B #11(커밋 ae7a74e)·#12(커밋 fd1003a) 완료됨. #11에서 비로그인 `/` 랜딩의 팀 카드가 `<a href="/{{ team.name | urlencode }}">`로 걸려 있는데 아직 `/팀이름` 라우트가 없어 404 — #13이 그 라우트를 만든다.

# 분류

백엔드 수정 → 라우트·필터 (backend) + 공개 포털 템플릿 (frontend) → code-review → qa.
실행 모드: 팀 모드 (backend → frontend → reviewer → qa, 순차).

# 핵심 설계 결정 (사전 advisor 검토 반영)

1. **라우트 등록 순서가 1차 메커니즘.** FastAPI/Starlette는 등록 순서대로 매칭, 첫 매치 승리. `/{team_name}` 동적 라우트는 `app.py`의 **모든 정적 페이지 라우트보다 뒤에** 등록해야 한다. 정적 페이지 라우트 목록(현재 `app.py`): `/` `/calendar` `/register` `/admin` `/kanban` `/gantt` `/project-manage` `/doc` `/doc/new` `/doc/{meeting_id}` `/doc/{meeting_id}/history` `/ai-import` `/changelog` `/alarm-setup` `/settings/mcp` `/notice` `/notice/history` `/check` `/check/new/edit` `/check/{checklist_id}/edit` `/check/{checklist_id}/history` `/trash` `/remote` `/avr` + `/favicon.ico` `/api/health` `/healthz` `/api/cert/rootCA.zip`. `/api/*` 라우트는 `/{team_name}`이 단일 세그먼트라 충돌 없음(다만 안전을 위해 `/{team_name}`을 `/api/*` 블록 **뒤**에 둬도 무방 — 가장 안전한 위치는 모든 `@app.get/post/...` 데코레이터 라우트 정의가 끝난 직후, 즉 `app.py` 라우트 정의 영역 맨 끝). FastAPI 자동 문서 `/docs` `/redoc` `/openapi.json`은 `app = FastAPI(...)`(line 153)에서 등록 — 모든 유저 라우트보다 앞.
   - **하드코딩 예약어 목록은 방어선(defense-in-depth)이지 1차 메커니즘 아님.**

2. **핸들러 결정 트리 — 404를 3가지 경우에 동일 의미로 반환:**
   - 이름이 정규식 `^[A-Za-z0-9_]+$` 불일치 → 404 (핸들러 안에서 검사; `Path(pattern=...)`는 422를 내므로 쓰지 않음)
   - 이름이 RESERVED 집합에 속함 (casefold 비교) → 404
   - DB 대소문자 정확 일치 조회(`SELECT * FROM teams WHERE name = ?`)가 빈 결과 → 404 (`teams.name`은 SQLite 기본 BINARY collation이라 `name = ?`는 대소문자 구분 — `/ABC` 유효 시 `/abc`는 자연히 404)
   - row 있음 + `deleted_at IS NOT NULL` → 삭제 예정 안내 페이지 (가입 버튼·팀 신청·공개 데이터 모두 비노출)
   - 그 외 → 공개 포털 페이지

3. **로그인 사용자도 라우트 레벨에서 분기하지 않음** — 항상 공개 포털 200(redirect 없음). "계정 가입" 버튼은 템플릿에서 `not user`일 때만. "팀 신청 / 가입 대기" 버튼 분기는 #14로 미룸(템플릿에 주석으로 명시).

4. **데이터 필터는 메뉴 설정과 독립** (계획서 섹션 9). 메뉴 노출 설정 = UI 진입(탭/링크) 차단만. `team_menu_settings` 시드는 #19 책임 → 현재 모든 팀에서 빈 상태. 따라서 `db.get_team_menu_visibility(team_id) -> dict`를 추가하되, 행이 없으면 계획서 섹션 9 기본값 fallback: `{"kanban": True, "gantt": True, "doc": True, "check": True, "calendar": False}`. 코드 주석: "interim default — #19가 team_menu_settings 시드 추가 시 그 값을 우선". 프론트는 이 dict 기준으로 탭 링크를 조건부 렌더; **데이터 필터에는 절대 넣지 않음** (캘린더 메뉴 OFF여도 같은 events가 칸반/간트 탭에 나오는 게 의도된 동작).

5. **히든 프로젝트(`is_hidden=1`) 항목은 모든 채널에서 완전 차단** (`is_public` 값 무관). 이미 기존 DB 함수들이 `viewer is None`일 때 `is_private = 1 OR is_hidden = 1` 프로젝트 제외 SQL을 적용 → 재사용한다.

# backend-dev 담당 작업

## 1. `database.py` — 헬퍼 추가 (SELECT만 — 마이그레이션 phase 추가 금지)

- `get_team_by_name_exact(name: str) -> dict | None` — `SELECT * FROM teams WHERE name = ? LIMIT 1` (대소문자 정확 일치, 삭제 예정 팀도 포함해서 반환 — `deleted_at` 판정은 라우트가 함). 이미 비슷한 게 있으면 재사용.
- `get_team_menu_visibility(team_id: int) -> dict` — `team_menu_settings`에서 `menu_key→enabled` 읽어 dict 빌드, 없는 키는 기본값(`kanban/gantt/doc/check` True, `calendar` False)으로 채움. 코멘트로 #19 시드 의존 명시.
- 공개 포털 데이터 집계 — **새 aggregator 함수 1개를 권장** (이름 예: `get_public_portal_data(team_id: int) -> dict`):
  - `kanban`: `get_kanban_events(team_id, viewer=None)` 재사용 (이미 `is_public` + private/hidden 프로젝트 제외 SQL 내장 — `viewer is None and team_id` 경로 확인됨).
  - `gantt`: `get_project_timeline(team_id, viewer=None)` 재사용 (`viewer is None`이면 `is_public==0` 제외, `is_public is None`이면 private/미지정 프로젝트 제외, 히든 프로젝트 제외 — 확인됨).
  - `docs`: 기존 `get_all_meetings(viewer=None)`은 **전 팀** 공개 문서를 주므로 그대로 쓰면 안 됨. team_id 필터가 필요 → ① `get_all_meetings`에 `team_id` 파라미터를 추가(None이면 기존 동작 유지)하거나, ② aggregator 안에서 `m.team_id = ?`로 직접 조회하는 작은 쿼리를 새로 작성. 둘 중 더 surgical한 쪽 선택. 조건: `deleted_at IS NULL AND is_public = 1 AND team_id = ? AND is_team_doc = 1` (개인 문서 `is_team_doc=0`은 포털에 노출 안 함 — 팀 자료가 아님). **추가로 히든 프로젝트는 문서엔 직접 연결 없음**(문서엔 project 컬럼 없음)이므로 별도 처리 불필요.
  - `checks`: 기존 `get_checklists(viewer=None)`은 `is_public` + private/hidden 프로젝트 제외는 하지만 `team_id` 필터는 안 함 → ① `get_checklists`에 `team_id` 파라미터 추가(None=기존), 또는 ② aggregator에서 결과를 받아 `r["team_id"] == team_id`로 Python 필터 (간단). 둘 중 surgical한 쪽. 히든 프로젝트는 `get_checklists`의 `public_filter`(`is_private = 1 OR is_hidden = 1` 제외) + `get_blocked_hidden_project_names(None)`로 이미 차단됨 — 확인하고 재사용.
  - `calendar`: 캘린더 탭 데이터는 칸반/간트와 같은 events 풀에서 온다(계획서 섹션 9). 별도 events 조회를 둘지(예: `_filter_events_by_visibility(db.get_all_events_for_team(team_id), None, scope_team_ids={team_id})`) 또는 칸반 데이터 재활용할지는 backend 판단. 단 캘린더 메뉴는 기본 OFF라 프론트가 탭을 안 그릴 수 있음 — 데이터는 채워두되 UI 진입은 메뉴 설정에 맡김.
  - `menu`: `get_team_menu_visibility(team_id)` 결과.
- aggregator는 **항상 team_id 단일 팀 기준**, viewer는 항상 None(공개 portal context). admin/로그인 사용자가 와도 동일 데이터 — URL은 권한 경계가 아님(계획서 섹션 7 마지막).

## 2. `app.py` — 라우트 + 예약어

- 모듈 상단(또는 `RESERVED_USERNAMES` 근처)에 `RESERVED_TEAM_PATHS` frozenset 정의 — 계획서 섹션 4 목록 전부:
  ```
  api, admin, doc, check, kanban, gantt, calendar, mcp, mcp-codex, uploads, static,
  settings, changelog, register, project-manage, ai-import, alarm-setup, notice,
  trash, remote, avr, favicon.ico, docs, redoc, openapi.json, healthz
  ```
  (모두 lowercase로 저장, 비교 시 `name.casefold()`로). 추가로 `healthz`·`api/health`의 첫 세그먼트 `api`는 이미 포함. **권장**: 정의 직후 또는 lifespan에서 `{r.path.strip("/").split("/")[0] for r in app.routes if isinstance(r, APIRoute)}`로 실제 등록된 라우트 첫 세그먼트를 모아 `RESERVED_TEAM_PATHS`에 합집합으로 보강 — 누락 자동 방지(advisor 권장). 단순화를 위해 합집합 보강이 까다로우면 하드코딩만으로도 검증 통과는 가능 — backend 판단.
- 새 라우트 (모든 정적 페이지 라우트 정의 이후, `app.py` 라우트 정의 영역 맨 끝 — 예: `/api/me/mcp-token` 관련 라우트들 뒤):
  ```python
  import re as _re   # 이미 import re 있으면 재사용
  _TEAM_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

  @app.get("/{team_name}", response_class=HTMLResponse)
  def team_public_portal(request: Request, team_name: str):
      # #13: /팀이름 비로그인 공개 포털. URL은 권한 경계 아님 — 항상 공개 portal context.
      if not _TEAM_NAME_RE.match(team_name) or team_name.casefold() in RESERVED_TEAM_PATHS:
          raise HTTPException(status_code=404, detail="Not Found")
      team = db.get_team_by_name_exact(team_name)
      if not team:
          raise HTTPException(status_code=404, detail="Not Found")
      if team.get("deleted_at"):
          # 삭제 예정 팀: 안내만 (가입 버튼·팀 신청·공개 데이터 모두 비노출)
          return templates.TemplateResponse(request, "team_portal.html",
                  _ctx(request, team=team, deleted=True))
      data = db.get_public_portal_data(team["id"])
      return templates.TemplateResponse(request, "team_portal.html",
              _ctx(request, team=team, deleted=False, portal=data))
  ```
  - `_ctx`가 이미 `user`를 채우므로 템플릿이 `not user`로 "계정 가입" 버튼을 조건부 렌더 가능. 로그인 사용자에게도 200 공개 포털을 주되 redirect 안 함(#13 범위).
  - `404`는 `HTTPException(404)` — FastAPI 기본 `{"detail": "Not Found"}` JSON 응답 (기존 패턴과 일관).

## 3. 주의

- 스키마 무변경, 마이그레이션 phase 추가 금지. DB 함수는 SELECT 전용.
- 기존 라우트·DB 함수의 시그니처를 깰 변경 금지 — `team_id` 파라미터를 추가한다면 keyword-only/default None으로 추가해 기존 호출부 무영향.
- 완료 후 `.claude/workspaces/current/backend_changes.md` 기록.

# frontend-dev 담당 작업

## 새 템플릿 `templates/team_portal.html`

- `base.html` 확장 여부: 기존 페이지 템플릿이 `base.html`을 extends 하는지 확인 후 동일 패턴 따름 (헤더·CSRF·`CURRENT_USER` 등 공통 인프라 재사용). 단 공개 포털은 비로그인도 보므로 base의 알림 벨/사용자 패널 등은 `not user`면 자연히 숨겨짐(이미 `is_unassigned`/`CURRENT_USER` 게이팅 존재).
- 구조:
  - **삭제 예정 분기** (`deleted` True): 페이지 상단에서 `{% if deleted %}` 짧은 안내 블록만 — "이 팀은 삭제 예정입니다" 류 메시지. **계정 가입 버튼·팀 신청·공개 데이터 모두 비노출.** admin이면 `/admin` 관리 링크 추가(계획서 섹션 7 표 — `{% if user and user.role == 'admin' %}`). 그 외엔 `{% else %}` 본문.
  - **본문** (정상 팀):
    - 헤더: 팀 이름 표시 + 홈 버튼(`/`로 이동) + (`{% if not user %}`) "계정 가입" 버튼 → `/register` (가입 후 자동 로그인 + `/`로 이동은 기존 #8 동작).
    - **#14 자리 표시 주석**: `{# 로그인 사용자용 "팀 신청 / 가입 대기 중" 버튼 분기는 #14 범위 #}` — #13에선 구현하지 않음.
    - 탭 네비게이션: `portal.menu` dict 기준 조건부 렌더 — `{% if portal.menu.kanban %}` 칸반 탭, `gantt` 간트 탭, `doc` 문서 탭, `check` 체크 탭, `calendar` 캘린더 탭(기본 OFF라 안 그려질 것). 탭 전환은 in-page JS(클릭 시 `display` 토글) — 서브 라우트(`/팀이름/calendar`) 만들지 않음(충돌 위험·범위 밖).
    - 각 탭 패널:
      - 칸반: `portal.kanban` 항목들을 읽기 전용 카드 목록으로 간단히 (기존 `home.html`의 `cardHTML`/`buildBoard`를 참고하되, 공개 포털은 단순 목록으로도 충분 — 과하게 만들지 말 것. 제목·프로젝트·날짜 정도).
      - 간트: `portal.gantt`(팀→프로젝트→일정 2단계 구조) 간단 목록 — 풀 간트 차트 라이브러리까지 끌어올 필요 없음. 프로젝트별 일정 리스트면 충분.
      - 문서: `portal.docs` 제목 + 작성자 + `updated_at[:10]` 목록 — **링크는 비활성 또는 제목만** (공개 포털 비로그인은 `/doc/{id}` 접근 시 어차피 가시성 검증으로 막힘; #13에선 목록 노출까지만, 클릭 동작은 깊게 안 감 — 단순 텍스트 표시).
      - 체크: `portal.checks` 제목 + 프로젝트 + `updated_at[:10]` 목록 (마찬가지로 텍스트).
      - 캘린더(렌더되면): `portal.calendar` 간단 목록 또는 FullCalendar — 기본 OFF라 우선순위 낮음. 단순 목록 OK.
    - 빈 데이터: "공개된 항목이 없습니다" 류 메시지.
- **임시 스크린샷은 `.claude/workspaces/current/screenshots/` 하위에만** 저장 (루트 금지 — CLAUDE.md 정책).
- 완료 후 `.claude/workspaces/current/frontend_changes.md` 기록.

# code-reviewer / qa

- code-review: `*_changes.md` + 변경 파일 정적 리뷰. 특히 ① 라우트 등록 순서(`/{team_name}`이 모든 정적 페이지 라우트 뒤인지) ② 예약어 casefold 비교 ③ 정규식 검사가 핸들러 안 (Path pattern 아님) ④ 데이터 필터가 메뉴 설정과 독립 ⑤ 삭제 예정 팀이 데이터·가입 버튼 비노출 ⑥ DB 함수 시그니처 호환성 ⑦ 마이그레이션 phase 추가 없음.
- qa: **TestClient(임시 DB)로 익명 요청 검증** (운영 서버는 IP 자동 로그인이라 비로그인 브라우저 재현 불가, TestClient `testclient` IP는 whitelist 미매칭이라 익명). 검증 매트릭스:
  - `GET /ABC`(대문자 팀 생성 후) → 200, 팀 이름 포함, "계정 가입" 포함
  - `GET /abc` → 404 (대소문자 불일치 분리)
  - `GET /Nonexistent` → 404
  - `GET /admin` → admin 로그인 페이지 (eclipse 안 됨)
  - `GET /api/health` → 200
  - `GET /docs` → 200, `GET /redoc` → 200, `GET /openapi.json` → 200
  - `GET /static/<존재하는 파일>` → 404 아님 (mount 살아있음)
  - 예약어 각각(`api`, `admin`, `docs`, `redoc`, `openapi.json`, `kanban`, `check`, `doc`, `notice`, `register`, ...) `GET /<예약어>` → 404 또는 해당 정적 라우트 응답(어쨌든 포털 아님)
  - `GET /Bad-Name`(하이픈 등 정규식 불일치) → 404
  - 공개 포털: `is_public=0` 일정·체크·문서가 응답 마크업에 안 나옴
  - 공개 포털: 히든 프로젝트(`is_hidden=1`) 하위 항목은 `is_public=1`이어도 안 나옴
  - 삭제 예정 팀: 안내 페이지, "계정 가입" 버튼 없음, 공개 데이터 없음
  - 로그인 사용자가 `GET /팀이름` → 200 포털 (redirect 안 됨)
  - 회귀: `tests/phase80_landing_page.py`(#11), `tests/phase81_unassigned_user.py`(#12) 여전히 PASS
  - 네이밍: `tests/phase82_team_portal.py` (todo 컨벤션 `tests/phaseN_*.spec.js` — 단 이 프로젝트는 Python TestClient 패턴이라 기존 `tests/phase8X_*.py` 따름)
- 서버 재시작 필요 여부를 결과에 명시 (코드 reload — 스키마 무변경이라 마이그레이션 불필요. 단 단위 검증은 TestClient로 완료).

# 범위 밖 (불변)

- #14: 로그인 사용자용 "팀 신청 / 가입 대기 중" 버튼 분기, 로그인 상태별 포털 UI
- #15: `work_team_id` 쿠키
- #19: `team_menu_settings` 기본값 시드 (이번엔 빈 상태 + fallback 기본값으로 동작)
- 한글 이름 레거시 팀: 정규식 `^[A-Za-z0-9_]+$` 불일치로 404 — 계획서 의도된 동작 (rename/마이그레이션은 범위 밖). #11의 `team.name | urlencode` 카드는 유효 팀명에 대해선 사실상 no-op.
