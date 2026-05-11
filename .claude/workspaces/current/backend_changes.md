# 팀 기능 그룹 A #10 — 백엔드 변경 내역

## 요약
문서·체크·일정·프로젝트의 팀 경계 가시성 쿼리를 다중 팀(`user_teams`) 모델로 전환 + 편집·삭제 권한 모델(8-1 "팀 공유 자료" + meetings 혼합 모델) 적용.
스키마 변경 없음(마이그레이션 phase 추가 없음). 라우트는 `team_id` 쿼리 파라미터를 받을 수 있고, 없으면 `auth.resolve_work_team` 대표 팀 fallback (#15 쿠키 통합 전 동작). 작업 팀 결정은 `_work_scope` 헬퍼가 담당하며 비소속 team_id 는 무시하고 대표 팀으로 fallback.

## auth.py
- `user_team_ids(user)`: `JOIN teams t ON t.id = ut.team_id WHERE ... AND t.deleted_at IS NULL` 추가 → 삭제 예정 팀 멤버십 자동 제외. legacy fallback 도 `teams.deleted_at` 체크. **전파 주의**: `user_can_access_team`, `is_team_admin`, `require_work_team_access`, `resolve_work_team`, 신규 가시성 필터 전부 자동 정합 — 추방·삭제팀이면 일관되게 차단됨.
- `can_edit_meeting(user, doc)` 신규: admin→True / `is_team_doc=1`→`team_id` None 이면 True(잔존 row 호환) 아니면 `user_can_access_team` / `is_team_doc=0`→`doc.created_by == user.id` (작성자 본인만, `team_share` 무관).
- `can_edit_event/can_edit_checklist/can_edit_project`: 변경 없음 — 이미 `user_can_access_team` 위임이라 팀 공유 모델로 정합. `user_team_ids` deleted 조인 추가로 추방·삭제팀 자동 차단도 자동.

## permissions.py
- `_scope_team_ids(user, work_team_ids)`, `_author_tokens(user)` 헬퍼 추가. `import auth` 추가 (순환 import 없음 — auth 는 db 만 import, permissions 는 db+auth).
- `_can_read_doc(user, doc, work_team_ids=None)`: 시그니처 확장. admin/`is_public=1`→True / `created_by == user.id`→True (작성자 본인 항상) / `team_id IS NULL`→작성자 외 False / `is_team_doc=1`→`team_id` ∈ scope / `is_team_doc=0`→`team_share=1 AND team_id ∈ scope`. `work_team_ids=None` & 비admin → `auth.user_team_ids` fallback.
- `_can_read_checklist(user, cl, work_team_ids=None)`: 시그니처 확장. admin→True / 히든→멤버십 / 로그인: `team_id IS NULL`→`created_by ∈ _author_tokens` / 아니면 `team_id ∈ scope` (추방 시 본인도 안 보임 — 작업 팀 컨텍스트 의존) / 비로그인→기존 공개 정책.

## app.py
- `_filter_events_by_visibility(events, user, scope_team_ids=None)`: 시그니처 확장 + 재작성. admin→무필터 / `scope_team_ids=None`&비admin→`user_team_ids` / 규칙: 히든 차단 → `team_id ∈ scope` 통과 → `team_id IS NULL` 이면 `created_by ∈ {str(user.id), user.name}` 만 → `is_public==1` 통과(기존: 비로그인 캘린더에 공개 일정 노출) → else skip. `created_by` 는 신규 쓰기 `str(user.id)` / legacy 이름 양쪽 인정.
- `_work_scope(request, user, explicit_id)` 신규: admin→None / 비admin→`resolve_work_team` 결과 1개 set, 단 명시·쿠키 team_id 가 비소속이면 무시하고 대표 팀 fallback, 미배정이면 `set()`. `_safe_int` 헬퍼 추가.
- `_can_write_doc(user, doc)`: `auth.can_edit_meeting` 에 위임 (단일팀 비교 제거).
- 라우트 `team_id: int = None` 파라미터 + `_work_scope`/`resolve_work_team` 적용:
  - 가시성(현재 작업 팀): `/api/events`, `/api/events/by-project-range`, `/api/events/search-parent`, `/api/events/{id}/subtasks`, `/api/events/{id}`, `/api/checklists`, `/api/projects`, `/api/projects-meta`, `/api/project-list`, `/api/project-timeline`, `/api/manage/projects`, `/api/kanban`, `/api/doc`, `/api/doc/calendar`, `/doc` 페이지, `/check` 페이지, `/check/new/edit`, `/check/{id}/edit`.
  - 쓰기(team_id 부여): `/api/events`(create), `/api/manage/projects/{name}/events`, `/api/ai/confirm`, `/api/checklists`(create), `/api/doc`(create) — `user.get("team_id")` legacy → `resolve_work_team`.
  - 충돌 검사 team_id: `/api/events/check-conflicts`, `/api/events/ai-conflict-review`, `/api/ai/weekly-report` — `user.get("team_id")` → `resolve_work_team`/`_work_scope`.
  - `search-parent` SELECT 에 `created_by` 추가 (NULL-team 작성자 판정용, 응답에서는 pop).
- `/api/kanban`, `/api/project-timeline`, `/api/ai/weekly-report`: 비admin & 작업 팀 결정 불가/비소속 → 빈 목록 반환 (다른 팀 누출 방지).

## database.py
신규 헬퍼: `_meeting_team_clause(team_ids)`, `_viewer_team_ids(viewer)`(auth 미import — 순환 회피), `_author_token_set(viewer)`, `_author_in_sql(viewer, col)`, `_project_team_filter_sql(work_team_ids, viewer, alias)`, `_events_checklists_team_name_set(conn, work_team_ids, viewer)`, `_filter_rows_by_work_team(rows, viewer, work_team_ids, author_col)`.

`work_team_ids=None` 인자 추가 (None & 비admin → `_viewer_team_ids` fallback, admin/None viewer → 무필터):
- `get_checklists`, `get_checklists_summary` (SELECT 에 `team_id`/`created_by` 추가; summary 는 응답에서 pop)
- `get_all_meetings`, `get_all_meetings_summary` (legacy 단일 `tid` → `_meeting_team_clause` IN 절. `m.created_by` 는 INTEGER user.id — 변경 없음)
- `get_unified_project_list`, `get_all_projects_with_events`, `get_all_projects_meta` (projects: `_project_team_filter_sql` — `team_id ∈ 집합 OR (team_id IS NULL AND owner_id = viewer.id)`. orphan 프로젝트 이름은 `_events_checklists_team_name_set` 으로 작업 팀 컨텍스트 것만. events 분류도 작업 팀 필터)
- `get_project_timeline` (team_id 미지정 시 비admin → 작업 팀 events 만)
- MCP: `get_event_for_mcp`, `get_events_filtered`, `get_projects_for_mcp`(응답에서 team_id/owner_id pop), `get_kanban_summary`, `search_kanban_mcp`, `search_documents_mcp`, `search_checklists_mcp`, `search_events_mcp`, `search_all` — 전부 `work_team_ids` 인자. events/checklists 는 SELECT 에 team_id/created_by 추가 후 `_filter_rows_by_work_team` 적용, 응답에서 pop.

## mcp_server.py
- `import auth` 추가, `_mcp_work_team_ids(user)` 헬퍼 (= `auth.user_team_ids(user)` — MCP 엔 작업 팀 쿠키 없으므로 소속 팀 전체를 작업 팀 집합으로. todo §10 "모든 소속 팀 통합 라우트"에 준함).
- 모든 `list_*`/`get_*`/`search_*` 도구 호출에 `work_team_ids=_mcp_work_team_ids(user)` 전달, `_can_read_doc`/`_can_read_checklist` 단건 검사에도 전달.

## 알려진 한계 / 후속(#15 등)
- `work_team_id` 쿠키 발급/검증/Set-Cookie, "팀 변경" UI, 화면별 팀 드롭다운 제거 = #15.
- assignee 기반 "내 스케줄" 미니 위젯(`/api/my-meetings`(event_type='meeting'), `/api/my-milestones`, `/api/project-milestones/calendar`): 담당자+히든 필터만 유지(팀 경계 미적용). 담당자로 지정됐지만 비소속 팀의 항목이 노출되는 엣지 케이스는 후속 처리. (todo §10 의 "내 스케줄 계열 = team_id IN user_team_ids" 정신과 부분 차이 — 핵심 list/detail/search 라우트는 모두 처리됨)
- `db.get_project(name)`/`get_project_by_name(name)` 이름 기반 단건 조회: 팀 간 동명 프로젝트 충돌 가능성 잔존(권한은 `can_edit_project` 가 `project.team_id` 로 판단해 안전). 이름 기반 API 의 `project_id` 전환은 §8-2 후속.
- SSE 페이로드의 `team_id` 일부는 여전히 `user.get("team_id")` legacy — 클라이언트 필터링용이라 정확도 영향 미미. 일부는 `payload["team_id"]` 로 정정.

## 검증
- import-time: `python -c "import app, mcp_server, permissions, auth, database"` OK.
- 합성 DB + TestClient: `.claude/workspaces/current/scripts/verify_team10.py` — 59 PASS / 0 FAIL (가시성/편집권한/추방·재가입 복구/NULL row 회귀 방지 전부).
- 기존 `tests/phase75_m6_mcp_owner_boundary.py` 21 PASS (회귀 없음).
- 마이그레이션 phase 추가 없음 → 서버 재시작 시 코드 reload 만 필요(스키마 무관). 단 VSCode 디버깅 모드라 수동 재시작 필요.
