# 팀 기능 그룹 A — #5 code review report

검토 대상: `backend_changes.md` + `database.py`(phase 5 본문, preflight check, `create_project`, `create_hidden_project`, `rename_project`) + `app.py`(라우트 3개).

검토 4영역: (a) phase 본문 idempotency, (b) 부분 인덱스 SQL, (c) 라우트 검사 누락/과잉, (d) preflight 정의.

## 결과 요약

**차단 결함 1건 발견 → 같은 사이클 안에서 backend 패치 후 재검토 통과.** 차단 결함 외 나머지 항목은 통과 + 비차단 메모 2건.

## 발견·조치 (차단 결함)

### B1. `rename_project` cross-team UPDATE 누출 [차단 → 패치 후 통과]

**위치:** `database.py:rename_project` 일반 rename 분기 + merge 분기 모두.

**증상:** 같은 이름 프로젝트가 팀 1과 팀 2에 동시에 존재할 수 있게 된 직후, 팀 1의 프로젝트를 rename하면 `UPDATE projects SET name=? WHERE name=?`가 팀 2의 같은 이름 row까지 휩쓸어 같이 갱신한다. 부분 UNIQUE 인덱스는 충돌이 아니므로(서로 다른 팀이라 키가 다름) 트리거되지 않아 silent corruption.

`UPDATE events / checklists ... WHERE project=?` 도 같은 라벨 일치만으로 다른 팀의 항목을 휩쓴다.

**조치:**
- 일반 rename 분기: `UPDATE projects SET name=?, name_norm=? WHERE id=?`로 좁힘. `old_proj`가 None인 orphan label rename(테이블에 row가 없고 events/checklists 라벨만 있는 경우)은 기존 전역 동작 유지(orphan은 본문 시점에는 팀 정보 자체가 없음).
- events/checklists 갱신: `old_team_id`가 있으면 `WHERE project=? AND team_id=?`로 좁힘. NULL 잔존 row + orphan은 기존 전역 동작 유지.
- merge 분기: target_proj가 항상 같은 팀에 있으므로 `WHERE project=? AND team_id=?` 추가 안전.

**재검토 결과:** 합성 시나리오(team1 foo + team2 foo + rename team1 foo→bar)에서 더 이상 cross-team 갱신이 발생하지 않는다. import-time 정상.

**QA 시나리오 추가 요청:** "다른 팀에 같은 이름 프로젝트가 있을 때 한 팀의 rename이 다른 팀에 영향을 주지 않는다" 시나리오를 `verify_projects_unique.py`에 명시 추가. 사양서가 정의한 "다른 팀에 같은 이름 허용" 검사만으로는 silent corruption을 잡지 못한다.

## (a) Phase 본문 idempotency — 통과

`_phase_5_projects_unique` (`database.py:1531`) 검증:

- `_table_exists(conn, "projects")` 가드 — projects 테이블이 없는 상황(이론적으로만 가능)에서 노옵.
- `_column_set(conn, "projects")` + `name_norm` 누락 시 `RuntimeError` — Phase 1이 컬럼 추가를 보장하므로 정상 흐름에서는 발생하지 않음. 마커 강제 삭제 시 Phase 1도 다시 실행되므로 column 누락 → 백필 → 인덱스 순서가 유지된다.
- `WHERE name_norm IS NULL` 가드: 이미 채워진 row는 재방문하지 않음. 두 번째 init_db()에서 마커가 살아있으면 본문 자체가 호출되지 않고, 마커 강제 삭제 후 재실행하면 백필 0건 + `IF NOT EXISTS` 노옵.
- `CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_team_name`: idempotent. 인덱스가 이미 존재해도 노옵.

PHASES 등록 순서(레지스트레이션 순):
1. team_phase_1_columns_v1
2. team_phase_2_backfill_v1
3. team_phase_4_indexes_v1
4. team_phase_3_admin_separation_v1
5. team_phase_4_data_backfill_v1
6. team_phase_5_projects_unique_v1 ← 신규

`_pending_phases`는 PHASES 순서를 보존하므로 Phase 5는 항상 데이터 백필(Phase 4-data) 다음에 실행. 데이터 백필이 `team_id`를 채운 후 인덱스 생성이 일어나는 의존이 보장된다.

## (b) 부분 인덱스 SQL — 통과

```sql
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_team_name
ON projects(team_id, name_norm) WHERE team_id IS NOT NULL
```

- SQLite 3.8.0+ partial index 문법. 운영 환경 호환 OK(사양 명시).
- 인덱스 이름 `idx_projects_team_name`은 `database.py` 안에서 유일.
- `team_id IS NOT NULL` 필터로 NULL 잔존 row가 인덱스에서 면제 — 운영자 정리 영역에 동일 이름 NULL 팀 row가 다수 있어도 인덱스 충돌이 안 일어남.
- `name_norm IS NOT NULL` 추가 가드는 인덱스 정의에 없으나, NULL이 SQLite UNIQUE에서는 항상 충돌하지 않으므로 문제 없음. preflight 검사가 명시적으로 `name_norm IS NOT NULL` 한정 → 본문 진입 전 NULL은 검사에서 제외하고, 본문 1단계에서 NULL을 채워 정상화함.

## (c) 라우트 검사 — 통과

### `POST /api/manage/projects` (`app.py:2441`)

- `auth.resolve_work_team(request, user, explicit_id=data.get("team_id"))` — 명시 인자 → 쿠키 → 사용자 대표 팀 → legacy users.team_id → None.
- admin이 명시 없이 호출하면 None → 400. 비-admin은 항상 fallback이 동작하므로 None이 되지 않음(legacy path가 살아 있을 동안). 이는 사양 §40 "라우트는 항상 명시 team_id로 호출하도록 변경"에 부합.
- `auth.require_work_team_access(user, team_id)` — admin은 슈퍼유저로 통과, 비-admin은 user_teams approved 검사. 누군가가 다른 팀 id를 명시 본문에 끼워 넣어 우회하는 시도를 차단.
- `(team_id, name_norm) AND deleted_at IS NULL` 사전 검사로 409. `db.create_project`의 IntegrityError race도 추가 catch.

### `PUT /api/manage/projects/{name}` (`app.py:2478`)

- `db.get_project(name)` 후 `auth.can_edit_project` 권한 검사 — 기존 동작 유지.
- 충돌 검사를 `(proj.team_id, normalize_name(new_name)) AND id != proj.id` 사전 + `IntegrityError` 후속 catch.
- `proj.team_id IS NULL`(운영자 정리 영역)인 경우 사전 검사 skip — `rename_project` 함수 자체가 NULL 끼리 충돌 무시.

### `POST /api/manage/hidden-projects` (`app.py:2662`)

- `auth.resolve_work_team` + `require_work_team_access` 동일 패턴.
- `team_id is None` → 403(기존 메시지 유지) — 히든 프로젝트는 정의상 팀 컨텍스트 필수.

### 누락·과잉 검토

- 사양 §주의사항: `update_project_*` 시리즈 호출부 변경은 본 사이클 범위 밖 → 라우트 PATCH /status, /privacy, /memo, /color, /dates, /milestones 6개는 미변경. 정합.
- PUT 라우트는 admin이 다른 팀 프로젝트를 수정할 때 `can_edit_project`를 통해 통과. `resolve_work_team`은 PUT에는 적용하지 않음 — rename은 본인 프로젝트 컨텍스트 안에서 일어나므로 별도 작업 팀 결정이 불필요. 정합.

## (d) Preflight 정의 — 통과

`_check_projects_team_name_unique` (`database.py:1568`):

- `_table_exists` + 컬럼 존재 가드로 마이그레이션 초기 단계 노옵.
- `team_id IS NOT NULL AND name_norm IS NOT NULL`로 좁힌 GROUP BY — 부분 인덱스 정의와 정합.
- 반환 튜플: `("preflight_projects_team_name", "...team_id=N name_norm='abc' duplicates=K ids=[...]")`.
- `_PREFLIGHT_CHECKS.append`로 등록. import-time에 등록 1건 확인.

### 인프라 변경 — `_PREFLIGHT_CHECKS` 시그니처 확장

기존 `(conn) -> list[str]` → `(conn) -> list[tuple[str, str]]` 로 확장. `_run_preflight_checks` + `_run_phase_migrations` 양쪽에서 unpacking 일관 적용. 기존 등록된 검사 함수가 0개라 마이그레이션 부담 없음. dedup은 `_append_team_migration_warning`이 이미 카테고리+메시지 단위로 처리(`database.py:790~`).

### 타이밍 — 통과 + 비차단 메모

preflight는 `_run_phase_migrations`에서 phase 본문 시작 전 1회 실행. 따라서 미적용 phase가 있는 DB라면 검사 시점에 Phase 4 데이터 백필이 아직 안 끝났을 수 있음 — 검사는 "이미 team_id가 채워진 row의 충돌"만 잡는다. 사양서 §exit criteria의 합성 DB 시나리오(`team_id=1, name_norm='abc'` row 2개)는 이 동작에 부합.

데이터 백필 후 새로 생긴 충돌은 phase 5 본문의 `CREATE UNIQUE INDEX`가 raise → ROLLBACK으로 떨어지고 RuntimeError로 시작 거부. 이 경로는 사양에 명시되지 않은 enhancement지만 동일하게 안전.

## 비차단 메모 (사양상 in-scope이지만 #15·#10이 후속 정리)

1. **admin UX 변경.** `manage_create_project`는 admin이 work_team_id 쿠키 미설정 + 명시 team_id 미지정 상태에서 400으로 거부됨. 사양 §40이 NULL 저장을 막도록 명시했으므로 의도된 동작. #15(쿠키 통합) 적용 후 admin은 쿠키로 컨텍스트를 부여하거나 본문에 명시. backend_changes.md "본 사이클이 손대지 않은 dormant 이슈"에 기록 권고(qa_report에서 cross-link).

2. **`db.get_project(name)` ambiguity.** PUT 라우트가 `db.get_project(name)`로 단건 조회하는데, 본 사이클 시점에 같은 이름이 다른 팀에 존재하지 않으므로 모호하지 않음. 단, QA가 PUT 라우트를 검증할 때 cross-team 동일 이름을 만든 뒤 PUT을 호출하지 않도록 시나리오 순서 주의 — POST 시 같은 이름 다른 팀 허용 검증과는 분리.

## 검증

- `import database; import app` import-time 정상.
- PHASES 6개, preflight 1개 등록 확인.

## 결론

**차단 결함 수정 후 통과.** QA 단계로 진행. QA가 추가로 다뤄야 할 시나리오: cross-team rename non-interference (#B1 회귀 방지).
