# 팀 기능 그룹 A #1 마무리 — 변경/감사 결과

## 결론
**소스 코드 변경 없음.** #1의 모든 미완 sub-task는 #2~#10 사이클 phase 본문에 이미 가드가
들어가 있었고, Phase 4 UNIQUE preflight 누락도 실제로는 없었다 (구조적으로 불필요한 케이스만 남음).
이번 사이클 산출물은 (1) 가드 감사 결과 기록, (2) preflight 점검 결론, (3) 검증 스크립트 추가,
(4) todo.md 토글이다.

따라서 **마이그레이션 phase 추가/수정 없음 → 운영 DB 반영 작업 없음.** 단, 본 사이클은 코드 자체를
바꾸지 않았으므로 서버 재시작도 불필요하다 (todo.md 토글 + 검증 스크립트만 추가).

## 1. 단계 내부 idempotency 가드 감사 — 전부 이미 존재

| sub-task | 위치 | 가드 | 상태 |
|---|---|---|---|
| `users.name_norm` 백필 | `database.py` `_phase_2_team_backfill` (L1015) | `SELECT ... WHERE name_norm IS NULL` 후 row 단위 UPDATE | ✅ 있음 |
| `users.password_hash` 변환 + `password` 비우기 | `_phase_7_password_hash` (L2186) | `SELECT ... WHERE password_hash IS NULL AND password IS NOT NULL AND password != ''` → 1회 실행 후 `password_hash` 비NULL+`password=''` 이므로 재실행 시 0건 | ✅ 있음 |
| admin `users.team_id` NULL 처리 | `_phase_3_admin_separation` (L1155) | `UPDATE ... WHERE role='admin' AND team_id IS NOT NULL` | ✅ 있음 |
| admin `mcp_token_hash`/`mcp_token_created_at` NULL | `_phase_3_admin_separation` (L1162-1171) | `WHERE role='admin' AND mcp_token_hash IS NOT NULL` (각각) + 컬럼 존재 가드 | ✅ 있음 |
| admin `user_ips` whitelist→history | `_phase_3_admin_separation` (L1174) | `UPDATE ... SET type='history' WHERE type='whitelist' AND user_id IN (admin)` (이미 history면 0건) | ✅ 있음 |
| `users.role` editor→member | `_phase_2_team_backfill` (L1042) | `UPDATE users SET role='member' WHERE role='editor'` | ✅ 있음 |
| `events/checklists.team_id` NULL 보강 | `_phase_4_data_backfill` (L1349-1402) | `SELECT ... WHERE team_id IS NULL` + 각 `UPDATE ... WHERE id=? AND team_id IS NULL` | ✅ 있음 |
| `projects.team_id` NULL 보강 | `_phase_4_data_backfill` (L1442-1470) | `SELECT ... WHERE team_id IS NULL` + `UPDATE ... WHERE id=? AND team_id IS NULL` | ✅ 있음 |
| `events.project_id` / `checklists.project_id` 백필 | `_phase_6_backfill_table_project_id` (L1996) | `SELECT ... WHERE project_id IS NULL` + `UPDATE ... WHERE id=? AND project_id IS NULL`. 재실행 시 0건 → 자동 프로젝트 중복 생성 불가 | ✅ 있음 |
| `notifications.team_id` 백필 | `_phase_4_data_backfill` (L1476) | `UPDATE ... WHERE team_id IS NULL AND event_id IS NOT NULL AND EXISTS(...)` | ✅ 있음 |
| `links.team_id` 백필 | `_phase_4_data_backfill` (L1490) | `SELECT ... WHERE scope='team' AND team_id IS NULL` + `UPDATE ... WHERE id=? AND scope='team' AND team_id IS NULL` | ✅ 있음 |
| `team_notices.team_id` 백필 | `_phase_4_data_backfill` (L1513) | `SELECT ... WHERE team_id IS NULL` + `UPDATE ... WHERE id=? AND team_id IS NULL` | ✅ 있음 |
| `pending_users` 자동 삭제 | `_phase_4_data_backfill` (L1535) | Phase 마커로 단계 자체 skip. 마커 강제 삭제 후 재실행 시엔 `DELETE FROM pending_users`가 이미 빈 테이블이라 0건 (무해) | ✅ 적절 |
| "관리팀" rename | `_phase_3_admin_separation` (L1193-1233) | `SELECT id FROM teams WHERE name='관리팀' LIMIT 1` → 없으면 즉시 return. rename 후엔 이름이 `AdminTeam`/`관리팀_legacy_*`이라 재실행 시 lookup 0건 → no-op. AdminTeam 사전 존재 시 fallback `관리팀_legacy_{id}` + `admin_separation` warning | ✅ 있음 |

검증: 위 가드들은 verify 스크립트 case 3에서 실제로 동작 확인됨 (마커 삭제 + 위험 합성 데이터 심은 뒤
재실행해도 password_hash/team_id/project_id/AdminTeam 이름 전부 불변, phase 7 "converted 0 plaintext").

## 2. Phase 4 UNIQUE preflight 점검

| 대상 | preflight | 상태 |
|---|---|---|
| `users.name_norm` 전역 | `_check_users_name_norm_unique` (`database.py` L2288, `_PREFLIGHT_CHECKS.append`) | ✅ 등록됨 |
| `teams.name_norm` 전역 | `_check_teams_name_norm_unique` (L2325) | ✅ 등록됨 |
| `(team_id, projects.name_norm)` | `_check_projects_team_name_unique` (L2246) | ✅ 등록됨 |
| `user_ips type='whitelist' ip_address` | `_check_user_ips_whitelist_unique` (L2361) | ✅ 등록됨 |
| `user_teams(user_id, team_id)` 중복 | (없음 — 불필요) | ✅ 의도적 미적용. `user_teams`는 `_phase_1_team_columns`에서 **빈 테이블**로 생성되고, `_phase_2_team_backfill`의 INSERT가 `WHERE NOT EXISTS` 가드로 중복을 만들지 않으며, Phase 1 이전엔 테이블 자체가 없어 사전 중복도 불가능. 만약 어떤 이유로든 중복이 존재하면 `_phase_4_team_indexes`의 `CREATE UNIQUE INDEX IF NOT EXISTS idx_user_teams_user_team`가 IntegrityError → phase 러너 ROLLBACK + `RuntimeError`로 서버 시작 거부. 별도 preflight는 over-engineering. |
| `team_menu_settings(team_id, menu_key)` 중복 | (없음 — 불필요, #19 책임) | ✅ 의도적 미적용. `team_menu_settings`도 Phase 1에서 빈 테이블로 생성, 시드는 #19에서. #19 전까지 비어 있으므로 중복 발생 불가. 인덱스(`idx_team_menu_settings`)도 같은 Phase 4에서 생성. #19가 시드를 채우는 시점에 중복 가능성이 생기면 그 사이클에서 preflight 추가 — todo.md #1 섹션에 주석 추가. |

preflight 일관성: `_run_phase_migrations` (L2421)에서 충돌 1건 이상이면 (a) 각 충돌을
`_append_team_migration_warning(conn, category, msg)`로 누적 + stdout 로그 → (b) commit 후
`RuntimeError(f"migration preflight failed with {n} conflict(s); ...")`로 서버 시작 거부. 4개 preflight
함수 모두 `(category, message)` 튜플 리스트를 반환하고 `category`는 `preflight_*` 네임스페이스. 일관됨.

## 3. 검증 스크립트
`.claude/workspaces/current/scripts/verify_team_a_001_close.py` (+ `.log`):
- case 1: 빈 임시 DB(`tempfile`, `WHATUDOIN_RUN_DIR` 오버라이드) 첫 `init_db()` → 등록 phase 10개 마커 전부 기록 + preflight 통과
- case 2: 재호출 → 모든 phase `is_phase_done` True, users/teams row 수 불변, `_pending_phases()==[]` (백업·preflight skip 경로)
- case 3: phase 마커 전부 삭제 + 위험 합성 데이터 심기(평문 password 잔존+hash 보유 user / team_id 채운 event / project_id 채운 event / AdminTeam 이름 팀) → `init_db()` 재호출 → 마커 재기록 + 데이터 무결성(password_hash 불변·재hash 안 됨·"converted 0", team_id/project_id 덮어쓰기 없음, AdminTeam 중복 rename 없음)
- **결과: 15 PASS / 0 FAIL**

## 4. 부수 메모 (out of scope — 기록만)
- `user_teams` 테이블 실제 컬럼은 `role`/`status`인데 todo.md §#2 명세는 `team_role`/`join_status`로 적혀 있다. #2 사이클에서 명칭이 단순화된 것으로 보임. 본 #1 범위 밖이라 변경하지 않음 — 미래에 "새 불일치"로 재발견하지 않도록 여기 기록만.

## 서버 재시작
**불필요.** 이번 사이클은 소스 코드를 바꾸지 않았다 (todo.md 토글 + 검증 스크립트 추가만).
