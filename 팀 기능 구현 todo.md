# 팀 기능 구현 TODO

본 문서는 `팀 기능 구현 계획.md`(이하 "계획서")를 기반으로 한 단계별 구현 체크리스트다.

## 사용법

- 항목 번호는 계획서 **섹션 17** 추천 구현 순서를 그대로 따른다.
- 각 항목 하위 sub-task는 체크박스로 정리. 작업 시작·완료 시 `[ ]` ↔ `[x]`로 토글한다.
- "**의존: ← #N**" 표기가 있으면 해당 선행 항목이 끝나야 진행 가능.
- "**📖 섹션 N**" 표기는 계획서의 관련 섹션.
- 각 항목은 일반적으로 다음 4가지로 분해 (해당 없는 항목은 생략):
  - **구현**: 코드 변경
  - **마이그레이션**: DB 스키마/데이터 변경
  - **테스트**: 단위·E2E
  - **검증**: 수동 확인 시나리오

---

## 그룹 A: DB·인증 기반 + 데이터 백필 (#1~#10)

### #1. DB 마이그레이션 인프라 구축

📖 섹션 13 (실행 절차 + Phase 1~5 + 운영자 체크리스트)

- [x] **구현**
  - [x] `init_db()`에 자동 백업 로직 추가 (미적용 마이그레이션 있을 때만 `whatudoin.db.bak.{ISO8601}` 생성) — 실제 위치는 `backupDB/whatudoin-migrate-{YYYYMMDDTHHMMSSffffff}.db`로 prefix 공유해 90일 retention 자동 적용 (`backup.py:run_migration_backup`)
  - [x] Phase 마커 헬퍼: `is_phase_done(key)`, `mark_phase_done(key)` — `settings` 테이블 사용 (호출자 conn 공유로 본문↔마커 동일 트랜잭션)
  - [x] Phase 단위 트랜잭션 래퍼 (실패 시 롤백 + 서버 시작 거부 + stdout 로그) — `isolation_level=None` + `BEGIN IMMEDIATE` 수동 발행으로 DDL implicit COMMIT 우회
  - [x] 마이그레이션 경고 누적 로그: `settings.team_migration_warnings` JSON 누적 (race-safe + 같은 (category, message) 쌍 중복 방지)
  - [x] `normalize_name(s: str) -> str` 헬퍼 (NFC + lower) — `unicodedata.NFC + casefold`
- [x] **구현 (단계 내부 idempotency 가드 — destructive 작업 보호)** — #2~#10 phase 본문에 이미 모두 들어가 있음 (감사 확인, `archive/.../backend_changes.md` 표 참고)
  - Phase 마커는 큰 틀의 idempotency만 보장한다. 단계 내부 SQL은 추가로 WHERE 가드를 둬 재실행/부분 실패 후 재진입에도 데이터를 망가뜨리지 않도록 한다.
  - [x] `users.name_norm` 백필: `WHERE name_norm IS NULL` — `_phase_2_team_backfill` (이미 채운 row 보호)
  - [x] `users.password_hash` 변환 + `users.password` NULL 처리: `WHERE password_hash IS NULL AND password IS NOT NULL AND password != ''` — `_phase_7_password_hash` (1회 후 hash 비NULL+password='' 이라 재실행 시 0건, hash() 재호출 없음)
  - [x] admin `users.team_id`/`mcp_token_hash`/`mcp_token_created_at` NULL 처리: `WHERE role='admin' AND ... IS NOT NULL` — `_phase_3_admin_separation` (이미 NULL이면 0건)
  - [x] admin `user_ips` whitelist → history 강등: `WHERE type='whitelist' AND user_id IN (admin)` — `_phase_3_admin_separation` (이미 history인 row는 건드리지 않음)
  - [x] `users.role` editor → member 일괄 갱신: `WHERE role='editor'` — `_phase_2_team_backfill`
  - [x] `events/checklists/projects.team_id` NULL 보강: 모두 `WHERE team_id IS NULL` (SELECT + 각 UPDATE) — `_phase_4_data_backfill`
  - [x] `events.project_id` / `checklists.project_id` 백필: `WHERE project_id IS NULL` (SELECT + UPDATE) — `_phase_6_backfill_table_project_id` (재실행 시 0건 → 자동 프로젝트 중복 생성 불가)
  - [x] `notifications.team_id`, `links.team_id`, `team_notices.team_id` 백필: 모두 `WHERE team_id IS NULL` — `_phase_4_data_backfill` (재실행 시 기존 값 덮어쓰지 않음)
  - [x] `pending_users` 자동 삭제: Phase 마커로 단계 자체를 건너뜀. 마커 강제 삭제 후 재실행 시엔 빈 테이블이라 `DELETE` 0건 (무해)
  - [x] "관리팀" rename: `SELECT id FROM teams WHERE name='관리팀'` → 없으면 즉시 return, rename 후엔 이름이 AdminTeam/관리팀_legacy_* 이라 재실행 시 lookup 0건 (노옵) — `_phase_3_admin_separation`
- [x] **구현 (Phase 4 UNIQUE preflight 검사)** — 4개 preflight 등록 완료, 나머지 2개는 구조적으로 불필요
  - [x] Phase 4 인덱스 생성 **직전**에 데이터 충돌 사전 점검 — 충돌 시 **서버 시작 거부 + `team_migration_warnings`에 충돌 row 정보 기록** (`_run_phase_migrations` → `_run_preflight_checks` → 충돌 시 `_append_team_migration_warning` + `RuntimeError`):
    - [x] `users.name_norm` 충돌 (전역, `is_active` 무관) — `_check_users_name_norm_unique` (#7)
    - [x] `teams.name_norm` 충돌 — `_check_teams_name_norm_unique` (#7)
    - [x] `(team_id, projects.name_norm)` 충돌 (팀 안에서만) — `_check_projects_team_name_unique` (#5)
    - [x] `user_ips`의 `type='whitelist'` `ip_address` 전역 충돌 — `_check_user_ips_whitelist_unique` (#9)
    - [x] `user_teams(user_id, team_id)` 중복 row — **preflight 불필요(의도적)**: `user_teams`는 Phase 1에서 빈 테이블로 생성 + Phase 2 백필이 `WHERE NOT EXISTS` 가드 → 구조적으로 중복 불가. 만일 존재하면 `CREATE UNIQUE INDEX IF NOT EXISTS idx_user_teams_user_team` IntegrityError → phase 러너 ROLLBACK + RuntimeError로 서버 시작 거부
    - [x] `team_menu_settings(team_id, menu_key)` 중복 row — **preflight 불필요(의도적)**: Phase 1에서 빈 테이블로 생성, 시드는 #19에서. #19 전까지 빈 상태이므로 중복 발생 불가, 인덱스(`idx_team_menu_settings`)도 같은 Phase 4에서 생성. #19가 시드를 채울 때 중복 가능성이 생기면 그 사이클(#19)에서 preflight 추가 책임
  - [x] 충돌 감지 시 운영자가 자동 백업으로 복구 + 충돌 데이터 정리 후 재시작 가능 (Phase 4는 Phase 2 백필 직후 + 백업은 미적용 phase 1건이라도 있으면 `_run_phase_migrations` 진입 시 1회 생성됨). 운영자 진단 도구: `tools/migration_doctor.py` (`main.py --doctor`)
- [x] **검증** — 합성 임시 DB 기반 (실서버 X), `verify_team_a_001_close.py` 15/15 PASS
  - [x] 빈 DB에서 첫 시작 → 등록된 phase 10개 마커 모두 기록 확인 (case 1) + preflight 통과
  - [x] 재시작 시 모든 Phase 건너뛰기 확인 (case 2: `is_phase_done` 전부 True, row 수 불변, `_pending_phases()==[]`)
  - [x] 인위적 실패 주입 시 서버 시작 거부 + 백업 파일로 복구 가능 확인 — `verify_phase_infra.py` case 3 PASS
  - [x] preflight 충돌 주입 시 (예: `name_norm` 중복 강제) 서버 시작 거부 + 경고 로그에 충돌 row 명시 — case 7 PASS
  - [x] **Phase 마커 강제 삭제 후 재실행 시뮬레이션** — 마커 삭제 + 위험 합성 데이터(평문 password 잔존+hash 보유 user, team_id/project_id 채운 event, AdminTeam 이름 팀) 심은 뒤 재실행 → 단계 내부 WHERE 가드 덕에 비밀번호 hash 재변환 안 됨("converted 0 plaintext"), team_id/project_id 덮어쓰기 없음, 관리팀 rename 중복 없음 확인 (case 3)

### #2. user_teams 모델 + users.role 전환 + name_norm + notifications.team_id

📖 섹션 3 (스키마), 섹션 13 (Phase 1~2), 섹션 16 (권한 헬퍼)
**의존: ← #1**

- [x] **마이그레이션 (Phase 1: 컬럼·테이블 추가 + 프로젝트 테이블 재구성)** — `team_phase_1_columns_v1`로 등록
  - [x] `users.name_norm`, `users.password_hash` 추가 (백필/UNIQUE는 #7)
  - [x] `teams.deleted_at`, `teams.name_norm` 추가 (`name UNIQUE`는 유지, name_norm UNIQUE 전환은 #7)
  - [x] `projects.name_norm` 추가
  - [x] `events.project_id`, `checklists.project_id` 추가 (백필은 #5)
  - [x] `notifications.team_id` 추가 (백필은 #4)
  - [x] `team_notices.team_id` 추가 (NULL 허용 — 팀별 공지 전환용, `#15-3`에서 라우트도 같이 전환)
  - [x] `user_teams` 테이블 신규 생성 — 컬럼/기본값/제약 명세:
    - `user_id INTEGER NOT NULL`
    - `team_id INTEGER NOT NULL`
    - `team_role TEXT NOT NULL DEFAULT 'member'` (값: `member` / `admin`)
    - `join_status TEXT NOT NULL DEFAULT 'pending'` (값: `pending` / `approved` / `rejected`)
    - `joined_at TEXT` (수락 시점 기록, NULL = 미수락. 대표 팀 우선순위 결정 기준)
    - `created_at TEXT DEFAULT CURRENT_TIMESTAMP`
    - 상태 전이: 신청 = `pending` row insert/update, 수락 = `approved` + `joined_at = CURRENT_TIMESTAMP`, 거절·추방 = `rejected`, 재신청 = 같은 row를 `pending`으로 갱신(`joined_at`은 재수락 시 갱신)
    - 기존 단일 팀 이관 row(Phase 2): `team_role='member'` + `join_status='approved'` + `joined_at = users.created_at`
    - `(user_id, team_id)` UNIQUE 인덱스는 Phase 4에서 생성
  - [x] `team_menu_settings` 테이블 신규 생성 — 컬럼/기본값/제약 명세:
    - `team_id INTEGER NOT NULL`
    - `menu_key TEXT NOT NULL` (값: `kanban` / `gantt` / `doc` / `check` / `calendar`)
    - `is_public_visible INTEGER NOT NULL DEFAULT 1` (1=공개 포털 진입 허용, 0=차단)
    - 기본값 시드(`#19`에서 적용): `calendar`만 0, 나머지 1
    - `(team_id, menu_key)` UNIQUE 인덱스는 Phase 4에서 생성
  - [x] **`projects` 테이블 재구성** — `projects.name UNIQUE` 제거 (Phase 2 백필 중 같은 이름 프로젝트를 다른 팀에 자동 생성해야 하므로 UNIQUE를 먼저 풀어둔다). 테이블 재생성 시 **`projects.id` 보존**: `INSERT INTO new_projects (id, team_id, name, name_norm, color, start_date, end_date, is_active, is_private, is_hidden, owner_id, memo, deleted_at, deleted_by, created_at) SELECT id, team_id, name, NULL, color, start_date, end_date, is_active, is_private, is_hidden, owner_id, memo, deleted_at, deleted_by, created_at FROM projects` 형태로 **명시적 컬럼 목록**으로 복사 (`SELECT *`는 `name_norm` 추가와 컬럼 개수 불일치로 위험). `name_norm`은 Phase 2 백필에서 채움. 이렇게 하면 `events.project_id`/`checklists.project_id`/`project_members.project_id`/`project_milestones.project_id`/`*.trash_project_id` 매핑 재작업 불필요. `(team_id, name_norm)` UNIQUE 인덱스는 Phase 4에서 생성.
  - [x] 재생성 후 row count 일치 검증 + `sqlite_sequence` 갱신 (`UPDATE … OR INSERT` 패턴, ON CONFLICT는 sqlite_sequence에 무효라 회피)
- [x] **마이그레이션 (Phase 2: 일부 백필)** — `team_phase_2_backfill_v1`로 등록
  - [x] `users.name_norm` ← `normalize_name(users.name)` (`teams.name_norm`도 동일 phase에서 백필)
  - [x] `users.role`: `editor` → `member` 일괄 갱신 (admin 유지, `WHERE role='editor'` 가드)
  - [x] `users.team_id` → `user_teams` approved row 이관 (admin 제외, `joined_at = users.created_at`, `WHERE NOT EXISTS` 가드)
- [x] **마이그레이션 (Phase 4: 제약·인덱스)** — `team_phase_4_indexes_v1`로 등록 (#2 범위만)
  - [x] `CREATE UNIQUE INDEX idx_user_teams_user_team ON user_teams(user_id, team_id)` — 재신청은 row 추가가 아니라 같은 row 갱신 보장
  - [x] `CREATE UNIQUE INDEX idx_team_menu_settings ON team_menu_settings(team_id, menu_key)` — 팀별 메뉴키 중복 차단
- [x] **구현 (권한 헬퍼)**
  - [x] 신규 헬퍼: `is_member`, `is_admin`, `user_team_ids`, `user_can_access_team`, `is_team_admin`, `require_work_team_access`, `resolve_work_team`, `admin_team_scope` (`auth.py`)
  - [x] 기존 `auth.is_editor` / `_require_editor` / `can_edit_*`를 유지하되 내부 구현을 새 헬퍼로 위임 (호환 단계)
  - [ ] member·admin 경로 분리, admin은 `require_work_team_access`로 `team_id` 검증 — 헬퍼는 추가됨, 라우트 호출부 적용은 #16 책임
- [x] **테스트**
  - [x] 마이그레이션 후 `user_teams` row 수 = (기존 비-admin + team_id NOT NULL) 사용자 수 (`verify_phase_migrations.py` T2 PASS)
  - [x] admin은 `user_teams` row 없음 확인 (T2 PASS)
  - [x] 권한 헬퍼 단위 테스트 (member/admin/team-admin 케이스) — `verify_auth_helpers.py` 28 checks PASS

### #3. 시스템 관리자(admin) 분리 + 관리팀 시드 처리

📖 섹션 2, 섹션 13 시드 데이터 정리
**의존: ← #2**

- [x] **마이그레이션 (Phase 2 + 3)** — `team_phase_3_admin_separation_v1`로 등록
  - [x] admin의 `users.team_id` → NULL (`WHERE role='admin' AND team_id IS NOT NULL` 가드)
  - [x] admin의 `users.mcp_token_hash`, `users.mcp_token_created_at` → NULL (`WHERE role='admin' AND (… IS NOT NULL)` 가드)
  - [x] **admin의 `user_ips` whitelist row → `type='history'`로 강등** (row 삭제하지 않음 — 접속 이력 정보는 보존, 자동 로그인 효력만 제거. 자동 로그인 쿼리가 `type='whitelist'`만 매칭하므로 강등만으로 충분)
  - [x] 기존 "관리팀" 처리: 참조 데이터 없으면 삭제, 있으면 `AdminTeam`으로 rename (`name`·`name_norm` 동시 갱신). 사양서 §13의 8+2 테이블(`users`, `user_teams`, `events`, `checklists`, `meetings`, `projects`, `notifications`, `team_notices`, `links`, `team_menu_settings`)을 `_ADMIN_TEAM_REF_TABLES`로 외화하여 검사. **AdminTeam 사전 존재 시 fallback `관리팀_legacy_{id}`** + `team_migration_warnings`에 `admin_separation` 카테고리 누적
- [x] **구현**
  - [x] `init_db()` 신규 환경 시드에서 "관리팀" 자동 생성 제거
  - [x] 신규 admin 시드 시 `team_id = NULL` 보장
  - [x] 일반 사용자 자동완성·assignee 후보·멤버 목록·MCP 일반 사용자 조회에서 admin 제외 — grep 결과 누락 0건, 의도적 미변경 5건은 backend_changes.md에 사유 명시
  - [x] 히든 프로젝트 멤버 후보에서도 admin 제외 (📖 섹션 12)
- [x] **검증**
  - [x] admin 로그인 후 일반 사용자 자동완성에 admin이 안 보임 — `verify_admin_separation.py` S7 PASS (4 케이스)
  - [x] 관리팀 rename 케이스 → AdminTeam으로 노출됨 — S3 PASS, S4 fallback PASS, S4-extra(더블 참조) PASS
  - [x] 마이그레이션 전 admin이 whitelist였던 IP에서 마이그레이션 후 접속 → 자동 로그인 안 됨 + history 이력은 `/admin`에서 조회 가능 — S5 PASS

### #4. 기존 데이터에 team_id 배정 (Phase 2 백필 — 1차)

📖 섹션 13 마이그레이션 대상
**의존: ← #2**

- [x] **마이그레이션 (events/checklists/meetings)** — `team_phase_4_data_backfill_v1`로 등록
  - [x] `events.team_id` / `checklists.team_id` NULL 보강 — **추론 우선순위 (계획서 섹션 13 확정안)**:
    1. 같은 row의 `project_id` (또는 백필 후 채워진 `project_id`)가 가리키는 `projects.team_id` — 본 사이클은 project_id 미백필이라 노옵, #6 채움 시 활성화
    2. 작성자(`created_by` 문자열 → users.name)가 단일 팀(`user_teams.join_status='approved'` 1건)에 소속이면 그 팀
    3. 결정 불가 시 `team_id` NULL 유지 + `team_migration_warnings`에 row id·시도 결과 기록 (마이그레이션은 통과, 서버 시작 거부 X)
  - [x] **임의 "기본 팀" 일괄 배정 금지** — 섞이면 분리 비용이 큼
  - [x] NULL 잔존 row의 가시성: 작성자 본인(events/checklists `created_by` str(id)·이름 토큰) + admin 슈퍼유저에게만 노출 — #10에서 `_filter_events_by_visibility`/`_can_read_checklist`/MCP 필터에 적용 완료
  - [x] `meetings.team_id` 백필 규칙 — **정상 경로 + 예외 분기 (계획서 섹션 13)**:
    - **정상 경로 (`is_team_doc=1` 팀 문서)**: 기존 `team_id`가 있으면 유지, 없으면 작성자(`created_by` INTEGER → users.id)의 기존 `users.team_id`로 백필
    - **정상 경로 (`is_team_doc=0` 개인 문서)**: 작성자의 기존 `users.team_id`로 1회 백필 (이후 작업 팀이 바뀌어도 변하지 않음)
    - **예외 (팀 문서, 작성자 admin/`team_id` NULL)**: 자동 백필 제외 + 경고 로그 — 운영자가 `/admin`에서 수동 배정
    - **예외 (개인 문서, 작성자 admin/`team_id` NULL)**: `team_id` NULL 유지 — 작성자 본인 한정 가시성 (가시성 쿼리에 명시)
    - 모든 경로에 `WHERE team_id IS NULL` 가드 적용 (이미 채운 row 보호)
- [x] **마이그레이션 (projects fallback — 계획서 섹션 13 "프로젝트 백필 원칙")** — 단계 1·2·4만 본 사이클, 단계 3은 #6 책임
  - [x] `projects.team_id` NULL 보강 — fallback 우선순위:
    1. 기존 `projects.team_id`가 있으면 우선 사용 (`WHERE team_id IS NULL` 가드로 자연 skip)
    2. `team_id`가 없고 `owner_id`가 있으면 owner의 기존 `users.team_id` 또는 이관된 대표 팀으로 배정
    3. `events.project` / `checklists.project` 문자열만 있고 대응 프로젝트가 없으면, 해당 항목의 `team_id` 안에 프로젝트 row를 **자동 생성**하고 `project_id` 연결 — **#6 책임** (본 사이클 X)
    4. 위 3단계로도 결정 불가 시 `projects.team_id` NULL 유지 + `team_migration_warnings`에 row id 기록 (마이그레이션 통과, 서버 시작 거부 X — events/checklists의 NULL 잔존 정책과 일관)
  - [x] **NULL 잔존 projects 가시성 정책**: `team_id IS NULL` 프로젝트는 `owner_id` 본인 + admin 슈퍼유저에게만 노출 — #10에서 `_project_team_filter_sql`(`team_id IN scope OR (team_id IS NULL AND owner_id = viewer.id)`) + orphan 이름도 작업 팀 컨텍스트 events/checklists 것만(`_events_checklists_team_name_set`)으로 적용. 일반 팀 화면 비노출 확인(공개 포털 `/팀이름`은 #13 책임)
  - [x] **문자열 프로젝트명만으로 전역 update를 하지 않는다** (같은 이름 프로젝트가 여러 팀에 생길 수 있음) — 사양서 명시 + 백필이 owner.team_id 기반으로만 동작
- [x] **마이그레이션 (보조 테이블)**
  - [x] `notifications.team_id` 백필 (`event_id → events.team_id`, 매핑 불가는 NULL = "팀 미상")
  - [x] `links.team_id` 백필 (`scope='team'` + NULL인 row만, 작성자 `users.team_id` 매칭, 매칭 실패는 로그만 남기고 그대로 둠)
  - [x] `team_notices.team_id` 백필 — `created_by` 문자열 → users.name 매칭 → 작성자 단일 팀이면 그 팀, 다중 팀이면 대표 팀(`joined_at` 최선), 작성자 admin/매칭 실패는 NULL 유지 + `team_migration_warnings`에 기록
  - [x] `pending_users` 모든 row 자동 삭제 (status 무관)
- [x] **검증**
  - [x] `team_migration_warnings` 조회 → 백필 누락 항목 확인 (5개 카테고리 사용)
  - [x] meetings/events/checklists의 NULL team_id row가 의도된 것만 남았는지 확인 (`verify_data_backfill.py` 40/40 PASS)
  - [ ] 자동 생성된 프로젝트 row가 의도된 팀 안에만 들어갔는지 확인 — #6 책임

### #5. 프로젝트 식별자 정리 (project_id 표준화)

📖 섹션 8-2, 섹션 13 마이그레이션 대상
**의존: ← #4**

> **마이그레이션 순서 결정**: 프로젝트 테이블 재구성(`projects.name UNIQUE` 제거 + id 보존)은 Phase 2 백필에서 같은 이름 프로젝트 자동 생성을 가능하게 하기 위해 **#2의 Phase 1로 이미 옮겼다.** `(team_id, name_norm)` UNIQUE 인덱스는 Phase 4에서 만든다. 본 항목은 백필 + 라우트 전환만 담당.

- [x] **마이그레이션 (Phase 2: 백필)** — `team_phase_5_projects_unique_v1`로 등록
  - [x] `projects.name_norm` 백필 (`normalize_name(projects.name)`) — #2에서 Phase 1 재구성 직후 채워졌으므로 본 사이클은 잔존 NULL row 방어용 (`WHERE name_norm IS NULL` 가드)
- [x] **마이그레이션 (Phase 4: 인덱스)** — preflight check `_check_projects_team_name_unique` 등록
  - [x] `CREATE UNIQUE INDEX idx_projects_team_name ON projects(team_id, name_norm) WHERE team_id IS NOT NULL` (부분 인덱스 — NULL 잔존 면제, 운영자 정리 영역)
- [x] **구현**
  - [x] 프로젝트 생성·이름 변경 API에서 `(team_id, name_norm)` 중복 검사로 교체 (`create_project` 시그니처 확장 `team_id=None`, `create_hidden_project`의 `LOWER(name)` 전역 검사 → 팀 제한, `rename_project`도 동일)
  - [x] 같은 이름의 프로젝트가 다른 팀에 존재 가능하도록 라우트·DB 쿼리 수정 (POST /api/manage/projects, PUT /api/manage/projects/{name}, POST /api/manage/hidden-projects에서 `resolve_work_team` 사용)
- [x] **검증**
  - [x] 같은 이름 프로젝트 두 팀에 생성 가능 — 9/9 시나리오 PASS
  - [x] 같은 팀 안에 같은 이름 프로젝트 두 개 생성 시 차단 — UNIQUE + 라우트 사전 검사
  - [x] Phase 1 재구성 전후 `projects.id` 동일 + 의존 테이블(`events.project_id` 등) 매핑 그대로 유지 — 검증 스크립트로 row count 일치 확인

### #6. events/checklists/milestones/project_members/trash을 project_id 기준으로 백필

📖 섹션 13 프로젝트 백필 원칙
**의존: ← #5**

- [x] **마이그레이션 (Phase 2: 백필 마무리)** — `team_phase_6_project_id_backfill_v1`로 등록
  - [x] `events.project_id` 백필 (`(team_id, name_norm)` 매칭, deleted_at IS NULL 우선, 실패 시 자동 생성)
  - [x] `checklists.project_id` 백필 (동일)
  - [x] `events.project` / `checklists.project` 문자열 컬럼은 호환용으로 유지(drop 안 함, Phase 5 책임)
  - [x] 매칭 실패한 row가 있으면 해당 `team_id` 안에 프로젝트 row 자동 생성 후 연결 (같은 phase 내 캐시로 중복 방지, warning `project_id_backfill_auto_created`)
- [x] **마이그레이션 (테이블 재생성 후 매핑 검증)** — dangling 검증, 발견 시 warning만 누적, 데이터 변경 X
  - [x] `project_milestones.project_id` 매핑 정상 검증
  - [x] `project_members.project_id` 매핑 정상 검증
  - [x] `events.trash_project_id`, `checklists.trash_project_id`, `meetings.trash_project_id` 검증
  - [x] 매핑 누락은 `team_migration_warnings`(`project_id_backfill_dangling_trash`)에 기록
- [x] **마이그레이션 (Phase 4: 인덱스)**
  - [x] `idx_events_project_id`, `idx_checklists_project_id` 생성
- [x] **구현**
  - [x] events·checklists 신규 쓰기 경로는 `project_id`를 우선 저장 (INSERT + PATCH /api/events/{id}/project + update_checklist 동반 갱신, 리뷰 1차 차단 결함 패치)
  - [ ] 읽기 경로는 `project_id` 기반으로 전환, `project` 문자열은 표시·호환용 — #10 책임
- [x] **검증**
  - [x] 백필 후 `events.project_id`, `checklists.project_id`가 의도대로 채워졌는지 확인 — 17 케이스 PASS
  - [x] 신규 일정·체크 작성 시 `project_id` 저장 확인

### #7. 로그인 인증 기반 정비 (#8과 한 페이즈)

📖 섹션 6 (로그인/인증), 섹션 17 #7
**의존: ← #2**

- [x] **마이그레이션 (Phase 2: 비밀번호 변환 + 평문 컬럼 비우기)** — `team_phase_7_password_hash_v1`로 등록
  - [x] `users.password` → `users.password_hash` 일괄 변환 (admin 포함)
  - [x] **hash 변환 성공 직후 같은 트랜잭션 안에서 `users.password` ← `''` 처리** (NOT NULL 제약 deviation으로 빈 문자열 사용 — 자가 발견 결함 패치). 컬럼 자체 drop은 Phase 5에서.
  - [x] 변환·NULL 처리 후 hash로 기존 평문 비밀번호 로그인이 정상 동작하는지 sanity check
- [x] **구현**
  - [x] 일반 `/api/login`을 비밀번호 단독 → 이름+비밀번호로 전환
  - [x] 일반 `/api/login`은 `users.role = admin` 사용자 조회 제외 (admin 존재 여부도 노출 금지 — 동일 에러 메시지, 더미 hash 비교로 timing 차이 최소화)
  - [x] `/api/admin/login`은 그대로 유지 (5분 세션, 내부 hash 검증으로 교체)
  - [x] `name_norm` 기반 case-insensitive 로그인·중복 검사
  - [x] **계정명 입력 검증 정규식**: `^[A-Za-z0-9가-힣]+$` — 헬퍼 추가
  - [x] `/api/me/change-password`에서 비밀번호 정책(영문+숫자 동시 포함) 검증
  - [x] 기존 `get_user_by_password` 단독 사용 제거
- [x] **마이그레이션 (Phase 4: 제약)** — preflight 2건 등록
  - [x] `users.name_norm` 전역 UNIQUE 인덱스 생성 (`is_active` 무관) — `_check_users_name_norm_unique` preflight
  - [x] `teams.name_norm` UNIQUE 인덱스 생성 — `_check_teams_name_norm_unique` preflight
- [x] **테스트**
  - [x] 평문 → hash 변환 후 기존 비밀번호로 로그인 가능 — 63 import-time PASS
  - [x] admin 이름으로 일반 `/api/login` 시도 시 401
  - [x] `Kim`과 `kim` 동일 계정으로 인식

### #8. 계정 가입과 팀 신청 분리

📖 섹션 6 (계정 가입과 팀 신청)
**의존: ← #7**

- [x] **구현 (계정 가입)**
  - [x] `/api/register` 신규 흐름: 이름·비밀번호만 받고 즉시 `users` row 생성 (`role=member`, `team_id=NULL`, `name_norm` 정규화 저장) — `db.create_user_account`
  - [x] **계정명 정규식 검증**: `^[A-Za-z0-9가-힣]+$` (`passwords.is_valid_user_name` 재사용)
  - [x] 비밀번호 정책(영문+숫자 동시 포함) 서버 검증 (`passwords.is_valid_password_policy` 재사용)
  - [x] 예약 사용자명 차단: `admin`, `system`, `root`, `guest`, `anonymous` (대소문자 무관 — `RESERVED_USERNAMES` + `casefold`)
  - [x] 비밀번호 중복 검사는 더 이상 하지 않음
  - [x] 가입 직후 자동 세션 생성 + `/`로 리다이렉트 (백엔드 set_cookie + register.html `window.location='/'`)
  - [x] `pending_users` 신규 쓰기 경로 제거 (`check_register_duplicate`/`create_pending_user` 호출 제거 — 함수 자체는 Phase 5 drop 검토 시 정리)
- [x] **구현 (팀 신청)**
  - [x] `/api/me/team-applications` (POST): `user_teams`에 `pending` row 생성/갱신 (`db.apply_to_team`)
  - [x] `pending` row가 1개라도 있으면 추가 신청 차단 (임의 팀 pending 존재 시 신규 차단)
  - [x] 거절·추방 후 재신청은 같은 `(user_id, team_id)` row를 `pending`으로 갱신 (row 추가 X, joined_at 보존)
  - [x] 시스템 관리자 또는 해당 팀 관리자가 수락/거절 처리 — `GET/POST /api/teams/{team_id}/applications[/{user_id}/decide]` (`_require_team_admin`). 멤버 관리 페이지 UI는 #18.
- [x] **테스트**
  - [x] 예약 사용자명 차단 확인 (`admin`, `Admin`, `ADMIN` + `system`/`ROOT`/`Guest`/`anonymous`)
  - [x] 가입 후 자동 세션 + `/` 리다이렉트 동작 (set_cookie + DB row 검증)
  - [x] pending 상태에서 추가 신청 차단 (같은 팀/다른 팀 모두 409)
  - 합성 DB + TestClient 72 PASS (`scripts/verify_team_a_008.py`). 실서버 브라우저 E2E는 서버 재시작 후 후속.

### #9. IP 자동 로그인 관리

📖 섹션 6 (자동 로그인 IP 등록), 섹션 10 (IP 관리 범위)
**의존: ← #7**

- [x] **구현 (사용자 자체 등록)**
  - [x] 설정 화면에 "현재 PC를 자동 로그인 대상으로 등록" 토글 — `base.html` 사용자 설정 사이드 패널 `#ip-autologin-section`
  - [x] 토글 ON 시 확인 모달 (보안 안내 문구 포함) — `wuDialog.confirm`, 사양서 §6 문구 그대로
  - [x] `/api/me/ip-whitelist` (POST): 클라이언트 IP를 `user_ips`에 `whitelist` 등록 (`db.set_user_whitelist_ip`). admin이면 403
  - [x] `/api/me/ip-whitelist` (DELETE): 본인 whitelist 해제 (`type='history'` 강등, row 보존)
  - [x] 충돌 시(다른 사용자가 같은 IP 등록) 409 + 안내. (보조: `GET /api/me/ip-whitelist`로 초기 토글 상태·충돌 안내 — todo 명세 외 구현)
- [x] **구현 (admin 관리)**
  - [x] `/admin` IP 모달에서 임의 사용자 IP 이력 조회·whitelist 토글·삭제 (`PUT /api/admin/ips/{id}/whitelist` 충돌 409 보강, `DELETE /api/admin/ips/{id}` 추가)
  - [x] 임의 IP 직접 입력 등록 (`POST /api/admin/users/{id}/ips`, 접속 이력 없는 IP도 가능)
- [x] **마이그레이션 (Phase 4: 제약)**
  - [x] `user_ips` 부분 UNIQUE 인덱스: `type='whitelist'`인 `ip_address` 전역 유일 — phase `team_phase_4b_user_ips_whitelist_unique_v1` + preflight `_check_user_ips_whitelist_unique`(충돌 시 abort + `team_migration_warnings`) + `tools/migration_doctor.py` [4] 진단 추가
- [x] **검증**
  - [x] admin 세션에서 사용자 자체 등록 토글이 안 보임 — `GET /api/me/ip-whitelist` admin=true → 섹션 숨김 + `POST /api/me/ip-whitelist` 서버 403
  - [x] 팀 관리자에게 IP 관련 UI/API 노출 안 됨 — IP API는 `_require_admin`(시스템 관리자 전용). 멤버 관리 페이지(#18)에서 팀 관리자 숨김은 #18 책임
  - 합성 DB + TestClient + 마이그레이션 phase 직접 호출 59 PASS (`scripts/verify_team_a_009.py`). 실서버 브라우저 E2E는 서버 재시작 후 후속.

### #10. 문서·체크 팀 경계 완성 + 편집·삭제 권한 모델

📖 섹션 8, 섹션 8-1 (데이터 소유·편집 권한 모델)
**의존: ← #4, #6**

> **#10과 #15의 분리 정책 (사용자 결정)**: #10은 **백엔드 가시성 쿼리 전환만** 담당한다. 작업 팀 결정은 `#2`의 `resolve_work_team(request, user, explicit_id)` 헬퍼 + 명시 `team_id` 파라미터에 의존한다(쿠키 없으면 대표 팀 fallback). **`work_team_id` 쿠키 발급/검증/Set-Cookie, 프로필 메뉴 "팀 변경" UI, 화면별 팀 드롭다운 제거는 #15에서 처리한다.** 따라서 #10 시점에는 프론트가 명시 `team_id`를 보내거나 서버가 대표 팀으로 fallback하도록 두고, #15 도입 후 자연스럽게 쿠키 기반 동작으로 합쳐진다. 이 정책 덕에 #10은 그룹 A에 머무르되 #15에 막히지 않는다.

- [x] **구현 (가시성 / 조회)**
  - [x] `_filter_events_by_visibility`를 `user_teams` 기반으로 교체. 팀 컨텍스트는 **`resolve_work_team` 헬퍼 결과** 사용 (호출부에서 explicit `team_id` 또는 대표 팀 fallback). 시그니처 `(events, user, scope_team_ids=None)`, `_work_scope` 헬퍼 신규
  - [x] meetings·checklists 가시성 쿼리를 다중 팀 모델로 교체 (동일 헬퍼) — `permissions._can_read_doc/_can_read_checklist`에 `work_team_ids` 인자, `database.get_all_meetings/get_checklists` 등에 `work_team_ids` 인자
  - [x] 개인 문서(`is_team_doc=0`) 가시성 규칙 적용:
    - 작성자 본인: 항상 노출
    - team_share=1: 같은 팀 멤버 + 현재 작업 팀 일치
    - `team_id IS NULL`: 작성자 본인만
    - (보강) 팀 문서(`is_team_doc=1`)는 작성자 예외 없음 — 추방되면 자기가 만든 팀 문서도 안 보임 (§8-1)
  - [x] 체크는 작업 팀 컨텍스트에 의존 (작성자 추방 시 본인도 안 보임 — `team_id IS NULL` 잔존 row 만 작성자 예외)
  - [x] 모든 팀 컨텍스트 API는 `team_id` 파라미터를 명시적으로 받을 수 있게 — 쿠키 미존재 시 `resolve_work_team` 대표 팀 fallback 동작 검증 (쿠키/UI 통합은 #15에서). 비소속 team_id 명시 시 무시하고 대표 팀 fallback
- [x] **구현 (편집·삭제 권한 — 8-1 "팀 공유 자료" 모델)**
  - [x] **일정·체크·프로젝트·팀 문서(`is_team_doc=1`)**: 같은 팀 승인 멤버(`user_teams.status='approved'` AND `teams.deleted_at IS NULL`) 누구나 편집·삭제 허용. `created_by`는 표시·로그용일 뿐 권한 판단에 쓰지 않는다 — `auth.can_edit_event/can_edit_checklist/can_edit_project`(이미 위임) + `can_edit_meeting`(신규)
  - [x] **개인 문서(`is_team_doc=0`)**: **작성자 본인만** 편집·삭제 가능. `team_share=1`은 다른 팀 멤버에게 **읽기만** 허용 (편집·삭제 불가) — `auth.can_edit_meeting`
  - [x] admin은 전역 슈퍼유저로 모든 팀 자료 편집·삭제 가능 (`work_team_id` 명시 강제는 `#16` 책임 — 본 사이클 미적용, 주석 표시)
  - [x] 일정·체크·프로젝트 작성 폼에 "내 일정/팀 일정" 토글 같은 **개인/팀 구분 UI를 두지 않는다** (변경 없음 — 추가 안 함)
  - [x] 추방·재가입 시 권한 자동 복구 — row의 동결값이 아니라 매 요청마다 `user_teams` 상태로 판단 (개인 문서는 추방과 무관하게 작성자 보유). `auth.user_team_ids`에 `teams.deleted_at IS NULL` 조인 추가로 삭제 예정 팀도 자동 제외
- [x] **검증 (가시성 — 라우트 전체 열거)** — 합성 DB + TestClient 71 PASS (`.claude/workspaces/current/scripts/verify_team10.py`)
  - [x] 다중 팀 사용자가 작업 팀 전환에 따라 자료가 바뀌는지 확인
  - [x] 추방 시나리오 → 재가입 시나리오에서 자료 가시성 자동 복구
  - [x] **현재 작업 팀 기준 라우트** (모두 `team_id` 파라미터 + `resolve_work_team` fallback): `/api/events`(+by-project-range/search-parent/subtasks/{id}), `/api/kanban`, **간트용** `/api/project-timeline`, `/api/checklists`, `/api/doc`(+calendar), `/api/projects`(+meta/project-list), **프로젝트 관리 목록** `/api/manage/projects`, `/check`·`/doc` 페이지
  - [~] **모든 소속 팀 통합 라우트** ("내 스케줄" 계열): MCP `list_*`/`search_*`는 `user_team_ids(user)` 기준으로 동작(현재 작업 팀 종속 X). assignee 기반 미니 위젯(`/api/my-meetings`(event_type='meeting')·`/api/my-milestones`·`/api/project-milestones/calendar`)은 담당자+히든 필터만 — 팀 경계 미적용 (후속, qa_report.md 기록)
  - [x] 각 라우트에서 다른 팀 row가 응답에 섞이지 않는지 통합 테스트
  - [x] **(후속 패치 2026-05-12)** 비로그인(`viewer=None`) 관점 누수 재점검 — `/api/project-timeline`(간트 데이터 소스)이 `if viewer and not auth.is_admin(viewer)` 가드 때문에 비로그인 시 else 분기로 빠져 `team_id=None`·`viewer=None` 무필터 조회 → 전 팀 public 일정·프로젝트가 비로그인 누구에게나 노출되던 버그 수정. `/api/kanban` 과 동일 골격(`if not auth.is_admin(viewer): scope=_work_scope; if not scope: return []`)으로 교체 → 비로그인 `[]`. 같은 흐름에서 events·checklists·projects·meetings·kanban·doc 류 GET 라우트 비로그인 관점 일괄 점검 — `/api/project-timeline` 외 추가 누수 없음 확인(나머지는 `_work_scope` 무조건 호출 / DB 함수 `viewer is None` public 필터 / `_can_read_*` 비로그인 차단 / `not user` 단락 / `_require_editor` 401 중 하나로 막힘). 합성 DB+TestClient 31 PASS. 자세한 근거: `archive/.../backend_changes.md §2`
- [x] **검증 (편집·삭제 권한)**
  - [x] 같은 팀 멤버 A가 만든 일정·체크·팀 문서를 멤버 B가 편집·삭제 가능
  - [x] `team_share=1` 개인 문서를 다른 팀 멤버가 읽기만 가능, 편집·삭제 시도 시 거부 (`_can_write_doc` False)
  - [x] 추방된 멤버가 자기 작성 팀 자료에도 접근 불가 (event·team-doc 양쪽 검증)
  - [x] 추방된 멤버도 자기 개인 문서는 계속 편집·삭제 가능
- [x] **검증 (NULL team row 노출 안 됨 — 회귀 방지)**
  - [x] 테스트 데이터로 `events.team_id IS NULL`(신규 str(id) 형식·legacy 이름 형식 둘 다), `checklists.team_id IS NULL`, `projects.team_id IS NULL` row 삽입
  - [x] 다른 팀 멤버 세션으로 `/api/events`/`/api/checklists`/`/api/projects`/칸반 — NULL row가 어느 팀 컨텍스트에도 노출되지 않음. MCP `list_*`/`search_*`도 `work_team_ids` 필터 적용(합성 DB로 간접 검증, 실서버 E2E는 서버 OFF로 불가)
  - [x] 작성자 본인(events/checklists, str(id)·이름 토큰 양쪽) 또는 owner(projects) 세션에서만 노출되는지 확인
  - [x] admin 슈퍼유저 세션에서 전체 노출되는지 확인 (`/admin` 화면 자체는 #16 범위)
  - [~] 공개 포털(`/팀이름`)에 NULL row 비노출 — 공개 포털 라우트는 #13 책임. 본 사이클 `_filter_events_by_visibility`는 `user=None`이면 `is_public=1`만 통과(NULL team row는 작성자 본인만 → `user=None`이면 절대 통과 안 함)으로 가드

---

## 그룹 B: 화면 정비 + 다중 팀 전환 후속 (#11~#15-3)

### #11. `/` 비로그인 접속 화면

📖 섹션 7 비로그인 사용자
**의존: ← #8**

- [x] **구현**
  - [x] `/` 비로그인: 팀 목록 + 로그인 + 계정 가입 버튼 표시
  - [x] 팀 클릭 시 `/팀이름`으로 이동 (링크 마크업 구현 — `/팀이름` 라우트 자체는 #13)
- [x] **검증**
  - [x] 팀 목록에 삭제 예정 팀(`teams.deleted_at IS NOT NULL`) 제외 확인

### #12. `/` 팀 미배정 로그인 사용자 + "내 자료" 영역

📖 섹션 7 팀 미배정 로그인 사용자
**의존: ← #8, #10**

- [x] **구현**
  - [x] 팀 목록 + 팀 신청 버튼 (신청 전/대기중/거절 상태 분기) — `db.get_my_team_statuses` → `team_status_map`, `pending`이면 "가입 대기 중"(비활성)·그 외(미신청/`rejected`)는 "팀 신청"(`applyToTeam` → `POST /api/me/team-applications`). `pending_other`(다른 팀 pending) 시 그 팀만 비활성·나머진 신청 가능·서버 409 메시지 toast로 노출 (계획서 §7 표 3상태 = 신청전/대기중/거절, 거절·추방 후 = 재신청 가능 = "팀 신청")
  - [x] "내 자료" 영역: 본인 작성 개인 문서(`is_team_doc=0`)만 조회·신규 작성 가능 — `db.get_my_personal_meetings`(일정·체크·팀 문서 제외), "+ 새 문서" → `/doc/new?personal=1`(`editor`/`admin`만)
  - [x] **본인 작성 개인 문서는 `team_share` 값과 무관하게 본인에게만 표시** — `get_my_personal_meetings`가 `team_share`/`team_id IS NULL`로 거르지 않음(추방 후 `team_id` 남은 본인 개인 문서도 포함). 자기 자료 통합 노출 목적
  - [x] 팀 미배정 사용자가 신규 개인 문서 작성 시 `team_id = NULL`로 저장 — `create_doc`: `None if auth.is_unassigned(user) else resolve_work_team(...)` (legacy `user.team_id` fallback 우회) + `is_team_doc=0`/`team_share=0` 서버 강제(`update_doc`/`rotate_doc_visibility`도)
  - [x] 알림 카드·뱃지·페이지 비노출 (팀 미배정 상태) — `base.html` 알림 벨 `{% if not is_unassigned %}` + 알림 IIFE `IS_UNASSIGNED` early-return + 백엔드 `/api/notifications/{count,pending}` 미배정 빈 응답(별도 알림 페이지 라우트는 원래 없음)
  - [x] `team_share` UI 비활성화 (팀 미배정 상태에서 토글 의미 없음) — `doc_editor.html`: 미배정이면 doc-type 세그먼트 숨김 + `VIS_OPTS`에서 "팀에게만 공개"(=team_share) 제거
- [x] **검증** — `tests/phase81_unassigned_user.py` 8/8 PASS (TestClient + 임시 DB)
  - [x] 팀 미배정 → 신규 작성 → `team_id NULL` 확인 — `POST /api/doc` `{is_team_doc:1, team_share:1}` → 저장 row `is_team_doc=0`·`team_share=0`·`team_id=NULL`
  - [x] 알림이 어디에도 안 보이는지 확인 — 미배정 `GET /` 마크업에 `notif-bell-wrap` 없음 + `/api/notifications/{count,pending}` → `{count:0}`/`[]`
  - [x] `team_share=1`로 저장된 본인 개인 문서가 "내 자료"에는 표시되되, 다른 사용자(다른 팀 멤버 포함)에겐 노출 안 됨 — 작성자 `get_my_personal_meetings`엔 포함, 다른 팀 멤버 `/api/doc`엔 미노출(작업 팀 일치 조건 — 그룹 A #10 가시성 규칙)
  - [x] (추가) 팀 신청→pending 반영→승인→배정→`view-user` 전환 / admin(`user_teams` 없음)은 미배정 아님→`view-user` / soft-deleted 팀 목록 제외 / 회귀(배정 사용자 `view-user`·비로그인 `view-guest`)

### #13. `/팀이름` 비로그인 공개 포털

📖 섹션 7, 섹션 9 (공개 포털 데이터 노출 정책)
**의존: ← #10**

- [x] **구현**
  - [x] `/팀이름` 동적 라우트 (예약어 차단) — `app.py` `@app.get("/{team_name}")` (라우트 정의 영역 맨 끝 = 모든 정적 페이지 라우트 뒤; FastAPI 등록 순서 = 매칭 순서). `_TEAM_NAME_RE`(`^[A-Za-z0-9_]+$`) + `RESERVED_TEAM_PATHS`(계획서 §4 하드코딩 + 실제 등록 라우트 첫 세그먼트 합집합, casefold 비교) → 불일치/예약어/없는 팀 모두 404. 정규식 검사는 핸들러 안(`Path(pattern)` 422 회피).
  - [x] **URL 대소문자 정확 일치** — `db.get_team_by_name_exact`(`WHERE name = ?`, SQLite 기본 BINARY collation). `/ABC` 유효 시 `/abc` → None → 404.
  - [x] **데이터 필터 (공개 포털 노출 쿼리)**: `is_public=1` + 히든 프로젝트 제외 — `db.get_public_portal_data(team_id)`: 칸반=`get_kanban_events(team_id, viewer=None)`, 간트=`get_project_timeline(team_id, viewer=None)`, 체크=`get_checklists(viewer=None)` 후 team_id 필터, 문서=`meetings WHERE team_id=? AND is_public=1 AND is_team_doc=1`. 메뉴 설정은 데이터 필터에 안 넣음.
  - [x] **메뉴 노출 설정**: UI 진입(탭/링크)만 차단 — `db.get_team_menu_visibility(team_id)`(team_menu_settings 행 + 없는 키는 계획서 §9 기본값 fallback: kanban/gantt/doc/check ON, calendar OFF). 템플릿 `{% if m.kanban %}` 등으로 탭 조건부 렌더. [ ] team_menu_settings 시드는 #19 책임 — 이번엔 빈 상태 + fallback 기본값으로 동작 (골격만).
  - [x] 따라서 캘린더 메뉴 OFF여도 같은 events 데이터가 칸반/간트 메뉴를 통해 노출 — `portal.calendar`는 칸반 events 풀 재활용, 데이터 필터에 메뉴 무관.
  - [x] 히든 프로젝트 항목 완전 차단 (`is_public` 값과 무관) — 위 세 DB 함수가 `viewer is None`일 때 `is_private=1 OR is_hidden=1` 프로젝트 제외 SQL + `get_blocked_hidden_project_names(None)` 적용. `is_public=1` 히든 프로젝트 항목 비노출 검증 완료. (SSE는 #13 범위 밖 — 포털은 SSR만.)
  - [x] "계정 가입" 버튼 노출 (가입 후 자동 로그인 + `/`로 이동) — 템플릿 `{% if not user %}` `<a href="/register" class="btn btn-sm btn-primary">계정 가입</a>` (가입 후 자동 로그인은 #8 동작).
  - [x] 삭제 예정 팀: 안내 페이지만 표시 — `team.deleted_at` 있으면 `deleted=True`로 안내 블록만, `portal` 컨텍스트 미전달 → 가입 버튼·팀 신청·공개 데이터·탭 모두 비노출. admin이면 `/admin` 링크.
  - [ ] (로그인 사용자 UI: "팀 신청 / 가입 대기 중" 버튼 분기) — #14 범위. 이번 #13은 라우트가 로그인이어도 200 공개 포털을 주되 redirect 안 함까지만.
- [x] **검증** — `tests/phase82_team_portal.py` 8/8 PASS (TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 비로그인 브라우저 재현 불가)
  - [x] 비로그인 사용자가 비공개 항목(`is_public=0` 일정/체크/문서)·히든 프로젝트 항목(`is_public=1`이어도)·개인 문서(`is_team_doc=0`)에 접근 못 함 — 마크업 부재 검증
  - [x] `/팀이름` 라우트가 `/admin`(admin 로그인 페이지)·`/api/health`·`/docs`·`/redoc`·`/openapi.json`·`/static/<파일>`(mount 살아 있음)과 충돌 없음 + 예약어 20종 각각 포털 마크업 부재
  - [x] 같은 이름 다른 대소문자(`/ABC` vs `/abc`)는 404로 분리 + `/Bad-Name`(하이픈, 정규식 불일치) 404 + 삭제 예정 팀 안내 페이지 + 로그인 사용자 200 포털(redirect 아님)
  - [x] 회귀: `tests/phase80_landing_page.py`(#11) 5/5, `tests/phase81_unassigned_user.py`(#12) 9/9 PASS. `tests/test_project_rename.py` 2 FAIL은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, #13 무관)

### #14. `/팀이름` 로그인 사용자 (소속 무관)

📖 섹션 7 팀에 배정된 로그인 사용자, 시스템 관리자
**의존: ← #13**

- [x] **구현**
  - [x] 로그인 상태에서도 `/팀이름`은 공개 포털만 표시 (admin도 동일, 리다이렉트 X) — `app.py team_public_portal` 은 `RedirectResponse` 미사용, 로그인/admin 모두 200 공개 포털. `my_team_status` 컨텍스트(`approved`|`pending`|`rejected`|`None`)를 템플릿에 전달.
  - [x] 홈 버튼은 `/`로 이동 — `templates/team_portal.html` `.portal-hero-actions` 의 `<a href="/" ...>홈</a>` (#13 에서 이미 구현, 유지).
  - [x] 팀 미소속 + pending 상태에 따라 "팀 신청" / "가입 대기 중" 버튼 분기 — 계획서 §7 표 전부: 비로그인=`/register` "계정 가입"(#13 유지) / `my_team_status=='approved'`=버튼 없음 / `=='pending'`="가입 대기 중"(disabled) / `user.role=='admin'`=버튼 없음(슈퍼유저 — §7 표에 admin 행 없음, 본 구현 결정) / else(미소속·rejected)="팀 신청"(`onclick="applyToTeam(team.id)"`). admin 분기는 `else` 앞에 배치(admin 은 `my_team_status=None` 이라 안 그러면 "팀 신청"으로 떨어짐). `applyToTeam` JS 는 home.html 의 것을 `team_portal.html` `{% block scripts %}` 에 최소 복제(버튼 1개 — surgical). `pending_other`(다른 팀 pending)는 #12 패턴대로 새 UI 안 만들고 서버 에러에 위임 — `/팀이름` 에서 "이 팀에 pending" 아니면 그냥 "팀 신청".
- [x] **검증** — `tests/phase83_team_portal_loggedin.py` 9/9 PASS (TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 특정 사용자 상태 브라우저 재현 불가)
  - [x] 미소속 로그인(신청 이력 없음)→"팀 신청" 노출·"가입 대기 중"·"계정 가입" 부재 / 이 팀 pending→"가입 대기 중"(disabled)·"팀 신청" 부재 / 다른 팀 pending(이 팀 미소속)→"팀 신청" 노출 / approved 멤버→가입 버튼 모두 부재 / rejected→"팀 신청" 재노출 / admin→200(redirect 아님)·가입 버튼 부재·포털 본문 정상 / 비로그인→"계정 가입" 그대로(#13 회귀)
  - [x] 모든 케이스 status 200·redirect(30x) 없음·홈 버튼 `href="/"` 존재
  - [x] 회귀: `tests/phase80_landing_page.py`(#11) 5/5, `tests/phase81_unassigned_user.py`(#12) 8/8, `tests/phase82_team_portal.py`(#13) 8/8 PASS (phase83 포함 30/30). `tests/test_project_rename.py` 2 FAIL 은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, #14 무관)

### #15. 프로필 메뉴 "팀 변경" UI + work_team_id 쿠키

📖 섹션 7 현재 작업 팀 선택, 섹션 16 권한 원칙
**의존: ← #10**

- [x] **구현 (서버)** — `auth.py`/`database.py`/`app.py` (스키마 무변경 — 신규 DB 헬퍼 SELECT 전용)
  - [x] `/api/me/work-team` (POST): 팀 변경 검증(`_check_csrf`+로그인 401+int 400+`get_team_active` 404+`require_work_team_access` 403) + `work_team_id` 쿠키 갱신(`_set_work_team_cookie`, max_age 1년·samesite=lax·path=/). (+ `GET /api/me/work-team` 도 추가 — 현재 작업 팀+선택 가능 팀 목록, 프로필 드롭다운용)
  - [x] SSR 첫 페이지 렌더 시 쿠키 읽고 검증, 없으면 대표 팀 계산해 Set-Cookie — `_ensure_work_team_cookie(request, response, user)` 헬퍼를 SSR 페이지 12개(`/`·`/calendar`·`/admin`·`/kanban`·`/gantt`·`/project-manage`·`/doc`·`/doc/new`·`/doc/{id}`·`/doc/{id}/history`·`/check`·`/trash`)에서 호출. 미배정·비로그인은 noop.
  - [x] **일반 사용자 기본값 (쿠키 없음)**: 대표 팀 = `db.primary_team_id_for_user(uid)` = `user_teams.status='approved'` AND `teams.deleted_at IS NULL` 중 `joined_at` 가장 이른 팀 (없으면 legacy `users.team_id`(비삭제일 때만) → None). (실제 컬럼명은 `status`/`joined_at` — todo §#2 명세의 `join_status` 와 다름, 기존 코드 컨벤션 준수.)
  - [x] **admin 기본값 (쿠키 없음)**: `db.first_active_team_id()` = `teams.deleted_at IS NULL` 중 `id` 가장 작은 팀 ('마지막 선택 팀'은 `work_team_id` 쿠키가 담당 — 쿠키 없을 때 첫 비삭제 팀으로 fallback, 계획서 §7).
  - [x] 저장된 작업 팀이 무효(삭제 예정·소속 빠짐) 시 쿠키 무시 + 새 대표 팀(admin은 첫 비삭제 팀) — `resolve_work_team` 쿠키 경로에 `user_can_access_team(user, ctid)` AND `_team_is_active(ctid)` 검증 추가(이전엔 무조건 신뢰). 무효면 `_work_team_default` fallback → `_ensure_work_team_cookie` 가 Set-Cookie 갱신.
  - [x] 모든 팀 컨텍스트 API가 `team_id` 파라미터를 명시적으로 받고 `require_work_team_access`/`_work_scope`로 검증 — #10 에서 골격 완성(라우트 ~20개), #15 는 쿠키 검증을 `resolve_work_team` 내부로 흡수해 자연 연결. `_work_scope` 자체는 무변경(admin None·explicit 우선 그대로).
- [x] **구현 (프론트엔드)** — `templates/base.html`·`kanban.html`·`project.html`·`calendar.html`·`doc_list.html`·`static/css/style.css`
  - [x] 프로필 메뉴에 "👥 팀 변경" 버튼 + 접히는 서브리스트 (`#work-team-list`, `GET /api/me/work-team` 으로 동적 로드, 현재 작업 팀엔 ✔, 이름은 `textContent` 주입)
  - [x] 팀 선택 시 `POST /api/me/work-team {team_id}` → `r.ok` 면 `location.reload()`, 실패면 `showToast`/`alert(detail)`
  - [x] 프로필 헤더에 현재 작업 팀 이름 표시 — `{{ user.name }} · {{ work_team_name }}`, admin은 `… · HW팀(슈퍼유저)` (계획서 §7 예시). `work_team_name` 없으면(미배정/팀 0개) 이름만. `current_user_payload` 에 `work_team_id`/`work_team_name` 추가.
  - [x] 기존 칸반·간트의 화면별 팀 선택 `<select id="team-filter">` + `_applyInitialTeamFilter` + localStorage 키(`kanban_team_filter`/`proj_team_filter`) 제거 → `loadKanban`/`loadData` 가 team_id 없이 fetch(서버 쿠키 결정). 캘린더는 원래 화면별 드롭다운 없음 — `CURRENT_USER.team_id` → `work_team_id` 4곳(kanban/calendar 멤버칩·calendar 편집 게이팅 힌트·doc_list weekly-team 기본값) 교체.
- [x] **검증** — `tests/phase84_work_team_cookie.py` 19/19 PASS (TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 다중 팀/admin 브라우저 재현 불가)
  - [x] 첫 로그인(쿠키 없음) → 대표 팀(joined_at 가장 이른) 자동 선택 + Set-Cookie / admin → 첫 비삭제 팀 + Set-Cookie / 유효 쿠키 → 사용·갱신 없음 / 무효 쿠키(삭제 예정·추방) → 새 대표 팀 갱신 / 미배정·비로그인 → Set-Cookie 없음
  - [x] 작업 팀 전환(`POST /api/me/work-team`) 후 캘린더(`/api/events`)·칸반(`/api/kanban`)·간트(`/api/project-timeline`)·문서(`/api/doc`)·체크(`/api/checklists`) 모두 새 팀 컨텍스트로 / #10 회귀(명시 `?team_id` 우선)·admin `_work_scope`=None(전 팀 노출) / `POST` 비소속 403·삭제 예정 404·비로그인 401 / `GET /api/me/work-team` 비admin=소속 팀·admin=전체 비삭제 팀. 회귀: phase80~83 30/30 PASS. `test_project_rename.py` 2 FAIL은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, #15 무관).

### #15-1. 히든 프로젝트 다중 팀 전환

📖 섹션 12, 섹션 8-1 (히든 프로젝트 추가 예외)
**의존: ← #5, #6, #15** (work_team_id 도입 후 헬퍼 전환 가능)

> 기존 히든 프로젝트 코드는 owner의 `users.team_id`를 단일 팀 기준으로 사용한다. 다중 팀 모델로 전환하면서 `projects.team_id`와 `user_teams`로 기준을 옮긴다.

- [x] **구현 (생성)**
  - [x] `create_hidden_project`: owner의 `users.team_id` 참조 제거 → 요청의 `work_team_id` 기준 `team_id` 저장 (team_id=None 시 `ValueError` — NULL row 생성 금지)
  - [x] `require_work_team_access`로 작성자가 그 팀 승인 멤버임을 검증 (라우트 `POST /api/manage/hidden-projects` — #15에서 이미 적용)
- [x] **구현 (멤버 후보 헬퍼 전환)**
  - [x] `get_hidden_project_addable_members` 등 멤버 후보 조회: `users.team_id` 단일 비교 → `user_teams.status='approved'` + `projects.team_id` 비교 (`_hidden_project_visible_row`·`add_hidden_project_member`·`transfer_hidden_project_owner`·`admin_change_hidden_project_owner`·`transfer_hidden_projects_on_removal` 동일 substitution)
  - [x] **`owner_id = NULL` 복구 시에도 owner의 팀이 아니라 `projects.team_id` 기준으로 후보 조회** — `get_hidden_project_addable_members`·`add_hidden_project_member`가 owner 미참조, `projects.team_id` 기준
  - [x] admin은 멤버 후보 명단에서 자동 제외 — `role != 'admin'` 필터 + admin은 user_teams row 없음(이중 보장)
  - [x] 일정/업무 담당자(`assignee`) 후보도 동일하게 `project_members`로 제한 — `_assert_assignees_in_hidden_project` 이미 `get_hidden_project_members` 멤버 이름만 비교 (변경 불필요, 이미 만족)
- [x] **구현 (admin 동등 관리 기능)**
  - [x] admin은 owner 자리를 차지하지 않지만 owner 동등 기능 사용 — 라우트 `_require_hidden_can_manage`(owner OR admin) + `add_hidden_project_member`/`admin_change_hidden_project_owner` owner 미참조로 owner_id NULL에도 동작
  - [x] `owner_id = NULL` 복구 흐름: admin이 같은 팀 승인 멤버 추가 → 그 멤버를 owner로 지정 (admin 자신은 owner 안 됨) — phase85 test_b 검증
- [x] **구현 (owner 자동 이양)**
  - [x] `transfer_hidden_projects_on_removal()`도 `user_teams` 기준으로 동작 — 다음 owner 후보 쿼리 `u.team_id = p.team_id` → `user_teams approved EXISTS on projects.team_id` (단일 caller `admin_update_user`는 레거시 단일 팀 경로; 다중 팀 부분 추방 시나리오는 그 라우트 자체가 레거시라 이번 범위 밖)
- [x] **검증**
  - [x] owner가 팀에서 추방 → `project_members` 중 같은 팀 활성 멤버에게 added_at 오름차순 자동 이양 (phase85 test_a)
  - [x] 후보 없으면 `owner_id = NULL` → admin이 신규 멤버 추가 후 owner 지정 가능 (phase85 test_b)
  - [x] admin이 멤버 후보 드롭다운·assignee 후보에 노출 안 됨 (phase85 test_c)
  - [x] 다중 팀 사용자가 owner인 히든 프로젝트는 `projects.team_id` 팀 기준으로만 멤버 후보 조회 (phase85 test_d)
  - [x] (추가) 멀티팀 가시성 — project_members + user_teams approved 기준, 팀 빠지면 안 보이고 재가입 시 복구 (phase85 test_e), owner_id NULL에도 addable_members가 projects.team_id 기준 (phase85 test_f)
  - 검증 방식: TestClient/직접 DB + 임시 DB (운영 서버 IP 자동 로그인이라 다중 팀 owner 시나리오 브라우저 재현 불가). `tests/phase85_hidden_project_multiteam.py` 11/11 PASS. 회귀 phase80~84 49/49 PASS. Playwright `phase46_hidden_project_*` 는 서버 재시작 후 별도 확인 권장(프론트 무변경). 사전 결함 `test_project_rename.py` 2 FAIL(옛 픽스처 DB에 projects.team_id 없음)은 #15-1 무관.

### #15-2. links 기능 다중 팀 전환

📖 섹션 13 links 테이블 정리, 섹션 16 권한 원칙
**의존: ← #2, #15** (work_team_id 도입 후 라우트 전환 가능)

> 현재 `/api/links`는 단일 `users.team_id` 기준으로 동작하므로 다중 팀 모델 전환에서 `work_team_id` 또는 명시 `team_id` 기반으로 옮긴다. 백필(`#4`)과 완전 삭제(`#23`)에는 이미 들어있지만 라우트 전환 항목이 별도로 필요하다.

- [x] **구현 (라우트 전환)** — 2026-05-13 완료
  - [x] `GET /api/links` 조회: `scope='personal'`은 작성자 본인 row만, `scope='team'`은 `work_team_id` 또는 명시 `team_id`의 승인 멤버에게만 노출 (`require_work_team_access` 검증) — `_work_scope(request, user, team_id)` → `db.get_links(name, work_team_ids)`. admin은 `_work_scope`→None→전 팀 노출(/api/checklists·events와 일관).
  - [x] `POST /api/links` 생성: `scope='team'`일 때 `team_id`를 `work_team_id`로 확정해 저장 (`personal`은 NULL 유지). admin은 `work_team_id` 명시 필수 — `auth.resolve_work_team(explicit_id=data.team_id)`; None→400; `require_work_team_access`(비admin 비소속→403).
  - [x] `PUT /api/links/{id}` 수정: **작성자 본인 + admin만** 편집 가능 (`scope='team'`/`personal` 모두). 같은 팀 멤버라도 작성자가 아니면 403. 일정·체크의 팀 공유 모델과 다른 예외 — 링크는 작성자가 직접 큐레이팅하는 자료라는 운영 의도(계획서 섹션 8-1) — `db.update_link(..., role)` admin 분기 추가(delete_link 패턴).
  - [x] `DELETE /api/links/{id}` 삭제: 위와 동일 권한 모델 (작성자 본인 + admin만) — `db.delete_link(..., role)` 이미 admin 분기 있음; default role `editor`→`member` 정리만.
- [x] **구현 (헤더 드롭다운 UI)** — 변경 불필요 (백엔드만으로 자연 반영)
  - [x] 헤더 링크 드롭다운이 현재 작업 팀의 `scope='team'` 링크 + 본인 `personal` 링크를 통합 표시하도록 갱신 — `_loadLinks()`가 드롭다운 open마다 `fetch('/api/links')` → 백엔드가 작업 팀 기준 응답 → 통합 표시. base.html JS 변경 없음.
  - [x] 작업 팀 전환 시 드롭다운이 새 팀 컨텍스트로 갱신 — `selectWorkTeam` → `POST /api/me/work-team` → `location.reload()` (base.html:1020·1027) → reload 후 다음 드롭다운 open 시 새 work_team_id 쿠키로 fetch. 자연 반영.
- [x] **검증** — `tests/phase86_links_multiteam.py` 13 PASS (TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 다중 팀/admin 브라우저 재현 불가)
  - [x] 다중 팀 사용자가 작업 팀 전환 → 헤더 링크 드롭다운이 새 팀의 `scope='team'` 링크로 바뀜 (쿠키 work_team_id; 명시 ?team_id 우선)
  - [x] 다른 팀 멤버 세션에서는 그 팀 `scope='team'` 링크가 안 보임 (명시 ?team_id로도 비소속 → 무시·대표팀 fallback)
  - [x] `personal` 링크는 작성자 본인에게만 노출 (작업 팀과 무관)
  - [x] admin이 `work_team_id` 명시 후 `scope='team'` 링크 생성·편집·삭제 가능 (+ admin GET은 전 팀 scope='team' 링크 노출)
  - [x] **같은 팀 멤버 A가 만든 `scope='team'` 링크를 멤버 B가 편집·삭제 시도 → 403** (작성자/admin만 허용 정책)
  - [x] admin이 다른 사용자가 만든 `scope='team'` 링크 편집·삭제 가능 (+ 타인 personal 링크도 admin 가능; admin이 work_team 없이 scope='team' POST → 400)
  - [x] 회귀: phase80~85 60 PASS

### #15-3. team_notices 팀별 공지 전환

📖 섹션 13 team_notices 팀별 공지 전환
**의존: ← #2, #4, #15** (Phase 1 컬럼 + Phase 2 백필 + work_team_id 도입 후)

> 현재 `team_notices`는 `team_id`가 없는 전역 공지 구조(`/api/notice` 단일 라우트). 1차 팀 기능 작업에서 팀별 공지로 전환한다(사용자 결정).

- [x] **구현 (라우트 전환)** — 헬퍼 `_notice_work_team(request, user, explicit_id)`(app.py) + DB 헬퍼 rename. 자세히는 단위 사이클 기록 참조.
  - [x] `GET /api/notice`: `team_id` 쿼리 파라미터 추가, `_notice_work_team`(비admin 비소속 explicit 무시·대표 팀 fallback / admin explicit 신뢰) 결정 후 `db.get_notice_latest_for_team(tid) or {}`. 비로그인/미배정 → `{}`. NULL 잔존 row는 GET 미반환 — admin 이력 화면(`get_notice_history(..., include_null=True)`)에서만 노출
  - [x] `POST /api/notice`: `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))` → None이면 400 → `auth.require_work_team_access` → `db.save_notice(content, team_id, user["name"])` (`/api/links` POST 패턴 미러; admin은 work_team 쿠키/명시 없으면 400)
  - [x] `POST /api/notice/notify`: 같은 패턴으로 work_team 결정(None→400) → `db.get_notice_latest_for_team(team_id)`(없으면 `{"ok":False,"reason":"no_notice"}`) → `db.create_notification_for_team(team_id, "notice", msg, exclude_user=user["name"])` (전역 `create_notification_for_all`→팀별; 글로벌 admin은 user_teams row 없어 미수신)
  - [x] **작성·갱신 권한 = 팀 공유 모델 (사용자 결정)**: `POST /api/notice`·`/notify` 에 작성자 본인 게이트 없음 — `_require_editor`(is_member) + work_team 접근 검증만. 같은 팀 승인 멤버 누구나 공지 작성·갱신·발송 가능 (계획서 섹션 8-1). 향후 운영 통제 강화가 필요하면 별도 작업으로 팀 관리자 전용 모델로 전환
- [x] **구현 (자동 정리 정책)**
  - [x] `save_notice` 의 30일 이전 / 100개 초과 자동 정리를 **팀별로 적용** (`WHERE team_id = ?` + 100개 캡 서브쿼리도 `WHERE team_id = ? ORDER BY id DESC LIMIT 100`). 전역 일괄 삭제 X. `team_id IS NULL` 잔존 row 는 자동 정리 대상 아님(운영자 사후 정리 — 계획서 §13)
- [x] **구현 (UI)**
  - [x] `/notice`, `/notice/history` 화면이 현재 작업 팀의 공지·이력만 표시 — SSR `notice`/`histories` 가 `_notice_work_team` 기준 조회(admin history 는 include_null=True) + `_ensure_work_team_cookie` 두 라우트에 추가(#15 SSR 12페이지 적용 시 빠졌던 분 보완). 템플릿(`notice.html`/`notice_history.html`/`base.html`) 코드 변경 없음 — SSR 값만 바뀌어 자연 반영
  - [x] 작업 팀 전환 시 화면이 새 팀 공지로 갱신 — base.html `selectWorkTeam`→`location.reload()`(#15) 흐름으로 새 work_team_id 쿠키 적용 후 SSR 다시 렌더
- [x] **검증** — `tests/phase87_team_notices_multiteam.py` 10/10 PASS (TestClient + 임시 DB — 운영 IP 자동 로그인이라 다중 팀/admin 브라우저 재현 불가) + 회귀 phase80~86 73 PASS
  - [x] 다중 팀 사용자가 작업 팀 전환 → 공지 화면(GET /api/notice)이 새 팀 공지로 바뀜 (명시 ?team_id 우선)
  - [x] 다른 팀 공지가 노출되지 않음 (명시 ?team_id 비소속도 무시·대표 팀 fallback)
  - [x] 공지 발송 알림이 같은 팀 approved 멤버에게만 도착(발송자 제외·pending 미수신·글로벌 admin 미수신), 다른 팀에는 미도착
  - [x] 자동 정리 후 다른 팀 공지는 영향 없음 (팀A 100개 캡·30일 정리 → 팀B·NULL orphan 그대로)
  - [x] NULL 잔존 row는 admin 외에 안 보임 (GET 미반환·비admin history 미포함·admin history 포함)
  - [x] 팀 공유 모델: 같은 팀 멤버 B 가 멤버 A 작성 공지 갱신·발송 가능 (links 와 반대)

---

## 그룹 C: 관리·통합 기능 (#16~#22)

### #16. 시스템 관리자 슈퍼유저 권한 정리

📖 섹션 2, 섹션 7 시스템 관리자, 섹션 16 권한 원칙
**의존: ← #2, #15**

- [x] **구현**
  - [x] admin이 `/`, `/doc`, `/check`, `/calendar`, `/kanban`, `/gantt`, `/project-manage`에 슈퍼유저로 진입
  - [x] admin의 쓰기 요청에서 `work_team_id` 명시 검증, 미선택 시 400
  - [x] `/admin`은 운영 기능 중심으로 정리 (일반 자료 편집은 일반 화면 사용)
- [x] **검증**
  - [x] admin이 일반 화면에서 모든 팀 자료를 보고 편집 가능
  - [x] admin이 팀 미선택 상태에서 쓰기 시도 → 400

> 2026-05-13: `auth.require_admin_work_team` 헬퍼 도입(묵시 first_active fallback 금지) + 10개 create 라우트 적용. QA 1차에서 비admin 비소속 explicit team_id 가 silent로 본인 대표 팀에 떨어지는 회귀가 발견되어 헬퍼 비admin 분기에 `explicit_id is not None → 403` 가드 추가(phase86/87 보안 경계 복원). phase86/87 정적 marker drift 패치 + phase88 case12 단언 반전(403)으로 마무리. 회귀 53/53 PASS, phase88 17/17 PASS. (test_project_rename 2건 실패는 #15-1 도입 후 선재 결함, #16과 무관.)

### #17. 팀 생성/관리 페이지

📖 섹션 4, 섹션 11 팀 관리자 지정, 섹션 13 제거 대상 API
**의존: ← #2**

- [x] **구현**
  - [x] 팀 생성 API: **팀명 정규식 `^[A-Za-z0-9_]+$`** (영문 대소문자·숫자·언더바만 허용)
  - [x] **예약 경로 검사**: 하드코딩 목록 + 실제 등록 route 목록을 **함께** 검사 (누락 방지)
    - 하드코딩 목록: `api`, `admin`, `doc`, `check`, `kanban`, `gantt`, `calendar`, `mcp`, `mcp-codex`, `uploads`, `static`, `settings`, `changelog`, `register`, `project-manage`, `ai-import`, `alarm-setup`, `notice`, `trash`, `remote`, `avr`, `favicon.ico`, `docs`, `redoc`, `openapi.json`
  - [x] `name_norm` UNIQUE 검사 (대소문자·NFC 정규화 후)
  - [x] **팀 이름은 생성 후 변경 불가** — 입력 대소문자 그대로 저장 + URL 세그먼트로 그대로 사용
  - [x] 팀 관리자 지정 UI: 같은 팀 멤버 중 선택해 `team_role = admin`
  - [x] 팀 관리자 다중 지정 가능
  - [x] **`PUT /api/admin/teams/{team_id}` 제거 + 프론트의 팀 이름 수정 UI 제거**
- [x] **검증**
  - [x] 예약어로 팀 생성 시 차단 (하드코딩 목록 + 실제 등록 route 모두)
  - [x] 같은 이름 다른 대소문자 팀 생성 시 차단 (`ABC` 존재 시 `abc` 생성 시도 → 차단)
  - [x] NFC 정규화 차이로도 중복 차단 (한글 결합형/조합형)
  - [x] 정규식 위반(공백·특수문자·한글) 시 차단

### #18. 멤버 관리 페이지

📖 섹션 10 멤버 관리
**의존: ← #8, #17**

- [x] **구현**
  - [x] 팀 드롭박스 + 신청 멤버 수락/거절 + 기존 멤버 추방
  - [x] 자기 자신 강등·추방 제한
  - [x] 마지막 팀 관리자 보호 (삭제 예정 팀은 면제)
  - [x] IP 관리 영역은 admin 세션에만 노출 (팀 관리자에게 숨김)
  - [x] 다중 소속 사용자의 IP 변경 시 영향 안내 표시 (계획서 §10 600 — IP 영역은 /admin 으로 이동, 안내 문구 포함)
- [x] **검증**
  - [x] 팀 관리자 1명 상태에서 자기 자신 추방 시도 → 차단
  - [x] 삭제 예정 팀의 마지막 팀 관리자 추방 가능
  - [x] 팀 관리자 세션에 IP 관리 UI 안 보임

### #19. 메뉴 외부 노출 관리 페이지

📖 섹션 9 1단계 메뉴 노출
**의존: ← #17**

- [x] **구현**
  - [x] `team_menu_settings` 기반 팀별 메뉴 토글 (kanban/gantt/doc/check/calendar)
  - [x] 기본값 적용 (캘린더만 X, 나머지 O — `_PORTAL_MENU_DEFAULTS` fallback)
  - [x] 시스템 관리자·팀 관리자가 설정 가능
- [x] **검증**
  - [x] 메뉴 OFF → `/팀이름` 공개 포털에서 해당 메뉴 진입 사라짐 (#13에서 적용 완료)
  - [x] 메뉴 OFF는 데이터 차단이 아니라 UI 진입 차단임을 확인 (`get_public_portal_data` 가 데이터는 그대로 채우고 menu 만 분기)

### #20. 문서·체크 항목별 공개 설정

📖 섹션 9 2단계 항목별 공개 설정
**의존: ← #10**

- [x] **구현** (이전 사이클 흡수 — 본 사이클은 invariant lock)
  - [x] meetings·checklists 작성 폼에 공개 토글 (doc_editor.html / check_editor.html — #10 이전 사이클에서 완료)
  - [x] **공개 포털 데이터 쿼리**: `is_public=1` 조건 + 히든 프로젝트 제외만 적용 (`get_public_portal_data`, `get_kanban_events(viewer=None)`, `get_checklists(viewer=None)`, `get_project_timeline(viewer=None)` 의 public_filter — 메뉴 토글 미참조 정적 invariant 검증 완료)
  - [x] 메뉴 노출은 UI 진입(탭/링크)에서만 분기 (`team_portal.html` `{% if m.X %}` — tab+panel 양쪽)
- [x] **검증**
  - [x] 공개 항목·비공개 항목이 외부 포털에서 의도대로 노출/차단 (phase92 INV1)
  - [x] 캘린더 메뉴 OFF + 칸반 메뉴 ON일 때, 같은 events row가 칸반에는 노출되고 캘린더 진입은 막힘 (phase92 INV6 — 데이터 payload 양쪽 존재 + 정적 INV7 panel 게이팅)

### #21. 알림 통합 표시 + 작업 팀 자동 전환

📖 섹션 8 알림 동작
**의존: ← #4, #15**

- [ ] **구현**
  - [ ] 알림 통합 표시 쿼리: `team_id IN user_team_ids(user)` (승인 + 비삭제)
  - [ ] 추방·탈퇴된 팀의 알림은 화면 필터로 제외 (row는 유지)
  - [ ] 알림 항목에 발생 팀 이름 표시
  - [ ] 알림 클릭 → `notifications.team_id`로 작업 팀 자동 전환 + toast
  - [ ] `team_id IS NULL` 알림은 작업 팀 전환 없이 알림 페이지에만
- [ ] **검증**
  - [ ] 다른 팀 알림 클릭 → 작업 팀 전환 + 상세 화면 이동
  - [ ] 추방된 팀의 과거 알림이 안 보임
  - [ ] 재가입 후 그 팀 알림 다시 보임

### #22. MCP 팀 범위 + admin 토큰 차단

📖 섹션 16-1 MCP 팀 범위
**의존: ← #2, #3**

- [ ] **구현**
  - [ ] MCP read-only 도구 기본 조회 범위: 모든 승인·비삭제 팀
  - [ ] `team_id` / `team` 선택 파라미터 추가
  - [ ] 프로젝트 필터: `project_id` 우선, `project` 문자열은 `team_id` 동반 시만 허용
  - [ ] 결과에 `team_id`, `team_name`, `project_id`, `project_name` 포함
  - [ ] 일반 사용자/멤버 조회에서 admin 제외
  - [ ] **admin의 MCP 토큰 발급 차단**: 토큰 생성/재발급 UI 비노출 + 토큰 생성 API 403
- [ ] **구현 (히든 프로젝트 가시성 — 모든 채널 차단 원칙)**
  - [ ] `list_events` / `list_documents` / `list_checklists` / `list_projects` / `search_*` 모두 **히든 프로젝트 멤버십 필터 적용**:
    - `is_hidden=1` 프로젝트는 토큰 사용자가 `project_members`에 포함된 경우에만 결과에 포함
    - 비포함 시 프로젝트·해당 프로젝트의 events/checklists/meetings 모두 결과에서 완전 제거 (이름·내용 노출 금지)
  - [ ] `get_*` 단건 조회도 동일 필터 적용 (project_id로 직접 조회하더라도 히든 + 비멤버면 404 응답, 존재 여부도 노출하지 않음)
  - [ ] `team_id` 파라미터로 강제 조회해도 히든 멤버십 검증은 그대로 적용 (MCP 우회 경로 차단)
- [ ] **마이그레이션 (Phase 2에서 처리)**
  - [ ] admin의 기존 `mcp_token_hash` **및 `mcp_token_created_at`** 둘 다 NULL 초기화 (hash만 비우면 UI에 토큰 보유 표시가 잔존할 수 있음 — `#3`과 일관)
- [ ] **검증**
  - [ ] admin 세션에서 토큰 발급 UI 안 보임 + API 403
  - [ ] 일반 사용자 MCP에서 다중 팀 데이터 통합 조회
  - [ ] 같은 팀 멤버이지만 히든 프로젝트 비멤버인 사용자 토큰으로 MCP 호출 시 히든 프로젝트 데이터가 어떤 도구에서도 노출되지 않음
  - [ ] `get_project(id=히든)` 직접 호출도 비멤버에게 404

---

## 그룹 D: 운영 정책 (#23~#24)

### #23. 팀 삭제 (soft delete + 90일 유예)

📖 섹션 5 팀 삭제 및 복원
**의존: ← #2, #15, #21**

- [ ] **구현 (soft delete + 유예기간)**
  - [ ] `/admin`에서만 팀 삭제·복원 (팀 관리자 차단)
  - [ ] 삭제 시 `teams.deleted_at` 기록 (즉시 접근 차단)
  - [ ] 유예기간 중 `/팀이름` 접속 → "팀 삭제 예정" 안내 페이지 (계정 가입·팀 신청·공개 데이터 모두 비노출)
  - [ ] 유예기간 중인 팀은 신규 팀 신청 후보 제외
  - [ ] 대표 팀/현재 작업 팀 후보에서 삭제 예정 팀 제외
  - [ ] 사용자가 삭제 예정 팀만 남으면 팀 미배정처럼 처리
  - [ ] **유예기간 중 `user_teams` row는 삭제하지 않고 그대로 유지** — 접근 차단은 서버에서 `teams.deleted_at IS NOT NULL`로 처리, 복원 시 멤버 관계 자동 복구
  - [ ] 시스템 관리자 복원 기능
- [ ] **구현 (자동 완전 삭제 스케줄러)**
  - [ ] 매일 새벽 DB 백업 완료 이후 실행
  - [ ] 90일 경과한 팀의 데이터 삭제 — **명시적 순서 + 테이블 목록**:
    1. **`notifications` (`team_id` 컬럼 기준)** — 외래키 없으므로 events보다 먼저. events 삭제 후엔 `event_id → events.team_id` 매핑 불가
    2. **편집 이력**: `meeting_histories` (해당 팀 meetings 참조), `checklist_histories` (해당 팀 checklists 참조)
    3. **lock 테이블**: `meeting_locks`, `checklist_locks` (해당 팀 row 참조)
    4. **휴지통 참조**: events·checklists·meetings의 `trash_project_id`가 해당 팀 프로젝트를 가리키는 row (또는 통째 cascade)
    5. **본문 데이터**: `events`, `checklists`, `meetings`
    6. **프로젝트 보조**: `project_milestones`, `project_members` (해당 팀 프로젝트 참조)
    7. **프로젝트 본체**: `projects` (`team_id` 기준)
    8. **링크**: `links` (`team_id` 기준, `scope='team'` 위주. `scope='personal'`은 NULL이므로 제외)
    9. **공지**: `team_notices` (`team_id` 기준)
    10. **팀 설정**: `team_menu_settings` (`team_id` 기준)
    11. **멤버 관계**: `user_teams` (`team_id` 기준)
    12. **팀 본체**: `teams`
  - [ ] 누락 위험 줄이기: 위 목록을 단일 함수에 상수로 정의하고, 신규 팀 종속 테이블 추가 시 이 목록도 같이 갱신
  - [ ] **구현 직전 `database.py` / `sqlite_master` 기준 최종 대조** — 위 12단계 목록이 실제 코드의 모든 팀 종속 테이블을 커버하는지 점검. 빠진 테이블 발견 시 삭제 순서 상수에 반영하고 그 결과를 본 TODO에 갱신 (이 체크포인트는 dangling FK row가 90일 후 누적되는 사고를 막는다)
  - [ ] **업로드 폴더는 즉시 물리 삭제하지 않는다** (`uploads/teams/{team_id}/...` + `/uploads/meetings/...` 모두) — 별도 고아 파일 정리 작업이 DB 참조·보존 기간 확인 후 처리
- [ ] **검증**
  - [ ] 팀 삭제 → 즉시 접근 차단 + 안내 페이지
  - [ ] 복원 → `user_teams`가 보존되어 있어 모든 데이터·멤버 관계 자동 복구
  - [ ] 90일 경과 팀 → 자동 완전 삭제 + 같은 이름 재사용 가능
  - [ ] notifications row가 events 삭제 전에 먼저 정리되어 dangling 포인터 없음

### #24. 첨부파일 저장 정책 분기

📖 섹션 14 첨부파일 저장 정책
**의존: ← #10**

- [ ] **구현**
  - [ ] 문서(meetings) 첨부는 기존 `/uploads/meetings/YYYY/MM/...` 유지
  - [ ] 체크·일정 등 신규 첨부는 `uploads/teams/{team_id}/...` 분리
  - [ ] 문서 외 업로드 API에 `team_id` 확정/권한 검증 추가
  - [ ] 다운로드는 raw `StaticFiles` 공개가 아니라 DB 참조 권한 검증 (`_ProtectedMeetingStaticFiles` 패턴 확장)
- [ ] **검증**
  - [ ] 새 체크 첨부가 `uploads/teams/{team_id}/checks/` 아래 저장
  - [ ] 다른 팀 멤버가 직접 URL로 첨부 접근 시도 → 차단
  - [ ] 기존 `/uploads/meetings/...` 파일 정상 다운로드

---

## 그룹 E: 본 릴리스 후 별도 작업

### Phase 5: 호환 컬럼 drop (별도 릴리스)

📖 섹션 13 Phase 5
**의존: ← 본 릴리스 #1~#24 적용 + 운영 검증 기간 경과**

- [ ] **마이그레이션**
  - [ ] `users.team_id` drop (테이블 재생성, 자동 백업)
  - [ ] **`users.password` drop** (Phase 2에서 NULL 처리됨, 여기서는 컬럼 자체 제거)
  - [ ] `events.project`, `checklists.project` 문자열 컬럼 drop
- [ ] **구현 정리**
  - [ ] 코드에서 `users.team_id` / `users.password` / `events.project` / `checklists.project` 참조 흔적 정리
  - [ ] 호환용 헬퍼 제거

### 자동 휴식 페이지 (별도 작업)

📖 섹션 15 (1차 구현 범위 제외)

본 릴리스에 포함하지 않음. 별도 작업으로 분리. 필요 시 새 todo 항목으로 추가한다.

### 사용자 이름 변경 기능

📖 섹션 13 사용자 이름/표시명 주의

1차 구현에서는 제한 또는 시스템 관리자 전용 고위험 작업으로 둔다. 활성화 시 cascade 갱신 대상 컬럼이 많으니 별도 todo로 작성한다.

---

## 진행 추적 메모

각 그룹 완료 시 여기에 한 줄로 기록하면 좋다.

- [x] 그룹 A 완료 (#1~#10) — DB·인증 기반 + 데이터 백필 끝
- [x] 그룹 B 완료 (#11~#15-3) — 화면 정비 + work_team_id 도입 + 히든 프로젝트/links/team_notices 다중 팀 전환 끝
- [ ] 그룹 C 완료 (#16~#22) — 관리·통합 기능 끝
- [ ] 그룹 D 완료 (#23~#24) — 운영 정책 끝
- [ ] 그룹 E 진입 — Phase 5 호환 컬럼 drop 검토

### 단위 사이클 기록

| 날짜 | 항목 | 핵심 결과 | 산출물 |
|------|------|-----------|--------|
| 2026-05-10 | #1 DB 마이그레이션 인프라 | `PHASES`/`_PREFLIGHT_CHECKS` 확장 포인트 + 자동 백업(`whatudoin-migrate-*.db`, 90일 retention 공유) + `BEGIN IMMEDIATE` 수동 트랜잭션 + 마커·경고·`normalize_name` 헬퍼. PHASES 본문 SQL은 추가하지 않음 (#2 이후 책임). 검증 8/8 PASS + 운영 DB 복사본 no-op smoke PASS + 사전 조건 2건(`database.py:254` 빈 DB OperationalError, settings 테이블 정의 중복) 인지. | `backup.py:28-42`, `database.py:498-501,631-811`. archive: `archive/TeamA_001_DBInfra_20260510_220510/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_phase_infra.py, scripts/smoke_prod_db_noop.py}` |
| 2026-05-10 | #2 user_teams + name_norm + 권한 헬퍼 | Phase 1 본문(`team_phase_1_columns_v1`): 9개 컬럼 추가, `user_teams`/`team_menu_settings` 신규, projects 재구성(`name UNIQUE` 제거 + name_norm 추가, id 보존, `_PROJECTS_REBUILD_COLUMNS` 명시 15개). Phase 2 본문(`team_phase_2_backfill_v1`): users.name_norm·teams.name_norm·role:editor→member·user_teams 이관(admin 제외). Phase 4 본문(`team_phase_4_indexes_v1`): user_teams/team_menu_settings UNIQUE 2건. auth.py 신규 헬퍼 7개 + 기존 4개 위임. 사후 수정 4건(시드 name_norm, projects CREATE 컬럼 흡수, sqlite_sequence ON CONFLICT 무효 회피, checklists CREATE 순서). 사전 조건 #1(`database.py:254`) 함께 해결. T1~T4 37 checks + 권한 헬퍼 28 checks ALL PASS. | `database.py`, `auth.py`. archive: `archive/TeamA_002_UserTeams_20260510_223048/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_phase_migrations.py, scripts/verify_auth_helpers.py}` |
| 2026-05-10 | #3 admin 분리 + 관리팀 시드 | Phase 본문(`team_phase_3_admin_separation_v1`): admin team_id NULL, mcp_token NULL, user_ips whitelist→history 강등, user_teams admin 정리, 관리팀 분기 처리(`_ADMIN_TEAM_REF_TABLES` 10개 검사 → 참조 0건 DELETE / ≥1건 AdminTeam rename). 시드 갱신: 관리팀 자동 생성 제거 + admin team_id=NULL. admin 제외 보강은 grep으로 누락 0건 확인(의도적 미변경 5건 사유 기록). qa 1차 차단 1건 발견(`teams.name UNIQUE` 충돌) → fallback `관리팀_legacy_{id}` + `admin_separation` warning 누적 패치. 9/9 시나리오 PASS(S1~S7 + S4-extra 더블 참조 + S4-rerun warning 중복 가드). | `database.py`. archive: `archive/TeamA_003_AdminSeparation_20260510_230342/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_admin_separation.py}` |
| 2026-05-10 | #4 데이터 백필 (1차) | Phase 본문(`team_phase_4_data_backfill_v1`): events/checklists.team_id 추론(2번 작성자 단일 팀 — 1번은 #6 후 활성화, 3번 NULL+warning), meetings 4분기(팀 문서×정상/예외, 개인 문서×정상/예외), projects fallback 단계 1·2·4(단계 3 자동 생성은 #6), notifications.team_id(event_id→events.team_id), links.team_id(`scope='team'` + NULL만, 작성자 → 단일 팀), team_notices.team_id(작성자 → 단일 팀/대표 팀), pending_users 전건 삭제. 헬퍼 `__phase4_resolve_user_single_team`(user_teams 단건 → 다건 시 joined_at 최선 → legacy users.team_id → None). 5개 warning 카테고리(`data_backfill_events`, `data_backfill_meetings_team_doc_no_owner`, `data_backfill_projects`, `data_backfill_links`, `data_backfill_team_notices`). 9 시나리오 40/40 PASS, 합성 DB. 리뷰 차단 0건(경고 2건). | `database.py`. archive: `archive/TeamA_004_DataBackfill_20260510_233008/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_data_backfill.py}` |
| 2026-05-10 | #5 projects (team_id, name_norm) UNIQUE + 라우트 중복 검사 | Phase 본문(`team_phase_5_projects_unique_v1`): 잔존 `name_norm IS NULL` 백필 + `idx_projects_team_name` 부분 UNIQUE 인덱스(`WHERE team_id IS NOT NULL` — NULL 잔존 면제). preflight `_check_projects_team_name_unique`로 충돌 시 서버 시작 거부 + `preflight_projects_team_name` warning. DB 함수: `create_project(team_id=None)` 시그니처 확장, `create_hidden_project`의 `LOWER(name)` 전역 검사 → `(team_id, name_norm)` 팀 제한, `rename_project` cross-team 누출 차단(리뷰 발견 결함 패치). 라우트: POST /api/manage/projects, PUT /api/manage/projects/{name}, POST /api/manage/hidden-projects에서 `resolve_work_team` 사용으로 team_id 결정. 9/9 시나리오 PASS(빈 DB·NULL 백필·preflight 충돌·다른 팀 같은 이름·같은 팀 차단·NULL 면제·마커 강제 삭제·cross-team rename 비간섭·히든 프로젝트). | `database.py`, `app.py`. archive: `archive/TeamA_005_ProjectsUnique_20260510_235413/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_projects_unique.py}` |
| 2026-05-11 | #6 events/checklists.project_id 백필 + 자동 프로젝트 생성 | Phase 본문(`team_phase_6_project_id_backfill_v1`): events/checklists.project_id 매칭 백필(`(team_id, name_norm)`, deleted_at IS NULL 우선) + team_id 있고 매칭 실패 row의 자동 프로젝트 생성(같은 phase 내 캐시로 중복 방지, `(team_id, name_norm)` 부분 UNIQUE와 정합). team_id NULL row는 project_id NULL + `project_id_backfill_no_team` warning. project_milestones/project_members/trash_project_id dangling 검증(발견 시 `project_id_backfill_dangling_trash` warning, 데이터 변경 X). Phase 4 인덱스(`idx_events_project_id`, `idx_checklists_project_id`) 추가. 신규 쓰기 경로: INSERT INTO events/checklists + PATCH /api/events/{id}/project + `update_checklist`(리뷰 1차 차단 결함 패치)에서 project_id 동반 갱신. 읽기 경로 전환은 #10 책임. 17 케이스 PASS. | `database.py`. archive: `archive/TeamA_006_ProjectIdBackfill_20260511_001118/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_project_id_backfill.py}` |
| 2026-05-11 | #7 비밀번호 hash + 일반 로그인 이름+비밀번호 + name_norm UNIQUE | 신규 `passwords.py` 모듈(hash_password/verify_password). Phase 본문(`team_phase_7_password_hash_v1`): 평문 password → password_hash 일괄 변환(admin 포함, 빈 password 가드), 같은 트랜잭션에서 `password = ''` 처리(NOT NULL 제약 deviation으로 빈 문자열 — 자가 발견 결함 패치). preflight 2건(`_check_users_name_norm_unique`, `_check_teams_name_norm_unique`) → 충돌 시 서버 시작 거부. Phase 4 인덱스: `users.name_norm` 전역 UNIQUE, `teams.name_norm` UNIQUE. 라우트: POST /api/login 이름+비밀번호 + 정규식(`^[A-Za-z0-9가-힣]+$`) + name_norm 매칭 + admin 제외(더미 hash 비교로 timing 차이 최소화), POST /api/me/change-password 새 비밀번호 정책(영문+숫자 동시 포함) + hash 저장, POST /api/admin/login 내부 hash 검증으로 교체(외부 동작 동일). DB 함수 `get_user_by_login` 신규, `get_user_by_credentials` 내부 변경, `reset_user_password` hash 저장. **운영 DB 반영은 서버 재시작 시 phase 자동 적용** — 재시작 전 `users.name_norm`/`teams.name_norm` 충돌 검사 SQL은 qa_report.md 참고. import-time 63 PASS. | `database.py`, `app.py`, `passwords.py`(신규), `templates/base.html`. archive: `archive/TeamA_007_PasswordHash_20260511_004746/{backend_changes.md, code_review_report.md, qa_report.md, scripts/verify_password_hash.py, scripts/verify_login_routes.py}` |
| 2026-05-11 | #8 계정 가입과 팀 신청 분리 | `/api/register` 를 pending_users 승인 대기 → 즉시 가입+자동 로그인으로 교체: `db.create_user_account`(name_norm 정규화·`password=''`+`password_hash`·`role='member'`·`team_id=NULL`, 사전 SELECT + IntegrityError 이중 가드), 정규식·비밀번호 정책(#7 헬퍼 재사용), 예약어 차단(`RESERVED_USERNAMES` + `casefold`), set_cookie 자동 세션. 팀 신청 분리: `POST /api/me/team-applications`(`db.apply_to_team` — 임의 팀 pending 1건이라도 있으면 신규 차단, approved 중복 차단, rejected→같은 row pending 갱신·joined_at 보존), `GET/POST /api/teams/{team_id}/applications[/{user_id}/decide]`(`_require_team_admin` = 글로벌 admin 또는 `user_teams.role='admin'`, decide는 화이트리스트+pending row만). `db.list_team_applications`/`decide_team_application`/`get_team_active` 신규. 프론트: register.html(memo 제거·`/` 리다이렉트), base.html 모달 문구. 마이그레이션 phase 변경 없음 → 서버 재시작 불필요. `check_register_duplicate`/`create_pending_user` 데드코드 보존(Phase 5 drop 시 정리). 리뷰 차단 0(경고 2). 합성 DB+TestClient 72 PASS. | `app.py`, `database.py`, `templates/register.html`, `templates/base.html`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_team_a_008.py` |
| 2026-05-11 | #9 IP 자동 로그인 관리 | `database.py`: `IPWhitelistConflict` 예외 + helper(`find_whitelist_owner`, `get_whitelist_status_for_ip`, `set_user_whitelist_ip`/`admin_set_whitelist_ip`(한 트랜잭션 충돌검사+history 1개 승격 or INSERT, IntegrityError→Conflict), `remove_user_whitelist_ip`(type='history' 강등 row 보존), `delete_ip_row`, `toggle_ip_whitelist` enable 충돌 시 예외). 신규 phase `team_phase_4b_user_ips_whitelist_unique_v1`(#4 데이터 백필 직후 등록): `idx_user_ips_whitelist_unique ON user_ips(ip_address) WHERE type='whitelist'` 부분 UNIQUE. preflight `_check_user_ips_whitelist_unique`(같은 IP 2명+ whitelist → abort + `preflight_user_ips_whitelist` warning, 자동 정리 없음 — phase 5a 식 자동 dedup 미적용). `app.py`: `GET/POST/DELETE /api/me/ip-whitelist`(POST는 admin 403), `POST /api/admin/users/{id}/ips`, `DELETE /api/admin/ips/{id}`, `PUT /api/admin/ips/{id}/whitelist` 충돌 409 보강, `_require_login` 헬퍼. 프론트: `base.html` 사용자 설정 패널 자동 로그인 토글(admin/비로그인 숨김, ON 시 `wuDialog.confirm` 사양서 §6 문구, OFF 즉시 DELETE, 초기 상태 `GET /api/me/ip-whitelist`), `admin.html` IP 모달에 임의 IP 등록 input + row 삭제 버튼 + 409 토스트. `tools/migration_doctor.py` [4] user_ips whitelist 충돌 진단 + 권장 SQL 추가. QA 자가 발견 결함 1건(history 중복 row 일괄 승격 → IntegrityError): MIN(id) 1개만 승격으로 패치. 리뷰 차단 0(경고 2). 합성 DB+TestClient+마이그레이션 phase 직접 호출 59 PASS. **서버 재시작 필요**(phase 4b + 새 라우트). | `database.py`, `app.py`, `templates/base.html`, `templates/admin.html`, `tools/migration_doctor.py`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_team_a_009.py` |
| 2026-05-11 | A보강 자동 dedup phase + 운영자 도구 | 회사 운영 DB 첫 실행 안전망. 신규 phase `team_phase_5a_projects_dedup_safe_v1` (#5 앞에 등록): `(team_id, name_norm)` 충돌 그룹 중 events/checklists.project_id, project_members, project_milestones, trash_project_id, 문자열 project 참조 모두 0건인 row만 안전 hard DELETE(모든 row 0 참조면 MIN(id) 1개 살림). unsafe 그룹은 보존 → 이후 #5 preflight 거부 흐름 유지. warning 카테고리 `dedup_projects_auto`. 신규 `tools/migration_doctor.py` + `main.py --doctor` sub-command(콘솔/sidecar/uvicorn 초기화 전 분기): `check`(read-only 진단, projects 안전/unsafe 분류, users/teams.name_norm 충돌+권장 SQL 템플릿), `fix-projects`(dry-run 기본) / `--apply`(자체 백업 후 정리). `WhatUdoin.spec`에 tools 폴더 + hiddenimports 추가. 리뷰 블로커 1건(BEGIN IMMEDIATE 트랜잭션 충돌) + 마이너 1건(GROUP_CONCAT split) 즉시 패치. dedup 7 시나리오 + doctor 5 시나리오 PASS. 운영 DB 사전 진단: 자동 정리 가능 1건만 검출(`team_id=17, name_norm='alpha', ids=[104,105]`), unsafe 0건 → 서버 재시작만으로 phase 5a 자동 흡수. | `database.py`, `tools/migration_doctor.py`(신규), `tools/__init__.py`(신규), `main.py`, `WhatUdoin.spec`. workspace(다음 사이클 시작 시 archive로 이동): `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_dedup_phase.py`, `scripts/verify_migration_doctor.py` |
| 2026-05-12 | #1 마무리 — 가드 감사 + preflight 점검 + 검증 | 소스 변경 0. #1 미완 sub-task 전수 감사: 단계 내부 idempotency 가드(name_norm/password_hash/team_id/project_id/관리팀 rename 등 14개)는 #2~#10 phase 본문에 이미 모두 존재 — `WHERE ... IS NULL`/`WHERE role='editor'`/`WHERE password_hash IS NULL ...` 등. Phase 4 UNIQUE preflight: `_check_users_name_norm_unique`/`_check_teams_name_norm_unique`/`_check_projects_team_name_unique`/`_check_user_ips_whitelist_unique` 4건 등록 확인, `user_teams`·`team_menu_settings` 중복 preflight는 두 테이블이 Phase 1에서 빈 상태로 생성 + Phase 2 백필 `WHERE NOT EXISTS`(user_teams)·시드 #19 이후(team_menu_settings)라 구조적으로 중복 불가 → 의도적 미적용으로 todo 주석 추가(team_menu_settings는 #19 책임). preflight 충돌 일관성(`_append_team_migration_warning` + `RuntimeError` 서버 시작 거부) 확인. 검증 스크립트 신규: 합성 임시 DB(`WHATUDOIN_RUN_DIR` 오버라이드)로 case 1(빈 DB→마커 10개+preflight 통과)/case 2(재호출→전 phase skip, `_pending_phases()==[]`)/case 3(마커 삭제+위험 합성 데이터→재실행 시 password_hash 불변·"converted 0"·team_id/project_id 덮어쓰기 없음·AdminTeam 중복 rename 없음) → 15/15 PASS. `user_teams` 실 컬럼명 `role`/`status` vs todo §#2 명세 `team_role`/`join_status` 불일치 인지(범위 밖, 기록만). 코드 무변경이라 서버 재시작 불필요. | `database.py`(읽기만), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_team_a_001_close.py` + `.log` |
| 2026-05-12 | #1 보강 — dedup phase ordering 버그 수정 | A보강 사이클이 의도한 "재시작만으로 안전 dedup → #5 preflight 통과 → 인덱스 생성"이 실제로는 죽은 코드였던 문제 수정 — `database.py:_run_phase_migrations()`가 preflight를 *모든* phase 본문보다 먼저 일괄 실행하므로, `team_phase_5a_projects_dedup_safe_v1`가 단순히 `PHASES.append` 순서상 #5보다 앞에 있어봐야 #5 preflight(`_check_projects_team_name_unique`)가 abort시키면 5a 본문이 실행 기회를 못 얻었다. 수정(러너 실행 순서/preflight gating만 — 새 phase 본문 추가 X, 5a 본문 미변경): (1) `_PRE_PREFLIGHT_PHASES = frozenset({"team_phase_5a_projects_dedup_safe_v1"})` 신규(`_PREFLIGHT_CHECKS` 정의 직후) — preflight보다 먼저 실행될 phase 집합, 계약은 idempotent + preflight invariant 비의존. (2) per-phase 격리 트랜잭션 루프 본문을 `_run_phase_body(name, body)` 헬퍼로 추출(동작·로그 불변). (3) `_run_phase_migrations`를 백업(불변) → `pending`을 PHASES 순서 유지하며 `pre_preflight`/`rest`로 필터 분할 → `pre_preflight` 먼저 실행(각자 독립 트랜잭션이라 5a 마커가 preflight 전에 커밋됨 — 직후 preflight가 unsafe 충돌로 RuntimeError를 내도 5a 마커는 롤백 안 됨) → preflight(불변) → `rest` 실행 으로 분할. (4) 5a 등록 블록 주석에서 거짓이 된 "PHASES.append 순서 = 실행 순서 … 보장된다"를 `_PRE_PREFLIGHT_PHASES` 설명으로 교체 + unsafe 시 5a 마커 유지·운영자 정리 후 재시작 흐름 1줄 명시. `tools/migration_doctor.py` 무영향(헬퍼 `_projects_duplicate_groups`/`_classify_projects_dedup_group`만 직접 호출, 러너 미참조). 검증: 합성 임시 DB 4 케이스 25 assertions ALL PASS — case1 safe-only 충돌(5a 1건 삭제→#5 마커·`idx_projects_team_name` 생성·`dedup_projects_auto` warning), case2 unsafe 충돌(discriminator: 두 row 모두 events.project_id 참조→5a 노옵 cleanly return→preflight RuntimeError, 단 5a 마커는 set·#5 마커 미set·`preflight_projects_team_name` warning), case3 충돌 0건 회귀(예외 없음·인덱스 생성), case4 재호출(`_pending_phases()==[]`→백업·preflight·phase 전부 skip). `tests/test_project_rename.py` 2건 실패는 사전 결함(master HEAD `ac98650`에서도 동일, 본 수정 무관 — 옛 픽스처 DB에 team_id 컬럼 없음). 스키마·새 phase 추가 없음 — **서버 재시작 필요**(러너 코드 reload용). 운영 DB는 이미 doctor로 정리됨(충돌 0건)+모든 phase 마커 set→`_pending_phases()` 빈 리스트라 본 수정이 운영 DB 기동 경로에 닿지 않음. | `database.py`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_dedup_phase_ordering.py` + `.log` |
| 2026-05-11 | #10 문서·체크 팀 경계 + 편집·삭제 권한 모델 | 스키마 무변경(마이그레이션 phase 추가 없음 — 라우트·쿼리·권한 헬퍼만). `auth.user_team_ids`에 `JOIN teams ... AND deleted_at IS NULL` 추가(삭제 예정 팀 자동 제외 — `user_can_access_team`/`is_team_admin`/`resolve_work_team` 전파). `auth.can_edit_meeting` 신규(혼합 모델: is_team_doc=1→같은 팀 승인 멤버 누구나·created_by 무관·NULL팀잔존은 작성자한정 / is_team_doc=0→작성자 본인만 / admin 전역). `permissions._can_read_doc/_can_read_checklist`에 `work_team_ids` 인자 + 작업 팀 scoping(None&비admin→`user_team_ids` fallback). `app._filter_events_by_visibility(events, user, scope_team_ids=None)` 재작성(admin 무필터 / team_id∈scope 통과 / team_id NULL→작성자 본인(str(id)·이름 토큰 양쪽) / is_public==1 통과 / else skip). `_work_scope` 헬퍼(admin→None, 비admin→resolve_work_team 1개 set, 비소속 team_id 무시·대표팀 fallback, 미배정→set()). 라우트 ~20개에 `team_id` 파라미터+scoping(events·by-project-range·search-parent·subtasks·{id}, checklists, projects·meta·project-list·project-timeline, manage/projects, doc·doc/calendar, kanban, /check·/doc 페이지). create 경로(events·ai/confirm·manage event·checklists·doc) team_id를 `resolve_work_team`로. `database.py` 헬퍼 7종(`_meeting_team_clause`/`_viewer_team_ids`(auth 미import 순환회피)/`_author_token_set`/`_author_in_sql`/`_project_team_filter_sql`/`_events_checklists_team_name_set`/`_filter_rows_by_work_team`) + `work_team_ids` 인자(checklists·meetings·projects 조회 + MCP 조회 함수 다수, MCP응답은 team_id/created_by pop). `mcp_server.py` `import auth`+`_mcp_work_team_ids`(=user_team_ids — MCP엔 작업팀 쿠키 없음) 전 도구에 전달. advisor 리뷰 발견 결함 1건(`_can_read_doc` 작성자 단축이 추방된 팀문서 작성자에게 노출 — §8-1 위반) 동일 흐름 패치(is_team_doc=1은 작성자 단축 제거, meetings SQL 4곳 `m.created_by = ? AND (is_team_doc=0 OR team_id IS NULL)` + 팀문서절 `team_id IS NOT NULL` 추가, `can_edit_meeting` NULL팀팀문서 작성자한정 정합). 합성 DB+TestClient 71 PASS / 0 FAIL(가시성 전 라우트·편집삭제 권한·추방재가입 복구·추방 후 자기작성 팀자료 차단·NULL row 회귀방지). 기존 `tests/phase75` 21 PASS. `tests/test_project_rename.py` 2건 실패는 사전 결함(master HEAD에서도 동일, #10 무관). **서버 재시작 필요**(코드 reload — 스키마 무변경이라 마이그레이션은 불필요). 알려진 한계: assignee 기반 "내 스케줄" 미니위젯(`/api/my-meetings`(event_type='meeting')·`/api/my-milestones`·`/api/project-milestones/calendar`)은 담당자+히든 필터만(팀 경계 미적용 — 후속), 이름 기반 단건 프로젝트 조회 동명 충돌 잔존(§8-2 후속), 읽기 경로 project_id 전환은 부분적(가시성 필터는 적용, 간트 by-project-range는 여전히 name 기반). | `auth.py`, `permissions.py`, `app.py`, `database.py`, `mcp_server.py`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_team10.py` |
| 2026-05-12 | #11 `/` 비로그인 접속 화면 (그룹 B 첫 항목) | 비로그인 `/` 가 `/kanban` 으로 redirect 하던 동작 제거 → 팀 목록 랜딩으로 교체. `app.py index()`: `RedirectResponse("/kanban")` 분기 삭제, 비로그인·로그인 모두 `home.html` 렌더, `teams = db.get_visible_teams()`. `database.py`: `get_visible_teams()` 신규 — `SELECT * FROM teams WHERE deleted_at IS NULL ORDER BY name` (삭제 예정 팀 제외). `get_all_teams()` 미변경(다른 라우트 광범위 공유). `templates/home.html`: `#view-guest` 블록을 비로그인용 칸반 보드(`guest-team-filter`/`guest-board`/`loadGuest()`)에서 "팀 목록 랜딩"으로 교체 — `.landing-hero`(타이틀+안내+「로그인」(`openLoginModal()`)·「계정 가입」(`/register`) 버튼) + `.landing-team-grid`(각 팀 `<a href="/{{ team.name | urlencode }}">` 카드, 한글/공백 URL 인코딩) + 빈 목록 시 "아직 생성된 팀이 없습니다". 고아 정리: `loadGuest()` 함수·`.home-toolbar*`/`.kanban-total-label` CSS·`guest-board`/`guest-team-filter`/`guest-total` DOM 제거, `DOMContentLoaded`(비로그인→`view-guest` 표시만, `loadProjColors()`는 로그인 시에만)·`window.onEventSaved`(`loadGuest` 참조 제거)·`wu:events:changed` 핸들러(비로그인 early-return) 정리. 공유 코드(`cardHTML`/`buildBoard`/`loadUser`/`renderNotice`/`#view-user`/`__pageSearch`) 미변경. **QA: 라이브 Playwright 대신 TestClient(임시 DB) 검증** — 운영 서버는 IP 자동 로그인이라 브라우저로 비로그인 화면 재현 불가, TestClient(`testclient` IP→화이트리스트 미매칭)는 익명 요청으로 비로그인 경로 직접 검증 가능. `tests/phase80_landing_page.py` 5/5 PASS: ① index() redirect 코드 제거 grep ② `get_visible_teams` 존재+`deleted_at IS NULL` 필터+`get_all_teams` 불변 grep ③ home.html 랜딩 마크업+고아 코드 제거 grep ④ 익명 `GET /`→200·`view-guest`·계정 가입 버튼·팀 카드(URL 인코딩 `%20`/`%XX`) ⑤ soft-deleted 팀 목록 제외 ⑥ 로그인 사용자 `GET /`→`view-user` 회귀. `tests/test_project_rename.py` 2건 실패는 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — `git stash` 후 동일, 본 변경 무관). 리뷰 차단 0·경고 0. **서버 재시작 필요**(운영 서버에 반영 시 — 코드 reload, 스키마 무변경이라 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료). 범위 밖(불변): #12("내 자료" 영역·팀 신청 버튼 분기), #13(`/팀이름` 동적 라우트 — 현재 팀 카드 클릭 시 404, 의도된 단계적 상태), #15(work_team_id 쿠키). | `app.py`, `database.py`, `templates/home.html`, `tests/phase80_landing_page.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-12 | #10 후속 — 비로그인 가시성 누수 패치 (`/api/project-timeline`) | #10 검증의 "비로그인 누수 점검"이 `/api/project-timeline`(간트 데이터 소스)에서 빠져 있던 버그 수정. 기존 본문이 `if viewer and not auth.is_admin(viewer): scope=…; else: teams=db.get_project_timeline(team_id, viewer=viewer)` 라 `viewer=None`이면 `else` 분기로 빠져 `team_id=None`·`viewer=None` 무필터 호출 → `db.get_project_timeline` `is_scoped=False` → 전 팀 public 일정·프로젝트가 비로그인 누구에게나 노출. `/api/kanban` 과 동일 골격(`if not auth.is_admin(viewer): scope=_work_scope(request, viewer, team_id); if not scope: return []; team_id=next(iter(scope))`)으로 교체 → 비로그인·팀미배정 `[]`, 로그인 비admin 은 작업 팀 1개만, admin 은 무필터(team_id 그대로) 유지. `proj_colors` 페치는 early-return 이후로 이동(/api/kanban 대칭). 같은 흐름에서 비로그인(`viewer=None`) 관점으로 events·by-project-range·search-parent·subtasks·{id}·checklists·checklists/{id}·histories·projects·projects-meta·project-list·manage/projects·kanban·conflicts·doc·doc/calendar·doc/{id}/events·my-meetings·project-milestones/calendar GET 라우트 일괄 점검 → `/api/project-timeline` 외 추가 누수 없음 확인(나머지는 `_work_scope` 무조건 호출→비로그인 빈 set→public만 / DB 함수가 `viewer is None`일 때 `public_filter`·`private_clause` 적용 / `_can_read_doc`·`_can_read_checklist`가 비로그인 시 public 외 False / `if not user` 단락 / `_require_editor` 401·403 중 하나). 스키마·DB 함수 무변경 — `app.py` `/api/project-timeline` 라우트 본문 13줄만. advisor 1회 검토(None-안전성·qa 분류표·audit 누락 보강 반영). 합성 임시 DB + FastAPI TestClient 31 PASS / 0 FAIL — 비로그인 `[]`(전엔 전 팀 노출)·팀A멤버 A만·다중팀 전환·비소속 명시 fallback·admin 무필터·팀미배정 `[]` + kanban/events/checklists/projects/doc 회귀(events rule4: is_public 일정은 작업 팀 무관 전원 노출 = 기존 동작·누수 아님 확인) + 비로그인 _require_editor 라우트 403. `app` import OK. **서버 재시작 필요**(코드 reload — 스키마 무변경). 범위 밖(불변): #11/#13 비로그인 화면·간트 by-project-range name 기반 조회 전환. | `app.py`, `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_team10_timeline_leak.py` |
| 2026-05-12 | #13 `/팀이름` 비로그인 공개 포털 (그룹 B) | 스키마 무변경(마이그레이션 phase 추가 없음 — 신규 DB 헬퍼는 SELECT 전용). `database.py` 신규: `get_team_by_name_exact(name)`(`WHERE name = ?` — SQLite 기본 BINARY collation, `teams.name` UNIQUE에 COLLATE NOCASE 없음 → 대소문자 정확 일치, `/ABC` 유효 시 `/abc` None; 삭제 예정 팀도 반환 — deleted 판정은 라우트), `_PORTAL_MENU_DEFAULTS` 상수(계획서 §9: kanban/gantt/doc/check ON, calendar OFF), `get_team_menu_visibility(team_id)`(team_menu_settings 행 + 없는 키는 기본값 fallback — 의미는 "UI 진입 차단"일 뿐 데이터 차단 아님; 시드는 #19 책임이라 현재 빈 상태), `get_public_portal_data(team_id)`(항상 단일 팀+`viewer=None` 공개 portal context: kanban=`get_kanban_events(team_id, viewer=None)`, gantt=`get_project_timeline(team_id, viewer=None)`, checks=`get_checklists(viewer=None)` 후 team_id Python 필터, calendar=칸반 events 풀 재활용, docs=`meetings WHERE team_id=? AND is_public=1 AND is_team_doc=1`+author JOIN, menu=메뉴 dict — 모든 채널에서 `is_public=1`만·private/히든 프로젝트 제외는 기존 viewer=None 경로 SQL이 처리). `app.py` 라우트 정의 영역 맨 끝(모든 정적 페이지 라우트 뒤 — FastAPI 등록 순서 = 매칭 순서, `/docs`·`/redoc`·`/openapi.json`는 `app=FastAPI()` 시점 등록이라 자연히 우선): `_TEAM_NAME_RE`(`^[A-Za-z0-9_]+$`), `_RESERVED_TEAM_PATHS_BASE`(계획서 §4 예약어 전부) + `_build_reserved_team_paths()`(하드코딩 + 실제 등록 라우트 첫 세그먼트 합집합 — 누락 자동 방지) → `RESERVED_TEAM_PATHS`(전부 casefold), `@app.get("/{team_name}") team_public_portal`(정규식 불일치 or `casefold() in RESERVED_TEAM_PATHS` or 없는 팀 → 404; `team.deleted_at` 있으면 `deleted=True` 안내 페이지만(가입 버튼·팀 신청·공개 데이터·탭 모두 비노출, admin이면 `/admin` 링크); 그 외 `db.get_public_portal_data(team["id"])` → 포털; 로그인 사용자·admin이 와도 동일 200 포털 — redirect 안 함, "팀 신청/가입 대기" 등 로그인 UI 분기는 #14). 신규 `templates/team_portal.html`(base.html extends; `{% if deleted %}` 안내 분기 / `{% else %}` 본문 = `.portal-hero`(팀 이름+홈 버튼+`{% if not user %}` `btn-primary` "계정 가입"→`/register`)+`#14` 미룸 주석+`#portal-tabs`(`portal.menu` dict 조건부 탭)+탭 패널 5종(칸반=title/project/status/날짜 카드, 간트=팀→프로젝트 2단계 일정 리스트, 문서=title/author/updated, 체크=title/project/updated, 캘린더=title/datetime — 모두 읽기 전용 텍스트 목록, 빈 데이터 메시지)+탭 전환 IIFE(`{% if not deleted %}` — 첫 탭 기본 활성화, 서브 라우트 안 만듦)). **QA: TestClient(임시 DB) — 운영 서버 IP 자동 로그인이라 비로그인 브라우저 재현 불가**. `tests/phase82_team_portal.py` 8/8 PASS: ① `/{team_name}` 정적 라우트 11종보다 뒤 등록(소스 오프셋)+핸들러 정규식/casefold/예약어/404/exact-lookup/deleted 사용 ② DB 헬퍼 3개 존재(`WHERE name = ?`)+`team_phase_13`/`public_portal_v1` 마커 부재(스키마 무변경) ③ 템플릿 deleted 분기+`{% if not user %}`/`/register`+`portal.menu`+`#14` 주석 ④ `/ABC`(대문자 팀)→200·팀 이름·`btn-primary">계정 가입`·`공개 포털 — ...` / `/abc`→404(대소문자 분리) / `/Nonexistent`→404 / `/Bad-Name`(하이픈)→404 ⑤ `/admin`→admin 로그인 페이지 / `/api/health`·`/docs`·`/redoc`·`/openapi.json`→200 / `/static/<실제 파일>`→404 아님 / 예약어 20종 각각 포털 마크업 부재 ⑥ `/ABC` 마크업: `PUBLIC_*` 노출·`PRIVATE_*`(is_public=0)·`PERSONAL_DOC`(is_team_doc=0)·`HIDDEN_PROJ_*`(히든 프로젝트, is_public=1이어도)·`HiddenProj` 이름 비노출 ⑦ 삭제 예정 팀→200·"삭제 예정" 노출·`btn-primary">계정 가입`·`공개 포털 — ...`·`id="portal-tabs"`·공개 데이터 부재 ⑧ 로그인 사용자 `/ABC`→200 포털(redirect 아님)·포털 본문 계정 가입 버튼 부재. 회귀: `tests/phase80_landing_page.py`(#11) 5/5, `tests/phase81_unassigned_user.py`(#12) 9/9 PASS. `tests/test_project_rename.py` 2 FAIL은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, #13 무관). `import app` OK. QA 자가 발견 2건(테스트 단언 정밀화만 — 소스 무변경): base.html 로그인 모달의 `/register` "계정 가입" 링크가 항상 렌더되므로 포털 본문 버튼은 `'btn-primary">계정 가입'`로 식별 / `.portal-tabs` CSS 셀렉터 텍스트 회피 위해 탭 부재는 `'id="portal-tabs"'`로 판별. 리뷰 차단 0·경고 3(`get_checklists` 전 팀 조회 후 Python team_id 필터·캘린더 탭 데이터는 칸반 풀 재활용·CSS 셀렉터 텍스트 — 모두 현 규모/범위에서 허용). 미완 sub-task: team_menu_settings 기본값 시드(#19 책임 — 골격만 + fallback 기본값으로 동작), 로그인 사용자 "팀 신청/가입 대기" 버튼 분기(#14 범위 — 라우트는 200 포털만). **운영 서버 반영 시 재시작 필요**(코드 reload — app.py/database.py + 새 템플릿. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료). 범위 밖(불변): #14(로그인 사용자 포털 UI), #15(work_team_id 쿠키), #19(team_menu_settings 시드). 한글 이름 레거시 팀은 정규식 불일치로 404 — 계획서 §4 의도된 동작(rename/마이그레이션 범위 밖); #11의 `team.name|urlencode` 카드는 유효 팀명엔 사실상 no-op. | `app.py`, `database.py`, `templates/team_portal.html`(신규), `tests/phase82_team_portal.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-12 | #12 `/` 팀 미배정 로그인 사용자 + "내 자료" 영역 (그룹 B) | 스키마 무변경(마이그레이션 phase 추가 없음 — 신규 DB 헬퍼는 SELECT만). `auth.is_unassigned(user)` 신규 — 로그인했으나 approved 소속 팀 0개인 비-admin(`user_team_ids`가 이미 `deleted_at IS NULL` 필터 → 삭제 예정 팀만 남은 사용자도 미배정 취급). admin은 `is_admin` 먼저 체크 → 미배정 아님(advisor 지적 준수). `database.py`: `get_my_team_statuses(user_id)`(=`{team_id: 'pending'|'rejected'}`, 비-삭제 팀만), `get_my_personal_meetings(user_id)`(본인 작성 `is_team_doc=0` 전체 — `team_share`·`team_id IS NULL`로 안 거름 → 추방 잔존 개인 문서 포함, 일정·체크·팀 문서 제외, `m.*`+`team_name`+`event_count`). `app.py`: `_ctx()`에 `"is_unassigned": auth.is_unassigned(user)`(비로그인·admin은 짧은 경로 즉시 반환 — 로그인 비-admin만 +1 `user_team_ids` 쿼리), `index()` 미배정이면 `team_status_map`/`my_docs` 추가 컨텍스트(비로그인·일반·admin 동작 불변), `create_doc` 미배정이면 `is_team_doc=0`/`team_share=0`/`team_id=None` 강제(`resolve_work_team` legacy fallback 우회 — 계획서 §3·7), `update_doc` 미배정이면 `is_team_doc=0`/`team_share=0` 강제, `rotate_doc_visibility` 미배정이면 `is_public` 0↔1만 토글, `/api/notifications/{count,pending}` 미배정 빈 응답(SSE/직접호출 방어). `templates/home.html`: 신규 `#view-unassigned` 블록(`#view-guest`와 `#view-user` 사이) — `.landing-hero` 안내 + 팀 목록(`.unassigned-team-card`, `team_status_map.get(team.id)=='pending'`→"가입 대기 중"(disabled)·그 외→"팀 신청"(`applyToTeam`), 팀 카드를 `/팀이름` 링크로 안 만듦 — #13 책임) + "📄 내 자료"(+ 새 문서→`/doc/new?personal=1`·`member`/`editor`/`admin` 모두(가입 role=member — `_require_editor`=is_member 통과), `my_docs` 각 항목 제목+team_name 태그+`updated_at[:10]`→`/doc/{id}`). JS: `const IS_UNASSIGNED`(SSR 플래그만 신뢰 — legacy `user.team_id` 재추론 금지), `applyToTeam()`(`POST /api/me/team-applications`→성공 toast+`location.reload()`, 실패 시 서버 detail toast — 409 "다른 팀 신청 처리 대기 중"도 그대로), DOMContentLoaded 3분기(`CURRENT_USER && IS_UNASSIGNED`→`view-unassigned` / `CURRENT_USER`→`view-user`+`loadUser` / else→`view-guest`), `wu:events:changed` 핸들러 early-return에 `|| IS_UNASSIGNED`. CSS `.unassigned-team-card`/`.my-docs-list`/`.my-doc-*` 추가. `templates/base.html`: `var IS_UNASSIGNED` 전역 + 알림 벨 `#notif-bell-wrap` 블록 `{% if not is_unassigned %}` + 앱 내 알림 IIFE 진입 가드 `if (!CURRENT_USER || IS_UNASSIGNED) return;`. `templates/doc_editor.html`: `const IS_UNASSIGNED` + 미배정이면 doc-type 세그먼트 숨김 + `VIS_OPTS`에서 "팀에게만 공개"(team_share) 옵션 제거(백엔드 강제와 중복 — UI 우회 무해). 공유 코드(`cardHTML`/`buildBoard`/`loadUser`/`renderNotice`/`#view-user`/`__pageSearch`) 미변경. **QA: 라이브 Playwright 대신 TestClient(임시 DB) — 운영 서버 IP 자동 로그인이라 미배정 사용자 화면 브라우저 재현 불가**. `tests/phase81_unassigned_user.py` 8/8 PASS: ① 정적 invariant(헬퍼·index 분기·`_ctx`·`create_doc` team_id=None·base 게이팅·home view-unassigned·팀 카드 비링크·공유 코드 유지) ② 미배정 `GET /`→200·`view-unassigned`·"팀 신청"/"내 자료"·`notif-bell-wrap` 없음·soft-deleted 팀 제외 ③ 팀 신청→200·"가입 대기 중" 노출·다른 팀 신청 409 ④ 승인→`view-user` 전환·알림 벨 복귀 ⑤ 미배정 `POST /api/doc` `{is_team_doc:1,team_share:1}`→`is_team_doc=0`/`team_share=0`/`team_id=NULL`·"내 자료" 노출·추방 잔존 문서 포함 ⑥ `team_share=1` 본인 개인 문서 작성자 내 자료엔 보이되 다른 팀 멤버 `/api/doc`엔 미노출 ⑦ admin(`user_teams` 없음)→`is_unassigned` False·`view-user`·알림 벨 ⑧ 미배정 알림 엔드포인트 빈 응답 ⑨ 회귀(배정 사용자 `view-user`·비로그인 `view-guest`). `tests/phase80_landing_page.py`(#11) 5/5 PASS, `tests/phase75` PASS. `tests/test_project_rename.py` 2건 실패는 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, 본 변경 무관). 리뷰 차단 0·경고 3(`_ctx` 페이지당 +1쿼리·`/doc/new` 미배정 직진입 시 숨겨진 세그먼트 'team' active이나 서버 강제로 무해·`applyToTeam` showToast 미정의 시 alert fallback — 모두 허용). `import app` OK. **운영 서버 반영 시 재시작 필요**(코드 reload — app.py/auth.py/database.py/템플릿 3종. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료). 범위 밖(불변): #13(`/팀이름` 동적 라우트 — 팀 카드 클릭 시 404, 의도된 단계적 상태), #15(`work_team_id` 쿠키 UI — admin은 여전히 `view-user`). | `auth.py`, `database.py`, `app.py`, `templates/home.html`, `templates/base.html`, `templates/doc_editor.html`, `tests/phase81_unassigned_user.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-12 | #14 `/팀이름` 로그인 사용자 (소속 무관) — 공개 포털 버튼 분기 (그룹 B) | 스키마 무변경(마이그레이션 phase 추가 없음 — 기존 헬퍼만 재사용). `app.py team_public_portal` deleted 아닌 경로에 `my_team_status` 컨텍스트 추가: `user = auth.get_current_user(request)` → 비로그인 또는 `auth.is_admin(user)` → `None`(admin = 슈퍼유저, "팀 신청" 의미 없음 → 버튼 없음. 계획서 §7 표에 admin 행 없어 본 구현 결정 + 주석 근거) / 그 외 `team["id"] in auth.user_team_ids(user)` → `"approved"` / 아니면 `db.get_my_team_statuses(user["id"]).get(team["id"])` → `"pending"`|`"rejected"`|`None`. `_ctx(... portal=portal, my_team_status=my_team_status)`. 라우트는 `RedirectResponse` 미사용 — 로그인/admin 모두 200 공개 포털(계획서 핵심: "URL 은 권한 경계가 아니다"). deleted 분기 무변경(안내만 — 버튼 없음). 라우트 상단 주석을 #14 완료 반영(미루기 문구 제거). `templates/team_portal.html`: `.portal-hero-actions` 버튼 분기를 계획서 §7 표대로 — `{% if not user %}`→`/register` "계정 가입"(#13 유지) / `{% elif my_team_status == 'approved' %}`→버튼 없음 / `{% elif my_team_status == 'pending' %}`→`<button class="btn btn-sm" disabled>가입 대기 중</button>` / `{% elif user.role == 'admin' %}`→버튼 없음(슈퍼유저) / `{% else %}`→`<button ... onclick="applyToTeam({{ team.id }})">팀 신청</button>`. admin 분기를 `else` **앞**에 배치(admin 은 `my_team_status=None` 이라 안 그러면 "팀 신청"으로 떨어짐). `applyToTeam` JS 를 `{% block scripts %}` `{% if not deleted %}` 안에 home.html 의 것 최소 복제(~18줄 — `fetch('/api/me/team-applications', POST {team_id})`, 성공 시 `showToast`(있으면)+600ms 후 `location.reload()`, 실패 시 `detail` 토스트/alert fallback; team_portal.html 엔 다른 JS 없어 독립 복제가 surgical — 버튼 1개). 상단 docstring 에 #14 버튼 분기 표 명시 + `.portal-hero-actions` 안의 "#14 범위 ... 구현하지 않는다" 미루기 주석 제거. `pending_other`(다른 팀 pending)는 #12 패턴대로 새 UI 안 만들고 서버 에러에 위임 — `/팀이름` 에서 "이 팀에 pending" 아니면 그냥 "팀 신청"(서버가 클릭 시 차단). 데이터 노출/탭/패널 5종/탭 전환 IIFE/CSS 무변경(#13 그대로). **QA: 라이브 Playwright 대신 TestClient(임시 DB) — 운영 서버 IP 자동 로그인이라 특정 사용자 상태(미소속/pending/rejected/approved/admin) 브라우저 재현 불가**. `tests/phase83_team_portal_loggedin.py` 9/9 PASS: ① 정적 — `team_public_portal` 가 `my_team_status` 전달 + `is_admin`/`user_team_ids`/`get_my_team_statuses` 사용 + `RedirectResponse` 미사용 + `import app` OK ② 정적 — `team_portal.html` `my_team_status == 'approved'/'pending'` 분기 + `user.role == 'admin'` 분기 + `applyToTeam(`/`async function applyToTeam`/`/api/me/team-applications` + `가입 대기 중` `disabled` + `#14` 주석 + "구현하지 않는다" 미루기 문구 제거됨 ③ 미소속 로그인(신청 이력 없음)→`/ABC` 200·`onclick="applyToTeam("` 노출·"가입 대기 중"·"계정 가입" 부재 ④ 이 팀 pending→200·`<button class="btn btn-sm" disabled>가입 대기 중</button>` 노출·"팀 신청" 부재 ⑤ 다른 팀 pending(이 팀 미소속)→200·"팀 신청" 노출(서버가 클릭 시 pending_other 차단 — UI 관심사 아님) ⑥ approved 멤버→200·"팀 신청"·"가입 대기 중"·"계정 가입" 모두 부재 ⑦ rejected→200·"팀 신청" 재노출 ⑧ admin→200(30x 아님)·"팀 신청"·"가입 대기 중"·"계정 가입" 부재·`id="portal-tabs"` 존재(포털 본문 정상) ⑨ 비로그인→200·`btn-primary">계정 가입` 노출(#13 회귀)·"팀 신청"·pending 버튼 부재. 공통(`_assert_portal_ok`): 모든 케이스 status 200·`공개 포털 — 공개 설정된 항목만` 본문·`href="/" class="btn btn-sm btn-outline">홈` 존재. 회귀: `tests/phase80_landing_page.py`(#11) 5/5, `tests/phase81_unassigned_user.py`(#12) 8/8, `tests/phase82_team_portal.py`(#13) 8/8 PASS(phase83 포함 30/30, 7.9s). `tests/test_project_rename.py` 2 FAIL 은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — master HEAD 동일, #14 무관). `import app` OK. 리뷰 차단 0·경고 1(`applyToTeam` home.html ↔ team_portal.html 중복 ~18줄 — 버튼 1개·surgical 원칙상 허용, 향후 공통 static JS 정리 후보). advisor 1회 검토(버튼 매트릭스·admin 결정·`pending_other`·`my_team_status` 키 이름 합의·분기 순서 반영). **운영 서버 반영 시 재시작 필요**(코드 reload — app.py + 템플릿. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료). 범위 밖(불변): #15(`work_team_id` 쿠키·프로필 "팀 변경" UI), #19(team_menu_settings 시드). | `app.py`, `templates/team_portal.html`, `tests/phase83_team_portal_loggedin.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-12 | #15 프로필 "팀 변경" UI + work_team_id 쿠키 (그룹 B) | 스키마 무변경(마이그레이션 phase 추가 없음 — 신규 DB 헬퍼는 SELECT 전용). **#10 가 이미 깐 골격**(`auth.resolve_work_team` 이 explicit→쿠키→대표 팀 순으로 읽고, 라우트 ~20개가 `team_id` 파라미터+`_work_scope`/`require_work_team_access` 검증) 위에 ① 쿠키 검증 ② Set-Cookie 발급 ③ `POST/GET /api/me/work-team` ④ 프로필 "팀 변경" UI ⑤ 화면별 팀 드롭다운 제거 를 얹음. `auth.py`: `_team_is_active(tid)`(=`db.get_team_active is not None`), `_work_team_default(user)`(admin→`db.first_active_team_id()` / 비admin→`db.primary_team_id_for_user(uid)` → legacy `users.team_id`(비삭제일 때만) → None), `resolve_work_team` 재작성 — explicit_id 그대로 신뢰(호출부 `_work_scope`/`require_work_team_access` 검증), 쿠키 경로에 `user_can_access_team(user, ctid) AND _team_is_active(ctid)` 검증 추가(이전엔 무조건 신뢰), 무효면 `_work_team_default` fallback. **변경점**: admin no/invalid-cookie fallback `None`→첫 비삭제 팀(계획서 §7), 비admin 대표 팀 `min(team_ids)`→joined_at 기준. `_work_scope`(app.py)·`admin_team_scope` 무변경 — admin `None` 반환·explicit 우선·미배정 빈 set 그대로(READ 슈퍼유저 유지). `database.py`(get_team_by_name_exact 뒤): `first_active_team_id()`(`teams WHERE deleted_at IS NULL ORDER BY id LIMIT 1`), `primary_team_id_for_user(uid)`(`user_teams` approved+`teams.deleted_at IS NULL` JOIN `ORDER BY ut.joined_at ASC, ut.team_id ASC LIMIT 1` — 실 컬럼명 `status`/`joined_at`), `user_work_teams(uid)`(본인 approved+비삭제 소속 팀 `[{id,name}]` joined_at 순). `get_team_active` 는 기존 함수 재사용. `app.py`: `_set_work_team_cookie(response, tid)`(max_age 1년·samesite=lax·httponly=False·path=/), `_ensure_work_team_cookie(request, response, user)`(user None/미배정→noop / `resolve_work_team` 결과(None이면 noop)와 현재 쿠키 다르면 Set-Cookie) — SSR 페이지 12개(`/`·`/calendar`·`/admin`·`/kanban`·`/gantt`·`/project-manage`·`/doc`·`/doc/new`·`/doc/{id}`·`/doc/{id}/history`·`/check`·`/trash`)에서 호출(`calendar_page` 는 `user` 변수 추출하도록 미세 리팩터, 동작 동일), `GET /api/me/work-team`(=`{current, teams:[{id,name}], is_admin}` — 비admin=`db.user_work_teams`/admin=`db.get_visible_teams()`, `_require_login`), `POST /api/me/work-team`(`_check_csrf`+로그인 401+int 파싱 400+`db.get_team_active` 404+`auth.require_work_team_access` 비admin 비소속 403 → `_set_work_team_cookie` → `{ok, team_id, team_name}`), `_ctx` 에 `work_team_id`/`work_team_name`(user None/미배정이면 None) 추가. `templates/base.html`: `current_user_payload` 에 `work_team_id`/`work_team_name` 추가, 프로필 헤더 이름줄 `{{ user.name }}{% if work_team_name %} · {{ work_team_name }}{% if user.role=='admin' %}(슈퍼유저){% endif %}{% endif %}`, 프로필 드롭다운 최상단 "👥 팀 변경" 토글+`#work-team-list` 서브리스트(`GET /api/me/work-team` 동적 로드, 현재 작업 팀 ✔, 이름 `textContent` 주입 XSS 회피)+구분선, JS `toggleWorkTeamMenu/loadWorkTeams/selectWorkTeam`(POST→`r.ok` 면 `location.reload()`, 실패 `showToast`/`alert(detail)`), `closeProfileMenu` 에 서브리스트 닫기 1줄. `static/css/style.css`: `.work-team-list`/`.work-team-empty`/`.work-team-item`(+`.active` `--accent`·굵게) 추가. `templates/kanban.html`: `<label>팀</label>`+`<select id="team-filter">` 제거, `_applyInitialTeamFilter` 함수+초기화 호출 제거, `loadKanban` → `fetch('/api/kanban')`(team-filter.value/`kanban_team_filter` localStorage 제거 — 서버 쿠키 결정), 팀원 칩 `CURRENT_USER.team_id`→`work_team_id`. `templates/project.html`(간트): `<select id="team-filter">` 제거, `LS_TEAM='proj_team_filter'`+`_applyInitialTeamFilter`+init 호출 제거, `loadData` → `fetch('/api/project-timeline')`. `templates/calendar.html`: 팀원 칩+편집 게이팅 힌트 `CURRENT_USER.team_id`→`work_team_id`(2곳; `role==='editor'` 리터럴은 #16 책임이라 미변경). `templates/doc_list.html`: `weekly-team` 모달 기본 선택값 `CURRENT_USER.team_id`→`work_team_id`(드롭다운 자체는 주간 보고서 per-report 파라미터라 #15 범위 밖, 유지). **QA: 라이브 Playwright 대신 TestClient(임시 DB) — 운영 서버 IP 자동 로그인이라 다중 팀 사용자 전환/admin 슈퍼유저 시나리오 브라우저 재현 불가**. `tests/phase84_work_team_cookie.py` 19/19 PASS: 정적×5(app.py 라우트/헬퍼/`_ctx`·auth `resolve_work_team` 쿠키 검증·db 헬퍼 3종+joined_at·템플릿 팀 변경 UI/드롭다운 제거/work_team_id·CSS) + A 첫 로드(쿠키 없음·2팀·joined_at 순)→가장 이른 팀 Set-Cookie / B admin→가장 작은 id 비삭제 팀 / C 유효 쿠키→사용·갱신 없음 / D 쿠키 팀 soft-deleted→새 대표 팀 갱신 / E 쿠키 팀 비소속(추방)→새 대표 팀 갱신 / F `POST {소속 팀}`→200+`{ok,team_id,team_name}`+Set-Cookie / G `POST {비소속}`(비admin)→403 / H `POST {삭제 예정}`→404·`POST {team_id:"abc"}`→400 / I `POST` 후 `/api/kanban`·`/api/events`·`/api/project-timeline` 새 팀 컨텍스트(다른 팀 데이터 미노출)·`/api/checklists`·`/api/doc` 200 / J #10 회귀 — `?team_id=X`(소속) 쿠키보다 우선·비소속 X 명시 무시→쿠키 fallback / K 미배정 `GET /`→Set-Cookie 없음·비로그인도 영향 없음 / L admin `_work_scope`=None — admin 쿠키 ta 라도 `/api/kanban` 전 팀 노출 / M `GET /api/me/work-team` 비admin=소속 팀+대표 팀·admin=전체 비삭제 팀 / N 비로그인 `GET`/`POST /api/me/work-team`→401. 회귀: phase80~83 30/30 PASS(10.2s). `tests/test_project_rename.py` 2 FAIL은 사전 결함(`git stash` 후 동일 — 옛 픽스처 DB에 `projects.team_id` 없음, master HEAD `04006ba` 동일, #15 무관). `import app` OK. Jinja `get_template` base/kanban/project/calendar/doc_list OK. 리뷰 차단 0·경고 3(`_ctx` 페이지당 +1~2 쿼리(resolve_work_team)·`_ensure_work_team_cookie` 도 한 번 더 호출·kanban/project 라우트 `teams=` 데드 인자 — 모두 현 규모 허용·#16 정리 후보) + 범위 밖(불변): `weekly-team` 모달 드롭다운·`calendar.html` `role==='editor'` 리터럴·#15-1/-2/-3·#19. advisor 1회 검토(resolve_work_team 이미 쿠키 읽음·admin discriminator·SSR hook 방식·CURRENT_USER.team_id 전환·드롭다운 orphan 정리·테스트 시나리오 enumeration·todo bookkeeping 반영). **운영 서버 반영 시 재시작 필요**(코드 reload — auth.py/database.py/app.py + 템플릿 5종 + style.css. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료). 범위 밖(불변): #15-1(히든 프로젝트 다중 팀 전환)·#15-2(links 다중 팀 전환)·#15-3(team_notices 팀별 공지) — 그룹 B 미완. | `auth.py`, `database.py`, `app.py`, `templates/base.html`, `templates/kanban.html`, `templates/project.html`, `templates/calendar.html`, `templates/doc_list.html`, `static/css/style.css`, `tests/phase84_work_team_cookie.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-13 | #15-1 히든 프로젝트 다중 팀 전환 (그룹 B) | 스키마 무변경(마이그레이션 phase 추가 없음 — 히든 프로젝트 함수의 SELECT 쿼리 전환 + `add_hidden_project_member` 시그니처 정리만). **그룹 A에서 깐 임시 골격**(`create_hidden_project(team_id=...)` 시그니처·`(team_id, name_norm)` 팀 제한 중복검사·일부 함수에 `u.team_id = p.team_id` 임시 비교)을 #15-1이 owner의 `users.team_id` 단일 비교를 마저 제거하고 멤버 후보·가시성·이양 헬퍼를 `user_teams.status='approved'` + `projects.team_id` 기준으로 옮김. 핵심 substitution(8개 중 6개 함수에 적용): `AND u.team_id IS NOT NULL AND u.team_id = p.team_id` → `AND EXISTS (SELECT 1 FROM user_teams ut WHERE ut.user_id = u.id AND ut.team_id = p.team_id AND ut.status = 'approved')` (`p.team_id IS NOT NULL` 유지, `teams.deleted_at IS NULL` 추가검증은 스코프 최소화 차원에서 의도적 미적용 — 기존 쿼리도 안 봤음). `database.py`: ① `create_hidden_project(name, color, memo, owner_id, team_id=None)` — `team_id is None` 시 owner의 `users.team_id` fallback(`SELECT team_id FROM users WHERE id=?`) 제거 → `ValueError("히든 프로젝트는 team_id가 필요합니다")` (NULL row 생성 금지; 라우트 `POST /api/manage/hidden-projects`는 이미 `resolve_work_team` None을 403으로 막아 정상흐름 영향 없음; 시그니처는 호환상 `int|None=None` 유지·본문 거부). ② `_hidden_project_visible_row(conn, project_id, user)` — 가시성 쿼리에 substitution 적용. 이 함수가 `is_hidden_project_visible`·`_can_view_hidden_trash_project`·`_trash_item_visible_to_viewer`의 단일 진입점이라 전부 반영. ③ `get_hidden_project_members(project_id)` — 변경 없음(쿼리 그대로), docstring에 "`u.team_id`는 표시용 legacy 값, 권한 판단 안 함" 1줄. ④ `get_hidden_project_addable_members(project_id)` — owner의 `users.team_id` 참조(`projects p JOIN users u ON u.id = p.owner_id`) 제거 → `SELECT team_id FROM projects WHERE id=?`로 `project_team_id` 조회(None→`[]`), 후보 쿼리 `is_active=1 AND role != 'admin' AND id NOT IN (project_members) AND EXISTS(user_teams approved on project_team_id) ORDER BY u.name`. **owner_id=NULL 복구 상황에서도 owner 미참조·`projects.team_id` 기준으로 동작** — 빈 리스트 X. ⑤ `add_hidden_project_member(project_id, user_id)` — **3-인자→2-인자(`owner_id` 인자 제거)**: owner 참조 대신 `SELECT team_id FROM projects WHERE id=?`로 `proj_team_id`(None→False), target은 `SELECT role, is_active`(team_id 컬럼 불필요), is_active=0 or role='admin'→False, `SELECT 1 FROM user_teams WHERE user_id=? AND team_id=proj_team_id AND status='approved'` 없으면 False, 이미 멤버 None, 아니면 INSERT 후 True. owner_id=NULL 복구 시 admin이 호출해도 owner 참조 없이 동작. docstring 갱신("False(팀 미승인/admin/비활성/NULL팀)"). ⑥⑦ `transfer_hidden_project_owner`/`admin_change_hidden_project_owner` — member 검증 쿼리에 substitution 적용, 시그니처·동작 무변경. ⑧ `transfer_hidden_projects_on_removal(user_id, hidden_projects)` — next_owner 후보 쿼리에 substitution 적용, 시그니처·동작 무변경(단일 caller `app.py:1776 admin_update_user`는 레거시 단일 팀 admin 경로 — 다중 팀 부분 추방 시나리오는 그 라우트 자체가 아직 레거시라 이번 범위 밖). docstring에 "후보 조회는 user_teams + projects.team_id 기준" 추가. `app.py`: `add_hidden_project_member_route`(line ~3146) — `db.add_hidden_project_member(proj["id"], target_user_id, proj["owner_id"])` → `db.add_hidden_project_member(proj["id"], target_user_id)`; 403 메시지 "같은 팀 사용자만…" → "해당 팀의 승인된 멤버만 멤버로 추가할 수 있습니다." `POST /api/manage/hidden-projects` 무변경(이미 `resolve_work_team`+`require_work_team_access`). `_assert_assignees_in_hidden_project` 무변경(이미 `get_hidden_project_members` 멤버 이름만 비교 → assignee 후보가 `project_members` 기준으로 이미 제한·admin은 멤버 아니라 자연 제외). **프론트엔드 변경 없음**(멤버 후보 드롭다운·assignee 후보는 백엔드 반환값만 렌더 — frontend-dev 생략). **QA: 라이브 Playwright 대신 TestClient/직접 DB(임시 DB) — 운영 서버 IP 자동 로그인이라 다중 팀 owner 시나리오 브라우저 재현 불가**. `tests/phase85_hidden_project_multiteam.py` 11/11 PASS(4.9s): 정적×4(히든 함수 8개 한정 `u.team_id = p.team_id` 잔존 0건·owner_row 참조 0건(create 제외)·`create_hidden_project` users.team_id fallback 제거+`ValueError`·6개 함수에 `user_teams`+`status='approved'`·`add_hidden_project_member` 2-인자·app.py 라우트 2-인자 호출+구 3-인자 부재·`import app`) + A owner 추방→`added_at` 오름차순 최선두 멤버에게 자동 이양+추방된 owner project_members 제거 / B 후보 없음→`owner_id IS NULL`→admin이 같은 팀 승인 멤버 `add_hidden_project_member`(owner 미참조) 성공→`admin_change_hidden_project_owner` 성공→owner_id=새멤버 / C admin이 (비정상이지만) user_teams approved row 가져도 `get_hidden_project_addable_members`에서 제외(role!='admin' 이중보장)·`get_hidden_project_members`(assignee 후보)에 admin 이름 미포함·`add_hidden_project_member(pid, admin)`→False / D owner가 팀A·B 둘 다 approved·owner인 히든 P(team_id=A)→`get_hidden_project_addable_members(P)` 후보는 팀A 멤버만(팀B 미포함)·`add_hidden_project_member(P, 팀B멤버)`→False·팀A멤버→True / E project_members row+user_teams approved→`is_hidden_project_visible` True·user_teams 제거(project_members 잔존)→False·재가입 approved→True·status='pending'→False·admin→항상 True / F P.owner_id=NULL·team_id=A→`get_hidden_project_addable_members(P)` 빈 리스트 아닌 팀A 승인멤버 반환(전 owner는 user_teams 잔존 시 후보·user_teams에서도 제거하면 후보에서도 빠짐) / G `create_hidden_project(team_id=None)`→`ValueError`·team_id 기준 저장·같은 팀 동일이름→None·다른 팀 동일이름→허용. 회귀: phase80~84 49/49 PASS(16.8s)·`import app` OK·Playwright `phase46_hidden_project_{a,b,c}.spec.js`는 미실행(서버 재시작 필요 — 프론트 무변경이라 위험 낮음, 재시작 후 별도 확인 권장). `tests/test_project_rename.py` 2 FAIL은 사전 결함(옛 픽스처 DB에 `projects.team_id` 없음 — #15 사이클·master HEAD 동일, #15-1 무관). 리뷰 차단 0·경고 2(user_teams approved EXISTS 서브쿼리 4함수 복제 — `_approved_member_clause()` SQL 조각 헬퍼 후보·#16 정리 시점 / `create_hidden_project` team_id=None 시 `None`→`ValueError` 동작 변경 — 현재 caller app.py 1곳뿐이라 OK·feature_spec과 일치). advisor 1회 검토(substitution 패턴 명시·`transfer_hidden_projects_on_removal` 다중 팀 부분 추방 결정·`create_hidden_project` None 거부·`get_hidden_project_members` u.team_id 표시용·QA 시나리오 추가 — 전부 반영). **운영 서버 반영 시 재시작 필요**(코드 reload — database.py + app.py. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient/직접 DB로 완료). 범위 밖(불변): #15-2(links 다중 팀 전환), #15-3(team_notices 팀별 공지) — 그룹 B 미완. | `database.py`, `app.py`, `tests/phase85_hidden_project_multiteam.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-13 | #15-2 links 기능 다중 팀 전환 (그룹 B) | 스키마 무변경(마이그레이션 phase 추가 없음 — `links` 테이블 그대로; 그룹 A #4에서 `links.team_id` 백필 이미 완료). `/api/links` 4개 라우트를 `users.team_id` 단일 비교 → `work_team_id`(또는 명시 `team_id`) 기반으로 전환. `database.py`: ① `get_links(user_name, work_team_ids)` — 시그니처 `team_id`→`work_team_ids`(컨벤션은 `app._work_scope` 와 동일: `None`=admin 무필터(전 팀 scope='team' 링크 + 본인 personal) / `set()`=팀 미배정(본인 personal만) / `{tid,...}`=해당 팀들 scope='team' + 본인 personal, `team_id IN (...)` 일반화 — 비admin은 항상 0~1개지만 admin 명시 다중 대비). `scope='personal'` 링크는 어느 경우든 작성자 본인 row만. (admin 무필터 시 `scope='team' AND team_id IS NULL` orphan 링크(#4 백필 누락분, 정상이면 0건)도 admin GET에 포함 — 슈퍼유저 패턴과 일관.) ② `update_link(link_id, title, url, desc, user_name, role)` — `role` 인자 추가, `role=='admin'`→`WHERE id=?`(작성자 무관) / else→`WHERE id=? AND created_by=?` (`delete_link` 와 동일 패턴; 계획서 §8-1 — 링크는 "작성자/admin만 편집·삭제"라는 일정·체크의 팀 공유 모델과 다른 예외). ③ `create_link` — 시그니처 무변경(주석 1줄: scope='team'이면 team_id는 호출부가 resolve_work_team으로 확정). ④ `delete_link` — 무변경(이미 admin 분기 있음). `app.py`: ① `GET /api/links` — `team_id: int = None` 쿼리 파라미터 추가, 비로그인 `[]` 유지, 로그인 시 `scope = _work_scope(request, user, team_id)` → `db.get_links(user["name"], scope)`. ② `POST /api/links` — `scope=='team'`이면 `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`; None이면 `400`(admin이 work_team 없이 호출 거부 — `manage/projects` 라우트 미러); `auth.require_work_team_access(user, team_id)`(비admin 비소속→403). `scope=='personal'`이면 `team_id=None`. `user.get("team_id")` 참조 제거. ③ `PUT /api/links/{id}` — `db.update_link(..., user["name"], user.get("role", "member"))`; 실패 시 403 유지. ④ `DELETE /api/links/{id}` — `user.get("role", "editor")`→`"member"` default 정리만(동작 동일 — 이미 role 전달). **프론트엔드 변경 없음** — 헤더 링크 드롭다운(`templates/base.html`)은 `_loadLinks()`가 드롭다운 open마다 `fetch('/api/links')` → 백엔드가 작업 팀 기준 응답 → 통합 표시 자연 반영; 작업 팀 전환은 `selectWorkTeam`→`location.reload()`(base.html:1020·1027) 후 다음 open 시 새 work_team_id 쿠키로 fetch. JS는 `link.created_by`만 비교 — `users.team_id` 미참조. **QA: 라이브 Playwright 대신 TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 다중 팀/admin 시나리오 브라우저 재현 불가**. `tests/phase86_links_multiteam.py` 13/13 PASS(5.5s): 정적×3(`get_links` 시그니처 `work_team_ids` / `update_link` `role` 인자+`role=='admin'` 분기 / `/api/links` 라우트가 `_work_scope`·`resolve_work_team`·`require_work_team_access` 사용·`user.get("team_id")` 미참조 + `import app`) + A 다중 팀 사용자 작업 팀 전환(쿠키)→GET /api/links 새 팀 scope='team' 링크로·명시 ?team_id 우선 / B 다른 팀 멤버 세션에선 그 팀 scope='team' 링크 안 보임(명시 ?team_id로도 비소속→무시·대표팀 fallback) / C personal 링크 작성자 본인만(작업 팀 무관) / D POST scope='team'→team_id가 work_team_id로 확정 저장·personal→NULL·명시 team_id 우선·비소속 명시→403 / E admin: 쿠키/명시 work_team_id 후 scope='team' POST·PUT·DELETE·admin GET은 전 팀 노출 / F 같은 팀 멤버 B가 멤버 A의 scope='team' 링크 PUT·DELETE→403·원본 유지·A 본인은 가능 / G admin이 타인 scope='team' 링크 PUT·DELETE 가능·타인 personal도 가능 / H admin이 work_team 없이(쿠키 X+?team_id X) scope='team' POST→400·personal POST→200 / I 회귀: personal CRUD 본인·비로그인 GET→[]·title/url 누락·잘못된 scheme→400 + db-conv(get_links None/set()/{ta}/{ta,tb}/타 사용자 컨벤션). 회귀: phase80~85 60/60 PASS(20.7s). `import app` OK. `get_links`/`update_link` 시그니처 외 호출부 없음(mcp_server.py에 link 도구 없음). 리뷰 차단 0·경고/관찰 4(admin GET orphan team 링크 노출=의도·수용 / `resolve_work_team` explicit 우선+`require_work_team_access` 후검증=의도·`manage/projects` 패턴 / `api_delete_link` default role 정리=무영향 / 관례 준수 ✔). advisor 2회 검토(plan 구조·admin GET 시맨틱·todo·커밋 / 시그니처 호출부 sanity grep — 전부 반영). **운영 서버 반영 시 재시작 필요**(코드 reload — `app.py`+`database.py`. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient로 완료). 범위 밖(불변): `users.team_id` 컬럼 자체 제거(#23), `_require_editor`(#16), `team_notices` 팀별 공지(#15-3) — 그룹 B 미완(#15-3 남음). | `app.py`, `database.py`, `tests/phase86_links_multiteam.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |
| 2026-05-13 | #15-3 team_notices 팀별 공지 전환 (그룹 B — **마지막 항목**, 그룹 B 완료) | 스키마 무변경(마이그레이션 phase 추가 없음 — `team_notices.team_id` 컬럼·백필 모두 그룹 A #2 Phase 1 / #4 Phase 2 에서 완료). 전역 단일 공지(`/api/notice` 무필터) → `work_team_id` 기준 팀별 공지로 전환 + 30일/100개 자동 정리 팀별 적용 + SSR 쿠키 hook 보완. `database.py`: ① `get_latest_notice()` → **`get_notice_latest_for_team(team_id)`** (rename + `WHERE team_id = ? ORDER BY id DESC LIMIT 1`; team_id None→None; NULL 잔존 row 미반환 — docstring). ② `save_notice(content, created_by)` → **`save_notice(content, team_id, created_by)`** (`INSERT INTO team_notices (team_id, content, created_by)`; 자동 정리 팀별: `DELETE ... WHERE team_id = ? AND created_at < datetime('now','-30 days')` + `DELETE ... WHERE team_id = ? AND id NOT IN (SELECT id FROM team_notices WHERE team_id = ? ORDER BY id DESC LIMIT 100)`; `team_id IS NULL` 잔존 row 는 매칭 안 되어 자동 정리 제외 — 계획서 §13 운영자 사후 정리·docstring). ③ `get_notice_history(limit=100)` → **`get_notice_history(team_id, include_null=False, limit=100)`** (`include_null=False`→`WHERE team_id = ?` / True(admin)→`WHERE team_id = ? OR team_id IS NULL`). ④ 신규 **`create_notification_for_team(team_id, type_, message, event_id=None, exclude_user=None)`** (`create_notification_for_all` 옆; `SELECT u.name FROM users u JOIN user_teams ut ON ut.user_id = u.id WHERE ut.team_id = ? AND ut.status = 'approved' AND u.is_active = 1` → exclude_user 제외 INSERT; 글로벌 admin user_teams row 없어 미수신 — docstring; team_id None→no-op). `app.py`: ① 신규 헬퍼 **`_notice_work_team(request, user, explicit_id=None) -> int|None`**(`_can_write_doc` 뒤; user None/미배정→None; 비admin 비소속 explicit_id 버림(`auth.user_can_access_team(user, _safe_int(explicit_id))` 실패 시 None) — 다른 팀 공지 임의 조회 차단; admin explicit 신뢰; → `auth.resolve_work_team(request, user, explicit_id=explicit_id)`). ② `GET /notice`(SSR): `user`+`tid=_notice_work_team(...)` → `notice = db.get_notice_latest_for_team(tid) if tid is not None else None` → `_ensure_work_team_cookie(request, resp, user)` 추가(#15 SSR 12페이지 적용 시 누락분 보완). ③ `GET /notice/history`(SSR): 동일 + `histories = db.get_notice_history(tid, include_null=auth.is_admin(user)) if tid is not None else []` + `_ensure_work_team_cookie`. ④ `GET /api/notice`: `api_get_notice(request, team_id: int = None)` — `tid=_notice_work_team(request, user, team_id)`; tid None→`{}`(비로그인/미배정/admin 비삭제팀0); else `db.get_notice_latest_for_team(tid) or {}`. ⑤ `POST /api/notice`: `_require_editor` → `data` → `team_id = auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`; None→`HTTPException(400, "현재 작업 팀이 필요합니다.")`(admin work_team 없이 호출 거부 — `/api/links` POST 미러); `auth.require_work_team_access(user, team_id)`(비admin 비소속→403; admin 통과); `db.save_notice(content, team_id, user["name"])`. **작성자 본인 게이트 없음 — 팀 공유 모델**(같은 팀 승인 멤버 누구나 작성·갱신; links 의 "작성자/admin만"과 의도적으로 다름 — 계획서 §8-1). ⑥ `POST /api/notice/notify`: `_require_editor` → `data`(빈 body면 `{}` — try/except) → `team_id = auth.resolve_work_team(request, user, explicit_id=(data or {}).get("team_id"))`; None→400; `require_work_team_access`; **resolve THEN fetch** — `notice = db.get_notice_latest_for_team(team_id)`; not notice→`{"ok":False,"reason":"no_notice"}`(기존 동작 보존); msg 생성 유지; `db.create_notification_for_team(team_id, "notice", msg, exclude_user=user["name"])`(전역 `create_notification_for_all`→팀별); `{"ok":True}`. **프론트엔드 변경 없음** — `notice.html`/`notice_history.html`/`base.html` 은 SSR `notice`(=`INITIAL_MD`)/`histories`(=`HISTORIES` tojson)/헤더 링크만 — 작업 팀 기준으로 바뀐 SSR 값이 자연 반영; 작업 팀 전환은 base.html `selectWorkTeam`→`location.reload()`(#15) 흐름으로 새 work_team_id 쿠키 적용 후 SSR 다시 렌더; `IS_EDITOR`/`user.role in ('editor','admin')` 리터럴 = #16 책임 — 미변경. **QA: 라이브 Playwright 대신 TestClient + 임시 DB — 운영 서버 IP 자동 로그인이라 다중 팀/admin 시나리오 브라우저 재현 불가**. `tests/phase87_team_notices_multiteam.py` 10/10 PASS(6.3s): 정적×2(database.py 4헬퍼 시그니처/쿼리·옛 `get_latest_notice` 부재·전역 일괄삭제 잔존 0 / app.py `_notice_work_team`·SSR `_ensure_work_team_cookie`+팀기준·`GET /api/notice` team_id 파라미터·`POST` resolve+require+400·작성자 게이트 없음·`notify` `create_notification_for_team`(전역 미사용)·`import app`) + A 다중 팀 사용자 작업 팀 전환(쿠키)→GET /api/notice 새 팀 최신 공지·명시 ?team_id 우선·old 팀 누수 없음 / B 다른 팀 멤버 세션 그 팀 공지 `{}`·명시 ?team_id 비소속도 무시·대표팀 fallback / C POST team_id work_team_id 확정·명시 본문 team_id 우선·비소속 명시→403·미배정→400·admin 쿠키/명시 후→200 / D notify 같은 팀 approved 멤버만 1건·발송자 제외·pending 미수신·다른 팀 미수신·글로벌 admin 미수신·공지 없는 팀→`{"ok":False,"reason":"no_notice"}` / E 팀 공유 모델 — 멤버 B 가 멤버 A 공지 POST(갱신)→200·created_by B 로·notify→C 수신·B 제외·GET 양쪽 최신 봄 / F 자동정리 팀별 — 팀A 100개+30일이전+팀B 5개+팀B 30일이전+NULL orphan 상태에서 팀A save 1회→팀A 정확히 100개·팀B 6개 그대로·NULL orphan 그대로·팀A 30일이전 삭제·팀B 30일이전 살아남음·새 팀A 공지 살아남음 / G NULL orphan — `get_notice_latest_for_team` 미반환·`get_notice_history(include_null=False)` 미포함·`include_null=True`(admin) 포함·SSR `/notice/history` 비admin 토큰 미포함·admin 토큰 포함 / H SSR — 쿠키 없는 멤버 `/notice`·`/notice/history` GET→Set-Cookie work_team_id 발급·작업 팀 공지 `INITIAL_MD` 렌더·미배정→Set-Cookie 없음·공지 미렌더·`GET /api/notice` `{}`·비로그인 `{}`. 회귀: phase80~86 73/73 PASS(28.0s). `import app` OK·Jinja `notice.html`/`notice_history.html`/`base.html` `get_template` OK. 호출부: `get_latest_notice`/`save_notice`(2-인자)/`get_notice_history`(0-인자) 옛 시그니처 — grep 결과 app.py 3곳 외 없음(mcp_server.py 에 notice 도구 없음), 전부 새 시그니처로 전환. `tests/check_notice.spec.js`(Playwright live server) 미실행 — 서버 재시작 필요, 재시작 후 별도 확인 권장(프론트 무변경·admin 은 first_active_team_id 로 작업 팀 결정되므로 활성 팀 있으면 정상). `tests/test_project_rename.py` 2 FAIL 사전 결함(옛 픽스처 DB `projects.team_id` 없음 — master HEAD 동일·#15-3 무관). 리뷰 차단 0·경고 2(`notice_page`/`notice_history_page` 가 `_ctx`(work_team_id 라벨) + `_notice_work_team` 둘 다 `resolve_work_team` 호출 — 페이지당 쿼리 1~2회 중복·#15에서 이미 기록된 같은 성질·#16 정리 후보 / `api_notify_notice` `(data or {}).get` 가 비-dict list 면 AttributeError — 기존 `api_save_notice` 도 무가드, 신규 결함 아님). advisor 1회 검토(NULL orphan GET 제외·history admin 포함·자동정리 team_id 파라미터화·`create_notification_for_team` approved JOIN·admin 미수신·SSR `_ensure_work_team_cookie` 누락 보완·POST 패턴 #15-2 미러·팀 공유 모델 작성자 게이트 없음·테스트 시나리오 enumeration·그룹 B 완료 토글 pre-check — 전부 반영). **운영 서버 반영 시 재시작 필요**(코드 reload — `app.py`+`database.py`. 스키마 무변경 → 마이그레이션 불필요. 단 본 단위 검증은 TestClient/임시 DB로 완료). **그룹 B(#11~#15-3) 완료** — 진행 추적 메모 `- [x] 그룹 B 완료` 토글. 다음: 그룹 C #16(시스템 관리자 슈퍼유저 권한 정리 — `_require_editor`/`is_editor`/`IS_EDITOR` 역할 리터럴·admin 슈퍼유저 진입·쓰기 시 work_team 검증 400). | `app.py`, `database.py`, `tests/phase87_team_notices_multiteam.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md` |

| 2026-05-13 | #12 회귀 핫픽스 — IS_UNASSIGNED 중복 선언 SyntaxError | 그룹 B #12(fd1003a)가 `base.html:348`에 `var IS_UNASSIGNED` 전역을 추가했는데 `home.html:334`·`doc_editor.html:272`의 `const IS_UNASSIGNED` 선언이 남아 같은 전역 스코프 재선언 → 파싱 단계 SyntaxError로 해당 `{% block scripts %}` `<script>` 전체(DOMContentLoaded·에디터 초기화 포함) 미실행 → 홈(`/`) 빈 화면(`#view-user`/`#view-unassigned`/`#view-guest` 모두 `display:none`)·문서 에디터(`/doc/{id}`) TUI Editor 미렌더. 핫픽스: 두 child 템플릿의 `const IS_UNASSIGNED` 선언 라인 제거(주석으로 base.html 전역 명시) — RHS 값(`is_unassigned`) 양쪽 동일이라 동작 무변화·사용처(home.html:609,623 / doc_editor.html:410,415) 그대로 base.html 전역 참조. 다른 base.html 전역(`CURRENT_USER`·`IP_AUTOLOGIN_WARNING`)은 child 재선언 없음 확인(`work_team_id`는 `CURRENT_USER` 객체 안 — 별도 const 없음). 검증: `import app`+Jinja `get_template` home/doc_editor/base OK · phase80~87 회귀(TestClient) 전부 PASS · 라이브(`192.168.0.18:8443`) Playwright — `/` view-user `display:block`·홈 정상 렌더·`const IS_UNASSIGNED` 0건/`var` 1건, `/doc/new` TUI Editor 툴바+본문 정상 렌더·콘솔 SyntaxError 없음(서버 템플릿 reload 후). 템플릿만 변경 → 운영 반영 시 재시작 필요. | templates/home.html, templates/doc_editor.html · `.claude/workspaces/current/{qa_report.md, screenshots/hotfix_home_after.png, screenshots/hotfix_doc_editor_after.png}`
| 2026-05-13 | #17 팀 생성/관리 페이지 (그룹 C 두 번째) | 스키마 phase 추가 없이 `teams.name_norm` UNIQUE 인덱스(`idx_teams_name_norm`) idempotent 추가 + 사전 dup detection → `team_migration_warnings` 카테고리 `teams_name_norm_duplicate` 누적 후 skip race 가드. `database.py`: `create_team` 4단계 재구성(`_TEAM_NAME_RE=^[A-Za-z0-9_]+$` → `normalize_name` → name_norm 사전 lookup → INSERT, `IntegrityError → ValueError("duplicate_name")`). `update_team` 함수 제거. 신규 `get_team_members(team_id)`(status='approved'+users JOIN) / `set_team_member_role(team_id, user_id, role)`(allowlist 'admin'|'member', not_member 가드). `app.py`: `RESERVED_TEAM_NAMES` 25개 + `_registered_route_first_segments()`(런타임 평가, `{xxx}` path-param 스킵) + `_team_name_collides_with_route()` NFC casefold 비교. `POST /api/admin/teams` 정규식/예약어/충돌/`duplicate_name` 분기 모두 한국어 detail. **`PUT /api/admin/teams/{team_id}` 라우트 제거 + admin.html 인라인 수정 UI 제거**(`startEditTeam`/`saveTeam` JS + `team-name-input`/`team-edit-btn`/`team-save-btn` DOM + `.team-name-input` CSS 1964-1968 일관 제거, dead reference 0건 grep 확인). 신규 라우트 `GET /api/admin/teams/{id}/members` + `PUT .../members/{user_id}/role`. `templates/admin.html`: 팀 row 단순화(span+삭제+멤버 관리 버튼만) + 팀 멤버 관리 모달(`#team-members-modal`, 560px) + JS 함수 `openTeamMembers`/`_loadTeamMembers`/`setTeamMemberRole`/`_renderTeamRow` 추가, 클라이언트 `_TEAM_NAME_RE` 사전 검증. 리뷰: 차단 0, 경고 3(.team-name-input CSS orphan은 리뷰 단계에서 자동 제거, onclick single-quote 위험은 백엔드 정규식 보호 의존, role-badge 기본 modifier 시각 빈약은 향후 디자인). **QA: TestClient + 임시 DB** — `tests/phase89_team_create_manage.py` 24/24 PASS(정적 S1~S10 + S_import_app 11건 / 행동 B1~B13 13건). B9 초기 실행 `database is locked` 인시던트는 테스트 코드 with-block 안 client.post 호출 버그 → fixture 수정으로 해소(프로덕션 무변경). `test_project_rename.py` 2건 실패는 #15-1 선재 결함 동일(본 사이클 무관). **서버 재시작 필요**(운영 반영 시 — 라우트 변경 reload + 인덱스 첫 부팅 자동 생성). 범위 밖(불변): #18 멤버 관리 `/admin/members` 전용 페이지(자기 자신/마지막 admin 가드 포함). | `database.py`, `app.py`, `templates/admin.html`, `static/css/style.css`, `tests/phase89_team_create_manage.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md, backend_changes.md, frontend_changes.md, code_review_report.md, qa_report.md`
| 2026-05-13 | #18 멤버 관리 페이지 (그룹 C 세 번째) | 스키마 무변경. `database.py`: ① `set_team_member_role` 에 마지막 admin 가드 추가 — `role='member'` 강등 시 대상이 'admin' 이고 `COUNT(role='admin' AND status='approved') ≤ 1` 이고 `teams.deleted_at IS NULL` 이면 `ValueError("last_admin_protected")` (트랜잭션 원자성). 삭제 예정 팀은 면제. ② `evict_team_member(team_id, user_id)` 신규 — `UPDATE user_teams SET status='rejected'` (row 보존, 계획서 §10 562), 같은 마지막 admin 가드. ③ `get_admin_teams_for(user)` — admin → 모든 active 팀, 팀 admin → `role='admin' AND status='approved'` 팀, 비권한 → `[]`. ④ `list_team_memberships(team_id)` — `{approved, pending, rejected}` 분류 dict, 각 list joined_at 오름차순. `app.py`: ① `GET /admin/members` 페이지 — 미로그인 401, `get_admin_teams_for` 빈 → 403, 컨텍스트 `manageable_teams/is_system_admin/current_user_id` + `_ensure_work_team_cookie`. ② `/api/team-manage/{team_id}/...` 3개 라우트 — `_require_team_admin` 가드(`auth.is_team_admin` 위임, CSRF 통합), PUT role + POST evict 자기 자신 가드(`caller.id == user_id` → 400, deleted_at 무관 — 계획서 §10 line 572 면제 대상은 "마지막 팀 관리자 보호" 만), ValueError 매핑(not_member 404, last_admin_protected 400, invalid_role 400). 기존 admin/teams/{id}/members* 라우트 (#17) 미변경. `templates/members_admin.html` 신규: base.html 확장, 팀 드롭다운 + 3 섹션(승인/대기/추방), 본인 row "(본인)" 마커 + disabled 버튼 + 툴팁, 마지막 admin 클라이언트 카운팅 후 disabled, IP 관리 영역은 `is_system_admin` 시에만 /admin 으로 deep-link, JS `loadTeamData`/`renderApproved`(`_countActiveAdmins`)/`renderPending`/`renderRejected`/`onToggleRole`/`onEvict`/`onDecide`(기존 `/api/teams/{id}/applications/{uid}/decide` 재사용). `templates/admin.html`: "멤버 목록" 탭에 새 페이지 안내 링크 1줄 추가. 리뷰: 차단 0, 경고 3(onclick single-quote 위험은 향후 일괄 리팩터링, 마지막 admin race는 BEGIN IMMEDIATE 직렬화로 안전, nav 메뉴 부재는 별건). **QA: TestClient + 임시 DB** — `tests/phase90_member_management.py` 19/19 PASS(정적 S1~S6 + S_import_app / 행동 B1~B12, 자기 자신·마지막 admin·deleted_at 면제·status 보존 모두 검증). **phase89 B10 회귀** 발견 — 마지막 admin 가드 도입으로 단순 토글 차단 → fixture 에 admin 1명 추가 시드 (정책 변경 반영, 프로덕션 무변경) → 24/24 재PASS. `test_project_rename.py` 2건 선재 결함 영역 무관. **서버 재시작 필요** (라우트 추가 reload). 범위 밖(불변): nav 진입 메뉴(별건), legacy `pending_users` (계정 가입 신청은 #2/#8 기능), IP 라우트 권한 변경 없음. | `database.py`, `app.py`, `templates/admin.html`, `templates/members_admin.html`(신규), `tests/phase89_team_create_manage.py`(B10 fixture 갱신), `tests/phase90_member_management.py`(신규), `팀 기능 구현 todo.md`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md, backend_changes.md, frontend_changes.md, code_review_report.md, qa_report.md`
| 2026-05-13 | #19 메뉴 외부 노출 관리 페이지 (그룹 C 네 번째) | 매우 소규모 사이클. 사전 자산: `team_menu_settings` 테이블 + UNIQUE 인덱스(#2), `get_team_menu_visibility` read 헬퍼 + `_PORTAL_MENU_DEFAULTS`(kanban/gantt/doc/check=True, calendar=False)(database.py:5553·5544), `team_portal.html` 의 `{% if m.X %}` 탭/패널 토글(#13). 본 사이클 추가: ① `database.py` `set_team_menu_visibility(team_id, menu_key, enabled)` — allowlist `_PORTAL_MENU_DEFAULTS.keys()` 외 `ValueError("invalid_menu_key")`, `INSERT ... ON CONFLICT(team_id, menu_key) DO UPDATE` UPSERT. ② `app.py` `GET /admin/menus` 페이지 — `get_admin_teams_for` 빈 → 403, 컨텍스트 `manageable_teams/is_system_admin`, 템플릿 `menu_settings.html`. ③ `app.py` API `GET /api/team-menu/{team_id}`(read 헬퍼 재사용) + `PUT /api/team-menu/{team_id}/{menu_key}`(_require_team_admin + body `enabled` 필수). ④ `templates/menu_settings.html` 신규 — 팀 드롭다운 + 5개 `switch switch-sm` 토글(admin.html 활성 토글과 동일 패턴) + 안내 문구(메뉴 OFF = UI 진입 차단, 데이터 차단 아님 §9) + JS `loadMenuSettings`/`onToggle`(실패 시 UI 복구). 시드 row 미추가 — `_PORTAL_MENU_DEFAULTS` fallback 으로 충분. 리뷰: 차단 0, 경고 0. **QA: TestClient + 임시 DB** — `tests/phase91_menu_settings.py` 12/12 PASS(정적 S1~S4 + S_import / 행동 B1~B7). 회귀: phase89 24/24 + phase90 19/19 PASS, 누적 55/55. **서버 재시작 필요**(라우트 추가 reload). 범위 밖(불변): admin.html 에 메뉴 관리 페이지 안내 링크(별건), team_portal.html 토글은 #13 에서 이미 적용. | `database.py`, `app.py`, `templates/menu_settings.html`(신규), `tests/phase91_menu_settings.py`(신규), `팀 기능 구현 todo.md`. workspace: `00_input/feature_spec.md, backend_changes.md, frontend_changes.md, code_review_report.md, qa_report.md`
| 2026-05-13 | #20 문서·체크 항목별 공개 설정 (그룹 C 다섯 번째) | **Invariant lock 사이클 — 프로덕션 코드 변경 0건**. 사전 자산: `meetings.is_public`/`checklists.is_public` 컬럼 + 에디터 폼 토글(doc_editor.html:444·703, check_editor.html:259·571·795)·#10 이전 흡수, `events.is_public` 3상태(1/NULL/0) + 마이그레이션(database.py:274-282) 그룹 A 이전, `get_kanban_events`/`get_project_timeline`/`get_checklists`/`get_public_portal_data` 의 viewer=None public_filter(database.py:3064·6172·5597), 히든/외부비공개 NOT IN 차단 통합, 미지정 이벤트 자동 강제(app.py:4209: `updated["is_public"] = 0 if (proj.is_private) else 1`), team_portal.html tab+panel 양쪽 `{% if m.X %}` 게이팅(#13). 본 사이클 산출물: `tests/phase92_public_portal_invariant.py` 신규 — INV1(is_public=0 차단), INV2(메뉴 OFF 가 payload 차단 안 함), INV3(NULL → 프로젝트 공개로 결정), INV4(히든 프로젝트 차단), INV5 정적(app.py:4209 미지정 강제 라인), INV6(menu.calendar OFF + kanban ON → 같은 row 양쪽 payload 존재), INV7 정적(5개 portal-panel 모두 `{% if m.X %}` 직속), + get_public_portal_data 가 menu_key 필터를 데이터 쿼리에 안 쓴다는 정적 검증. 8/8 PASS. fixture 인시던트 1건: `_make_event` kanban_status 기본값 누락 → `get_kanban_events` filter(kanban_status NOT NULL OR project NULL) 충돌 → fixture 에 `kanban_status='todo'` 기본값 추가(프로덕션 무변경). 회귀: phase89 24/24 + phase90 19/19 + phase91 12/12 모두 PASS, 누적 63/63. **서버 재시작 불필요** (코드 변경 0). 흡수 메모: todo "meetings·checklists 작성 폼에 공개 토글" 는 #10 이전 사이클에서 완료된 작업. | `tests/phase92_public_portal_invariant.py`(신규), `팀 기능 구현 todo.md`. workspace: `00_input/feature_spec.md, backend_changes.md, frontend_changes.md, code_review_report.md, qa_report.md`