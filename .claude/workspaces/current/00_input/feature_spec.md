# 팀 기능 그룹 A — #2 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#2. user_teams 모델 + users.role 전환 + name_norm + notifications.team_id**를 한 사이클로 끊어 진행한다. 마스터 plan은 `팀 기능 구현 계획.md` §3 (스키마), §13 (Phase 1~2), §16 (권한 헬퍼).

#3 이후는 본 사이클 범위 밖.

## 분류

백엔드 핵심: **Phase 1 본문 SQL + Phase 2 일부 백필 + Phase 4 일부 인덱스** + **신규 권한 헬퍼 모듈 추가 (auth.py)**.
프론트 변경 없음.
**팀 모드: backend → reviewer → qa.**

## 전제 (#1에서 완료된 것)

- `database.py:631-811` PHASES / _PREFLIGHT_CHECKS 확장 포인트 + 트랜잭션·마커·경고·preflight 골격 + `normalize_name(s)` 헬퍼 (`unicodedata.NFC + casefold`).
- `backup.py:run_migration_backup()` — pending phase 있을 때만 자동 백업.
- 본 사이클은 위 인프라 위에 phase 본문을 **등록**한다 (`PHASES.append((name, body))`, `_PREFLIGHT_CHECKS.append(check)` 패턴).

## 핵심 인계 사실 (메인이 이미 파악)

### 현재 스키마 (database.py)
- `users` (L60-69): `id, name, password, role DEFAULT 'editor', team_id, is_active, created_at`
- `teams` (L52-57): `id, name UNIQUE, created_at`
- `projects` (L117-125 + `_migrate` L260-266): `id, name UNIQUE, color, start_date, end_date, created_at, is_active, memo, is_private, deleted_at, deleted_by, team_id, is_hidden, owner_id`. **`name UNIQUE` 존재 — Phase 1에서 제거해야 함.**
- `notifications` (L383-392): `id, user_name, type, message, event_id, is_read, created_at` — **team_id 없음, 추가 필요.**
- `team_notices` (L374-380): `id, content, created_by, created_at` — **team_id 없음, 추가 필요.**
- `events.project_id`, `checklists.project_id` 컬럼 없음 — 추가 필요.

### 권한 헬퍼 현황 (auth.py)
- `is_editor(user)` L41 — `role in ("editor","admin")`
- `is_admin(user)` L45 — `role == "admin"`
- `can_edit_event(user, event)` L49 — 단일 `team_id` 비교
- `can_edit_checklist(user, checklist)` L67 — 단일 `team_id` 비교
- `can_edit_project(user, project)` L85 — 단일 `team_id` 비교
- `app.py:666` `_require_editor(request)` — 위 `is_editor` 사용

### 마이그레이션 등록 패턴 (#1 인계)
```python
# database.py 모듈 레벨 — PHASES 정의 직후
def _phase_1_team_columns(conn):
    # _migrate(conn, "users", [...]) 또는 직접 ALTER
    # CREATE TABLE IF NOT EXISTS user_teams ...
    ...
PHASES.append(("team_phase_1_columns_v1", _phase_1_team_columns))

def _phase_2_team_backfill(conn):
    # UPDATE users SET name_norm = ... WHERE name_norm IS NULL
    ...
PHASES.append(("team_phase_2_backfill_v1", _phase_2_team_backfill))

def _phase_4_team_indexes(conn):
    # CREATE UNIQUE INDEX ...
    ...
PHASES.append(("team_phase_4_indexes_v1", _phase_4_team_indexes))
```

### projects 테이블 재구성 핵심 (계획서 §13)
- `name UNIQUE` 제거 + `name_norm` 추가.
- **`projects.id` 보존** 필수 — `events.project_id`/`checklists.project_id`/`project_members.project_id`/`project_milestones.project_id`/`*.trash_project_id` 매핑 재작업 회피.
- 명시적 컬럼 목록으로 INSERT (SELECT * 금지 — 컬럼 개수 불일치 위험).
- 재생성 후 row count 일치 검증 + `sqlite_sequence` 갱신.

## #2 step 분해 (플래너 참고, backend 호출 횟수는 플래너 판단)

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #2-S1 | Phase 1 컬럼·테이블 추가 + projects 재구성 | `users.{name_norm,password_hash}`, `teams.{deleted_at,name_norm}`, `projects.name_norm`, `events.project_id`, `checklists.project_id`, `notifications.team_id`, `team_notices.team_id` 추가. `user_teams`, `team_menu_settings` 테이블 신규. `projects` 테이블 재구성 (UNIQUE 제거 + id 보존). 빈 DB·기존 DB 양쪽 idempotent. |
| #2-S2 | Phase 2 일부 백필 (#2 범위만) | `users.name_norm` ← `normalize_name(name)` (admin 포함). `users.role`: `editor` → `member` 일괄 갱신 (admin 유지). `users.team_id` NOT NULL 사용자(admin 제외) → `user_teams` `(user_id, team_id, 'member', 'approved', users.created_at)` row insert. **본 사이클은 #4의 events/checklists/projects/notifications/team_notices/links 백필은 다루지 않는다.** |
| #2-S3 | Phase 4 일부 인덱스 (#2 범위만) | `CREATE UNIQUE INDEX idx_user_teams_user_team ON user_teams(user_id, team_id)`, `CREATE UNIQUE INDEX idx_team_menu_settings ON team_menu_settings(team_id, menu_key)`. **`users.name_norm` UNIQUE / `teams.name_norm` UNIQUE / `projects(team_id, name_norm)` UNIQUE는 #5/#7 책임이라 본 사이클 X.** preflight 검사도 본 사이클에서 등록되는 인덱스 한정. |
| #2-S4 | 권한 헬퍼 신규 + 기존 헬퍼 위임 | `auth.py`에 다음 추가: `is_member(user)`, `is_admin(user)`(기존 유지·확장), `user_team_ids(user) -> set[int]`, `user_can_access_team(user, team_id) -> bool`, `is_team_admin(user, team_id) -> bool`, `require_work_team_access(user, team_id) -> None` (검증 실패 시 HTTPException 403), `resolve_work_team(request, user, explicit_id=None) -> int` (쿠키 미존재 시 대표 팀 fallback. 쿠키/UI 통합은 #15 책임 — 본 사이클은 헬퍼 시그니처와 fallback 로직만), `admin_team_scope(user) -> Optional[int]` (admin이 명시 work_team_id 없으면 None). 기존 `is_editor`/`_require_editor`/`can_edit_*`는 유지하되 내부 구현을 새 헬퍼로 위임 (호환 단계). |

> step 분할 기준: S1+S2는 phase 1·2 본문이라 같은 backend 호출에 묶어도 무방. S3 인덱스도 phase 4 본문이라 묶을 수 있다. S4는 auth.py 변경이라 별도. 플래너가 backend 호출을 묶을지 분리할지 판단.

## exit criteria (사이클 전체)

### 마이그레이션 동작
- [ ] 빈 DB로 init_db() 호출 → 모든 테이블이 최신 스키마(=신규 컬럼 포함)로 생성됨. PHASES 본문은 **빈 데이터에 대해 노옵**으로 끝나야 함 (백필할 게 없음). 마커는 정상 기록.
- [ ] 기존 DB(운영 DB 복사)로 init_db() 호출 → 백업 1회 + Phase 1 본문(컬럼·테이블 추가, projects 재구성) + Phase 2 본문(name_norm·role·user_teams 백필) + Phase 4 본문(인덱스 2개) 모두 통과.
- [ ] 두 번째 init_db() 호출 → 마커 덕에 phase 본문 재실행 안 됨, 백업 추가 없음.
- [ ] **Phase 마커 강제 삭제 후 재실행** → idempotency 가드 덕에 데이터 손상 없음:
  - `users.name_norm` 백필: `WHERE name_norm IS NULL`
  - `users.role`: `WHERE role='editor'`
  - `user_teams` 이관: `INSERT OR IGNORE` 또는 `WHERE NOT EXISTS` 가드
  - `projects` 재구성: 마커가 있으면 다시 안 돔 (그러나 가드 없이 재실행되면 데이터 망가짐 가능 → 마커 의존이 핵심)

### 권한 헬퍼
- [ ] 단위 검증 스크립트 — member/admin/team-admin 케이스에 대해 `is_member`, `user_team_ids`, `user_can_access_team`, `is_team_admin` 동작 확인. admin은 `user_teams` row 없으므로 `user_team_ids` = 빈 집합, 그러나 `user_can_access_team`은 admin이면 True (슈퍼유저 정책 — 단, work_team_id 명시는 #16 책임이라 본 사이클은 헬퍼 자체에서는 검증 안 함).
- [ ] 기존 `is_editor`/`can_edit_event`/`can_edit_checklist`/`can_edit_project` 호출이 라우트 단위에서는 그대로 동작 (호환 위임).

### 데이터 정합성
- [ ] 백필 후 `user_teams` row 수 = (기존 `users.team_id` NOT NULL AND `role != 'admin'`) 사용자 수.
- [ ] admin은 `user_teams`에 row 없음.
- [ ] `projects.id` 재구성 전후 동일. `events.project_id`, `checklists.project_id`는 본 사이클에서 채우지 않음 (NULL 유지, 컬럼만 추가).

## 진행 방식

- step별 또는 묶음 단위 backend 호출. step 종료마다 `backend_changes.md`에 4종(변경 파일·요약·검증 명령·다음 step 인계) 추가.
- 모든 backend 완료 후 1회 reviewer 호출 → 1회 qa 호출.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **본 사이클은 #2 범위만.** #3 (admin 분리), #4 (events/checklists/meetings/projects 백필), #5 (project_id 표준화), #7 (비밀번호 hash 변환), #8 (계정 가입 분리), #9 (IP 자동 로그인), #10 (가시성·권한 적용)은 모두 후속 사이클.
- **Phase 1·2·4는 #2 범위 한정.** users.name_norm UNIQUE 인덱스는 #7, projects(team_id, name_norm) UNIQUE는 #5에서 등록한다 — 본 사이클에서 미리 만들지 말 것.
- **`users.password_hash` 컬럼은 추가만 한다.** 실제 hash 변환은 #7 책임.
- **projects 재구성 시 명시 컬럼 목록**: `id, team_id, name, name_norm(NULL), color, start_date, end_date, is_active, is_private, is_hidden, owner_id, memo, deleted_at, deleted_by, created_at`. `name_norm`은 Phase 2 백필이 아니라 **재구성 직후 같은 phase 안에서 채워도 됨**(추가 phase 분리 불필요). 단, `_PREFLIGHT_CHECKS`는 본 사이클에서 추가 안 함 (UNIQUE 인덱스 자체가 #5/#7 책임).
- `user_teams.joined_at`은 `users.created_at` 사용 (마이그레이션 사양). 신규 가입 흐름의 joined_at = CURRENT_TIMESTAMP는 #8 책임.
- `team_menu_settings`는 테이블만 생성. 기본값 시드(`calendar=0`, 나머지=1)는 #19 책임.
- `_PREFLIGHT_CHECKS` 등록은 본 사이클에서 추가하지 않는다 (등록되는 인덱스가 새 테이블 UNIQUE라 충돌 가능성 없음).
- VSCode 디버깅 모드 — qa는 import-time 검증 + 운영 DB 복사 검증 위주, 실서버 재시작 필요 시 사용자에게 요청.

## 산출물 위치

- `backend_changes.md`: backend 변경 일지 (step별 섹션)
- `code_review_report.md`: reviewer 결과
- `qa_report.md`: qa 결과
- 검증 스크립트는 `.claude/workspaces/current/scripts/` 아래
