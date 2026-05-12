## QA 보고서 — #11 `/` 비로그인 접속 화면

### 테스트 방식
- 라이브 Playwright 대신 **TestClient(임시 DB) 기반 테스트** 사용. 이유: 운영 서버는 IP 자동 로그인이라 브라우저로는 비로그인 `/` 화면 자체를 재현할 수 없다 (Playwright 접속 시 항상 자동 로그인 상태). TestClient는 `testclient` IP라 화이트리스트 미매칭 → 익명 요청으로 비로그인 경로를 직접 검증 가능.
- 신규 테스트 파일: `tests/phase80_landing_page.py` (5 케이스, 모두 통과).
- 서버 재시작 **불필요** — 테스트가 임시 DB로 격리 실행되고, 운영 서버에 의존하지 않음. (단, 운영 서버에 변경을 반영하려면 코드 배포 후 재시작 필요 — 아래 참조.)

### 통과 ✅
- [x] `app.py index()` — 비로그인 `/kanban` redirect 코드 제거 확인 (소스 grep)
- [x] `database.py get_visible_teams()` 존재 + `deleted_at IS NULL` 필터 확인, `get_all_teams()` 미변경 확인
- [x] `templates/home.html` — `#view-guest`가 팀 목록 랜딩(`landing-team-card`, 계정 가입, `openLoginModal`)으로 교체, 게스트 칸반 고아 코드(`loadGuest`, `guest-board`, `home-toolbar` 등) 전부 제거, 공유 코드(`buildBoard`/`cardHTML`/`loadUser`/`view-user`) 유지
- [x] 익명 `GET /` → 200, `view-guest` + 계정 가입 버튼 + 비삭제 팀 카드 렌더, 팀 이름 href URL 인코딩(공백 `%20`, 한글 `%XX`) 확인
- [x] soft-deleted 팀(`deleted_at IS NOT NULL`)이 목록에서 제외됨 — `삭제예정팀` HTML에 미노출
- [x] 로그인 사용자 `GET /` → 200, `view-user` 대시보드 정상 렌더 (회귀 확인)

### 실패 ❌
- 없음

### 회귀 확인
- `tests/phase80_landing_page.py` 5/5 통과.
- `tests/test_project_rename.py` 2건 실패 — **본 변경과 무관한 사전 실패** (해당 테스트의 자체 hand-rolled 스키마에 그룹 A 마이그레이션이 추가한 `projects.team_id` 컬럼이 없어서 발생; `git stash` 후 동일하게 실패함 확인).

### 알려진 한계 (의도된 단계적 상태)
- 팀 카드 → `/팀이름` 링크는 #13(`/팀이름` 동적 라우트)이 아직 없어 현재 404. 링크 마크업은 사양대로 구현됨. #13 완료 시 동작 — 결함 아님.

### 서버 반영 안내
- `app.py`·`database.py`·`templates/home.html` 변경. 운영 서버(VSCode 디버깅 모드, `https://192.168.0.18:8443/`)에 반영하려면 **사용자가 수동으로 서버를 재시작**해야 함. (단, 본 단위의 검증은 TestClient로 완료되었으므로 재시작 없이도 머지 가능.)
