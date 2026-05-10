# 팀 기능 그룹 A — #6 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#6. events/checklists/milestones/project_members/trash을 project_id 기준으로 백필**을 한 사이클로 진행. 마스터 plan은 `팀 기능 구현 계획.md` §13 프로젝트 백필 원칙.

#7 이후는 본 사이클 범위 밖.

## 분류

백엔드: 새 phase 본문 1건 등록 (events.project_id, checklists.project_id 백필 + 자동 프로젝트 생성 + Phase 4 인덱스 추가) + 신규 쓰기 경로 일부 변경.
프론트 변경 없음. **팀 모드: backend → reviewer → qa.**

## 전제 (#1·#2·#3·#4·#5 완료)

- PHASES 인프라 + `normalize_name()` (#1).
- Phase 1 본문에서 events.project_id, checklists.project_id 컬럼 추가됨 (#2).
- Phase 4 데이터 백필 phase에서 events.team_id, checklists.team_id, projects.team_id 등 백필 완료. 단 일부 row는 NULL 잔존 가능 (warning 누적됨, #4).
- Phase 5에서 `(team_id, name_norm)` 부분 UNIQUE 인덱스 + DB 함수·라우트 중복 검사 교체 완료 (#5).
- `create_project(name, color, memo, team_id=None)` 시그니처 + `(team_id, name_norm)` 사전 검사 사용 가능 (#5).

## 핵심 인계 사실 (메인이 이미 파악)

### 현재 컬럼 상태
- `events.project` (TEXT, 호환용 유지), `events.project_id` (INTEGER, 본 사이클이 백필) — `database.py:36-37`.
- `checklists.project` (TEXT NOT NULL DEFAULT ''), `checklists.project_id` (INTEGER) — `database.py:133-134`.
- `events.trash_project_id`, `checklists.trash_project_id`, `meetings.trash_project_id` — 휴지통 참조. #2 재구성 후 매핑 그대로 동작해야 함 (검증 대상).
- `project_milestones.project_id` (CREATE @ L529), `project_members.project_id` (CREATE @ L331). #2 재구성 후 dangling 없는지 검증.

### 매칭 규칙 (사양서 정의)
백필은 row 단위로 다음을 수행:
1. row의 `team_id`가 있고 `project` 문자열이 비어있지 않을 때만 시도.
2. `projects` 테이블에서 `(team_id, name_norm = normalize_name(project))` 매칭. (deleted_at IS NULL 우선, 없으면 deleted_at 포함 row).
3. 매칭 1건 → 그 `projects.id`로 events/checklists.project_id UPDATE.
4. 매칭 0건 + row의 team_id 있음 → **자동 프로젝트 생성**: `INSERT INTO projects (team_id, name, name_norm, created_at) VALUES (?, ?, normalize_name(name), CURRENT_TIMESTAMP)`. 생성된 id로 연결. warning 카테고리 `project_id_backfill_auto_created`(생성 row 카운트만).
5. row의 team_id NULL → project_id NULL 유지 + warning `project_id_backfill_no_team` (row id, project 문자열 포함).
6. project 문자열 비어있음 → 그대로 NULL.

### 자동 생성 정책
- `is_active=1`, `is_hidden=0`, `is_private=0`, `owner_id=NULL`, `color=NULL`, `memo=NULL`.
- 같은 phase 안에서 이미 자동 생성된 (team_id, name_norm)을 재사용 (메모리 dict 캐시).
- 같은 phase에서 events·checklists 양쪽이 같은 (team_id, name) 자동 생성하면 1개만 생성됨.
- 자동 생성 후 `(team_id, name_norm)` 부분 UNIQUE 인덱스(#5)와 충돌하지 않음 (정상 INSERT).

### 검증 대상 (회귀 방지)
- `project_milestones.project_id`, `project_members.project_id`가 `projects.id`로 모두 살아있는지 (dangling 없음).
- `events.trash_project_id`, `checklists.trash_project_id`, `meetings.trash_project_id` row가 `projects.id` 유효 참조인지. 무효 참조 발견 시 warning `project_id_backfill_dangling_trash` 누적, 데이터 변경 X.
- 본 사이클은 dangling 발견 시 데이터 cleanup이 아니라 warning만 누적 (cleanup은 별도 운영 작업).

## #6 step 분해 (플래너 참고)

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #6-S1 | phase 본문(`team_phase_6_project_id_backfill_v1`) + Phase 4 인덱스 | events/checklists.project_id 백필 + 자동 프로젝트 생성 + dangling 검증 + warning 누적 + 인덱스 `idx_events_project_id`, `idx_checklists_project_id`. |
| #6-S2 | 신규 쓰기 경로에 project_id 동반 | INSERT INTO events / checklists 시 project_id도 동반 저장. PATCH /api/events/{id}/project 갱신 시 project_id 동기화. rename_project는 events/checklists의 project 문자열도 갱신하던 기존 로직 유지(project_id는 그대로 — id가 동일하므로 이름만 갱신해도 정합). 호환 읽기 경로는 본 사이클 범위 외(#10 라우트 전환 시 함께). |

> 1 backend 호출에서 S1+S2 묶음 권장.

## exit criteria (사이클 전체)

### 마이그레이션 동작
- [ ] 빈 DB → phase 본문 노옵.
- [ ] 합성 구 DB(events 5건, checklists 5건, projects 3건, team_id 결정 row + NULL 잔존 row 혼합):
  - 매칭 케이스 → project_id 채움.
  - 매칭 실패 + team_id 있음 → 자동 프로젝트 생성 + 연결.
  - team_id NULL → project_id NULL + warning.
  - project 문자열 비어있음 → 그대로 NULL.
- [ ] 두 번째 init_db() → 마커로 phase 미실행. 마커 강제 삭제 후 재실행 → `WHERE project_id IS NULL` 가드로 노옵.
- [ ] `project_milestones.project_id`/`project_members.project_id` 모두 살아있음 검증 (dangling 0건).
- [ ] trash_project_id dangling 합성 케이스 → warning 누적, 데이터 변경 X.
- [ ] 자동 생성된 projects가 부분 UNIQUE 인덱스(#5)에 충돌하지 않고 정상 INSERT.

### 신규 쓰기 경로
- [ ] 새 events INSERT 시 project 문자열 + project_id 함께 저장 (project가 비어있으면 둘 다 NULL/'').
- [ ] 새 checklists INSERT 시 동일.
- [ ] PATCH /api/events/{id}/project 호출 시 project 문자열 + project_id 동시 갱신.
- [ ] rename_project 시 같은 (team_id) 내 events/checklists의 project 문자열이 갱신됨 (기존 동작 유지). project_id는 그대로 (id 자체는 변경 없음).

## 진행 방식

- backend가 1회 호출에서 phase 본문 + 신규 쓰기 경로 변경 함께 처리.
- reviewer는 (a) 매칭/자동 생성 정책 정합성 (b) dangling 검증의 false positive 방지 (c) 신규 쓰기 경로 동기화 누락 검토.
- qa는 시나리오 합성 DB로 (1) 매칭 케이스, (2) 자동 생성, (3) NULL team_id 잔존, (4) dangling 검증, (5) 마커 강제 삭제 후 재실행, (6) 신규 INSERT/PATCH 시 project_id 동반.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **본 사이클은 #6 범위만.** 가시성 라우트 적용·project_id 기반 읽기 경로 전환은 #10. project 문자열 컬럼 drop은 Phase 5(별도 릴리스).
- **자동 생성 프로젝트는 추후 운영자가 정리 가능하도록 warning에 row 카운트만 누적** (이름 목록은 노이즈 우려). 운영자는 `team_migration_warnings` + `projects` 조회로 확인.
- **idempotent 가드**: 모든 UPDATE는 `WHERE project_id IS NULL`. 자동 생성도 같은 phase 안 캐시로 중복 없음. 마커 강제 삭제 후 재실행 시에도 이미 채워진 row는 다시 안 잡힘.
- 매칭 시 `deleted_at IS NULL` 우선, 없으면 deleted_at 포함 row 사용 (운영 의미: 활성 프로젝트 우선, 휴지통 프로젝트라도 매칭되면 연결 — 활성 복구 시점에 자연 정합).
- `(team_id, name_norm)` 부분 UNIQUE 인덱스(#5) 덕분에 같은 팀 안에서 자동 생성이 같은 이름 두 번 발생하지 않음 (캐시가 fail-safe로도 동작).
- VSCode 디버깅 모드 — qa는 import-time + 합성 DB 위주.

## 산출물 위치

- `backend_changes.md`, `code_review_report.md`, `qa_report.md`: `.claude/workspaces/current/` 직속
- 검증 스크립트: `.claude/workspaces/current/scripts/verify_project_id_backfill.py`
