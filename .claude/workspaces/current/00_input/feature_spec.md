# 팀 기능 그룹 A — #5 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#5. 프로젝트 식별자 정리 (project_id 표준화)**를 한 사이클로 진행. 마스터 plan은 `팀 기능 구현 계획.md` §8-2, §13.

#6 이후는 본 사이클 범위 밖.

## 분류

백엔드: 새 phase 본문 1건 등록 + preflight check 1건 등록 + DB 함수·라우트 중복 검사 교체.
프론트 변경 없음. **팀 모드: backend → reviewer → qa.**

## 전제 (#1·#2·#3·#4 완료)

- PHASES 인프라 + `normalize_name()` (#1).
- Phase 1 본문에서 **projects 테이블 재구성**(name UNIQUE 제거 + name_norm 컬럼 추가)이 끝났고 **재구성 직후 같은 phase에서 Python 백필로 `name_norm`이 채워짐** (#2 backend_changes 명시 — `database.py:986`). 따라서 본 사이클의 `name_norm` 백필은 **잔존 NULL row 방어용**으로만 동작 (`WHERE name_norm IS NULL`).
- Phase 4 본문에 user_teams/team_menu_settings UNIQUE 2개 등록됨 (#2). projects는 미등록 — 본 사이클이 추가.
- Phase 4 데이터 백필 본문(#4) 등록됨. projects.team_id NULL 잔존 row가 있을 수 있음 (`team_migration_warnings` 카테고리 `data_backfill_projects`).
- `auth.py` 권한 헬퍼 + `resolve_work_team(request, user, explicit_id=None)` 사용 가능 (#2).

## 핵심 인계 사실 (메인이 이미 파악)

### DB 함수 (database.py)
- `create_project(name, color, memo) -> int` (L2760) — 단순 INSERT, 중복 검사 없음.
- `create_hidden_project(name, color, memo, owner_id)` (L2768) — `LOWER(name)` **전역** 중복 검사. team 무관. 본 사이클이 `(team_id, name_norm)` 검사로 교체.
- `rename_project(old_name, new_name, merge=False)` (L3063) — `name UNIQUE` 가정으로 동작. `merge=True` 분기는 같은 이름 통합 의미. 본 사이클: `(team_id, name_norm)` 안에서 새 이름 충돌 검사 + 충돌 시 409 또는 merge.
- `update_project_*` (L3184+) — INSERT-or-UPDATE 패턴 (이름으로 매칭, 없으면 INSERT). 이들 함수가 본 사이클에서 직접 변경 대상은 아니나 **호출부가 team_id를 결정해 넘기지 않으면** name 충돌 가능. **본 사이클은 호출부 변경 없이 함수만 (team_id, name_norm) 안에서 동작하도록 안전하게 유지** — INSERT 분기에서 team_id 인자 누락이면 NULL 저장(현재 동작 유지). 라우트 변경은 신규 생성·rename 두 경로만.

### 라우트 (app.py — 정확한 sub-path는 backend가 검증)
- `POST /api/manage/projects` L2441 — 프로젝트 생성.
- `PUT /api/manage/projects/{name}` L2461 — 프로젝트 정보 수정 (이름 변경 포함).
- `POST /api/manage/hidden-projects` L2623 — 히든 프로젝트 생성.
- 그 외 patch 라우트(status/privacy/memo/color/dates)는 이름 변경 없으므로 본 사이클 영향 없음.
- **라우트 호출부는 `resolve_work_team(request, user, explicit_id=None)`을 사용해 `team_id` 결정.** 쿠키 통합은 #15 책임이라 본 사이클은 명시 `team_id` 파라미터 + 사용자 대표 팀 fallback이면 충분.

### NULL team_id 정책 (사양 결정)
- `(team_id, name_norm)` UNIQUE는 **부분 인덱스로 등록**: `CREATE UNIQUE INDEX idx_projects_team_name ON projects(team_id, name_norm) WHERE team_id IS NOT NULL`.
- 이유: NULL 잔존 projects(#4 결정 불가 row)는 `owner_id` 본인 + admin만 보는 분리된 영역이라 같은 이름이 여러 NULL 팀에 있어도 운영 충돌 없음. 인덱스 충돌도 회피.
- 라우트 신규 생성·rename 시 검사도 부분 인덱스 정합성 유지: **`team_id IS NULL`인 새 row는 (team_id, name_norm) 중복 검사 면제** (운영자가 #10 후속 정리 시 처리). 단, 신규 생성 라우트는 항상 `resolve_work_team`으로 team_id를 결정해 NULL 저장하지 않는 게 정상.

## #5 step 분해 (플래너 참고)

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #5-S1 | phase 본문(`team_phase_5_projects_unique_v1`) + preflight check(`_check_projects_team_name_unique`) | Phase 2 본문에 잔존 `name_norm IS NULL` 백필. Phase 4 본문에 부분 UNIQUE 인덱스 등록. preflight: 데이터 충돌 검사 → 충돌 시 서버 시작 거부 + warning(`preflight_projects_team_name`). 충돌 0건이면 정상 통과. |
| #5-S2 | DB 함수 + 라우트 중복 검사 교체 | `create_project`, `create_hidden_project`, `rename_project`에 `(team_id, name_norm)` 중복 검사 추가. `create_project`는 호출부가 team_id를 명시하도록 시그니처 확장(`create_project(name, color, memo, team_id=None)`). 라우트 3개에서 `resolve_work_team` 사용으로 team_id 결정. 같은 이름이 다른 팀에 가능하도록 admin 대시보드 등 조회 쿼리도 점검. |

> 1 backend 호출에서 S1+S2를 함께 처리해도 됨. reviewer/qa는 (a) phase 본문 idempotency, (b) 부분 인덱스 동작, (c) 라우트 중복 검사, (d) preflight 동작을 분리 검증.

## exit criteria (사이클 전체)

### 마이그레이션 동작
- [ ] 빈 DB → phase 본문 노옵 (잔존 NULL 0건, 인덱스 신규 생성).
- [ ] 합성 구 DB(잔존 NULL name_norm row 1건 + 같은 팀 다른 프로젝트) → 백필 후 부분 UNIQUE 인덱스 정상 생성.
- [ ] **preflight 충돌 케이스**: 합성 DB에 `(team_id=1, name_norm='abc')` row 2개 → 서버 시작 거부 + warning에 충돌 row 정보. 운영자가 정리 후 재시작 가능.
- [ ] 두 번째 init_db() → 마커로 phase 본문 미실행, 인덱스 그대로.
- [ ] 마커 강제 삭제 후 재실행 → `WHERE name_norm IS NULL` 가드로 노옵, 인덱스 `IF NOT EXISTS`로 노옵.

### 라우트·DB 함수
- [ ] 같은 이름 프로젝트를 다른 팀에 생성 → 성공.
- [ ] 같은 팀 안에서 같은 이름(대소문자·NFC 다른 표기 포함) 프로젝트 두 번째 생성 시 차단 (UNIQUE 제약 또는 사전 검사 — 둘 다 권장).
- [ ] rename 시 같은 팀 안에 동일 name_norm row가 있으면 차단(409). 다른 팀에 같은 이름은 허용.
- [ ] 히든 프로젝트도 같은 정책 (`(team_id, name_norm)`).
- [ ] `team_id IS NULL` 프로젝트 끼리 같은 이름은 허용(운영자 정리 영역).

### 데이터 무결성
- [ ] `projects.id`는 #2 재구성 후 그대로. 본 사이클은 id를 변경하지 않음.
- [ ] events.project_id, checklists.project_id, project_members.project_id, project_milestones.project_id, *.trash_project_id 매핑은 #2에서 보존된 그대로 유지 (본 사이클이 직접 검증 — `verify_projects_unique.py`에서 합성 DB row count 일치 확인).

## 진행 방식

- backend가 1회 또는 2회 호출(S1, S2 분리 가능). 변경 일지를 step별 섹션으로 분리.
- reviewer는 (a) preflight 정의 (b) 부분 인덱스 SQL (c) 라우트 검사 누락/과잉 검토.
- qa는 시나리오 합성 DB로 (1) 정상 백필·인덱스, (2) preflight 충돌 거부, (3) 같은 이름 다른 팀 허용, (4) 같은 팀 중복 차단, (5) NULL team_id 면제 정책, (6) 마커 강제 삭제 후 재실행.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **본 사이클은 #5 범위만.** events.project_id·checklists.project_id 백필 + 자동 프로젝트 생성·milestones·project_members·trash 검증은 #6. 가시성 라우트 적용은 #10.
- **부분 인덱스**(`WHERE team_id IS NOT NULL`)는 SQLite 3.8.0+에서 지원. 운영 환경 호환 OK.
- **preflight check**는 `_PREFLIGHT_CHECKS.append(...)`로 등록. 인덱스 생성 직전(#1 인프라가 phase 본문 시작 전 호출)에 실행됨.
- **`create_project` 시그니처 확장**: `team_id=None` 추가. 기존 호출부(있다면)는 호환을 위해 None 인자로 동작 — 그러나 라우트는 항상 명시 team_id로 호출하도록 변경.
- **rename 시 `merge=True` 분기**: 같은 팀 내 같은 이름 다른 row를 합치는 의미. 본 사이클은 `merge` 분기 동작을 변경하지 않되, 매칭 검사를 `(team_id, name_norm)` 기반으로만 수정. 다른 팀의 동일 이름은 자동 머지 대상이 아님.
- preflight 카테고리: `preflight_projects_team_name`. dedup 보장.
- VSCode 디버깅 모드 — qa는 import-time + 합성 DB 위주, 실서버 재시작 필요 시 사용자에게 요청.

## 산출물 위치

- `backend_changes.md`, `code_review_report.md`, `qa_report.md`: `.claude/workspaces/current/` 직속
- 검증 스크립트: `.claude/workspaces/current/scripts/verify_projects_unique.py`
