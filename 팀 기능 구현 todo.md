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
- [ ] **구현 (단계 내부 idempotency 가드 — destructive 작업 보호)**
  - Phase 마커는 큰 틀의 idempotency만 보장한다. 단계 내부 SQL은 추가로 WHERE 가드를 둬 재실행/부분 실패 후 재진입에도 데이터를 망가뜨리지 않도록 한다.
  - [ ] `users.name_norm` 백필: `WHERE name_norm IS NULL` (이미 채운 row 보호)
  - [ ] `users.password_hash` 변환 + `users.password` NULL 처리: `WHERE password IS NOT NULL AND password != ''` (이미 hash 변환 후 NULL인 row를 다시 hash()에 넘기지 않음)
  - [ ] admin `users.team_id`/`mcp_token_hash`/`mcp_token_created_at` NULL 처리: 이미 NULL이면 노옵 (NULL → NULL은 안전하지만 명시적으로 `WHERE ... IS NOT NULL`로 갱신 카운트 0인 경우와 구분)
  - [ ] admin `user_ips` whitelist → history 강등: `WHERE type='whitelist' AND user_id IN (admin)` (이미 history인 row는 건드리지 않음)
  - [ ] `users.role` editor → member 일괄 갱신: `WHERE role='editor'`
  - [ ] `events/checklists/projects.team_id` NULL 보강: 모두 `WHERE team_id IS NULL` (이미 채운 row 보호)
  - [ ] `events.project_id` / `checklists.project_id` 백필: `WHERE project_id IS NULL`
  - [ ] `notifications.team_id`, `links.team_id`, `team_notices.team_id` 백필: 모두 `WHERE team_id IS NULL` (재실행 시 기존 값 덮어쓰지 않음)
  - [ ] `pending_users` 자동 삭제: 한 번 후 빈 테이블이라 무해하지만 Phase 마커로 단계 자체를 건너뜀
  - [ ] "관리팀" rename: `WHERE name='관리팀'` (재실행 시 이미 AdminTeam이면 노옵)
- [ ] **구현 (Phase 4 UNIQUE preflight 검사)**
  - [ ] Phase 4 인덱스 생성 **직전**에 데이터 충돌 사전 점검 — 충돌 시 **서버 시작 거부 + `team_migration_warnings`에 충돌 row 정보 기록**:
    - `users.name_norm` 충돌 (전역, `is_active` 무관)
    - `teams.name_norm` 충돌
    - `(team_id, projects.name_norm)` 충돌 (팀 안에서만)
    - `user_ips`의 `type='whitelist'` `ip_address` 전역 충돌
    - `user_teams(user_id, team_id)` 중복 row
    - `team_menu_settings(team_id, menu_key)` 중복 row
  - [ ] 충돌 감지 시 운영자가 자동 백업으로 복구 + 충돌 데이터 정리 후 재시작 (Phase 4는 Phase 2 백필 직후이므로 사전 정리 후 재시작 가능)
- [ ] **검증**
  - [ ] 빈 DB에서 첫 시작 → Phase 1~4 마커 모두 기록 확인
  - [ ] 재시작 시 모든 Phase 건너뛰기 확인
  - [x] 인위적 실패 주입 시 서버 시작 거부 + 백업 파일로 복구 가능 확인 — `verify_phase_infra.py` case 3 PASS
  - [x] preflight 충돌 주입 시 (예: `name_norm` 중복 강제) 서버 시작 거부 + 경고 로그에 충돌 row 명시 — case 7 PASS
  - [ ] **Phase 마커 강제 삭제 후 재실행 시뮬레이션** — 마커가 없어 단계가 다시 돌아도 단계 내부 WHERE 가드 덕에 비밀번호 hash 재변환·team_id 덮어쓰기·관리팀 rename 중복 등 데이터 망가짐 없음 확인

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

- [ ] **구현**
  - [ ] `/` 비로그인: 팀 목록 + 로그인 + 계정 가입 버튼 표시
  - [ ] 팀 클릭 시 `/팀이름`으로 이동
- [ ] **검증**
  - [ ] 팀 목록에 삭제 예정 팀(`teams.deleted_at IS NOT NULL`) 제외 확인

### #12. `/` 팀 미배정 로그인 사용자 + "내 자료" 영역

📖 섹션 7 팀 미배정 로그인 사용자
**의존: ← #8, #10**

- [ ] **구현**
  - [ ] 팀 목록 + 팀 신청 버튼 (신청 전/대기중/거절 상태 분기)
  - [ ] "내 자료" 영역: 본인 작성 개인 문서(`is_team_doc=0`)만 조회·신규 작성 가능
  - [ ] **본인 작성 개인 문서는 `team_share` 값과 무관하게 본인에게만 표시** — 팀 미배정 화면에서는 `team_share=1`이라도 타인에게 공유되는 의미가 없음 (계획서 섹션 7·8). 자기 자료 통합 노출 목적의 영역
  - [ ] 팀 미배정 사용자가 신규 개인 문서 작성 시 `team_id = NULL`로 저장
  - [ ] 알림 카드·뱃지·페이지 비노출 (팀 미배정 상태)
  - [ ] `team_share` UI 비활성화 (팀 미배정 상태에서 토글 의미 없음)
- [ ] **검증**
  - [ ] 팀 미배정 → 신규 작성 → `team_id NULL` 확인
  - [ ] 알림이 어디에도 안 보이는지 확인
  - [ ] `team_share=1`로 저장된 본인 개인 문서가 "내 자료"에는 표시되되, 다른 사용자(다른 팀 멤버 포함)에겐 노출 안 됨

### #13. `/팀이름` 비로그인 공개 포털

📖 섹션 7, 섹션 9 (공개 포털 데이터 노출 정책)
**의존: ← #10**

- [ ] **구현**
  - [ ] `/팀이름` 동적 라우트 (예약어 차단)
  - [ ] **URL 대소문자 정확 일치**: `ABC`로 생성한 팀은 `/ABC`만 유효, `/abc`는 404 (계획서 섹션 4)
  - [ ] **데이터 필터 (공개 포털 노출 쿼리)**: `is_public=1` + 히든 프로젝트(`is_hidden=1`) 제외 — 메뉴 노출 설정은 데이터 필터에 **넣지 않는다**
  - [ ] **메뉴 노출 설정**: 공개 포털 UI의 탭/링크/네비게이션 진입만 차단 (계획서 섹션 9: "메뉴 노출 설정 = UI 진입 차단, 데이터 차단 아님")
  - [ ] 따라서 캘린더 메뉴 OFF여도 같은 events 데이터가 칸반/간트 메뉴를 통해 노출될 수 있어야 함 (의도된 동작)
  - [ ] 히든 프로젝트 항목 완전 차단 (SSE 등 모든 채널, `is_public` 값과 무관)
  - [ ] "계정 가입" 버튼 노출 (가입 후 자동 로그인 + `/`로 이동)
  - [ ] 삭제 예정 팀: 안내 페이지만 표시 (계정 가입 버튼·팀 신청·공개 데이터 모두 비노출)
- [ ] **검증**
  - [ ] 비로그인 사용자가 비공개 항목·히든 프로젝트 항목에 접근 못 함
  - [ ] `/팀이름` 라우트가 `/admin`, `/api/...`, `/docs`, `/redoc`, `/openapi.json`과 충돌 없음
  - [ ] 같은 이름 다른 대소문자(`/ABC` vs `/abc`)는 404로 분리

### #14. `/팀이름` 로그인 사용자 (소속 무관)

📖 섹션 7 팀에 배정된 로그인 사용자, 시스템 관리자
**의존: ← #13**

- [ ] **구현**
  - [ ] 로그인 상태에서도 `/팀이름`은 공개 포털만 표시 (admin도 동일, 리다이렉트 X)
  - [ ] 홈 버튼은 `/`로 이동
  - [ ] 팀 미소속 + pending 상태에 따라 "팀 신청" / "가입 대기 중" 버튼 분기

### #15. 프로필 메뉴 "팀 변경" UI + work_team_id 쿠키

📖 섹션 7 현재 작업 팀 선택, 섹션 16 권한 원칙
**의존: ← #10**

- [ ] **구현 (서버)**
  - [ ] `/api/me/work-team` (POST): 팀 변경 검증 + `work_team_id` 쿠키 갱신
  - [ ] SSR 첫 페이지 렌더 시 쿠키 읽고 검증, 없으면 대표 팀 계산해 Set-Cookie
  - [ ] **일반 사용자 기본값 (쿠키 없음)**: 대표 팀 = `user_teams.join_status='approved'` AND `teams.deleted_at IS NULL` 중 `joined_at` 가장 이른 팀
  - [ ] **admin 기본값 (쿠키 없음)**: `user_teams` row가 없으므로 대표 팀 로직 적용 안 함 — **마지막 선택 팀(별도 저장 시) 또는 첫 번째 비삭제 팀**(`teams.deleted_at IS NULL` 중 `id` 가장 작은 팀)으로 fallback (계획서 섹션 7)
  - [ ] 저장된 작업 팀이 무효(삭제 예정·소속 빠짐) 시 쿠키 무시 + 새 대표 팀(또는 admin은 첫 번째 비삭제 팀)
  - [ ] 모든 팀 컨텍스트 API가 `team_id` 파라미터를 명시적으로 받고 `require_work_team_access`로 검증
- [ ] **구현 (프론트엔드)**
  - [ ] 프로필 메뉴에 "팀 변경" 버튼 + 드롭다운
  - [ ] 팀 선택 시 `/api/me/work-team` 호출 + 페이지 리로드
  - [ ] 프로필에 현재 작업 팀 이름 표시 (admin은 "슈퍼유저" 표시)
  - [ ] 기존 칸반·간트·캘린더의 화면별 팀 선택 드롭다운 제거
- [ ] **검증**
  - [ ] 첫 로그인(쿠키 없음) → 대표 팀 자동 선택 + Set-Cookie
  - [ ] 작업 팀 전환 시 캘린더·칸반·간트·문서·체크 모두 새 팀 컨텍스트로

### #15-1. 히든 프로젝트 다중 팀 전환

📖 섹션 12, 섹션 8-1 (히든 프로젝트 추가 예외)
**의존: ← #5, #6, #15** (work_team_id 도입 후 헬퍼 전환 가능)

> 기존 히든 프로젝트 코드는 owner의 `users.team_id`를 단일 팀 기준으로 사용한다. 다중 팀 모델로 전환하면서 `projects.team_id`와 `user_teams`로 기준을 옮긴다.

- [ ] **구현 (생성)**
  - [ ] `create_hidden_project`: owner의 `users.team_id` 참조 제거 → 요청의 `work_team_id` 기준 `team_id` 저장
  - [ ] `require_work_team_access`로 작성자가 그 팀 승인 멤버임을 검증
- [ ] **구현 (멤버 후보 헬퍼 전환)**
  - [ ] `get_hidden_project_addable_members` 등 멤버 후보 조회: `users.team_id` 단일 비교 → `user_teams.join_status='approved'` + `projects.team_id` 비교
  - [ ] **`owner_id = NULL` 복구 시에도 owner의 팀이 아니라 `projects.team_id` 기준으로 후보 조회** (owner 부재 케이스 핵심)
  - [ ] admin은 멤버 후보 명단에서 자동 제외
  - [ ] 일정/업무 담당자(`assignee`) 후보도 동일하게 `project_members`로 제한 (히든 프로젝트 한정)
- [ ] **구현 (admin 동등 관리 기능)**
  - [ ] admin은 owner 자리를 차지하지 않지만 owner 동등 기능 사용 (멤버 추가/삭제, owner 지정/변경, 프로젝트 편집)
  - [ ] `owner_id = NULL` 복구 흐름: admin이 같은 팀 승인 멤버 추가 → 그 멤버를 owner로 지정 (admin 자신은 owner 안 됨)
- [ ] **구현 (owner 자동 이양)**
  - [ ] `transfer_hidden_projects_on_removal()`도 `user_teams` 기준으로 동작 검증 (다음 owner 후보 조회를 `users.team_id`가 아닌 `user_teams + projects.team_id`로)
- [ ] **검증**
  - [ ] owner가 팀에서 추방 → `project_members` 중 같은 팀 활성 멤버에게 자동 이양
  - [ ] 후보 없으면 `owner_id = NULL` → admin이 신규 멤버 추가 후 owner 지정 가능
  - [ ] admin이 멤버 후보 드롭다운·assignee 후보에 노출 안 됨
  - [ ] 다중 팀 사용자가 owner인 히든 프로젝트는 `projects.team_id` 팀 기준으로만 멤버 후보 조회

### #15-2. links 기능 다중 팀 전환

📖 섹션 13 links 테이블 정리, 섹션 16 권한 원칙
**의존: ← #2, #15** (work_team_id 도입 후 라우트 전환 가능)

> 현재 `/api/links`는 단일 `users.team_id` 기준으로 동작하므로 다중 팀 모델 전환에서 `work_team_id` 또는 명시 `team_id` 기반으로 옮긴다. 백필(`#4`)과 완전 삭제(`#23`)에는 이미 들어있지만 라우트 전환 항목이 별도로 필요하다.

- [ ] **구현 (라우트 전환)**
  - [ ] `GET /api/links` 조회: `scope='personal'`은 작성자 본인 row만, `scope='team'`은 `work_team_id` 또는 명시 `team_id`의 승인 멤버에게만 노출 (`require_work_team_access` 검증)
  - [ ] `POST /api/links` 생성: `scope='team'`일 때 `team_id`를 `work_team_id`로 확정해 저장 (`personal`은 NULL 유지). admin은 `work_team_id` 명시 필수
  - [ ] `PUT /api/links/{id}` 수정: **작성자 본인 + admin만** 편집 가능 (`scope='team'`/`personal` 모두). 같은 팀 멤버라도 작성자가 아니면 403. 일정·체크의 팀 공유 모델과 다른 예외 — 링크는 작성자가 직접 큐레이팅하는 자료라는 운영 의도(계획서 섹션 8-1)
  - [ ] `DELETE /api/links/{id}` 삭제: 위와 동일 권한 모델 (작성자 본인 + admin만)
- [ ] **구현 (헤더 드롭다운 UI)**
  - [ ] 헤더 링크 드롭다운이 현재 작업 팀의 `scope='team'` 링크 + 본인 `personal` 링크를 통합 표시하도록 갱신
  - [ ] 작업 팀 전환 시 드롭다운이 새 팀 컨텍스트로 갱신
- [ ] **검증**
  - [ ] 다중 팀 사용자가 작업 팀 전환 → 헤더 링크 드롭다운이 새 팀의 `scope='team'` 링크로 바뀜
  - [ ] 다른 팀 멤버 세션에서는 그 팀 `scope='team'` 링크가 안 보임
  - [ ] `personal` 링크는 작성자 본인에게만 노출 (작업 팀과 무관)
  - [ ] admin이 `work_team_id` 명시 후 `scope='team'` 링크 생성·편집·삭제 가능
  - [ ] **같은 팀 멤버 A가 만든 `scope='team'` 링크를 멤버 B가 편집·삭제 시도 → 403** (작성자/admin만 허용 정책)
  - [ ] admin이 다른 사용자가 만든 `scope='team'` 링크 편집·삭제 가능

### #15-3. team_notices 팀별 공지 전환

📖 섹션 13 team_notices 팀별 공지 전환
**의존: ← #2, #4, #15** (Phase 1 컬럼 + Phase 2 백필 + work_team_id 도입 후)

> 현재 `team_notices`는 `team_id`가 없는 전역 공지 구조(`/api/notice` 단일 라우트). 1차 팀 기능 작업에서 팀별 공지로 전환한다(사용자 결정).

- [ ] **구현 (라우트 전환)**
  - [ ] `GET /api/notice`: 현재 작업 팀(`work_team_id`)의 최신 공지 1건 반환. NULL 잔존 row는 admin 슈퍼유저에게만 노출
  - [ ] `POST /api/notice`: 새 공지를 `work_team_id` 기준으로 저장 (admin은 `work_team_id` 명시 필수)
  - [ ] `POST /api/notice/notify`: 공지 발송 알림을 같은 팀 승인 멤버에게만 전송 (현 작업 팀 기준)
  - [ ] **작성·갱신 권한 = 팀 공유 모델 (사용자 결정)**: 같은 팀 승인 멤버 누구나 공지 작성·갱신·발송 가능 (계획서 섹션 8-1 자료별 적용 표). 향후 운영 통제 강화가 필요하면 별도 작업으로 팀 관리자 전용 모델로 전환
- [ ] **구현 (자동 정리 정책)**
  - [ ] 30일 이전 / 100개 초과 자동 정리를 **팀별로 적용** (`WHERE team_id = ?` 조건). 전역 일괄 삭제 X
- [ ] **구현 (UI)**
  - [ ] `/notice`, `/notice/history` 화면이 현재 작업 팀의 공지·이력만 표시
  - [ ] 작업 팀 전환 시 화면이 새 팀 공지로 갱신
- [ ] **검증**
  - [ ] 다중 팀 사용자가 작업 팀 전환 → 공지 화면이 새 팀 공지로 바뀜
  - [ ] 다른 팀 공지가 노출되지 않음
  - [ ] 공지 발송 알림이 같은 팀 멤버에게만 도착, 다른 팀에는 미도착
  - [ ] 자동 정리 후 다른 팀 공지는 영향 없음 (한 팀 100개 초과 시 그 팀만 정리)
  - [ ] NULL 잔존 row는 admin 외에 안 보임

---

## 그룹 C: 관리·통합 기능 (#16~#22)

### #16. 시스템 관리자 슈퍼유저 권한 정리

📖 섹션 2, 섹션 7 시스템 관리자, 섹션 16 권한 원칙
**의존: ← #2, #15**

- [ ] **구현**
  - [ ] admin이 `/`, `/doc`, `/check`, `/calendar`, `/kanban`, `/gantt`, `/project-manage`에 슈퍼유저로 진입
  - [ ] admin의 쓰기 요청에서 `work_team_id` 명시 검증, 미선택 시 400
  - [ ] `/admin`은 운영 기능 중심으로 정리 (일반 자료 편집은 일반 화면 사용)
- [ ] **검증**
  - [ ] admin이 일반 화면에서 모든 팀 자료를 보고 편집 가능
  - [ ] admin이 팀 미선택 상태에서 쓰기 시도 → 400

### #17. 팀 생성/관리 페이지

📖 섹션 4, 섹션 11 팀 관리자 지정, 섹션 13 제거 대상 API
**의존: ← #2**

- [ ] **구현**
  - [ ] 팀 생성 API: **팀명 정규식 `^[A-Za-z0-9_]+$`** (영문 대소문자·숫자·언더바만 허용)
  - [ ] **예약 경로 검사**: 하드코딩 목록 + 실제 등록 route 목록을 **함께** 검사 (누락 방지)
    - 하드코딩 목록: `api`, `admin`, `doc`, `check`, `kanban`, `gantt`, `calendar`, `mcp`, `mcp-codex`, `uploads`, `static`, `settings`, `changelog`, `register`, `project-manage`, `ai-import`, `alarm-setup`, `notice`, `trash`, `remote`, `avr`, `favicon.ico`, `docs`, `redoc`, `openapi.json`
  - [ ] `name_norm` UNIQUE 검사 (대소문자·NFC 정규화 후)
  - [ ] **팀 이름은 생성 후 변경 불가** — 입력 대소문자 그대로 저장 + URL 세그먼트로 그대로 사용
  - [ ] 팀 관리자 지정 UI: 같은 팀 멤버 중 선택해 `team_role = admin`
  - [ ] 팀 관리자 다중 지정 가능
  - [ ] **`PUT /api/admin/teams/{team_id}` 제거 + 프론트의 팀 이름 수정 UI 제거**
- [ ] **검증**
  - [ ] 예약어로 팀 생성 시 차단 (하드코딩 목록 + 실제 등록 route 모두)
  - [ ] 같은 이름 다른 대소문자 팀 생성 시 차단 (`ABC` 존재 시 `abc` 생성 시도 → 차단)
  - [ ] NFC 정규화 차이로도 중복 차단 (한글 결합형/조합형)
  - [ ] 정규식 위반(공백·특수문자·한글) 시 차단

### #18. 멤버 관리 페이지

📖 섹션 10 멤버 관리
**의존: ← #8, #17**

- [ ] **구현**
  - [ ] 팀 드롭박스 + 신청 멤버 수락/거절 + 기존 멤버 추방
  - [ ] 자기 자신 강등·추방 제한
  - [ ] 마지막 팀 관리자 보호 (삭제 예정 팀은 면제)
  - [ ] IP 관리 영역은 admin 세션에만 노출 (팀 관리자에게 숨김)
  - [ ] 다중 소속 사용자의 IP 변경 시 영향 안내 표시
- [ ] **검증**
  - [ ] 팀 관리자 1명 상태에서 자기 자신 추방 시도 → 차단
  - [ ] 삭제 예정 팀의 마지막 팀 관리자 추방 가능
  - [ ] 팀 관리자 세션에 IP 관리 UI 안 보임

### #19. 메뉴 외부 노출 관리 페이지

📖 섹션 9 1단계 메뉴 노출
**의존: ← #17**

- [ ] **구현**
  - [ ] `team_menu_settings` 기반 팀별 메뉴 토글 (kanban/gantt/doc/check/calendar)
  - [ ] 기본값 적용 (캘린더만 X, 나머지 O)
  - [ ] 시스템 관리자·팀 관리자가 설정 가능
- [ ] **검증**
  - [ ] 메뉴 OFF → `/팀이름` 공개 포털에서 해당 메뉴 진입 사라짐
  - [ ] 메뉴 OFF는 데이터 차단이 아니라 UI 진입 차단임을 확인

### #20. 문서·체크 항목별 공개 설정

📖 섹션 9 2단계 항목별 공개 설정
**의존: ← #10**

- [ ] **구현**
  - [ ] meetings·checklists 작성 폼에 공개 토글
  - [ ] **공개 포털 데이터 쿼리**: `is_public=1` 조건 + 히든 프로젝트(`is_hidden=1`) 제외만 적용 — 메뉴 노출 설정은 **데이터 쿼리에 포함하지 않는다** (계획서 섹션 9 1단계 정의)
  - [ ] 메뉴 노출은 UI 진입(탭/링크)에서만 분기. 같은 데이터가 여러 메뉴를 통해 표시될 수 있음
- [ ] **검증**
  - [ ] 공개 항목·비공개 항목이 외부 포털에서 의도대로 노출/차단
  - [ ] 캘린더 메뉴 OFF + 칸반 메뉴 ON일 때, 같은 events row가 칸반에는 노출되고 캘린더 진입은 막히는지 확인

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

- [ ] 그룹 A 완료 (#1~#10) — DB·인증 기반 + 데이터 백필 끝
- [ ] 그룹 B 완료 (#11~#15-3) — 화면 정비 + work_team_id 도입 + 히든 프로젝트/links/team_notices 다중 팀 전환 끝
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
| 2026-05-11 | #10 문서·체크 팀 경계 + 편집·삭제 권한 모델 | 스키마 무변경(마이그레이션 phase 추가 없음 — 라우트·쿼리·권한 헬퍼만). `auth.user_team_ids`에 `JOIN teams ... AND deleted_at IS NULL` 추가(삭제 예정 팀 자동 제외 — `user_can_access_team`/`is_team_admin`/`resolve_work_team` 전파). `auth.can_edit_meeting` 신규(혼합 모델: is_team_doc=1→같은 팀 승인 멤버 누구나·created_by 무관·NULL팀잔존은 작성자한정 / is_team_doc=0→작성자 본인만 / admin 전역). `permissions._can_read_doc/_can_read_checklist`에 `work_team_ids` 인자 + 작업 팀 scoping(None&비admin→`user_team_ids` fallback). `app._filter_events_by_visibility(events, user, scope_team_ids=None)` 재작성(admin 무필터 / team_id∈scope 통과 / team_id NULL→작성자 본인(str(id)·이름 토큰 양쪽) / is_public==1 통과 / else skip). `_work_scope` 헬퍼(admin→None, 비admin→resolve_work_team 1개 set, 비소속 team_id 무시·대표팀 fallback, 미배정→set()). 라우트 ~20개에 `team_id` 파라미터+scoping(events·by-project-range·search-parent·subtasks·{id}, checklists, projects·meta·project-list·project-timeline, manage/projects, doc·doc/calendar, kanban, /check·/doc 페이지). create 경로(events·ai/confirm·manage event·checklists·doc) team_id를 `resolve_work_team`로. `database.py` 헬퍼 7종(`_meeting_team_clause`/`_viewer_team_ids`(auth 미import 순환회피)/`_author_token_set`/`_author_in_sql`/`_project_team_filter_sql`/`_events_checklists_team_name_set`/`_filter_rows_by_work_team`) + `work_team_ids` 인자(checklists·meetings·projects 조회 + MCP 조회 함수 다수, MCP응답은 team_id/created_by pop). `mcp_server.py` `import auth`+`_mcp_work_team_ids`(=user_team_ids — MCP엔 작업팀 쿠키 없음) 전 도구에 전달. advisor 리뷰 발견 결함 1건(`_can_read_doc` 작성자 단축이 추방된 팀문서 작성자에게 노출 — §8-1 위반) 동일 흐름 패치(is_team_doc=1은 작성자 단축 제거, meetings SQL 4곳 `m.created_by = ? AND (is_team_doc=0 OR team_id IS NULL)` + 팀문서절 `team_id IS NOT NULL` 추가, `can_edit_meeting` NULL팀팀문서 작성자한정 정합). 합성 DB+TestClient 71 PASS / 0 FAIL(가시성 전 라우트·편집삭제 권한·추방재가입 복구·추방 후 자기작성 팀자료 차단·NULL row 회귀방지). 기존 `tests/phase75` 21 PASS. `tests/test_project_rename.py` 2건 실패는 사전 결함(master HEAD에서도 동일, #10 무관). **서버 재시작 필요**(코드 reload — 스키마 무변경이라 마이그레이션은 불필요). 알려진 한계: assignee 기반 "내 스케줄" 미니위젯(`/api/my-meetings`(event_type='meeting')·`/api/my-milestones`·`/api/project-milestones/calendar`)은 담당자+히든 필터만(팀 경계 미적용 — 후속), 이름 기반 단건 프로젝트 조회 동명 충돌 잔존(§8-2 후속), 읽기 경로 project_id 전환은 부분적(가시성 필터는 적용, 간트 by-project-range는 여전히 name 기반). | `auth.py`, `permissions.py`, `app.py`, `database.py`, `mcp_server.py`. workspace(다음 사이클 시작 시 archive로 이동): `00_input/feature_spec.md`, `backend_changes.md`, `code_review_report.md`, `qa_report.md`, `scripts/verify_team10.py` |
