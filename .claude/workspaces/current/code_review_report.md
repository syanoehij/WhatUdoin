# 코드 리뷰 보고서 — 팀 기능 그룹 A #2

## 리뷰 대상 파일
- `database.py` (init_db CREATE 갱신: events/teams/users/projects/notifications/team_notices/checklists + user_teams/team_menu_settings 신규, line 30~496 일부; phase 본문 등록 line 775~1015)
- `auth.py` (전체)

## 차단(Blocking)
- 없음 (1건이 발견되어 리뷰 단계에서 즉시 수정 후 재검증 통과 — 아래 §수정 이력 참조)

## 경고(Warning)
1. `auth.py:is_team_admin` — 글로벌 admin(`users.role == 'admin'`)도 모든 팀에 대해 True를 반환하도록 구현했다. 사양서 §S4는 단순히 "user_teams.role == 'admin'" 시그니처만 명시했고 글로벌 admin 처리를 명시하지 않았다. 슈퍼유저 정책과 정합성을 위한 의도적 결정. **#16 라우트 호출부 전환 시 의도 확인 필요.**
2. `auth.py:user_team_ids` — DB 조회 실패(`Exception`) 시 `users.team_id` legacy fallback. 마이그레이션 직전 호출 등 예외 상황 대비. 정상 가동 후엔 도달하지 않아야 함.
3. `auth.py:resolve_work_team` — 사용자의 대표 팀을 `min(user_team_ids)` 결정성 정렬로 선택. 사양은 "대표 팀 fallback"만 명시 — 후속 사이클(#15 UI 통합)에서 명시 선호 팀 컬럼이 추가될 수 있음.

## 통과
- [x] **트랜잭션 안전성**: phase body 안에서 `conn.commit()` 호출 없음. 호출자 (`_run_phase_migrations`)가 BEGIN IMMEDIATE/COMMIT 관리.
- [x] **DDL in WAL**: `_phase_1`의 `DROP TABLE projects` + `ALTER … RENAME`이 BEGIN IMMEDIATE 안에서 실행됨. WAL 모드(`_ensure_wal_mode`)가 init_db 시작 시 보장되므로 DDL이 트랜잭션 안에서 안전하게 롤백 가능.
- [x] **idempotency 가드**:
  - `_phase_1`: 모든 ALTER가 `_column_set()` 체크. `CREATE TABLE IF NOT EXISTS`. `projects` 재구성은 `sqlite_master.sql`에 `name TEXT NOT NULL UNIQUE`가 남아 있을 때만 트리거 — 재구성 후엔 sql에서 사라지므로 마커 강제 삭제 후 재실행에도 노옵.
  - `_phase_2`: `WHERE name_norm IS NULL`, `WHERE role = 'editor'`, `WHERE NOT EXISTS` 가드.
  - `_phase_4`: `CREATE UNIQUE INDEX IF NOT EXISTS`.
- [x] **SQL 파라미터화**: 모든 동적 값이 `?` 바인딩. f-string은 신뢰 가능한 컬럼명/테이블명 매크로(`_PROJECTS_REBUILD_COLUMNS`)에만 사용.
- [x] **projects.id 보존**: 명시 컬럼 INSERT + `sqlite_sequence` 갱신. row count 일치 검증 후 raise.
- [x] **명시 컬럼 목록 (15개)**: `_PROJECTS_REBUILD_COLUMNS`가 사양서 §주의사항 컬럼 목록과 일치 (id, team_id, name, name_norm, color, start_date, end_date, is_active, is_private, is_hidden, owner_id, memo, deleted_at, deleted_by, created_at).
- [x] **본 사이클 범위 준수**: `users.name_norm` UNIQUE / `teams.name_norm` UNIQUE / `projects(team_id, name_norm)` UNIQUE 모두 미생성 (각각 #7, #5 책임). `_PREFLIGHT_CHECKS` 추가 등록 없음.
- [x] **권한 헬퍼 위임**: `is_editor` → `is_member` 위임. `can_edit_*`의 팀 비교가 `user_can_access_team` 사용. 시그니처는 모두 유지 — 라우트 호출부(`app.py:_require_editor`) 변경 없음.
- [x] **빈 DB 노옵**: 시드 INSERT (admin, 관리팀)에 `name_norm` 포함하도록 수정 완료 → Phase 2가 빈 DB에서 진정으로 노옵.

## 수정 이력 (리뷰 + QA 단계에서 발견 → 즉시 수정)

### Issue #1 — 빈 DB에서 Phase 2가 노옵이 아니었음
- 발견: 시드 INSERT가 `name_norm` 없이 admin 사용자를 만들어, 빈 DB로 init_db() 호출 시 Phase 2가 1건 UPDATE 실행.
- 사양 exit criteria: "PHASES 본문은 빈 데이터에 대해 노옵으로 끝나야 함."
- 수정: `database.py` 시드 INSERT 두 곳에 `name_norm` 컬럼 + `normalize_name()` 적용.

### Issue #2 — projects CREATE에서 누락 컬럼
- 발견: 빈 DB로 init_db() 실행 중 line 263의 `UPDATE projects SET deleted_at = ...`가 `OperationalError: no such column: deleted_at` 발생. _migrate(projects, [..., deleted_at])가 line 273에 있어 *후행* 호출이라 컬럼이 없는 상태에서 UPDATE가 먼저 실행됨.
- 진단: pre-existing 결함이지만 사양 T1 ("빈 DB로 init_db() → 모든 테이블이 최신 스키마") 통과 필수.
- 수정: projects CREATE에 `is_active, memo, is_private, deleted_at, deleted_by, team_id, is_hidden, owner_id` 흡수. _migrate 호출은 그대로 두어 기존 DB의 누락 컬럼 보강은 유지.

### Issue #3 — `sqlite_sequence` ON CONFLICT 무효
- 발견: T2 합성 구 DB에서 phase 1이 `OperationalError: ON CONFLICT clause does not match any PRIMARY KEY or UNIQUE constraint` 발생. 내가 작성한 `INSERT INTO sqlite_sequence … ON CONFLICT(name) DO UPDATE` 코드 결함 — `sqlite_sequence`는 시스템 테이블이라 UNIQUE 제약이 없음.
- 수정: UPDATE-or-INSERT 패턴 (`SELECT 1 → UPDATE / INSERT`)으로 교체. SQLite는 AUTOINCREMENT INSERT가 한 번도 발생하지 않은 테이블에는 sqlite_sequence row가 없을 수 있어 두 분기 모두 필요.

### Issue #4 — checklists CREATE 순서 (pre-existing)
- 발견: 빈 DB에서 line 357 `CREATE INDEX … ON checklists(...)` 실행 시 `no such table: main.checklists`. checklists는 line 470 부근의 `executescript`에서 생성되는데, 인덱스 CREATE와 일부 UPDATE가 더 이른 위치에서 호출됨.
- 진단: pre-existing 결함이지만 T1 통과 필수.
- 수정: checklists CREATE를 line ~115 (checklist_histories 직후)로 분리·이동. 후행 executescript는 `CREATE TABLE IF NOT EXISTS`라 노옵으로 끝남 (checklist_locks만 실제 생성).

재검증: `verify_phase_migrations.py` (T1~T4 모두 ALL PASS, 37 checks) + `verify_auth_helpers.py` (28 checks ALL PASS).

## 최종 판정
**통과** — 차단 결함 없음, QA 진행 가능. 경고 3건은 후속 사이클(#15, #16) 인계 사항으로 기록.

## QA 인계 — 핀포인트 검증 케이스 (필수)
QA는 다음 4개 시나리오 모두 검증해야 한다:

- **T1. 빈 DB**: `init_db()` → 시드 admin/관리팀 생성, PHASES 마커 3개 기록, Phase 2가 노옵(추가 user_teams row 없음, name_norm 모두 미리 채워짐).
- **T2. 합성 구 DB**: `name UNIQUE`가 있는 projects + `editor` role 사용자(team_id 있음) + `user_teams` 미존재 → init_db() →
  - projects.id 보존 (rebuild 전후 비교)
  - `sqlite_master.sql`에서 `UNIQUE` on name 사라짐
  - `sqlite_sequence.seq(projects)` == 이전 MAX(id)
  - row count(projects) 일치
  - `user_teams` row 수 == count(`users WHERE team_id IS NOT NULL AND role != 'admin'`) — admin 제외
  - 모든 `projects.name_norm == normalize_name(name)`
  - `users.role = 'editor'` 행 수 == 0
  - `users.name_norm IS NOT NULL` 모든 행
- **T3. 두 번째 init_db()**: 마커 존재 → phase 본문 미실행 (백업 추가 없음, user_teams 추가 없음).
- **T4. 마커 강제 삭제 후 재실행**: `DELETE FROM settings WHERE key LIKE 'migration_phase:team_phase_%'` → init_db() →
  - 데이터 손상 없음 (counts 동일, user_teams 중복 없음, projects rebuild 가드 발동 없음 — sqlite_master.sql에 UNIQUE가 이미 사라졌으므로 needs_rebuild=False)
  - 모든 `name_norm` NULL 없음.

## 권한 헬퍼 단위 검증 (QA 작성 필요)
- member: `is_member`=True, `is_admin`=False, `user_team_ids`=approved 팀 set, `user_can_access_team(my_team)`=True, `is_team_admin(my_team)`=False
- admin: `is_member`=True, `is_admin`=True, `user_team_ids`=빈 집합, `user_can_access_team(any_team)`=True, `is_team_admin(any_team)`=True
- team_admin: `user_teams.role='admin'` 추가 row → `is_team_admin(that_team)`=True, 다른 팀에는 False
