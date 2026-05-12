## QA 보고서 — #15-1 히든 프로젝트 다중 팀 전환

### 신규 테스트: `tests/phase85_hidden_project_multiteam.py` — 11/11 PASS (4.9s)

**정적 invariant (4)**
- [x] `test_static_no_legacy_team_id_join` — 히든 프로젝트 함수 8개 한정 `u.team_id = p.team_id` 잔존 0건, owner 의 `users.team_id` 참조(owner_row) 0건 (create_hidden_project 제외).
- [x] `test_static_create_hidden_project_no_users_teamid_fallback` — `SELECT team_id FROM users` fallback 제거, `team_id None` 시 `ValueError`.
- [x] `test_static_user_teams_approved_exists` — `_hidden_project_visible_row`/`get_hidden_project_addable_members`/`transfer_hidden_project_owner`/`admin_change_hidden_project_owner`/`transfer_hidden_projects_on_removal` 에 `user_teams` + `status = 'approved'`; `add_hidden_project_member` 2-인자(owner_id 인자 제거) + user_teams approved.
- [x] `test_static_app_route_call_updated` — app.py 라우트가 `db.add_hidden_project_member(proj["id"], target_user_id)` 2-인자 호출 + 구 3-인자 호출 부재 + `import app` OK.

**동작 (7)**
- [x] **A** `test_a_transfer_on_removal_picks_oldest_member` — 팀 A owner+멤버2, owner를 `transfer_hidden_projects_on_removal` → `added_at` 오름차순 최선두(선임)에게 owner 이양 + 추방된 owner는 project_members 에서 제거.
- [x] **B** `test_b_owner_null_then_admin_recovery` — 팀 A 히든에 owner만 → 추방 → `owner_id IS NULL` 확인 → admin(owner 부재)이 `add_hidden_project_member(pid, 새멤버)` (owner 참조 없이 projects.team_id 기준) 성공 → `admin_change_hidden_project_owner(pid, 새멤버)` 성공 → owner_id = 새멤버.
- [x] **C** `test_c_admin_excluded_from_candidates` — admin 이 (비정상이지만) user_teams approved row 를 가져도 `get_hidden_project_addable_members` 결과에서 제외 (role != 'admin' 필터 이중 보장); `get_hidden_project_members`(=assignee 후보)에 admin 이름 미포함; `add_hidden_project_member(pid, admin)` → False.
- [x] **D** `test_d_multiteam_owner_candidate_scope` — owner 가 팀 A·B 둘 다 approved. owner 가 owner 인 히든 P(team_id=A) → `get_hidden_project_addable_members(P)` 후보는 팀 A 멤버만, 팀 B 멤버 미포함. `add_hidden_project_member(P, 팀B멤버)` → False; `add_hidden_project_member(P, 팀A멤버)` → True.
- [x] **E** `test_e_visibility_follows_user_teams` — project_members row + user_teams approved → `is_hidden_project_visible` True. user_teams 에서 제거(project_members row 는 잔존) → False. 재가입 approved → True. status='pending' → False. admin → 항상 True.
- [x] **F** `test_f_addable_members_when_owner_null` — P.owner_id=NULL, P.team_id=A → `get_hidden_project_addable_members(P)` 가 빈 리스트가 아니라 팀 A 승인 멤버(전 owner 포함, project_members 만 빠진 상태) 반환. 전 owner 를 user_teams 에서도 제거하면 후보에서도 빠짐.
- [x] **G** `test_g_create_requires_team_id` — `create_hidden_project(team_id=None)` → `ValueError`. team_id 기준 저장 확인. 같은 팀 동일 이름 → None. 다른 팀 동일 이름 → 허용.

### 회귀 확인 ✅
- [x] `import app` OK.
- [x] `tests/phase80_landing_page.py` + `phase81_unassigned_user.py` + `phase82_team_portal.py` + `phase83_team_portal_loggedin.py` + `phase84_work_team_cookie.py` — 49/49 PASS (16.8s).
- [x] `tests/phase46_hidden_project_{a,b,c}.spec.js` (Playwright E2E) — **미실행**. 운영 서버 IP 자동 로그인 + 코드 변경(database.py/app.py) 후 서버 재시작 필요 → 사용자 재시작 후 실행 가능. 본 단위 검증은 TestClient/직접 DB 로 완료. (프론트엔드 무변경이라 마크업 회귀 위험 없음 — 멤버 후보 드롭다운·assignee 후보는 백엔드 반환값만 렌더.)

### 사전 결함 (이번 변경 무관) ⚠️
- `tests/test_project_rename.py` 2 FAIL (`no such column: team_id`) — 옛 픽스처 DB 에 `projects.team_id` 없음. #15 사이클에서 이미 확인된 사전 결함 (`git stash` 후 동일, master HEAD 동일). #15-1 무관.

### 임시 산출물
- 테스트 실행 중 생성된 `_phase85_*.db` / `_phase8*.db` 등 임시 DB 는 실행 후 정리 완료.

### 최종 판정
- **통과** — 신규 11/11 PASS + 회귀 49/49 PASS. 차단 결함 없음. Playwright phase46 은 서버 재시작 후 별도 확인 권장(무변경 프론트라 위험 낮음).
- **운영 서버 반영 시 재시작 필요** (database.py + app.py reload). 스키마 무변경 → 마이그레이션 불필요.
