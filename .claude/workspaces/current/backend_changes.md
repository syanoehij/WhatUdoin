# backend_changes — 팀 기능 그룹 B #15-2 (links 다중 팀 전환)

## database.py
- `get_links(user_name, work_team_ids)` — 시그니처 `team_id` → `work_team_ids`. 컨벤션 (`app._work_scope` 와 동일):
  - `None` (admin 슈퍼유저): 전 팀의 `scope='team'` 링크 + 본인 `scope='personal'` 링크. SQL: `WHERE (scope='personal' AND created_by=?) OR (scope='team')`.
    - **주의**: `scope='team' AND team_id IS NULL` 인 orphan 링크(그룹 A #4 백필 누락 잔존, 정상이면 0건)도 admin GET에 포함됨 — admin 슈퍼유저 패턴과 일관 (모든 자료 가시).
  - `set()` (팀 미배정): 본인 개인 링크만.
  - `{tid, ...}`: 해당 팀들의 `scope='team'` 링크 + 본인 개인 링크. `team_id IN (...)` 일반화 (비admin은 항상 0~1개; admin이 명시 work_team_id 다중 줄 가능성 위해 일반화).
- `update_link(link_id, title, url, desc, user_name, role)` — `role` 인자 추가. `role=='admin'` → `WHERE id=?` (작성자 무관), else → `WHERE id=? AND created_by=?`. `delete_link` 와 동일 패턴. 계획서 §8-1 — 링크는 "작성자/admin만 편집·삭제" (일정·체크의 팀 공유 모델과 다른 예외).
- `create_link` — 시그니처 무변경. 주석 한 줄 추가 (scope='team'이면 team_id는 호출부가 resolve_work_team으로 확정).
- `delete_link` — 무변경 (이미 role 분기 있음).
- **스키마 무변경** — 마이그레이션 phase 추가 없음. `links` 테이블 그대로 (그룹 A #4에서 `links.team_id` 백필 이미 완료).

## app.py — `/api/links` 4개 라우트
- `GET /api/links` — `team_id: int = None` 쿼리 파라미터 추가. 비로그인 `[]` 유지. 로그인 시 `scope_team_ids = _work_scope(request, user, team_id)` (admin→None / 비admin→{tid} 또는 set()) → `db.get_links(user["name"], scope_team_ids)`.
- `POST /api/links` — `scope=='team'`이면 `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`; `None`이면 `400` (admin이 work_team 없이 호출 거부 — `manage/projects` 라우트 미러); `auth.require_work_team_access(user, team_id)` (admin 통과 / 비admin 비소속 → 403). `scope=='personal'`이면 `team_id=None`. `user.get("team_id")` 참조 제거.
- `PUT /api/links/{link_id}` — `db.update_link(..., user["name"], user.get("role", "member"))`. 실패 시 403 유지. 작성자 본인 + admin만 편집.
- `DELETE /api/links/{link_id}` — `user.get("role", "editor")` → `user.get("role", "member")` (default만 정리; 동작 동일 — 이미 role 전달했음).

## admin GET 시맨틱 (리뷰어 확인 요청)
`_work_scope`가 admin에 `None` 반환 → DB 무필터 → admin은 헤더 링크 드롭다운에서 **전 팀의 scope='team' 링크 + 본인 개인 링크**를 본다. `/api/checklists`·`/api/events`·`/api/doc`와 일관 (모두 admin → 전 팀 슈퍼유저). 의도와 다르면 flag.

## 검증
- `tests/phase86_links_multiteam.py` 13 PASS (정적 invariant 3 + TestClient 시나리오 A~I + 직접 DB 컨벤션).
- 회귀: `tests/phase80~85` 60 PASS.
- `import app` OK. `get_links`/`update_link` 시그니처 외 호출부 없음 (mcp_server.py에 link 도구 없음).

## 서버 재시작
**필요** — 코드 reload (스키마 무변경이라 마이그레이션 불필요).
