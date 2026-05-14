# 비로그인 진입 화면 재설계 + `/팀이름` 화면 재구성

## 분류
기능 추가 + UI 재설계 (catchup) / **팀 모드** (backend + frontend + reviewer + qa)

## 확정 정책 결정 (사용자 결정 그대로 + advisor 검토 반영)

1. **헤더 메뉴 분기 정책 (base.html)**
   - 로그인 사용자: 기존 nav 유지 (전역 `/doc`, `/check`, `/kanban`, `/gantt` 노출).
   - 비로그인 + 팀 컨텍스트 없음 (예: `/`, `/register`): nav에서 문서/체크/칸반/간트 **모두 제거** (미니멀).
   - 비로그인 + 팀 컨텍스트 (`/{팀}` 또는 `/{팀}/{메뉴}`): 해당 팀의 `team_menu_settings` 에서 외부공개=true 인 메뉴(`kanban`/`gantt`/`doc`/`check`)만 nav에 노출. 링크 target은 `/{팀}/{한글키}` (사용자 결정: A안 path 형식).
   - 컨텍스트 전달: `_ctx()`에 `portal_team`(dict 또는 None) + `portal_menu`(dict 또는 None) 추가.

2. **`/{team_name}` 기본 화면 정책**
   - 외부공개 메뉴가 1개 이상: 그 팀의 **첫 외부공개 메뉴**로 본문 렌더 (kanban → gantt → doc → check 순서로 우선). 라우트는 그대로 `/{team_name}` 유지 (redirect 안 함 — UX 일관). 본문은 메뉴별 콘텐츠 partial 렌더 (별도 `.portal-tabs` 탭 영역 제거).
   - 외부공개 메뉴가 0개: "현재 공개된 콘텐츠가 없습니다" 안내 + 계정 가입 버튼만 유지 (404 아님).
   - 삭제 예정 팀: 기존 안내 페이지 유지.
   - **invariant 보존**: `team_portal.html` 의 hero-sub "공개 포털 — 공개 설정된 항목만 ..." 문구는 그대로 유지 (phase82 line 138/234 assert).

3. **신규 라우트 4종** (`/{team_name}/{한글메뉴}`)
   - URL path 세그먼트는 **한글**(사용자 결정): `/{team_name}/칸반`, `/간트`, `/문서`, `/체크`.
   - `_TEAM_NAME_RE = ^[A-Za-z0-9_]+$` 는 segment 1(team_name)만 제약 — segment 2(한글 메뉴 키)에는 영향 없음. FastAPI/Starlette 는 UTF-8 path를 디코드해 핸들러에 전달함.
   - 4개 개별 라우트로 등록 (parameterized + enum 보다 명시적 + 잠식 가능성 적음).
   - 핸들러는 공통 헬퍼 `_render_team_menu(request, team_name, menu_key)` 호출.
   - 각 라우트는 해당 팀이 그 메뉴를 외부공개로 켜놨는지 확인 → 안 켜놨으면 404.
   - 권한 모델: viewer=None 공개 portal context (그룹 A #10) 유지.
   - **reserved-set 검증**: `_build_reserved_team_paths` 가 walk 하는 `path.strip("/").split("/", 1)[0]` 은 `/{team_name}/칸반` 의 경우 `{team_name}` 이 `{` 포함이라 자동 skip — 칸반/간트/문서/체크가 RESERVED 에 안 끼는지 테스트 invariant 로 추가.
   - **링크 빌드 일관성**: home.html 의 `{{ team.name | urlencode }}` 패턴을 따라 모든 곳에서 `{{ '칸반' | urlencode }}` 식으로 인코딩 (또는 raw — 둘 중 하나로 통일). 본 변경은 **urlencode 사용** 으로 결정 (브라우저는 자동 디코드, 서버는 디코드 후 매칭).

4. **로그인 사용자가 `/{팀}` 또는 `/{팀}/메뉴` 진입 시**
   - 그룹 B #14 기존 동작 보존 = redirect 없음, 같은 포털을 200으로 노출, 우상단 버튼 분기(my_team_status)만 다름. 본 변경에서도 동일하게 유지.
   - 로그인 사용자가 `/{팀}/문서` 등에 진입하면, 헤더 nav는 **로그인 nav** (`/doc` 글로벌)을 그대로 사용. `request.url.path.startswith('/doc')` 등 active-state 매칭은 한글 path엔 안 걸리지만 active 표시만 빠질 뿐 동작은 정상 — 의식적 수용 (active state는 본 변경 범위 밖).

5. **`/admin/teams` 미리보기**
   - 기존 admin.html 팀 탭(`tab-teams`) **내부 섹션**으로 추가 (별도 페이지 분기 비용 회피).
   - 표시 내용: "비로그인 사용자가 보는 / 진입 화면 미리보기" — 단순히 `get_visible_teams()` 결과를 카드 그리드로 노출 + 각 팀 이름이 `/{팀}` 로 링크. 별도 백엔드 데이터 추가 없음 (이미 `teams` 컨텍스트 있음).
   - SSR 렌더(JS gate 없음). admin이 탭을 클릭하면 보이는 구조는 그대로 유지.

## 영향 파일

### backend (`app.py`, `database.py`)
- `app.py` `_ctx()`: `portal_team` (dict|None), `portal_menu` (dict|None) 추가.
- `app.py` index() (`/`): 변경 없음 (home.html이 view-guest 그대로 노출, base.html nav만 미니멀화).
- `app.py` `team_public_portal`: `active_menu` 결정 (kanban > gantt > doc > check 우선, 켜진 게 0개면 None), 템플릿에 `active_menu` 추가. `_ctx`에 `portal_team`+`portal_menu` 주입.
- `app.py` 신규 라우트 4개: `/{team_name}/칸반`, `/{team_name}/간트`, `/{team_name}/문서`, `/{team_name}/체크` (등록 순서: `/{team_name}` 직전).
  - 공통 헬퍼: `_render_team_menu(request, team_name, menu_key)` — team 검증 + menu_settings 확인 + 404 처리 + 템플릿 렌더.
- `database.py` 변경 없음 (헬퍼 재사용).

### frontend (`templates/*.html`)
- `base.html` `<nav>` (line 386~395): 분기 로직 추가.
  - 문서/체크/칸반/간트 각각: `{% if user %}` (전역 nav) **OR** `{% if portal_team and portal_menu.<key> %}` (팀 컨텍스트 nav, `href="/{팀}/{한글키 urlencode}"`).
  - 우선순위: 로그인이면 전역 nav, 비로그인이면 portal_team 분기, 둘 다 아니면 비노출.
- `home.html` view-guest: 거의 그대로. 사용자 요청은 "헤더 메뉴 제거" 측면이므로 본문은 변동 적게.
- `team_portal.html`:
  - 별도 `.portal-tabs` 영역 **제거**.
  - `active_menu` 1개에 대한 단일 패널 렌더 (기존 panel CSS 재사용, `display: block` 으로).
  - menu_settings 0개 → "현재 공개된 콘텐츠가 없습니다" 안내 추가.
  - hero-sub "공개 포털 — 공개 설정된 항목만 ..." 문구 유지 (phase82 보존).
- `admin.html` `tab-teams`: 표 위에 "비로그인 진입 화면 미리보기" 섹션 추가 (카드 그리드).

## 라우트 순서 invariant
- 신규 4개 라우트 → `/{team_name}` 라우트 → 모두 정적 페이지 라우트 뒤.
- 신규 4개는 `/{team_name}` 보다 위에 등록 (더 구체적 경로 우선이라 FastAPI 매칭 순서상 이상 없음. but 명시적으로 4개를 5518 직전에 둔다).

## 테스트 (TestClient 신규 슈트 `phase99_unauth_redesign.py`)
1. **정적 invariant**
   - app.py: `/{team_name}/칸반`, `/간트`, `/문서`, `/체크` 라우트 존재. 4개 모두 `/{team_name}` 라우트보다 위(소스상 앞).
   - app.py: `_build_reserved_team_paths` 호출 결과에 `"칸반".casefold()`, `"간트".casefold()`, `"문서".casefold()`, `"체크".casefold()` 가 **없음** (reserved 잠식 방지).
   - base.html nav에 portal_team/portal_menu 분기 마커 존재.
   - admin.html `tab-teams` 에 비로그인 미리보기 섹션 마커 존재.
   - team_portal.html: `portal-tabs` 클래스(별도 탭 영역) 제거 확인, `active_menu` 사용.
2. **TestClient 동작 (임시 DB, phase82 패턴)**
   - 비로그인 `/` → 200, 헤더 nav HTML 에 `href="/doc"`, `href="/check"`, `href="/kanban"`, `href="/gantt"` 부재.
   - 비로그인 `/ABC` (default 메뉴 4개 ON) → 200, 헤더 nav에 4개 메뉴 href 가 `/ABC/...` 한글 형태로 빌드. 첫 패널(kanban) 본문 노출. `class="portal-tabs"` 부재. `"공개 포털 — 공개 설정된 항목만"` 문구 유지.
   - 비로그인 `/ABC/칸반` → 200, 칸반 콘텐츠. `/간트`, `/문서`, `/체크` 동일.
   - 비로그인 `/ABC/캘린더` (이 라우트 없음) → 404. 추가: 신규 4개 라우트는 영어 키(`/ABC/kanban`)로도 매칭되지 않아야 함 (404).
   - 메뉴 모두 OFF 인 팀 `/EmptyTeam` → 200 + "공개된 콘텐츠가 없습니다" 안내 + 헤더 nav 빈 채.
   - 메뉴 모두 OFF 인 팀 `/EmptyTeam/칸반` → 404.
   - 삭제 예정 팀: 기존 안내만, 헤더 nav 빈 채.
   - 로그인 사용자 `/ABC` → 200, redirect 없음, 헤더 nav 는 전역 (`/doc` 등) — phase82 line 232 invariant 유지.
   - 예약어 `/admin`, `/api/...` 잠식 없음 (phase82 회귀).
3. **회귀**
   - phase81~98 전체 PASS.

## 주의사항
- 한글 URL 사용 — Jinja `{{ '칸반' | urlencode }}` 패턴 통일. 라우트 데코레이터는 raw 한글 `@app.get("/{team_name}/칸반")` (Starlette가 매칭 시 디코드해 비교).
- 서버 재시작 필요 (라우트 추가) — 코드 완료 후 사용자에게 명시 요청.
- active-state 매칭(active class)은 본 변경 범위 밖 (advisor 권고대로 의식적 수용).
