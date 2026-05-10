# Backend Changes — 팀 기능 그룹 A #6

## 요약

`team_phase_6_project_id_backfill_v1` phase 본문 + Phase 4 인덱스 2개 + 신규 쓰기 경로(events/checklists) project_id 동반.

## 변경 파일

- `database.py` — phase 6 본문, 인덱스, 런타임 헬퍼, 쓰기 경로 6곳 수정.

## 변경 내역

### 1. Phase 4 인덱스 추가 (`_phase_4_team_indexes`)

기존 UNIQUE 인덱스 2개에 더해 비-UNIQUE 인덱스 2개를 추가.

- `idx_events_project_id` ON events(project_id)
- `idx_checklists_project_id` ON checklists(project_id)

`_table_exists` + `_column_set` 가드로 phase 1·6 사이에 끼어도 안전.

### 2. Phase 6 본문 등록 (`_phase_6_project_id_backfill`)

PHASES 등록 순서: phase_5 다음.

**구성 함수:**

| 함수 | 책임 |
|------|------|
| `_phase_6_lookup_or_create_project(conn, team_id, name, cache, auto_created_ids)` | (team_id, name_norm) 매칭. 활성 우선, 없으면 휴지통 포함. 매칭 0건 시 auto INSERT — `is_active=1`, `is_hidden=0`, `is_private=0`, `owner_id=NULL`, `color=NULL`, `memo=NULL`, `name_norm` 동시 채움. cache로 같은 phase 안 중복 INSERT 방지. |
| `_phase_6_backfill_table_project_id(conn, table, cache, auto_created_ids)` | events 또는 checklists에 `WHERE project_id IS NULL` 가드 + row 단위 매칭/생성. project 비어있음 → NULL 유지. team_id NULL → warning + NULL 유지. |
| `_phase_6_check_dangling_refs(conn)` | project_milestones, project_members(복합 PK라 project_id+user_id 출력), events/checklists/meetings.trash_project_id 무효 참조 → warning만 누적, 데이터 변경 X. |
| `_phase_6_project_id_backfill(conn)` | 위 3개를 묶어 호출. cache + auto_created_ids 공유로 events·checklists 양쪽이 같은 (team_id, name)을 1개의 신규 project로 흡수. 자동 생성 row 카운트만 warning에 누적. |

**warning 카테고리 (5종):**
- `project_id_backfill_no_team`
- `project_id_backfill_auto_created` (count만)
- `project_id_backfill_dangling_milestone`
- `project_id_backfill_dangling_member`
- `project_id_backfill_dangling_trash`

**Idempotency:**
- 모든 UPDATE는 `WHERE project_id IS NULL`. 마커 강제 삭제 후 재실행해도 노옵.
- 자동 생성도 cache로 같은 phase 안 중복 없음. (team_id, name_norm) 부분 UNIQUE 인덱스(#5)와 충돌 없음.

### 3. 런타임 헬퍼 추가 (`_resolve_project_id_for_write`)

신규 쓰기 경로 전용. 매칭 정책은 phase 본문과 동일하지만 **자동 생성하지 않음**(NULL 반환). 매칭 0건이면 라우트 계층이 별도로 create_project를 호출했을 때 자연 정합.

### 4. 신규 쓰기 경로 변경 (6곳)

| 함수 | 변경 |
|------|------|
| `create_event(data)` | INSERT 컬럼에 `project_id` 추가, caller 미지정 시 `_resolve_project_id_for_write`로 결정. |
| `_generate_recurrence_children(conn, parent_id, parent_data)` | 자식 INSERT에 부모의 `project_id` 상속(없으면 conn으로 해석). |
| `_apply_event_update(conn, event_id, data)` | 기존 row의 team_id를 SELECT로 조회 → `project_id` 재해석 후 UPDATE에 동반. |
| `update_event(event_id, data)` | 하위 업무 propagation에도 `project_id` 동기화. existing.team_id 사용. |
| `update_event_recurring_from_here(event_id, data)` | 새 부모 INSERT에 `project_id` 동반. |
| `update_event_project(event_id, project)` | UPDATE에 `project_id` 컬럼 동기화. |
| `create_checklist(...)` | INSERT 컬럼에 `project_id` 추가. |

## 검증 결과

`scripts/verify_project_id_backfill.py` — **PASS** (모든 시나리오 통과)

- [1/3] 빈 DB init_db() — 7개 phase 마커 정상 박힘, events/checklists 0개 노옵.
- [2/3] 합성 시나리오 — 매칭(scenario 1) / 자동 생성(scenario 2, 1개로 합쳐짐) / NULL team(3) / 빈 project(4) / warning 5종(5) / 마커 강제 삭제 후 재실행 노옵(6).
- [3/3] 신규 쓰기 경로 — create_event / create_checklist / update_event_project(None / no-match / match) 5케이스 통과.
- 인덱스 2개 정상 생성 확인.

## 주의 사항

- 본 사이클은 **#6 범위만**. 가시성 라우트 적용·project_id 기반 읽기 경로 전환은 #10. project 문자열 컬럼 drop은 Phase 5(별도 릴리스).
- `_resolve_project_id_for_write`는 자동 생성하지 않음. 신규 라우트가 새 프로젝트 이름을 처음 쓰면 project_id는 NULL로 들어감 → #10에서 라우트 전환 시 별도 create_project 호출 필요.
- `update_event` propagation의 자식 project_id는 부모와 동일한 team_id 가정. 부모/자식 team_id가 다른 비정상 row는 별도 마이그레이션 영역.
- VSCode 디버깅 모드라 서버 자동 재시작 불가하지만, 본 사이클 변경은 import-time 마이그레이션이라 다음 서버 재시작 시 자동 적용.
