# 프론트엔드 변경 — 팀 기능 그룹 B #13 (`/팀이름` 비로그인 공개 포털)

## 신규 템플릿 `templates/team_portal.html`

- `base.html` extends — 헤더·CSRF·`CURRENT_USER` 등 공통 인프라 재사용. 비로그인도 보므로 base의 알림 벨/사용자 패널 등은 `not user`면 자연히 숨겨짐(기존 게이팅). **주의**: base.html의 로그인 모달(`#login-modal-overlay`)은 항상 렌더되므로 그 안에 `/register` "계정 가입" 링크(`class="login-link"`)가 항상 마크업에 존재 — 포털 본문의 "계정 가입" 버튼은 `class="btn btn-sm btn-primary"`로 구분된다(qa 테스트도 `'btn-primary">계정 가입'`로 식별).
- 블록 구조:
  - **삭제 예정 분기** (`{% if deleted %}`): `.portal-deleted` 안내 블록만 — "「팀이름」 팀은 삭제 예정입니다" + 설명. **계정 가입 버튼·팀 신청·공개 데이터·탭 모두 비노출.** admin이면 `/admin` 관리 링크 추가(`{% if user and user.role == 'admin' %}` — 계획서 섹션 7 표). 홈 링크. `<script>`(탭 전환)도 `{% if not deleted %}`로 안 나옴.
  - **본문** (`{% else %}` — 정상 팀):
    - `.portal-hero`: 팀 이름 + "공개 포털 — 공개 설정된 항목만 표시됩니다." 안내 + 홈 버튼(`/`) + (`{% if not user %}`) "계정 가입" 버튼(`btn-primary`) → `/register`.
    - `{# #14 범위: 로그인 사용자용 "팀 신청 / 가입 대기 중" 버튼 분기는 #13 에서 구현하지 않는다. #}` — 자리 표시 주석.
    - `.portal-tabs#portal-tabs`: `portal.menu` dict 기준 조건부 탭 — `{% if m.kanban %}`/`m.gantt`/`m.doc`/`m.check`/`m.calendar`. (캘린더는 기본 OFF라 보통 안 그려짐.) 데이터 필터에는 메뉴 설정이 안 들어감 — 캘린더 메뉴 OFF여도 같은 events가 칸반/간트 탭에 나옴(의도된 동작).
    - 탭 패널(`.portal-panel[data-panel]`):
      - 칸반: `portal.kanban` → `title` + `project` 태그 + `kanban_status` 태그 + `start_datetime[:10]` 읽기 전용 카드 목록.
      - 간트: `portal.gantt`(팀→프로젝트 2단계, 키: `team_name` / `projects[].name` / `projects[].events`) → 프로젝트별 일정 리스트(`title` + `start_datetime[:10] ~ end_datetime[:10]`). 풀 간트 차트 라이브러리는 안 끌어옴.
      - 문서: `portal.docs` → `title` + `author_name` 태그 + `updated_at[:10]` (텍스트만 — `/doc/{id}` 클릭 동작은 깊게 안 감, 어차피 비로그인은 가시성 검증으로 막힘).
      - 체크: `portal.checks` → `title` + `project` 태그 + `updated_at[:10]` (텍스트).
      - 캘린더(렌더되면): `portal.calendar` → `title` + `start_datetime[:16]` 단순 목록.
    - 빈 데이터: "공개된 ... 항목이 없습니다" 메시지.
  - `{% block scripts %}` (`{% if not deleted %}`): 탭 전환 IIFE — 첫 탭 기본 활성화, 클릭 시 `.active` 클래스 토글. 서브 라우트(`/팀이름/calendar`)는 만들지 않음(충돌 위험·범위 밖 — 모두 in-page).
- CSS는 `{% block extra_head %}`의 `<style>`에 인라인 — `.portal-*` 클래스. (CSS 셀렉터 텍스트 `.portal-tabs`가 마크업에 항상 들어가므로 테스트는 `id="portal-tabs"`로 요소 존재를 판별.)

## 스크린샷

운영 서버는 IP 자동 로그인이라 비로그인 브라우저 재현 불가 → 라이브 Playwright 스크린샷 없음. 단위 검증은 TestClient(qa) 참조. (스크린샷이 필요했다면 `.claude/workspaces/current/screenshots/` 하위에만 저장.)
