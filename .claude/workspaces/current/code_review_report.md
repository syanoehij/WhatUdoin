# 코드 리뷰 보고서 — 팀 기능 그룹 A #4 (데이터 백필 1차)

## 리뷰 대상

- `database.py` line 1213-1517 (신규 phase 본문 + 헬퍼)
  - `__phase4_resolve_user_single_team(conn, user_name_or_id, _cache)` (line 1242-1307)
  - `_phase_4_data_backfill(conn)` (line 1310-1512)
  - `PHASES.append("team_phase_4_data_backfill_v1", ...)` (line 1515)
- `.claude/workspaces/current/backend_changes.md` (인계 정보 검증용)

## 차단(Blocking) 결함

**없음.**

## 경고(Warning)

### W1. 함수명 더블 언더스코어 prefix (스타일)

`__phase4_resolve_user_single_team` (line 1242)

- 모듈 레벨 함수에서 더블 언더스코어 prefix는 Python name mangling 대상이 아니므로 동작상 문제는 없다.
- 다만 사양서 §126 "phase 본문 내부 또는 모듈 private 헬퍼"는 보통 단일 언더스코어(`_phase4_resolve_…`)를 권장한다.
- 다른 phase의 헬퍼들(`_phase_1_team_columns`, `_phase_2_team_backfill`, `_phase_3_admin_separation`)이 모두 단일 prefix.
- **권장:** 후속 cleanup에서 `_phase4_resolve_user_single_team`로 정리. 본 사이클은 통과.

### W2. `meetings.is_team_doc` NULL 안전성 (방어 강화 권장)

`_phase_4_data_backfill` line 1404 `if is_team_doc == 1:`

- `meetings.is_team_doc`은 `INTEGER DEFAULT 1`이지만 legacy DB에 NULL row가 있을 수 있다 (column 추가 시점 직전 row).
- 현 구현: NULL이면 `is_team_doc == 1` False → 분기 (B)로 합류, warning 미발생. 사양서가 정의한 4분기에는 NULL 케이스 부재라 결정이 모호.
- **방어 강화:** NULL을 (A)로 처리할지 (B)로 처리할지 사양 명시 필요. 현 코드는 (B) 처리 = "개인 문서로 간주 → warning 안 함". 가장 보수적이라 OK.
- **본 사이클은 통과.** QA에서 NULL is_team_doc 케이스를 명시 확인 권장.

## 통과 ✅

### 가드

- [x] 모든 UPDATE에 `WHERE team_id IS NULL` 가드 (events·checklists·meetings·projects·links·team_notices)
- [x] `links` UPDATE에 추가 가드 `AND scope = 'team'` (idempotency 보강)
- [x] `notifications` UPDATE에 `event_id IS NOT NULL` + `EXISTS (events.team_id IS NOT NULL)` 이중 가드
- [x] `events` 우선순위 1번 UPDATE에도 `WHERE events.team_id IS NULL` + EXISTS 가드
- [x] 모든 테이블 접근에 `_table_exists(conn, …)` 가드 (8개 위치)
- [x] `events.project_id`, `projects.deleted_at` 컬럼 존재 가드 (`_column_set` 사용)
- [x] `pending_users` 삭제는 `_table_exists` 가드 후 무조건 DELETE — 빈 테이블이면 0행 영향
- [x] 헬퍼 입력 `None`/빈 문자열 즉시 `None` 반환

### `_resolve_user_single_team` 우선순위

사양서 §39-46과 line 1290-1307 비교:

- [x] 우선순위 1·2 묶음: `SELECT team_id … ORDER BY joined_at ASC, id ASC` 후 `len >= 1`이면 첫 row → 1건도 ≥2건도 동일 처리. id ASC tie-breaker 추가는 같은 joined_at의 결정성 보장 (사양서가 명시 안 했으나 결정성 위해 적절).
- [x] 우선순위 3: `legacy_team_id` (admin은 #3에서 NULL 처리됨)
- [x] 우선순위 4: 사용자 매칭 실패 → `None`
- [x] INTEGER 입력 → `users.id` 직접 조회
- [x] TEXT 입력 → `users.name` 매칭 (대소문자 그대로, name_norm 매칭 X — 사양서 §42 일치)
- [x] dict 캐시로 중복 조회 회피, 캐시 키 `("id", uid)` / `("name", name)`로 타입 충돌 방지

### warning 카테고리 (사양서 §exit criteria 5종)

- [x] `data_backfill_events` (events + checklists 합산)
- [x] `data_backfill_meetings_team_doc_no_owner`
- [x] `data_backfill_projects`
- [x] `data_backfill_links`
- [x] `data_backfill_team_notices`
- [x] dedup은 `_append_team_migration_warning` (database.py L773-809) 내장 가드 활용 — 같은 (category, message)는 재삽입 안 됨

### meetings 4분기

- [x] 분기 (A) `is_team_doc=1` + 작성자 admin/팀미배정 → NULL 유지 + `data_backfill_meetings_team_doc_no_owner` warning
- [x] 분기 (B) `is_team_doc=0` + admin/팀미배정 → NULL 유지 (warning 안 함)
- [x] 분기 (C) `is_team_doc=1` + 정상 → 작성자 단일 팀으로 백필
- [x] 분기 (D) `is_team_doc=0` + 정상 → 백필 (사양서 §82 "동일")

### projects (단계 1·2·4)

- [x] 단계 1 (기존 team_id) → `WHERE team_id IS NULL` 가드로 자동 skip
- [x] 단계 2 (`owner_id` 존재) → `__phase4_resolve_user_single_team(owner_id)` (INTEGER 분기)
- [x] 단계 3 (자동 프로젝트 생성) → **시도 안 함** (#6 책임)
- [x] 단계 4 (결정 불가) → NULL 유지 + warning `data_backfill_projects` (id, name, owner_id 포함)

### SQL 안전성

- [x] 모든 사용자 데이터는 `?` 파라미터화
- [x] f-string은 `guard_deleted` (line 1419-1422) 한 곳에만 사용. 삽입값은 정적 문자열 `"AND deleted_at IS NULL"` 또는 `""`로만 변동 → SQL injection 위험 없음

### idempotency

- [x] 모든 UPDATE에 NULL 가드 → 마커 강제 삭제 후 재실행해도 이미 채운 row는 다시 안 잡힘
- [x] NULL 유지 row는 다시 시도하지만 `_resolve_user_single_team` 결과가 같으므로 동일 결과
- [x] warning은 dedup으로 중복 누적 안 됨
- [x] `DELETE FROM pending_users`는 두 번째 실행 시 0행 영향

### import-time 검증

```
$ python -c "import database; print('PHASES:', len(database.PHASES))"
PHASES: 5
  - team_phase_1_columns_v1
  - team_phase_2_backfill_v1
  - team_phase_4_indexes_v1
  - team_phase_3_admin_separation_v1
  - team_phase_4_data_backfill_v1
```

신규 phase가 마지막에 등록되어 phase 3 admin separation(admin team_id NULL 처리) 이후 실행됨 → admin 작성 row가 헬퍼에서 자연스럽게 우선순위 3에서 NULL 반환되도록 의존 순서가 정확.

## 헬퍼 라우트 사용 여부 검토

`grep "_phase4_resolve_user_single_team\|__phase4_resolve_user_single_team"` → `database.py` 단일 파일에서만 정의·호출. app.py·auth.py 사용 흔적 없음. 사양서 §126 "phase 본문 내부 사용 한정" 준수.

## 사이클 범위 준수

본 사이클이 다루지 않은 항목 (사양서 §주의사항):

- [x] `projects.name_norm` 백필·UNIQUE는 #5 — 본 사이클에서 시도 안 함
- [x] `events.project_id`/`checklists.project_id` 백필 + 자동 프로젝트 생성·milestones·project_members·trash 검증은 #6 — 본 사이클에서 시도 안 함 (단 코드 1번 우선순위는 #6 적용 후를 대비해 둠 → 매칭 0건이 정상)
- [x] 가시성·편집 권한 라우트 적용은 #10 — 본 사이클은 라우트 미변경

## 최종 판정

**통과.** 차단 결함 없음. 경고 2건은 본 사이클 범위 외 cleanup 권장(W1) + QA 시나리오 보강 권장(W2)으로 QA 진행 가능.
