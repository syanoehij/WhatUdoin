# 팀 기능 그룹 A — #5 backend changes

본 사이클은 plan §8-2 / §13의 #5(`projects (team_id, name_norm) UNIQUE`)를 한 사이클로 처리한다.
사양: `.claude/workspaces/current/00_input/feature_spec.md`.

## S1 — phase 본문 + preflight check 등록

### 인프라 변경 — preflight 시그니처 확장

`database.py:_PREFLIGHT_CHECKS` 시그니처를 `(conn) -> list[str]` 에서 `(conn) -> list[tuple[str, str]]` 로 확장.
각 튜플은 `(warning_category, message)`. 이 변경으로 preflight 충돌이 사양이 정의한 카테고리(`preflight_projects_team_name`)로 dedup된다.

영향 범위:
- `database.py` L724-729: 주석 갱신.
- `database.py:_run_preflight_checks` (~L1610): 검사 함수 raise → `("preflight", repr)` 단일 튜플.
- `database.py:_run_phase_migrations` (~L1561): `for category, msg in conflicts:` unpack 후 `_append_team_migration_warning`에 카테고리 전달.

기존 등록된 `_PREFLIGHT_CHECKS`는 0개라 마이그레이션 부담 없음.

### Phase 5 본문 등록 — `team_phase_5_projects_unique_v1`

`database.py:_phase_5_projects_unique` (~L1551).
1. `WHERE name_norm IS NULL` 가드 + Python `normalize_name()`로 잔존 NULL row 방어 백필. Phase 1 본문이 이미 채웠으므로 정상 흐름에서는 0건.
2. `CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_team_name ON projects(team_id, name_norm) WHERE team_id IS NOT NULL`. SQLite 3.8.0+ partial index. `IF NOT EXISTS` 가드로 마커 강제 삭제 후 재실행도 노옵.

`PHASES.append(("team_phase_5_projects_unique_v1", _phase_5_projects_unique))` — Phase 4 데이터 백필 다음, 즉 PHASES 목록 마지막에 추가.

### Preflight check 등록 — `_check_projects_team_name_unique`

`database.py:_check_projects_team_name_unique` (~L1582).
- `team_id IS NOT NULL AND name_norm IS NOT NULL`로 좁힌 뒤 `GROUP BY team_id, name_norm HAVING COUNT(*) > 1`.
- 충돌이 있으면 `("preflight_projects_team_name", "projects (team_id=N, name_norm='abc') duplicates=2 ids=[1,2]")` 형태 튜플 반환.
- `_PREFLIGHT_CHECKS.append(_check_projects_team_name_unique)` 등록.

타이밍: preflight는 phase 본문 시작 전 1회 실행 → 검사 시점에서 이미 `team_id IS NOT NULL`인 row의 충돌만 감지한다(Phase 4 데이터 백필이 아직 안 끝난 미적용 DB라면 백필 후 충돌은 phase 5의 `CREATE UNIQUE INDEX`가 raise → ROLLBACK으로 떨어진다). 사양서가 정의한 “이미 데이터 충돌이 있는 합성 DB → 시작 거부” 시나리오는 이 동작과 정합.

## S2 — DB 함수 + 라우트 (team_id, name_norm) 검사 교체

### `create_project(name, color, memo, team_id=None)`  (`database.py` ~L2760)

- 시그니처에 `team_id: int | None = None` 추가.
- `team_id`가 명시되면 `(team_id, name_norm)` 사전 검사 → 충돌 시 `sqlite3.IntegrityError`.
- INSERT 시 `name_norm = normalize_name(name)`도 함께 저장.
- 호출부가 `team_id=None`으로 부르면 호환을 위해 NULL 저장(운영자 정리 영역).

### `create_hidden_project(name, color, memo, owner_id, team_id=None)` (`database.py` ~L2785)

- 시그니처에 `team_id: int | None = None` 추가.
- `team_id`가 None이면 `users.team_id` fallback. 라우트는 `resolve_work_team`으로 항상 명시 team_id를 넘기는 게 정상 흐름.
- 기존 `LOWER(name)` **전역** 중복 검사를 `(team_id, name_norm)` 검사로 교체. team_id가 NULL인 경우는 면제 + `name_norm`도 INSERT 시 함께 저장.
- 반환 dict에 `team_id` 키 추가(라우트가 활용 가능 — 현재는 단순 ack).

### `rename_project(old_name, new_name, merge=False)` (`database.py` ~L3072)

- target_proj 검색을 `name = new_name` → `(team_id, name_norm)` 한정으로 교체. **다른 팀의 동일 이름은 충돌이 아니다.**
- `old_proj.team_id`가 NULL이면 target 검색 skip(운영자 정리 영역).
- merge=False + 같은 팀 같은 name_norm 다른 row → `sqlite3.IntegrityError`. 라우트에서 409로 변환.
- rename 분기에서 `name`과 `name_norm`을 동시 갱신.

### 라우트 변경

#### `POST /api/manage/projects` (`app.py` ~L2441)

- `auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`로 작업 팀 결정.
- `team_id is None` → 400 (admin이 명시 없이 호출한 경우). 비-admin은 user_teams approved 또는 legacy `users.team_id`로 fallback되므로 정상 흐름에서는 None이 되지 않는다.
- `auth.require_work_team_access(user, team_id)`로 접근 권한 검증(admin은 통과).
- `(team_id, name_norm)` 사전 중복 검사 → 409.
- `db.create_project(..., team_id=team_id)` 호출. `IntegrityError` race 시 409.
- 응답 dict에 `team_id` 포함.
- 기존 `db.project_name_exists(name, case_insensitive=True)` 전역 검사 제거(같은 이름이 다른 팀에 존재해도 차단되지 않도록).

#### `PUT /api/manage/projects/{name:path}` (`app.py` ~L2476)

- 현재 프로젝트의 `team_id`를 `db.get_project(name)` 결과에서 추출.
- `team_id is not None`이면 `(team_id, name_norm) AND id != proj["id"]` 사전 검사 → 409.
- `team_id IS NULL`(운영자 정리 영역)인 프로젝트는 사전 검사 skip — `rename_project` 함수가 자체 가드로 NULL 키 끼리 충돌 무시.
- `force=True` 시 merge 분기 유지.
- `IntegrityError`도 409로 변환(race 안전망).

#### `POST /api/manage/hidden-projects` (`app.py` ~L2625)

- `user.get("team_id")` 직접 검사 → `auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))`로 교체.
- `team_id is None` → 403 (기존 메시지 유지).
- `auth.require_work_team_access(user, team_id)`로 권한 검증.
- `db.create_hidden_project(..., team_id=team_id)` 호출.

## 본 사이클이 손대지 않은 dormant 이슈 (#10에서 처리)

본 사이클은 가시성 라우트 적용 범위가 아니라 다음 함수들이 “이름 기준 매칭”을 그대로 유지한다. 같은 이름이 다른 팀에 동시 존재하지 않는 한 동작은 동일하다. #10에서 `team_id` 파라미터를 추가하면서 `(team_id, name_norm)` 키로 일제 전환 예정.

- `database.get_unified_project_list` (~L2649) — `proj_map[name]` dict 키.
- `database.get_all_projects_with_events` (~L2714) — `proj_map[name]` dict 키.
- `database.get_project(name)` (~L3037) — `WHERE name = ?` 단건. rename 라우트가 사용하지만 본 사이클 시점에 같은 이름 다른 팀 row는 아직 존재할 수 없다.
- `database.update_project_*` 시리즈 (~L3344+) — `WHERE name = ?` 매칭 + INSERT 분기에서 `team_id` NULL 저장. 사양서 §주의사항 L28에서 명시적으로 본 사이클 범위 밖 처리.
- `database.delete_project` (~L3272) — `UPDATE events SET deleted_at=?, ..., trash_project_id=? WHERE project=? AND deleted_at IS NULL`가 cross-team 동일 이름 events를 모두 휩쓴다. `rename_project`의 B1과 동일 패턴이지만 사양 L28이 “호출부 변경 없이 함수만 안전하게 유지”로 한정해 본 사이클은 손대지 않는다. #10에서 라우트가 team_id를 함께 넘기도록 시그니처 확장 시 함께 정리.
- `database.project_name_exists` (~L3422) — LOWER 전역 검사. 라우트가 더 이상 호출하지 않으나 호환을 위해 유지.

## exit criteria 매핑

| 항목 | 구현 위치 |
|------|----------|
| 빈 DB → 노옵 | `_phase_5_projects_unique`: NULL 백필 0건 + `IF NOT EXISTS` |
| 잔존 NULL 백필 후 인덱스 | 본문 1) → 본문 2) |
| preflight 충돌 시 시작 거부 | `_check_projects_team_name_unique` + `_run_phase_migrations` |
| 두 번째 init_db() 노옵 | `_pending_phases` 마커 가드 |
| 마커 강제 삭제 후 재실행 노옵 | `WHERE name_norm IS NULL` + `IF NOT EXISTS` |
| 같은 이름 다른 팀 허용 | `create_project`/`create_hidden_project`/`rename_project` 모두 `(team_id, name_norm)` 한정 |
| 같은 팀 중복 차단 | 동일 — IntegrityError 또는 사전 검사 |
| rename 다른 팀 동일 이름 허용 | `rename_project` target_proj 검색이 `team_id = old_team_id` 한정 |
| 히든 프로젝트 동일 정책 | `create_hidden_project` `(team_id, name_norm)` 검사 |
| `team_id IS NULL` 끼리 같은 이름 허용 | 부분 인덱스 + 함수 가드 |
| `projects.id` 보존 | 본 사이클은 `id` 변경 안 함 — #2 보존분 그대로 |
| FK 매핑 보존 | `verify_projects_unique.py` qa 단계에서 합성 DB row count 확인 |

## 변경 파일

- `database.py` — preflight 시그니처 확장, phase 5 본문 + preflight check 등록, `create_project`/`create_hidden_project`/`rename_project` 시그니처·검사 교체.
- `app.py` — 라우트 3개(`POST /api/manage/projects`, `PUT /api/manage/projects/{name}`, `POST /api/manage/hidden-projects`)에 `resolve_work_team` 적용 + `(team_id, name_norm)` 사전 검사.
