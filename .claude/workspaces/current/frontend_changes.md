# frontend_changes — 팀 기능 그룹 B #15-2

## 결론: 변경 불필요

헤더 링크 드롭다운 (`templates/base.html` 1490~1641):
- `_loadLinks()` 가 드롭다운 열 때마다(`toggleLinksDropdown`) `fetch('/api/links')` 호출 → 백엔드가 작업 팀(`work_team_id` 쿠키 / `?team_id`) 기준으로 응답 → 드롭다운에 현재 작업 팀의 `scope='team'` 링크 + 본인 `scope='personal'` 링크가 통합 표시됨. **JS 변경 없이 자동 반영.**
- 작업 팀 전환(`selectWorkTeam` → `POST /api/me/work-team` → `location.reload()`, base.html:1020·1027): reload 후 새 work_team_id 쿠키로 다음 드롭다운 open 시 새 팀 컨텍스트로 fetch. **자연 반영.**
- 드롭다운 JS는 `link.created_by`(작성자 이름)만 비교 — `users.team_id`/`CURRENT_USER.team_id` 참조 없음.
- `_renderLinks` 의 `isMine = link.created_by === CURRENT_USER.name` 분기는 그대로 유효 — 작성자 본인에게만 수정/삭제 버튼 노출. admin이 타인 링크 수정·삭제하려면 버튼이 안 보이지만 백엔드는 admin 권한 허용 — UI에서 admin이 직접 편집할 경로는 이번 범위 밖(기존 동작 유지; admin은 `/admin` 운영 화면이 아니라 자료 큐레이션은 작성자 위임).

base.html / kanban.html / project.html / calendar.html / static — 변경 없음.
