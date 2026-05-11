## 코드 리뷰 보고서 — 팀 기능 그룹 A #10 (문서·체크 팀 경계 + 편집·삭제 권한 모델)

### 리뷰 대상 파일
- `auth.py` — `user_team_ids` deleted-team 조인, `can_edit_meeting` 신규
- `permissions.py` — `_can_read_doc`/`_can_read_checklist` 시그니처 확장 + 작업 팀 scoping, `import auth`
- `app.py` — `_filter_events_by_visibility` 재작성, `_work_scope`/`_safe_int` 신규, `_can_write_doc` 위임, ~20개 라우트 `team_id` 파라미터 + scoping, create 경로 team_id resolve
- `database.py` — 신규 헬퍼 7종, `work_team_ids` 인자 추가 (checklists/meetings/projects/MCP 조회 함수 다수)
- `mcp_server.py` — `import auth`, `_mcp_work_team_ids`, 전 도구에 `work_team_ids` 전달

### 차단(Blocking) ❌
없음. (advisor 리뷰에서 발견된 `_can_read_doc` 작성자 단축 결함 — 추방된 팀문서 작성자에게 노출 — 은 같은 흐름에서 패치 완료: `permissions._can_read_doc` 에서 `is_team_doc=1` 인 경우 작성자 단축 제거(NULL-team 잔존 row 제외), `database.py` 4곳 meetings SQL 의 `m.created_by = ?` → `(m.created_by = ? AND (m.is_team_doc = 0 OR m.team_id IS NULL))` + 팀문서 절에 `m.team_id IS NOT NULL` 추가. `auth.can_edit_meeting` 도 NULL-team 팀문서를 작성자 한정으로 정합화. 재검증 71 PASS / 0 FAIL.)

### 경고(Warning) ⚠️
- assignee 기반 "내 스케줄" 미니 위젯(`/api/my-meetings`(event_type='meeting'), `/api/my-milestones`, `/api/project-milestones/calendar`)에 팀 경계 미적용 — 담당자+히든 필터만. 담당자로 지정됐지만 비소속 팀 항목 노출 엣지 케이스. todo §10 의 "내 스케줄 계열 = team_id IN user_team_ids" 정신과 부분 차이. backend_changes.md 알려진 한계에 기록됨. 핵심 list/detail/search 라우트는 모두 처리. → 후속 사이클 권고 (차단 아님).
- `db.get_project(name)`/`get_project_by_name(name)` 이름 기반 단건 조회: 팀 간 동명 프로젝트 충돌 잔존. 권한은 `can_edit_project` 가 `project.team_id` 로 판단해 안전. §8-2(project_id 전환) 후속.
- SSE 페이로드의 `team_id` 일부 여전히 `user.get("team_id")` legacy — 클라이언트 필터링용이라 영향 미미. 일부 정정함.

### 통과 ✅
- [x] 권한 체크: create/edit/delete 경로 모두 기존 `_require_editor`/`can_edit_*` 유지. `can_edit_meeting` 신규로 meetings 혼합 모델 정합.
- [x] SQL 파라미터화: 동적으로 합성한 SQL fragment(`{team_clause}`, `{auth_sql}`, `{team_where}`)는 모두 `","`.join("?" ...)` 플레이스홀더 + 하드코딩 테이블 별칭만. 값은 전부 `?` 바인딩. SQL injection 없음.
- [x] DB 경로: 변경 없음 (스키마 무변경 — `_migrate` phase 추가 없음).
- [x] `_ctx()`: 새 템플릿 라우트 없음 (페이지 라우트는 기존 그대로, 쿼리 파라미터만 추가).
- [x] 순환 import 없음: `permissions`/`mcp_server` 가 `auth` import — `auth` 는 `database` 만 import. `database` 는 어느 것도 역참조 안 함 (내부에 `_viewer_team_ids` 자체 쿼리로 순환 회피).
- [x] 회귀 방지: 단일팀 사용자는 `resolve_work_team` 이 대표 팀을 채워줘 영향 없음. `team_id NULL` events 의 작성자 본인 노출은 신규 쓰기(`str(id)`)/legacy(이름) 양쪽 토큰 인정. 추방·재가입 자동 복구 (row 동결값 아님, 매 요청 `user_teams` 판단). `teams.deleted_at` 자동 제외.
- [x] import-time: `import app, mcp_server, permissions, auth, database` OK.
- [x] 기존 테스트: `tests/phase75_m6_mcp_owner_boundary.py` 21 PASS (MCP write-owner 경계 회귀 없음).
- [x] 합성 DB + TestClient: `.claude/workspaces/current/scripts/verify_team10.py` 71 PASS / 0 FAIL (가시성 전 라우트, 편집·삭제 권한, 추방·재가입 복구, 추방 후 자기 작성 팀자료 차단, NULL row 회귀 방지).

### 최종 판정
- **통과** (advisor 발견 결함 1건 동일 흐름 패치 완료, 재검증 통과)
