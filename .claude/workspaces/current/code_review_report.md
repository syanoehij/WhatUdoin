# 코드 리뷰 보고서 — 팀 기능 그룹 A #6 (events/checklists.project_id 백필)

## 리뷰 대상 파일

- `database.py`
  - `_phase_4_team_indexes` (인덱스 추가 영역, 라인 ~1057)
  - `_phase_6_lookup_or_create_project` / `_phase_6_backfill_table_project_id` / `_phase_6_check_dangling_refs` / `_phase_6_project_id_backfill` (phase 본문, 라인 ~1618)
  - `_resolve_project_id_for_write` (런타임 헬퍼, 라인 ~3036)
  - `create_event` / `_generate_recurrence_children` / `_apply_event_update` / `update_event` / `update_event_recurring_from_here` / `update_event_project` (events 쓰기 경로)
  - `create_checklist` / `update_checklist` (checklists 쓰기 경로)
  - `rename_project` merge 분기(라인 ~3574, 주석만 추가)

## 차단(Blocking) — 패치 후 통과

- [x] `database.py:update_checklist()` (라인 4661) — **차단 후 패치 완료**
  - **결함**: `UPDATE checklists SET title = ?, project = ?, ...` 경로에 `project_id` 동기화 누락. 사양서 "신규 쓰기 경로에 project_id 동반" 위반.
  - **수정**: 기존 row의 team_id를 SELECT로 조회 → `_resolve_project_id_for_write`로 새 project_id 결정 → UPDATE 컬럼에 추가. 두 분기(attachments 유/무) 모두 수정.
  - **검증**: `verify_project_id_backfill.py`에 케이스 3개 추가 (matched / no-match / 빈 문자열) → PASS.

## 경고(Warning) — 후속 사이클 watch item

- [x] `database.py:rename_project()` merge 분기 (라인 ~3574) — **차단 아님, #10에서 처리**
  - **현상**: merge 분기는 events/checklists.project 문자열을 target_proj 이름으로 갱신하지만 `project_id`는 soft-deleted된 old_proj.id를 그대로 가리킴.
  - **사양 근거**: "rename_project: project_id는 그대로". 본 사이클(#6)은 명시적으로 read 경로 전환(#10)까지 이 비정합을 유지한다. 그 전까지는 `project_id`를 신뢰하지 않는 read 경로뿐이라 가시 영향 없음.
  - **조치**: `# TODO #10: project_id sync on merge — defer until reader switch` 주석 추가. SQL은 변경 없음.

## 통과 ✅

### 매칭/자동 생성 정책 정합성
- [x] `_phase_6_lookup_or_create_project`: 활성 프로젝트(`deleted_at IS NULL`) 우선, 없으면 휴지통 포함 매칭. 사양서 §매칭 규칙 준수.
- [x] cache `(team_id, name_norm)` 공유로 events·checklists 양쪽이 같은 (team_id, name)을 자동 생성해도 1번만 INSERT. `verify` scenario#2가 입증 (events.project_id == checklists.project_id).
- [x] 자동 생성 row의 모든 필드가 사양서 기준 일치: `is_active=1`, `is_hidden=0`, `is_private=0`, `owner_id=NULL`, `color=NULL`, `memo=NULL`. `name_norm`도 동시에 채움 → (team_id, name_norm) 부분 UNIQUE 인덱스(#5)와 충돌 없음.
- [x] `normalize_name()` 일관 사용 — 백필·런타임 헬퍼 양쪽 동일.

### Idempotency 가드
- [x] `_phase_6_backfill_table_project_id`: `WHERE project_id IS NULL`로 SELECT, UPDATE 양쪽 모두 가드. 마커 강제 삭제 후 재실행해도 노옵. `verify` scenario#6이 입증.
- [x] 자동 생성 cache는 같은 phase 안 한 번만 INSERT. 두 번째 init_db() 시에는 마커가 존재해 phase 진입 자체가 skip.

### dangling 검증의 false positive 방지
- [x] 모든 dangling 쿼리: `LEFT JOIN ... WHERE p.id IS NULL` — NOT EXISTS 시맨틱. 정상 row를 잘못 잡지 않음.
- [x] `trash_project_id`는 `IS NOT NULL` 필터로 정상 NULL row 제외. 빈 휴지통 events가 false positive로 잡히지 않음.
- [x] `project_members`는 `(project_id, user_id)` 복합 PK이므로 `id` 컬럼 SELECT 금지 — (project_id, user_id) 쌍으로 출력. (개발 중 1차 SQL 오류 → 즉시 수정.)
- [x] 데이터 변경 X — warning만 누적. 사양서 §검증 대상 준수.

### 신규 쓰기 경로 동기화 (8곳)
- [x] `create_event` — `project_id` 컬럼을 INSERT에 추가, caller 미지정 시 `_resolve_project_id_for_write` 사용.
- [x] `_generate_recurrence_children` — 부모의 project_id 상속, 없으면 conn으로 해석.
- [x] `_apply_event_update` — UPDATE에 `project_id` 동기화. team_id는 기존 row에서 SELECT.
- [x] `update_event` — 자식 propagation에도 `project_id` 동기화.
- [x] `update_event_recurring_from_here` — 새 부모 INSERT에 동반.
- [x] `update_event_project` — UPDATE에 동기화.
- [x] `create_checklist` — INSERT에 동반.
- [x] `update_checklist` — 차단 결함 수정 후 동반 (위 차단 항목 참조).

### 인덱스
- [x] `idx_events_project_id`, `idx_checklists_project_id` — 비-UNIQUE, IF NOT EXISTS, 컬럼 존재 가드. Phase 4 indexes 함수 안에 등록.

### 보안 / 권한
- [x] phase 본문은 init_db() 안에서만 호출, 라우트 노출 없음.
- [x] `_resolve_project_id_for_write`는 SELECT만 — 권한 체크 불필요.
- [x] SQL 모두 파라미터화 (f-string은 테이블명 한정, 사용자 입력 없음).

### 회귀 영향
- [x] 기존 `create_event` 호출자(app.py 3곳)는 `project_id`를 전달하지 않음 → setdefault로 자동 해석. 시그니처 호환.
- [x] `_apply_event_update`는 `data["project_id"]`를 본문에서 덮어씀 → caller가 잘못된 project_id를 보내도 정합 유지.
- [x] `rename_project`의 일반 분기와 orphan label 분기는 project_id를 건드리지 않음 — 사양서 명시.

## 최종 판정

**통과** (차단 결함 1건 수정 완료, 경고 1건은 #10으로 이연)
