# QA 보고서 — 팀 기능 그룹 A #2

본 사이클은 사양상 import-time 검증 + 합성 DB 검증 위주 (Playwright 미사용 — VSCode 디버깅 모드 + 핀포인트가 마이그레이션·헬퍼 단위 동작이므로). 운영 DB는 건드리지 않고 tempdir에 합성 DB를 만들어 검증.

## 실행 환경
- Python: `D:\Program Files\Python\Python312\python.exe`
- 실행 명령:
  ```
  set PYTHONIOENCODING=utf-8
  python .claude/workspaces/current/scripts/verify_phase_migrations.py
  python .claude/workspaces/current/scripts/verify_auth_helpers.py
  ```
- 두 스크립트 모두 `WHATUDOIN_RUN_DIR`을 임시 디렉토리로 지정해 운영 DB와 격리.
- 서버 재시작 불필요 (라우트 호출부 미변경, import-time 검증).

## 통과

### Phase 마이그레이션 (verify_phase_migrations.py — RESULT: ALL PASS)

**T1. 빈 DB로 init_db()** (17 checks)
- PHASES 마커 3개 모두 기록 (`team_phase_1_columns_v1`, `team_phase_2_backfill_v1`, `team_phase_4_indexes_v1`)
- admin/관리팀 시드에 `name_norm` 미리 채워짐 → Phase 2가 진정으로 노옵 (user_teams 빈 집합)
- 신규 테이블 `user_teams`, `team_menu_settings` 존재
- 신규 컬럼 9개 모두 존재: `users.{name_norm, password_hash}`, `teams.{name_norm, deleted_at}`, `projects.name_norm`, `events.project_id`, `checklists.project_id`, `notifications.team_id`, `team_notices.team_id`
- Phase 4 인덱스 2개 (`idx_user_teams_user_team`, `idx_team_menu_settings`) 생성
- 본 사이클 범위 외 UNIQUE 인덱스(users.name_norm, teams.name_norm, projects(team_id, name_norm))는 미생성 — #5/#7 책임 보존

**T2. 합성 구 DB → init_db()** (12 checks)
- 합성 구 DB 조건: projects 테이블에 `name TEXT NOT NULL UNIQUE` 제약 + admin 1명 + 5명 editor (3명 team_id NOT NULL) + user_teams 미존재 + name_norm 미존재
- `projects.id` 보존 (rebuild 전후 [1,2,3] 일치)
- `projects` name→id 매핑 보존 (Alpha=1, Beta=2, Gamma=3)
- `sqlite_master.sql`에서 `name TEXT NOT NULL UNIQUE` 제거 확인
- `sqlite_sequence(projects).seq == 3` (= 이전 MAX(id))
- row count 일치 (3 → 3)
- `user_teams` row 수 = 3 (= count(`users WHERE team_id IS NOT NULL AND role != 'admin'`))
- admin은 user_teams에 row 없음
- 모든 `projects.name_norm == normalize_name(name)` (alpha/beta/gamma)
- `users.role = 'editor'` 행 0건 (모두 'member'로 전환)
- `users.role = 'member'` 5건
- `users.name_norm` 모두 채워짐, `teams.name_norm` 모두 채워짐

**T3. 두 번째 init_db() (마커 존재 — 노옵)** (2 checks)
- 백업 파일 추가 없음 (pending phase 0건이므로 `_run_phase_migrations` 즉시 반환)
- `user_teams` row 변화 없음

**T4. 마커 강제 삭제 → 재실행 (idempotency)** (6 checks)
- `DELETE FROM settings WHERE key LIKE 'migration_phase:team_phase_%'` 후 init_db() 재호출
- `projects` row count 동일 (needs_rebuild 가드 — `sqlite_master.sql`에 `name UNIQUE`가 이미 사라졌으므로 재구성 미트리거)
- `projects.id` 동일
- `user_teams` row 중복 없음 (`WHERE NOT EXISTS` 가드 작동)
- `users.role='member'` 수 동일 (`WHERE role='editor'` 가드 작동 — 0건이므로 재실행 시 노옵)
- `users.name_norm` NULL 없음 (`WHERE name_norm IS NULL` 가드 작동)
- 마커 3개 재생성

### 권한 헬퍼 (verify_auth_helpers.py — RESULT: ALL PASS, 28 checks)
- member (alice / team 1): `is_member`, `user_team_ids={1}`, `user_can_access_team` 정확, `is_team_admin(1)` False
- admin (글로벌): `user_team_ids` 빈 집합, `user_can_access_team(any)` True, `is_team_admin(any)` True (슈퍼유저), `admin_team_scope` None
- team admin (bob / team 2): `is_team_admin(2)` True, 다른 팀(1)은 False, 글로벌 admin 아님
- team member (carol / team 2): `is_team_admin(2)` False
- `require_work_team_access`: 정상 통과 / 권한 없으면 `HTTPException(status=403)`
- `resolve_work_team`: 우선순위 (explicit_id → 쿠키 → admin은 None → user_teams 대표 팀) 모두 정확
- 기존 헬퍼 호환: `is_editor` 위임 작동, `can_edit_event`이 새 `user_can_access_team` 위임 작동, admin 슈퍼유저 우회 작동

## 실패

없음.

## 회귀 확인

코드 리뷰 단계에서 발견·수정된 결함 3건 (모두 본 검증 스크립트가 잡았음):

1. **시드 INSERT의 `name_norm` 누락** — 빈 DB Phase 2 노옵 보장을 위해 `INSERT INTO teams/users` 시드에 `name_norm` 컬럼 + `normalize_name()` 적용. (advisor 권고 (1a) 채택)
2. **`projects` CREATE에서 누락 컬럼** — 후행 코드(line 263 `UPDATE projects SET deleted_at = ...`)가 _migrate가 ALTER하기 전에 실행되어 빈 DB에서 OperationalError. CREATE에 `is_active, memo, is_private, deleted_at, deleted_by, team_id, is_hidden, owner_id` 흡수.
3. **`sqlite_sequence` ON CONFLICT 무효** — `sqlite_sequence`는 PRIMARY KEY/UNIQUE 제약이 없는 시스템 테이블이라 ON CONFLICT 절 사용 불가. UPDATE-or-INSERT 패턴으로 교체.
4. **`checklists` CREATE 순서** — 인덱스 CREATE(line ~360)와 UPDATE(line ~245)가 checklists 테이블 정의(line ~470 executescript) 이전에 실행되던 pre-existing 결함. checklists CREATE를 line ~115 (checklist_histories 다음)로 끌어올림. 후행 executescript는 `IF NOT EXISTS`라 노옵 (checklist_locks만 실제 생성).

## 본 사이클 범위 외 — 후속 인계
- `users.name_norm` UNIQUE 인덱스 (#7)
- `teams.name_norm` UNIQUE 인덱스 (#7/#5)
- `projects(team_id, name_norm)` UNIQUE 인덱스 (#5)
- `events.project_id` / `checklists.project_id` / `notifications.team_id` / `team_notices.team_id` 데이터 백필 (#4, #5)
- `password_hash` 변환 (#7)
- `_PREFLIGHT_CHECKS` 추가 등록 (각 사이클의 새 UNIQUE에 맞춰)
- 라우트 호출부 권한 헬퍼 전환 (#16)
- `work_team_id` 쿠키 set/clear UI (#15)

## 행동 결정 (#16/#3 reviewer 인계)
- `is_team_admin(global_admin, any_team)` = True 채택 — 사양 §S4는 명시 안 했으나 슈퍼유저 정책 일관성. #16 라우트 호출부 전환 시 의도 검증 권장.
- `resolve_work_team` 대표 팀 = `min(user_team_ids)` — 결정성 정렬. #15에서 명시 선호 팀 컬럼이 도입되면 교체.
- `user_team_ids`의 예외 fallback (`users.team_id` legacy) — 마이그레이션 전 호출 등 예외 상황. 정상 가동 후엔 도달하지 않음.
