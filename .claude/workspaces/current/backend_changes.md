# 팀 기능 그룹 A #2 — backend changes

## 사이클 범위
Phase 1 본문 (컬럼·테이블 추가 + projects 재구성) +
Phase 2 일부 백필 (#2 범위 한정: name_norm, role, user_teams) +
Phase 4 일부 인덱스 (#2 범위 한정: idx_user_teams_user_team, idx_team_menu_settings) +
auth.py 신규 권한 헬퍼 + 기존 헬퍼 위임.

#3 이후(events/checklists/meetings/projects 백필, 비밀번호 hash 변환, IP 자동 로그인 등)는 본 사이클에서 다루지 않는다.

---

## Step #2-S1+S2+S3 — database.py phase 1·2·4 본문 등록

### 변경 파일
- `database.py`

### 요약

**init_db() 내 CREATE TABLE 정의 갱신 (빈 DB용 최신 스키마):**
- `events` — `project_id INTEGER` 추가 (NULL 유지, 백필은 #5 책임)
- `teams` — `name_norm TEXT`, `deleted_at TEXT` 추가 (`name UNIQUE`는 유지 — `name_norm UNIQUE`로의 전환은 #7)
- `users` — `name_norm TEXT`, `password_hash TEXT` 추가 (백필/UNIQUE는 #7)
- `projects` — `name UNIQUE` 제거, `name_norm TEXT` 추가 (`(team_id, name_norm) UNIQUE`는 #5)
- `notifications` — `team_id INTEGER` 추가
- `team_notices` — `team_id INTEGER` 추가
- `checklists` — `project_id INTEGER` 추가
- `user_teams` 신규 테이블 (id, user_id, team_id, role, status, joined_at)
- `team_menu_settings` 신규 테이블 (id, team_id, menu_key, enabled, updated_at)

**phase 본문 등록 (PHASES.append 패턴):**
- `team_phase_1_columns_v1` (`_phase_1_team_columns`):
  - 누락 컬럼 ALTER TABLE … ADD COLUMN (idempotent — `_column_set()` 체크 후 추가)
  - `user_teams`, `team_menu_settings` `CREATE TABLE IF NOT EXISTS`
  - **projects 재구성**: `sqlite_master.sql`에 `name TEXT NOT NULL UNIQUE`가 남아 있을 때만 트리거. 명시 컬럼 목록(`_PROJECTS_REBUILD_COLUMNS` 15개)으로 `INSERT … SELECT`. row count 일치 검증 + `sqlite_sequence` 갱신 → `projects.id` 보존. 재구성 직후 같은 phase 안에서 `name_norm` Python 백필.
- `team_phase_2_backfill_v1` (`_phase_2_team_backfill`):
  - `users.name_norm`, `teams.name_norm` 백필 (`WHERE name_norm IS NULL` 가드, `normalize_name()` = NFC+casefold)
  - `users.role`: `editor` → `member` (`WHERE role = 'editor'` 가드)
  - `user_teams` 백필: `team_id NOT NULL AND role != 'admin'` 사용자 → `(user_id, team_id, 'member', 'approved', users.created_at)`. `WHERE NOT EXISTS` 가드로 마커 강제 삭제 후 재실행에도 안전.
- `team_phase_4_indexes_v1` (`_phase_4_team_indexes`):
  - `idx_user_teams_user_team` UNIQUE on `user_teams(user_id, team_id)`
  - `idx_team_menu_settings` UNIQUE on `team_menu_settings(team_id, menu_key)`

**Phase 마커는 `_run_phase_migrations()`(#1)가 본문 성공 시 같은 트랜잭션에 기록한다.** preflight check는 본 사이클에서 추가하지 않음 (등록 인덱스가 새 테이블 UNIQUE라 충돌 없음).

### 의도적으로 하지 않은 것 (#2 범위 외)
- `users.name_norm` UNIQUE 인덱스 — #7 책임
- `teams.name_norm` UNIQUE 인덱스 — #7/#5 후속 책임
- `projects(team_id, name_norm)` UNIQUE 인덱스 — #5 책임
- `events.project_id`, `checklists.project_id` 데이터 백필 — #5 책임
- `notifications.team_id`, `team_notices.team_id` 백필 — #4 책임
- `password_hash` 변환 — #7 책임
- `_PREFLIGHT_CHECKS` 추가 등록 — UNIQUE가 #2 범위에서 모두 새 테이블이라 불필요

### 검증 명령
- `python -c "import database; database.init_db()"` (빈 DB·기존 DB 양쪽)
- `python .claude/workspaces/current/scripts/verify_phase_migrations.py` (qa 단계에서 작성)

---

## Step #2-S4 — auth.py 권한 헬퍼 신규 + 기존 위임

### 변경 파일
- `auth.py`

### 요약

**신규 헬퍼:**
- `is_member(user)` — `role in ('member', 'editor', 'admin')` (백필 전후 호환)
- `is_admin(user)` — 기존 유지
- `user_team_ids(user) -> set[int]` — `user_teams` approved 멤버십 집합. admin은 빈 집합 (DB 조회 실패 시 `users.team_id` legacy fallback)
- `user_can_access_team(user, team_id) -> bool` — admin은 슈퍼유저 정책 True
- `is_team_admin(user, team_id) -> bool` — `user_teams.role == 'admin'`. 글로벌 admin도 True
- `require_work_team_access(user, team_id) -> None` — 실패 시 `HTTPException(403)`
- `resolve_work_team(request, user, explicit_id=None)` — 우선순위: 명시 인자 → 쿠키(`work_team_id`) → admin이면 None → 사용자 대표 팀(min user_team_ids) → `users.team_id` legacy. UI 통합은 #15 책임이라 본 사이클은 시그니처와 fallback 로직만.
- `admin_team_scope(user)` — admin이 명시 work_team_id 없으면 None (전 팀)

**기존 헬퍼 위임 (시그니처 유지, 내부 구현만 새 헬퍼로 교체):**
- `is_editor(user)` → `is_member(user)`로 위임
- `can_edit_event(user, event)` → 팀 비교 부분이 `user_can_access_team()` 사용
- `can_edit_checklist(user, checklist)` → 동일
- `can_edit_project(user, project)` → 동일

라우트 호출부(`app.py:_require_editor`)와 호출 사이트는 본 사이클에서 변경하지 않는다 (#16 책임).

### 검증 명령
- `python .claude/workspaces/current/scripts/verify_auth_helpers.py` (qa 단계에서 작성)

### 다음 사이클 인계
- #3 admin 분리, #4 events/checklists/meetings/projects 백필, #5 project_id 표준화, #7 비밀번호 hash + name_norm UNIQUE, #8 가입 흐름, #15 work_team 쿠키/UI 통합, #16 라우트 호출부 권한 헬퍼 전환.

---

## 사후 수정 (리뷰 + QA 단계에서 발견)

검증 스크립트가 사양 exit criteria를 실제로 강제했고, 그 과정에서 4건의 결함이 발견되어 같은 사이클 안에서 수정·재검증 통과.

| 파일 | 결함 | 수정 |
|------|------|------|
| `database.py` 시드 INSERT | 빈 DB에서 Phase 2 백필이 1건 UPDATE 실행 → 사양 exit criteria 위배 | `INSERT INTO teams/users` 시드에 `name_norm` 컬럼 + `normalize_name()` 추가 |
| `database.py` projects CREATE | 후행 line 263 `UPDATE projects SET deleted_at` 가 _migrate ALTER 이전에 실행되어 빈 DB에서 컬럼 미존재 OperationalError | projects CREATE에 `is_active, memo, is_private, deleted_at, deleted_by, team_id, is_hidden, owner_id` 흡수 |
| `database.py` `_phase_1_team_columns` | `INSERT INTO sqlite_sequence … ON CONFLICT(name)` — sqlite_sequence는 UNIQUE 제약 없어 ON CONFLICT 무효 | UPDATE-or-INSERT 패턴으로 교체 |
| `database.py` checklists CREATE 순서 | 인덱스 CREATE / UPDATE가 checklists 정의(executescript) 이전에 실행되어 빈 DB에서 `no such table` (pre-existing 결함이지만 사양 T1 통과 위해 수정) | checklists CREATE를 line ~115 (checklist_histories 직후)로 끌어올림. 후행 executescript는 `IF NOT EXISTS`라 노옵 |

재검증: `verify_phase_migrations.py` (T1~T4 모두 ALL PASS) + `verify_auth_helpers.py` (28 checks ALL PASS).

### 검증 명령
- `set PYTHONIOENCODING=utf-8 && "D:\Program Files\Python\Python312\python.exe" .claude/workspaces/current/scripts/verify_phase_migrations.py`
- `set PYTHONIOENCODING=utf-8 && "D:\Program Files\Python\Python312\python.exe" .claude/workspaces/current/scripts/verify_auth_helpers.py`
