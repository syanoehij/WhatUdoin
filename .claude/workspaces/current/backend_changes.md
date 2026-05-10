# Backend Changes — 팀 기능 그룹 A #4 (데이터 백필 1차)

## 요약

`database.py`에 phase 본문 1건 등록. 7개 테이블 `team_id` 백필 + `pending_users` 자동 삭제를 한 트랜잭션 안에 묶음. 헬퍼 `__phase4_resolve_user_single_team` (phase 본문 전용 prefix `__phase4`) 추가.

## 변경 파일

- `database.py` (line 1212~ 신규 블록 약 230줄)
  - `__phase4_resolve_user_single_team(conn, user_name_or_id, _cache)` 모듈 private 헬퍼
  - `_phase_4_data_backfill(conn)` phase 본문
  - `PHASES.append(("team_phase_4_data_backfill_v1", _phase_4_data_backfill))`

## 등록 순서 (실행 순서)

```
1. team_phase_1_columns_v1
2. team_phase_2_backfill_v1
3. team_phase_4_indexes_v1
4. team_phase_3_admin_separation_v1
5. team_phase_4_data_backfill_v1   ← #4 사이클 신규
```

신규 phase는 마지막에 등록되어 phase 3 admin separation 직후 실행됩니다.

## 헬퍼 `__phase4_resolve_user_single_team`

phase 본문 전용. 라우트·UI에서 사용 금지(런타임 헬퍼는 #15 `resolve_work_team` 책임).

**우선순위 (사양서 §13 확정안):**

1. `user_teams` approved row가 정확히 1건 → 그 팀
2. ≥2건 → `joined_at` 최선(가장 이른 가입) 팀 = 대표 팀 (구현: `ORDER BY joined_at ASC, id ASC LIMIT 1`)
3. 0건 → legacy `users.team_id` (admin은 #3에서 NULL 처리됨)
4. 사용자 매칭 실패 → `None`

**입력 타입 분기:**

- `INTEGER` → `users.id` 직접 조회 (예: `meetings.created_by`)
- `TEXT` → `users.name` 매칭 (대소문자 그대로; `name_norm` 매칭은 후속 사이클 책임)

**캐시:** dict 캐시(`_cache`)로 같은 작성자 중복 조회 회피. 키는 `("id", uid)` 또는 `("name", name)`.

**가드:** `None`/빈 문자열 입력 → 즉시 `None` 반환.

## 7개 백필 + 1개 삭제 동작

### 1) `events.team_id`
- 가드: `WHERE team_id IS NULL`
- 우선순위 1번(project_id → projects.team_id): `project_id` 컬럼 존재 시 `UPDATE`. 본 사이클에서 `project_id`는 NULL이므로 매칭 0건이 정상이지만 #6 이후를 대비해 코드 유지.
- 우선순위 2번: `__phase4_resolve_user_single_team(created_by)`로 결정.
- 실패 → NULL 유지 + warning `data_backfill_events`.

### 2) `checklists.team_id`
- events와 동일 규칙. 단, exit criteria의 "5개 카테고리만 사용" 제약에 맞춰 warning 카테고리도 `data_backfill_events`에 합산 (메시지 prefix `checklists`로 구분 가능).

### 3) `meetings.team_id` (4분기)
- `created_by`는 INTEGER NOT NULL → `__phase4_resolve_user_single_team` INTEGER 분기 사용.
- 분기 (C) `is_team_doc=1` + 정상 → 백필
- 분기 (D) `is_team_doc=0` + 정상 → 백필 (작성자 본인 가시성 + 팀 컨텍스트 부여)
- 분기 (A) `is_team_doc=1` + admin/팀미배정 → NULL 유지 + warning `data_backfill_meetings_team_doc_no_owner`
- 분기 (B) `is_team_doc=0` + admin/팀미배정 → NULL 유지 (정상, warning 안 함)

### 4) `projects.team_id` (단계 1/2/4)
- 가드: `WHERE team_id IS NULL AND deleted_at IS NULL`
- 단계 1(기존 team_id): 가드로 자동 skip
- 단계 2(`owner_id` 존재 시): `__phase4_resolve_user_single_team(owner_id)` (INTEGER)
- 단계 3(자동 프로젝트 생성): **#6 책임** — 본 사이클에서 시도 X
- 단계 4(결정 불가): NULL 유지 + warning `data_backfill_projects` (id, name, owner_id 포함)

### 5) `notifications.team_id`
- 가드: `WHERE team_id IS NULL AND event_id IS NOT NULL` + `events.team_id IS NOT NULL` EXISTS 조건
- `event_id`가 NULL이거나 매칭 events.team_id가 NULL이면 NULL 유지
- warning **누적 안 함** (사양서: 알림은 transient 데이터, noise 회피)

### 6) `links.team_id`
- 가드: `WHERE scope = 'team' AND team_id IS NULL` (`scope='personal'`은 영향 없음)
- `created_by`(TEXT) → `__phase4_resolve_user_single_team`
- 실패 → NULL 유지 + warning `data_backfill_links` (id, created_by, title 포함)

### 7) `team_notices.team_id`
- 가드: `WHERE team_id IS NULL`
- `created_by`(TEXT) → `__phase4_resolve_user_single_team`
- 실패 → NULL 유지 + warning `data_backfill_team_notices` (id, created_by 포함)

### 8) `pending_users` 삭제
- `DELETE FROM pending_users` (status 무관, 가드 불필요)
- 빈 테이블이면 0행 영향 (노옵)
- warning 누적 안 함 (의도된 청소)

## idempotency

- 모든 UPDATE에 `WHERE team_id IS NULL` 가드 → 마커 강제 삭제 후 재실행해도 이미 채워진 row는 다시 안 잡힘
- NULL 유지 row는 다시 시도하지만 `_resolve_user_single_team` 결과가 같으므로 결과 동일
- warning은 `_append_team_migration_warning` 내장 dedup으로 (category, message) 동일 시 재삽입 안 됨
- `DELETE FROM pending_users`는 두 번째 실행 시 0행 영향

## 가드·방어

- 모든 테이블 접근에 `_table_exists(conn, …)` 가드 (빈 DB · 누락 테이블 대비)
- `events.project_id` 컬럼 존재 가드 (`_column_set` 사용)
- `projects.deleted_at` 컬럼 존재 가드 (Phase 1이 추가하지만 방어적)
- `created_by` None/빈 문자열 → 헬퍼가 즉시 None 반환

## 주의

- `_resolve_user_single_team`은 phase 본문 안에서만 호출. 사양서 §주의사항: 라우트·UI에서 사용 금지.
- meetings 4분기 모두 별도 케이스로 검증 필요(특히 admin + is_team_doc=1 → warning).
- legacy `users.team_id`는 fallback 키로 사용. drop은 Phase 5(별도 릴리스).
- 본 사이클은 #4 범위만. projects.name_norm UNIQUE는 #5, project_id 백필·자동 생성은 #6, 가시성·편집 권한 라우트는 #10.

## import-time 검증

```
$ python -c "import database; print(len(database.PHASES))"
OK 5 phases
  - team_phase_1_columns_v1
  - team_phase_2_backfill_v1
  - team_phase_4_indexes_v1
  - team_phase_3_admin_separation_v1
  - team_phase_4_data_backfill_v1   ← 신규
```
