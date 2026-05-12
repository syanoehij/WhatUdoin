# #15 프론트엔드 변경 — 프로필 "팀 변경" UI + 화면별 팀 드롭다운 제거

## templates/base.html

- `current_user_payload` 에 `work_team_id`/`work_team_name` 추가 (백엔드 `_ctx` 가 넘김) → JS `CURRENT_USER.work_team_id` 접근 가능.
- 프로필 드롭다운 헤더 이름줄: `{{ user.name }}{% if work_team_name %} · {{ work_team_name }}{% if user.role == 'admin' %}(슈퍼유저){% endif %}{% endif %}` — 계획서 §7 예시 (`홍길동 · HW팀`, admin `admin · HW팀(슈퍼유저)`). work_team_name 없으면(미배정/팀 0개) 이름만.
- 프로필 드롭다운 최상단(헤더 아래)에 "👥 팀 변경" 토글 버튼 + `#work-team-list` 접히는 서브리스트 + 구분선. 토글 시 `GET /api/me/work-team` 한 번 fetch → 팀 목록 렌더(현재 작업 팀엔 ✔). 이름은 `textContent` 로 주입(XSS 회피).
- 팀 선택 → `selectWorkTeam(id)`: `POST /api/me/work-team {team_id}` → `r.ok` 면 `location.reload()`, 실패면 `showToast`(있으면)/`alert` 로 `detail`.
- JS 신규: `_workTeamLoaded` 플래그, `toggleWorkTeamMenu()`, `loadWorkTeams()`, `selectWorkTeam(teamId)`. `closeProfileMenu()` 에 `#work-team-list` 닫기 1줄 추가.

## static/css/style.css

- `.profile-dropdown-sep` 뒤에 `.work-team-list` / `.work-team-list.hidden` / `.work-team-empty` / `.profile-dropdown-item.work-team-item` / `.profile-dropdown-item.work-team-item.active` (max-height 220 + overflow 스크롤, active 는 `--accent` 색·굵게) 추가.

## templates/kanban.html (화면별 팀 드롭다운 제거)

- `<label>팀</label>` + `<select id="team-filter" onchange="loadKanban()">...{% for team in teams %}...</select>` 제거.
- `_applyInitialTeamFilter()` 함수 제거 + 초기화 호출부 제거.
- `loadKanban()`: `team-filter.value` / `kanban_team_filter` localStorage / 조건부 URL 제거 → `fetch('/api/kanban')` (서버가 `work_team_id` 쿠키로 결정).
- 팀원 칩 로드: `CURRENT_USER.team_id` → `CURRENT_USER.work_team_id`.
- `project-filter`·`kbn-my-only-btn`·`kanban-team-members`·member-chip 로직 그대로.

## templates/project.html (간트 — 화면별 팀 드롭다운 제거)

- `<select id="team-filter" onchange="loadData()">...{% for team in teams %}...</select>` 제거.
- `const LS_TEAM = 'proj_team_filter';` + `_applyInitialTeamFilter()` 함수 + init 호출부 제거.
- `loadData()`: `team-filter.value` / `LS_TEAM` localStorage / 조건부 URL 제거 → `fetch('/api/project-timeline')` (서버 쿠키 의존).
- nav 버튼·접기·my-only 그대로.

## templates/calendar.html

- 팀원 칩 로드: `CURRENT_USER.team_id` → `CURRENT_USER.work_team_id`.
- 일정 상세 수정 버튼 게이팅: `p.team_id === CURRENT_USER.team_id` → `p.team_id === CURRENT_USER.work_team_id` (실제 권한은 서버 최종 판단 — UI 힌트만). `CURRENT_USER.role === 'editor'` 리터럴은 #16 책임이라 미변경 (surgical).
- `/api/events` fetch는 team_id 파라미터 안 보냄 — 그대로 (서버 쿠키 의존). 캘린더는 원래 화면별 팀 드롭다운 없음.

## templates/doc_list.html

- 주간 업무 작성 모달 `#weekly-team` 기본 선택값: `CURRENT_USER.team_id` → `CURRENT_USER.work_team_id`. 드롭다운 자체(`{% for t in teams %}` "전체 팀" 옵션)는 그대로 — 주간 보고서의 per-report 파라미터(어느 팀 기준으로 보고서 생성할지)이지 화면 컨텍스트 필터가 아니라서 #15 범위 밖. (계획서 §7은 칸반·간트·캘린더만 명시.)

## 알려진 한계 / 범위 밖

- `teams` 컨텍스트 변수는 `kanban_page`/`project_page` 라우트가 여전히 넘기지만 kanban.html/project.html 에서 더 이상 사용 안 함. 라우트 시그니처는 admin.html 등 다른 템플릿이 공유하므로 미변경 (백엔드와 합의).
- `weekly-team` 모달 드롭다운 / `CURRENT_USER.role === 'editor'` 리터럴 — #15 범위 밖.

## Jinja 구문 검증

`Environment(FileSystemLoader('templates')).get_template(...)` — base/kanban/project/calendar/doc_list 전부 OK.
