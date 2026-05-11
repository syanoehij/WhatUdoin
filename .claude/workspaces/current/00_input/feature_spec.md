# 요청
'팀 기능 구현 todo.md' 그룹 A의 #1 (DB 마이그레이션 인프라 구축) 마무리. 그룹 A의 마지막 미완 항목.
- 단계 내부 idempotency 가드 감사 + 필요 시 보강
- Phase 4 UNIQUE preflight 누락 점검 (특히 user_teams 인덱스 직전 preflight 명시 여부, team_menu_settings 중복 점검)
- 검증 시나리오 3건 스크립트 추가 (빈 DB → Phase 마커 기록 / 재시작 → skip / 마커 강제 삭제 후 재실행 → 데이터 무결성)
- todo.md sub-task 토글 + line 683 `그룹 A 완료 (#1~#10)` 마킹 + 단위 사이클 기록 1줄 + commit

# 분류
백엔드 수정 (감사 위주 + 검증 스크립트 + 가능하면 가드 보강) / 백엔드 모드 (backend → reviewer → qa)

# 배경 (이미 완료된 것)
- #1 "구현(기본)" [x]: `init_db()` 자동 백업(`backup.py:run_migration_backup`), Phase 마커 헬퍼(`is_phase_done`/`mark_phase_done` — `settings` 테이블, conn 공유), Phase 단위 트랜잭션 래퍼(`isolation_level=None` + `BEGIN IMMEDIATE`), 경고 누적(`settings.team_migration_warnings` JSON), `normalize_name`(NFC + casefold).
- #2~#10 + 보강 사이클 전부 master 커밋됨 (최신 `fc8d071`).
- `database.py` 핵심 위치:
  - `PHASES` 리스트 (L721 부근 정의, 각 phase 본문이 `PHASES.append(...)`로 등록)
  - `_PREFLIGHT_CHECKS` 리스트 (L736)
  - phase 본문: `_phase_1_team_columns`(L852), `_phase_2_team_backfill`(L1005), `_phase_4_team_indexes`(L1064), `_phase_3_admin_separation`(L1148), `_phase_4_data_backfill`(L1335), `_phase_4b_user_ips_whitelist_unique`(L1553), `_phase_5a_projects_dedup_safe`(L1801), `_phase_5_projects_unique`(L1869), `_phase_6_project_id_backfill`(L2112), `_phase_7_password_hash`(L2168)
  - preflight: `_check_projects_team_name_unique`(L2246), `_check_users_name_norm_unique`(L2288), `_check_teams_name_norm_unique`(L2325), `_check_user_ips_whitelist_unique`(L2361). 모두 `_PREFLIGHT_CHECKS.append(...)`.
  - preflight 실행 루프: L2407 부근 `for check in _PREFLIGHT_CHECKS:`

# backend-dev 담당 작업

## 1. 단계 내부 idempotency 가드 감사 (todo.md "### #1" 섹션 "구현 (단계 내부 idempotency 가드)" 항목들)
각 phase 본문 SQL을 읽고 아래 가드가 실제로 있는지 확인. 있으면 → 감사 결과 기록(backend_changes.md), todo 토글 대상으로 표시. **없으면** → 보강 (surgical: 기존 WHERE에 조건 추가 정도, 리팩터 금지).
  - `users.name_norm` 백필 (`_phase_2_team_backfill`): `WHERE name_norm IS NULL` 여부
  - `users.password_hash` 변환 + `users.password` 비우기 (`_phase_7_password_hash`): `WHERE password IS NOT NULL AND password != ''` (이미 hash 변환·비운 row를 다시 hash() 안 넘김)
  - admin `users.team_id`/`mcp_token_hash`/`mcp_token_created_at` (`_phase_3_admin_separation`): 이미 NULL이면 노옵 (`WHERE ... IS NOT NULL`)
  - admin `user_ips` whitelist→history (`_phase_3_admin_separation`): `WHERE type='whitelist' AND user_id ...` (이미 history면 안 건드림)
  - `users.role` editor→member (`_phase_2_team_backfill`): `WHERE role='editor'`
  - `events/checklists/projects.team_id` NULL 보강 (`_phase_4_data_backfill`): 모두 `WHERE team_id IS NULL`
  - `events.project_id` / `checklists.project_id` 백필 (`_phase_6_project_id_backfill`): `WHERE project_id IS NULL`
  - `notifications.team_id`, `links.team_id`, `team_notices.team_id` 백필 (`_phase_4_data_backfill`): 모두 `WHERE team_id IS NULL`
  - `pending_users` 자동 삭제 (`_phase_4_data_backfill`): Phase 마커로 단계 자체 skip (마커 강제 삭제 후 재실행 시엔 빈 테이블이라 무해 — 확인만)
  - "관리팀" rename (`_phase_3_admin_separation`): `WHERE name='관리팀'` 또는 동등 (이미 AdminTeam이면 노옵)

## 2. Phase 4 UNIQUE preflight 누락 점검
  - `users.name_norm`, `teams.name_norm`, `(team_id, projects.name_norm)`, `user_ips type='whitelist' ip_address` preflight는 이미 등록됨 — 확인만.
  - **`user_teams(user_id, team_id)` 중복**: `_phase_4_team_indexes`가 `CREATE UNIQUE INDEX IF NOT EXISTS idx_user_teams_user_team` 만 함. 인덱스 생성 직전 명시적 preflight가 있는지 확인. 없으면: (a) preflight 추가하거나, (b) 정상 마이그레이션 경로(Phase 2가 `WHERE NOT EXISTS` 가드로 중복을 만들지 않음 — backend가 확인)에서 중복 발생 불가능하고 인덱스 생성 시 IntegrityError로 abort되면 트랜잭션 롤백+서버 시작 거부가 되는지 확인. **판단: 정상 경로에서 중복이 구조적으로 불가능하면 preflight 추가는 over-engineering — 그 사실을 backend_changes.md에 명시하고 todo에 그렇게 기록. 불확실하면 advisor 호출.**
  - **`team_menu_settings(team_id, menu_key)` 중복**: 이 테이블은 `_phase_1_team_columns`에서 생성(빈 테이블, 시드는 #19). Phase 1에서 빈 상태로 만들어지고 #19 전엔 채워지지 않으므로 중복 발생 불가. 인덱스(`idx_team_menu_settings`)도 같은 Phase 4에서 생성. → preflight 불필요. todo.md "### #1" 섹션의 해당 라인에 "테이블이 #19 시드 전까지 빈 상태 — preflight 불필요, IF NEEDED는 #19 책임" 주석 추가.
  - 충돌 감지 시 패턴 일관성: 모든 preflight가 충돌 시 (a) 예외/abort로 서버 시작 거부 + (b) `team_migration_warnings`에 `preflight_*` 카테고리 기록을 하는지 확인.

## 3. 검증 스크립트 추가
`.claude/workspaces/current/scripts/verify_team_a_001_close.py` 생성. Python 인터프리터는 `D:\Program Files\Python\Python312\python.exe`.
합성 임시 DB(`tempfile`)로 (실서버 띄우지 않음):
  - case 1: 빈 임시 DB 경로 지정 → `database.init_db()` 호출 → `settings` 테이블에서 모든 등록 phase 마커(`team_phase_*`) 키가 기록됐는지 확인 (`PHASES`의 키 전부). preflight도 통과(예외 없음).
  - case 2: 같은 DB로 `init_db()` 재호출 → 각 phase에 대해 `is_phase_done(...)` True 확인. 두 번째 호출에서 phase 본문이 다시 실행되지 않음을 확인 (예: 카운트 변화 없음 — 간단히는 마커 row 이미 존재 + 데이터 동일).
  - case 3: `settings`에서 phase 마커 row 전부(또는 destructive 위험 있는 #2/#3/#4/#7) 삭제 → `init_db()` 재호출 → WHERE 가드 덕에:
    - 비밀번호: `password_hash`가 한 번 더 hash되지 않음 (이미 `password=''`인 row가 hash() 입력으로 안 들어감 — hash 값 불변 확인)
    - `team_id` 컬럼들 덮어쓰기 없음 (재실행 전후 값 동일)
    - "관리팀" rename 중복 없음 (`AdminTeam` 그대로, `AdminTeam_legacy_...` 같은 게 안 생김 — 데이터에 관리팀 시나리오 넣어 테스트하거나, 빈 DB라 노옵임을 확인)
  - 가능하면 약간의 합성 데이터(평문 password 가진 user, team_id NULL인 event 등)를 case 3 전에 심어서 가드가 실제로 동작하는지 검증. 단 #2~#7 phase 본문이 빈 DB 첫 init_db에서 이미 다 돌았을 것이므로, "마커만 지우고 데이터는 마이그레이션 후 상태"인 시뮬레이션이 핵심.
  - 결과를 PASS/FAIL 형태로 stdout 출력. subprocess capture 시 디스크에도 남도록.

## 4. todo.md 업데이트
`D:/Github/WhatUdoin/팀 기능 구현 todo.md`:
  - "### #1. DB 마이그레이션 인프라 구축" 섹션:
    - L31~42 "구현 (단계 내부 idempotency 가드)": 실제 확인·구현된 항목만 `[ ]` → `[x]` (감사로 확인된 것). 헤더 `[ ]` → `[x]` (전부 확인되면).
    - L43~51 "구현 (Phase 4 UNIQUE preflight 검사)": 확인·정리된 항목 `[x]`. team_menu_settings 라인에 주석 추가 (위 2번).
    - L52~57 "검증": case 1·2·3에 해당하는 3개 `[ ]` → `[x]`.
  - L683 `- [ ] 그룹 A 완료 (#1~#10) — ...` → `- [x] ...` (#1까지 다 끝났으므로).
  - "### 단위 사이클 기록" 표(L691~)에 1줄 추가: `| 2026-05-12 | #1 마무리 — 가드 감사 + preflight 점검 + 검증 | [핵심 결과 요약] | [산출물] |`

## 5. backend_changes.md
`.claude/workspaces/current/backend_changes.md`에 감사 결과(가드별 위치+상태), 보강한 게 있으면 diff 요약, preflight 점검 결론, 검증 스크립트 결과, 서버 재시작 필요 여부(가드 보강 정도면 코드 reload용 재시작 — phase 추가 안 했으면 명시) 기록.

# 주의사항
- 가드가 이미 충분하면 코드 변경 없이 "감사 + todo 토글 + 검증 스크립트 추가"만으로 끝날 수 있음. 불필요한 보강·리팩터 금지 (CLAUDE.md surgical changes).
- 실서버 E2E 불가 (VSCode 디버깅 모드, 서버 꺼짐). 검증은 합성 임시 DB로만.
- phase 본문에 새 SQL을 추가하면 서버 재시작 필요. 가드만 보강해도 코드 reload용 재시작 필요. 그 사실을 명시.
- commit prefix: 코드 변경 거의 없으면 `chore:` 또는 `test:`, 가드 보강 포함이면 `fix:` 또는 `feat:` 적절히. commit 메시지 끝에 Co-Authored-By 라인 추가 (planner 규칙).
- `tempfile` 임시 DB는 루트가 아니라 OS temp dir 또는 `.claude/workspaces/current/` 하위에 — 루트에 .db 파일 남기지 말 것.
