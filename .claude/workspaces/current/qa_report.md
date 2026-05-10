# QA 보고서 — 팀 기능 그룹 A #6 (events/checklists.project_id 백필)

## 검증 도구

`scripts/verify_project_id_backfill.py` — import-time 합성 DB 검증.

- VSCode 디버깅 모드라 서버 자동 재시작 불가 → import-time 위주 (사양서 §주의사항).
- 임시 DB 디렉토리(`tempfile.mkdtemp`)를 `WHATUDOIN_RUN_DIR`로 잡아 운영 DB 침범 없음.
- Windows cp949 환경 대응 위해 stdout을 UTF-8로 reconfigure.

## 실행 결과

```
============================================================
[1/3] 빈 DB init_db() — 노옵 검증
============================================================
[WhatUdoin][migration] phase 'team_phase_1_columns_v1' OK
[WhatUdoin][migration] phase 'team_phase_2_backfill_v1' OK
[WhatUdoin][migration] phase 'team_phase_4_indexes_v1' OK
[WhatUdoin][migration] phase 'team_phase_3_admin_separation_v1' OK
[WhatUdoin][migration] phase 'team_phase_4_data_backfill_v1' OK
[WhatUdoin][migration] phase 'team_phase_5_projects_unique_v1' OK
[WhatUdoin][migration] phase 'team_phase_6_project_id_backfill_v1' OK
  OK — markers: 7

============================================================
[2/3] 합성 시나리오 — 마커 삭제 후 재실행으로 phase 6 본문 트리거
============================================================
  scenario#1 matched OK — events.project_id=1
  scenario#2 auto-create OK — single project_id=2 reused
  scenario#3 NULL team OK — project_id NULL
  scenario#4 empty project OK — project_id NULL
  scenario#5 warnings OK — categories: ['project_id_backfill_auto_created',
                                          'project_id_backfill_dangling_member',
                                          'project_id_backfill_dangling_milestone',
                                          'project_id_backfill_dangling_trash',
                                          'project_id_backfill_no_team']
  scenario#6 idempotent OK — no changes on rerun (events frozen, projects=3)

============================================================
[3/3] 신규 쓰기 경로 — create_event / create_checklist / update_event_project
============================================================
  new write create_event OK — project_id=1
  new write create_checklist OK — project_id=1
  new write update_event_project(None) OK — both NULL
  new write update_event_project (no match) OK — string set, project_id NULL
  new write update_event_project('ProjectX') OK — project_id=1
  new write update_checklist('ProjectX') OK — project_id=1
  new write update_checklist (no match) OK — string set, project_id NULL
  new write update_checklist('') OK — project_id NULL
  index OK — idx_events_project_id
  index OK — idx_checklists_project_id

============================================================
PASS — 모든 시나리오 통과
```

## 통과 ✅

### 마이그레이션 동작
- [x] **빈 DB 첫 init_db()** — 7개 phase 마커 모두 박힘. events/checklists 0개라 phase 6 본문 노옵.
- [x] **합성 시나리오 (events 5건 + checklists 2건 + projects 1건 시드)**:
  - 매칭 케이스 (scenario#1) — 활성 ProjectX와 (team_alpha, name_norm) 매칭 → project_id 채움.
  - 자동 생성 (scenario#2) — events 'AutoNew' + checklists 'AutoNew' (둘 다 team_beta) → 1개 project로 통합 INSERT, 양쪽 같은 project_id로 연결.
  - team_id NULL (scenario#3) — project_id NULL 유지 + warning `project_id_backfill_no_team` 누적.
  - 빈 project 문자열 (scenario#4) — project_id NULL 유지, warning 없음.
- [x] **마커 강제 삭제 후 재실행 (scenario#6)** — `WHERE project_id IS NULL` 가드로 events/projects 모두 변화 없음.
- [x] **dangling 검증 (scenario#5)** — `project_milestones.project_id=9998`, `project_members.project_id=9999`, `events.trash_project_id=9997` 모두 무효 참조 → warning 5종 누적. 데이터 변경 X.
- [x] **자동 생성 row** (team_beta + AutoNew)이 (team_id, name_norm) 부분 UNIQUE 인덱스(#5)에 충돌 없이 INSERT.

### 신규 쓰기 경로 (events 3 + checklists 4 = 7케이스)
- [x] `create_event` — team_alpha + 'ProjectX' → project_id=proj_x_id 매칭.
- [x] `create_checklist` — 동일.
- [x] `update_event_project(None)` — project 문자열 + project_id 둘 다 NULL.
- [x] `update_event_project('NoSuchProject')` — 매칭 0건 → project 문자열만 설정, project_id NULL (자동 생성은 phase 본문 책임이라 의도된 동작).
- [x] `update_event_project('ProjectX')` — 매칭 → project_id 동기화.
- [x] `update_checklist('ProjectX')` — 매칭 → project_id 동기화 (리뷰 차단 결함 패치 후 회귀 방지).
- [x] `update_checklist('NoSuchProject')` — 매칭 0건 → project 문자열만, project_id NULL.
- [x] `update_checklist('')` — 빈 문자열 → project_id NULL.

### 인덱스
- [x] `idx_events_project_id` 생성 확인 (sqlite_master 조회).
- [x] `idx_checklists_project_id` 생성 확인.

## 실패 ❌

없음.

## 회귀 확인

- [x] 빈 DB 첫 init_db()에서 기존 6개 phase 모두 정상 OK 후 phase 6 신규 진입.
- [x] phase 1·2·3·4·4(data)·5 마커 보존 + phase 6 마커 새로 추가.
- [x] `update_event_project` 기존 시그니처 변화 없음. caller(app.py:2185) 호환.
- [x] `create_event` `data` dict 구조 호환 — caller가 project_id를 안 줘도 자동 해석.
- [x] `create_checklist` 시그니처 호환 — 추가 인자 없음.
- [x] `update_checklist` 시그니처 호환 — 추가 인자 없음.

## 서버 재시작 필요 여부

**필요**. 본 사이클 변경은 import-time phase 마이그레이션이라, 다음 서버 시작 시 phase 6 본문이 자동 실행되어 운영 DB의 events/checklists.project_id가 채워진다.

**사용자 액션 권장:** VSCode 디버깅 세션을 적당한 시점에 재시작하여 운영 DB에 phase 6 적용을 트리거할 것. 본 QA는 임시 DB로만 검증되었고, 운영 DB 적용은 사용자 재시작 시점에 자동 발생.

## 산출물

- 검증 스크립트: `.claude/workspaces/current/scripts/verify_project_id_backfill.py`
- 백엔드 변경 정리: `.claude/workspaces/current/backend_changes.md`
- 코드 리뷰: `.claude/workspaces/current/code_review_report.md`
- QA 보고서: 본 문서
