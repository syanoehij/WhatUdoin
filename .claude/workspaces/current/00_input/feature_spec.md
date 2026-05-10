# 팀 기능 그룹 A — #4 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#4. 기존 데이터에 team_id 배정 (Phase 2 백필 — 1차)**를 한 사이클로 진행. 마스터 plan은 `팀 기능 구현 계획.md` §13 마이그레이션 대상.

#5 이후는 본 사이클 범위 밖.

## 분류

백엔드: 새 phase 본문 1건 등록 (events/checklists/meetings/projects/notifications/links/team_notices의 team_id 백필 + pending_users 자동 삭제).
프론트 변경 없음. **팀 모드: backend → reviewer → qa.**

## 전제 (#1·#2·#3에서 완료된 것)

- `database.py:631-811` PHASES 인프라 + `normalize_name()` (#1).
- Phase 1·2·4 본문(`team_phase_1_columns_v1`, `team_phase_2_backfill_v1`, `team_phase_4_indexes_v1`) (#2).
- Phase 3 본문(`team_phase_3_admin_separation_v1`) — admin team_id NULL, mcp_token NULL, user_ips whitelist→history, 관리팀 분기 (#3).
- `auth.py` 신규 권한 헬퍼 7개 + 기존 위임 (#2).
- `users.role`은 admin/member 두 값. `user_teams`는 (admin 제외) 사용자 → 팀 매핑.
- 신규 시드: 관리팀 자동 생성 X, admin team_id=NULL.

## 핵심 인계 사실 (메인이 이미 파악)

### 현재 스키마 (database.py)
- `events` (L32-): `team_id INTEGER`, `project TEXT`, `project_id INTEGER` (NULL — #6 책임), `created_by TEXT`
- `checklists` (L131-, L480-): `team_id INTEGER DEFAULT NULL`, `project TEXT NOT NULL DEFAULT ''`, `project_id INTEGER`, `created_by TEXT`
- `meetings` (L81-): `team_id INTEGER`, `created_by INTEGER NOT NULL`, `is_team_doc INTEGER DEFAULT 1`
- `projects` (#2 재구성 후): `team_id INTEGER`, `name TEXT NOT NULL`, `name_norm TEXT`, `owner_id INTEGER`, `is_hidden INTEGER`
- `notifications` (L435-): `user_name TEXT NOT NULL`, `team_id INTEGER`, `event_id INTEGER`
- `team_notices` (L424-): `team_id INTEGER`, `created_by TEXT NOT NULL`
- `links` (L514-): `scope TEXT NOT NULL DEFAULT 'personal'`, `team_id INTEGER`, `created_by TEXT NOT NULL`, FK to teams
- `pending_users` (L184-): `name, password, memo, status, created_at` — #4에서 모두 삭제

### users.team_id 의미 (백필 시 fallback 키)
- `#3` 후: admin은 NULL. 일반 사용자는 단일 팀 legacy 값(아직 drop 안 함, Phase 5 책임).
- `user_teams` approved row가 다중 팀 멤버십의 정답. legacy `users.team_id`는 #2 백필의 출처로만 정확.

### 작성자 → 단일 팀 결정 헬퍼 (사양서가 정의하는 우선순위)
헬퍼 시그니처: `_resolve_user_single_team(conn, user_name_or_id) -> Optional[int]`
- 입력이 INTEGER(예: meetings.created_by) → users.id 직접 조회
- 입력이 TEXT(예: events/checklists/team_notices의 created_by 문자열) → `users.name` 매칭 (대소문자 그대로 — 백필 시점에는 name_norm 매칭은 후속 사이클의 책임)
- 매칭된 user에 대해:
  1. `user_teams` approved row가 정확히 1건이면 그 팀
  2. ≥2건이면 `joined_at` 최선(가장 이른) 팀 (대표 팀)
  3. 0건이면 legacy `users.team_id` (admin이거나 팀 미배정이면 NULL)
  4. 사용자 매칭 실패 → NULL

이 헬퍼는 `database.py` phase 본문 안에 모듈 private으로 정의. 매 row마다 호출하므로 prepared statement 또는 dict 캐시 사용 권장.

## #4 step 분해 (플래너 참고)

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #4-S1 | phase 본문 등록 (`team_phase_4_data_backfill_v1`) | 다음 7개 백필 + pending_users 삭제를 한 phase 본문 안에 모두 묶음. 모든 UPDATE에 `WHERE team_id IS NULL` 가드 (이미 채운 row 보호). 결정 불가 row는 NULL 유지 + `team_migration_warnings` 누적. |

> 본 사이클은 1 step. backend 1회 호출 + reviewer + qa. 검증 시나리오가 분기마다 다르므로 qa는 시나리오별 합성 DB로.

## phase 본문 세부 (사양서가 정의)

phase 이름: `team_phase_4_data_backfill_v1` (Phase 4가 아니라 #4 본문 — 이름은 todo의 항목 번호 기반이며 Phase 단계와 무관).

### 1) events.team_id 백필
가드: `WHERE team_id IS NULL`.
추론 우선순위 (사양서 §13 확정안):
- (1번 — events.project_id → projects.team_id): 본 사이클에서는 `project_id`가 아직 없으므로(채움은 #6 책임) **시도하되 매칭 0건이 정상**. 코드는 두되 실효는 후속 사이클이 PHASES.append 했을 때 발생.
- (2번 — 작성자 단일 팀): `_resolve_user_single_team(conn, events.created_by)`로 결정. 결정되면 UPDATE.
- (실패 시): NULL 유지 + warning 카테고리 `data_backfill_events` 누적 (row id, created_by 포함).

### 2) checklists.team_id 백필
events와 동일.

### 3) meetings.team_id 백필 (4분기)
가드: `WHERE team_id IS NULL`.
`created_by`는 INTEGER NOT NULL.
- 작성자 user 조회. 작성자 admin이거나 `users.team_id IS NULL`이면 예외 분기:
  - `is_team_doc=1` (팀 문서) + 예외 → 자동 백필 제외 + warning `data_backfill_meetings_team_doc_no_owner`
  - `is_team_doc=0` (개인 문서) + 예외 → `team_id` NULL 유지 (정상, 작성자 본인 한정 가시성)
- 정상 분기:
  - `is_team_doc=1` + 정상 → 작성자의 user_teams 단일 팀 또는 legacy users.team_id로 백필
  - `is_team_doc=0` + 정상 → 동일 (작성자가 이후 팀을 옮겨도 변화 없음)

### 4) projects.team_id 백필 (단계 1/2/4만)
가드: `WHERE team_id IS NULL AND deleted_at IS NULL`.
- (단계 1) 기존 `team_id`가 있으면 우선 사용 → 이미 가드로 skip.
- (단계 2) `owner_id`가 있으면 owner의 user_teams 단일 팀 또는 legacy users.team_id.
- **(단계 3) 자동 프로젝트 생성은 #6 책임.** 본 사이클에서는 시도하지 않음.
- (단계 4) 결정 불가 → NULL 유지 + warning `data_backfill_projects` (row id, name 포함).

### 5) notifications.team_id 백필
가드: `WHERE team_id IS NULL`.
- `event_id`가 있고 events.team_id가 있으면 그 값으로.
- `event_id`가 NULL이거나 매칭 events.team_id가 NULL이면 → NULL 유지 (warning 누적은 노이즈가 크니 생략. 알림은 transient 데이터).

### 6) links.team_id 백필
가드: `WHERE scope='team' AND team_id IS NULL`. (`scope='personal'`은 `team_id` NULL이 정상)
- 작성자(`created_by` 문자열) → `_resolve_user_single_team` 결과로.
- 매칭 실패 → NULL 유지 + warning `data_backfill_links` (row id, created_by, title 포함).

### 7) team_notices.team_id 백필
가드: `WHERE team_id IS NULL`.
- 작성자(`created_by` 문자열) → `_resolve_user_single_team`.
- 매칭 실패 → NULL 유지 + warning `data_backfill_team_notices` (row id, created_by 포함).

### 8) pending_users 자동 삭제
- `DELETE FROM pending_users` (status 무관). 가드 불필요(빈 테이블이면 노옵).
- warning 누적 안 함 (의도된 청소).

## exit criteria (사이클 전체)

### 마이그레이션 동작
- [ ] 빈 DB → phase 본문 노옵 (UPDATE/DELETE 모두 0행).
- [ ] 합성 구 DB(7개 백필 대상에 대해 정상 케이스·실패 케이스 혼합) → 정상 케이스는 채워지고, 실패 케이스는 NULL 유지 + warning 누적.
- [ ] 두 번째 init_db() → 마커 덕에 phase 미실행. 마커 강제 삭제 후 재실행 → 가드 덕에 노옵 (이미 채운 row는 다시 안 잡힘, NULL 유지 row는 다시 시도하지만 결과 동일).
- [ ] meetings 4분기 모두 별도 케이스로 검증. 특히 admin 작성 + is_team_doc=1 케이스가 warning에 잡히는지.
- [ ] notifications.team_id 백필 → events.team_id가 NULL인 경우 그대로 NULL.
- [ ] links의 `scope='personal'` row는 백필 영향 받지 않음.
- [ ] pending_users는 마이그레이션 후 0건.

### warning 카테고리
- [ ] `data_backfill_events`, `data_backfill_meetings_team_doc_no_owner`, `data_backfill_projects`, `data_backfill_links`, `data_backfill_team_notices` 5종 카테고리만 사용.
- [ ] 같은 row를 두 번 시도해도 dedup으로 중복 누적 안 됨 (`_append_team_migration_warning` 내장 가드).

## 진행 방식

- backend가 phase 본문 1건 추가. 헬퍼 `_resolve_user_single_team`은 `_phase_4_data_backfill` 함수 내부 또는 모듈 private 헬퍼로.
- reviewer는 가드·분기 누락·헬퍼 우선순위 정합성 검토.
- qa는 시나리오별 합성 DB로 case 매트릭스 검증.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **본 사이클은 #4 범위만.** projects.name_norm 백필·`(team_id, name_norm) UNIQUE` 인덱스는 #5. events.project_id·checklists.project_id 백필 + 자동 프로젝트 생성·milestones·project_members·trash 검증은 #6. 가시성·편집 권한 라우트 적용은 #10.
- **legacy users.team_id**는 사용 가능. Phase 5(별도 릴리스)에서 drop. 본 사이클에서는 fallback 키로 활용.
- `_resolve_user_single_team`은 phase 본문 안에서만 사용. 라우트·UI에서 쓰지 말 것 (런타임 헬퍼는 #15에서 `resolve_work_team`으로 다른 책임).
- meetings의 `created_by`는 INTEGER. events/checklists/links/team_notices의 `created_by`는 TEXT. 혼동 주의.
- warning 누적은 1행씩 호출하지 말고 결정 불가 row를 모은 뒤 한 번에 누적해도 됨 (성능). 단, dedup은 `_append_team_migration_warning`이 보장.
- VSCode 디버깅 모드 — qa는 import-time + 합성 DB 위주. 실서버 재시작 필요 시 사용자에게 요청.

## 산출물 위치

- `backend_changes.md`, `code_review_report.md`, `qa_report.md`: `.claude/workspaces/current/` 직속
- 검증 스크립트: `.claude/workspaces/current/scripts/verify_data_backfill.py`
