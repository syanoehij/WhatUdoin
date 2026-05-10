# 팀 기능 그룹 A — 보강 사이클: 자동 dedup phase + 운영자 도구

## 요청

회사 운영 DB 첫 실행에서 #5 preflight(`_check_projects_team_name_unique`)가 충돌을 막을 가능성이 있다. 본 사이클은 두 가지를 함께 추가한다(옵션 C):

1. **자동 dedup phase**: 안전 조건을 만족하는 충돌 그룹은 phase 본문이 자동 흡수 → preflight까지 가지 않게 한다.
2. **운영자 도구 (`migration_doctor`)**: WhatUdoin.exe에 sub-command(`--doctor`)로 추가. 회사 운영자가 사전 점검·사후 진단·SQL 안내를 받을 수 있게.

이 사이클이 끝나면 회사 첫 실행 위험이 크게 줄고, 만약 unsafe 충돌이 남아 있더라도 운영자가 도구로 진단하고 정리한 뒤 재시작 가능.

## 분류

백엔드: 새 phase 본문 1건(`team_phase_5a_projects_dedup_safe_v1`) + 운영자 도구 1건 + main.py sub-command + (필요 시) PyInstaller spec 업데이트.
프론트 변경 없음. **팀 모드: backend → reviewer → qa.**

## 전제 (#1~#7 완료)

- PHASES 인프라 + Phase 1·2·3·4·5·6·7 본문 등록됨.
- 회사 운영 DB에 `(team_id, name_norm)` 충돌 가능성 확인됨(개발 DB에서 1건 발견 — `team_id=17`, `name_norm='alpha'`, ids=[104,105], 둘 다 빈 + 참조 0건).
- `users.name_norm`/`teams.name_norm` 충돌 가능성도 잠재적으로 존재(현재 미확인).
- 자동 dedup은 **projects만** 다룬다. users/teams는 운영 결정 영역 → 도구 진단까지만, 자동 정리 X.

## 핵심 설계

### 1) 자동 dedup phase — 안전 조건

phase 이름: `team_phase_5a_projects_dedup_safe_v1`. **#5(`team_phase_5_projects_unique_v1`) 앞**에 등록되어 같은 init_db() 호출에서 dedup → preflight → 인덱스 생성 순서가 되도록 PHASES 리스트 순서를 명시 관리.

같은 `(team_id, name_norm)` 그룹의 모든 row가 다음 안전 조건을 모두 만족할 때만 자동 정리 대상:

- `events.project_id` 참조 0건 (`deleted_at IS NULL`만 카운트 — 휴지통은 제외해도 안전)
- `events.project = projects.name AND project_id IS NULL` 문자열 잔존 참조 0건
- `checklists.project_id` 참조 0건 (deleted_at IS NULL)
- `checklists.project = projects.name AND project_id IS NULL` 문자열 잔존 참조 0건
- `meetings.trash_project_id` 참조 0건 (휴지통 메타이지만 보수적 접근)
- `events.trash_project_id`, `checklists.trash_project_id` 참조 0건 (동일)
- `project_members` 참조 0건
- `project_milestones` 참조 0건
- 메타데이터 동일 또는 모두 NULL: `memo`, `color`, `owner_id`, `is_hidden`, `is_private`, `start_date`, `end_date`, `is_active` 모든 컬럼이 그룹 내 일치(또는 모두 NULL)
- 그룹 내 `deleted_at IS NULL` row가 적어도 1개(살릴 row 후보 존재)

### 정리 동작 (만족 그룹)
- `MIN(id) WHERE deleted_at IS NULL` row 1개를 유지(없으면 그룹 자체 skip).
- 나머지 row는 **hard DELETE** (참조 0건이므로 cascade 사고 없음, phase 시작 전 자동 백업이 떠 있어 복구 가능).
- warning 누적: 카테고리 `dedup_projects_auto`, 메시지에 그룹 키와 정리된 id 목록 포함. dedup 가드는 `_append_team_migration_warning` 내장(중복 누적 방지).

### unsafe 그룹
- 안전 조건 미충족 그룹은 그대로 둔다.
- 이후 `team_phase_5_projects_unique_v1`의 preflight이 거부 → 운영자가 도구로 진단·정리 → 재시작.

### Idempotency
- 두 번째 init_db() → 마커로 phase 미실행.
- 마커 강제 삭제 후 재실행 → 같은 그룹이 이미 정리되어 row 수 1이라 `GROUP BY HAVING COUNT(*) > 1` 자체가 0건 매칭. 노옵.

### 2) 운영자 도구 `tools/migration_doctor.py`

WhatUdoin.exe에 sub-command 형태로 노출. 회사 운영자가 별도 exe 설치/Python 설치 없이 진단 가능.

**진입점 변경 (main.py)**:
- `if __name__ == "__main__"` 블록 가장 처음에 `sys.argv` 검사. `--doctor` 또는 `doctor` 첫 인자면 도구 모드로 진입(uvicorn·tray 안 뜸).

**도구 명령어**:
- `WhatUdoin.exe --doctor check` (기본): 등록된 모든 `_PREFLIGHT_CHECKS` 순회 + dedup 그룹 진단(자동 처리 가능 vs 운영자 결정 필요 분류). 운영 DB(`whatudoin.db`)는 read-only로 열어 검사. 출력은 사람이 읽기 좋은 한국어 + ASCII 표.
- `WhatUdoin.exe --doctor fix-projects --apply`: dedup 안전 조건 충족 그룹만 자동 정리. `--apply` 없으면 dry-run으로 어떤 row가 지워질지 미리보기. 자체 백업 호출 후 정리.
- `WhatUdoin.exe --doctor --help`: 사용법 출력.

**unsafe 충돌 (users/teams.name_norm)**:
- 진단만 출력. 자동 정리 안 함. 운영자가 직접 처리하도록 **권장 SQL 템플릿**을 출력(예: `UPDATE users SET name='...' WHERE id=?`). 운영자가 의도 결정.

### 3) PyInstaller spec 업데이트

- `migration_doctor.py`를 `tools/migration_doctor.py`로 두고 `datas` 또는 `pathex`에 추가 — 단일 파일 sub-command 방식이라 별도 entry point는 불필요(main.py가 dispatch).
- 필요 시 hiddenimports에 추가될 모듈 없음(이미 sqlite3, json만 사용).

## step 분해 (플래너 참고)

| step | 제목 | exit criteria |
|------|------|---------------|
| S1 | 자동 dedup phase 본문 등록 + PHASES 순서 정렬 | `team_phase_5a_projects_dedup_safe_v1`이 `team_phase_5_projects_unique_v1` 앞에 위치. 안전 조건 헬퍼 정확. unsafe 그룹은 변경 X. 합성 DB 7 시나리오 PASS. |
| S2 | `tools/migration_doctor.py` + main.py sub-command | check/fix-projects 두 명령어 동작. read-only 진단 + dry-run 보호. 자체 백업 후 정리. |
| S3 | PyInstaller spec 업데이트 + 빌드 사전 검증 | spec 변경 후 `pyinstaller --noconfirm WhatUdoin.spec` 시도(qa는 spec 파일 점검만, 실제 빌드는 사용자가 실행 — 시간 큼). 빌드 시도 실패 시 spec 패치. |

## exit criteria (사이클 전체)

### 자동 dedup phase
- [ ] 빈 DB → phase 본문 노옵.
- [ ] 합성 DB 시나리오:
  - (a) 안전 그룹(빈 + 참조 0 + 메타 일치) → MIN id 유지, 나머지 DELETE, dedup_projects_auto warning 누적.
  - (b) 한 row만 참조 있음 + 다른 row 빈 + 메타 일치 → 정리 대상 (참조 있는 쪽이 살아남도록 — `MIN(id WHERE 참조有)` 또는 단순 MIN id가 다를 수 있음. **사양 결정**: 단순 MIN(id)를 유지 후, 그것이 우연히 참조 없는 row면 자동 dedup이 위험 — 따라서 **"그룹 내 참조 없는 row만 DELETE"** 정책으로 변경. 사양 명세 끝.)
  - (c) 메타 다름 → 그대로 둠 → 이후 #5 preflight 거부.
  - (d) 양쪽 모두 참조 있음 → 그대로 둠 → preflight 거부.
- [ ] 두 번째 init_db() → 노옵.
- [ ] 마커 강제 삭제 후 재실행 → row 수 1이라 노옵.

### 운영자 도구
- [ ] `WhatUdoin.exe --doctor check` (또는 dev: `python main.py --doctor check`):
  - projects (team_id, name_norm) 충돌 그룹 + 안전 분류 출력.
  - users/teams.name_norm 충돌 출력 + 권장 SQL 템플릿.
  - 충돌 0건이면 "이상 없음".
- [ ] `--doctor fix-projects` dry-run: 어떤 row 지워질지만 출력, 변경 X.
- [ ] `--doctor fix-projects --apply`: 백업 후 안전 그룹 정리. dry-run 결과와 동일 동작.

### main.py 변경
- [ ] `--doctor` 인자 없이 실행 → 기존 동작 100% 유지(uvicorn + tray).
- [ ] `--doctor` 인자 있으면 도구 모드 진입, 정리 후 종료. tray 안 뜸.

### PyInstaller
- [ ] spec에 `tools/migration_doctor.py` 포함.
- [ ] 사용자 직접 빌드 시 정상 패키징.

## 정리된 자동 dedup 정책 (사양 확정)

**룰**: 같은 `(team_id, name_norm)` 그룹에서 다음을 만족하는 row만 자동 DELETE:
- 그 row의 events/checklists.project_id 참조 0건
- 그 row의 events/checklists/meetings.trash_project_id 참조 0건
- 그 row의 project_members 참조 0건
- 그 row의 project_milestones 참조 0건
- `events.project = projects.name AND project_id IS NULL` 문자열 참조 0건
- `checklists.project = projects.name AND project_id IS NULL` 문자열 참조 0건
- 그룹 내에 살아남을 row(참조 ≥ 1건 또는 메타데이터 보존이 필요한 row)가 존재

→ 즉 그룹의 "빈 + 참조 0건" row만 DELETE. 모든 row가 참조 0건이면 `MIN(id)` 1개를 살리고 나머지 DELETE. 메타데이터 동일성 검사는 살아남는 row의 메타가 그룹 대표가 되도록 설계가 단순해짐. 메타가 서로 달라도 빈 row는 안전 정리, 메타 보존은 살아남는 row가 자기 메타 그대로.

이 룰의 장점: 메타 동일성 검사를 안 해도 안전(참조 없는 row는 메타가 무엇이든 운영 영향 0).

## 진행 방식

- backend가 S1+S2+S3을 한 흐름으로 처리. step별 섹션으로 분리.
- reviewer는 (a) phase 순서 (b) 안전 조건 정밀도(거짓 양성/음성) (c) 도구 dry-run 보호 (d) main.py sub-command 분기.
- qa는 시나리오 (1) 안전 그룹 자동 정리, (2) unsafe 그룹 보존, (3) 도구 check, (4) 도구 fix-projects dry-run, (5) 도구 fix-projects --apply, (6) main.py 일반 모드 영향 0.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **정리는 hard DELETE**. phase 시작 시 `run_migration_backup()`이 자동으로 떠 있고, 도구의 `--apply`도 자체 백업 호출 후 진행. 백업 위치는 `backupDB/whatudoin-migrate-*.db`.
- 도구는 `whatudoin.db`를 default로 사용하지만 `--db-path` 옵션으로 다른 경로 지정 가능(QA 시 합성 DB 사용).
- **users.name_norm / teams.name_norm 자동 정리는 본 사이클 X.** 도구가 진단·SQL 템플릿만 출력. 자동 정리는 운영 합병 의사결정이 필요해 별도 사이클 검토.
- `--doctor` sub-command는 sys.argv를 일찍(콘솔 초기화 전·트레이 전·sidecar 전) 검사. 도구 모드에서는 colon stdout/stderr 인코딩만 하고 나머지 초기화 skip.
- VSCode 디버깅 모드 — 서버 자동 재시작 불가. qa는 import-time + 합성 DB.

## 산출물 위치

- `backend_changes.md`, `code_review_report.md`, `qa_report.md`: `.claude/workspaces/current/` 직속
- 검증 스크립트: `.claude/workspaces/current/scripts/verify_dedup_phase.py`, `verify_migration_doctor.py`
