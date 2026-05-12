## 코드 리뷰 보고서 — #11 `/` 비로그인 접속 화면

### 리뷰 대상 파일
- `app.py` — `index()` 라우트 (line ~690)
- `database.py` — `get_visible_teams()` 신규 (line ~4546)
- `templates/home.html` — `#view-guest` 블록, CSS, JS 정리

### 차단(Blocking) ❌
- 없음

### 경고(Warning) ⚠️
- 없음 (참고: 팀 카드 → `/팀이름` 링크는 #13 라우트 미구현으로 현재 404. 의도된 단계적 상태로, 결함 아님.)

### 통과 ✅
- [x] `_ctx()` 사용: `templates.TemplateResponse(request, "home.html", _ctx(request, teams=teams))` 유지
- [x] SQL 파라미터화: `get_visible_teams()`는 상수 쿼리, 사용자 입력 없음
- [x] DB 경로: 변경 없음 (DB 접근 헬퍼 `get_conn()` 그대로 사용)
- [x] 권한 체크: `/` 라우트는 누구나 접근 (사양상 비로그인 진입점). 권한 체크 불필요. `RedirectResponse` import는 다른 곳에서도 쓰이므로 고아 아님
- [x] `get_all_teams()` 시그니처/동작 미변경 — 다른 라우트 영향 없음
- [x] Jinja2 자동 이스케이프: 팀 이름 `{{ team.name }}` 본문 출력은 자동 이스케이프, href는 `| urlencode` 필터로 인코딩 → XSS/경로 조작 방어
- [x] 고아 코드 정리: `loadGuest()`, `.home-toolbar*` CSS, `guest-board/guest-team-filter/guest-total` DOM 모두 제거 확인 (grep no match)
- [x] 공유 코드 미변경: `cardHTML`, `buildBoard`, `loadUser`, `renderNotice`, `#view-user`, `__pageSearch` 그대로
- [x] `loadProjColors()` 호출이 로그인 사용자 한정으로 좁혀짐 — 비로그인 시 불필요한 fetch 제거 (개선)
- [x] 구문 검사 통과 (ast.parse), Jinja2 템플릿 로드 통과

### 최종 판정
- **통과**
