# QA — 그룹 A 보강 사이클

**검증 모드**: import-time + 합성 DB. 서버 재시작 불필요 (VSCode 디버깅 모드).

**검증 스크립트**:
- `.claude/workspaces/current/scripts/verify_dedup_phase.py` (7개 시나리오)
- `.claude/workspaces/current/scripts/verify_migration_doctor.py` (5개 시나리오)

## 자동 dedup phase 본문 — `verify_dedup_phase.py`

각 시나리오는 NamedTemporaryFile로 합성 DB를 만들어 phase 1·2 적용 후 상태를 모방한다
(projects/events/checklists/meetings/project_members/project_milestones/settings 테이블).
phase 본문은 `BEGIN IMMEDIATE` 트랜잭션으로 호출되며, projects ID + warning 누적을 검증.

| 시나리오 | 결과 | 비고 |
|---------|------|------|
| (1) 빈 DB → 노옵 | PASS | DELETE 0건, warning 0건 |
| (2) 안전 그룹 (빈+참조 0) → MIN(id) 유지 | PASS | ids=[104,105,106] → [104], warning 1건 누적, 메시지 정확 |
| (3) 한 row 참조 + 한 row 빈 → 참조 보존 | PASS | (200 참조, 201 빈) → [200], warning 정확 |
| (4) 양쪽 모두 참조 → 정리 X | PASS | (300, 301) 둘 다 보존, warning 0건 |
| (5) 참조 row 2개 + 빈 row 1개 (unsafe) | PASS | 빈 401만 DELETE, 참조 [400,402] 보존 |
| (6) 본문 두 번째 호출 → 노옵 | PASS | warning 추가 누적 없음 (dedup 가드) |
| (7) phase 5 본문이 5a 직후 정상 실행 | PASS | `idx_projects_team_name` 부분 UNIQUE 인덱스 생성 성공 |

**전체 결과**: ALL DEDUP PHASE SCENARIOS PASSED

## 운영자 도구 `migration_doctor` — `verify_migration_doctor.py`

| 시나리오 | 결과 | 비고 |
|---------|------|------|
| (D1) check — 충돌 0건 | PASS | exit 0, "이상 없음" 출력, 3개 섹션(projects/users/teams) 모두 표시 |
| (D2) check — safe + unsafe + users 충돌 | PASS | exit 1, 분류 라벨 정확, name 콤마 split 깨짐 회귀 방지 확인 |
| (D3) fix-projects dry-run | PASS | exit 0, DRY-RUN 라벨, projects row 변경 0 |
| (D4) fix-projects --apply | PASS | exit 0, 백업 1건 생성(`backupDB/whatudoin-migrate-*.db`), safe 정리 + unsafe 보존 |
| (D5) main._is_doctor_invocation argv 패턴 | PASS | 6가지 패턴 모두 기대대로 분기 |

**전체 결과**: ALL MIGRATION_DOCTOR SCENARIOS PASSED

### D5 검증 케이스 상세

| argv | 기대 | 실제 |
|------|------|------|
| `['main.py']` | False | False |
| `['main.py', '--doctor']` | True | True |
| `['main.py', '--doctor', 'check']` | True | True |
| `['main.py', '--doctor', 'fix-projects', '--apply']` | True | True |
| `['main.py', 'doctor', 'check']` | True | True (alias) |
| `['main.py', 'somethingelse', '--doctor']` | False | False (의도된 — 첫 위치만 인정) |

## 이슈 추적

### QA 1차에서 발견된 결함

1. **dedup 검증 스크립트 cp949 인코딩 깨짐** (스크립트 결함, 본 코드 무결함)
   - 패치: `sys.stdout/stderr` UTF-8 wrapping 추가.
2. **doctor 검증 D4: 백업 파일 경로 오인** (스크립트 결함)
   - `backup.run_migration_backup`은 `run_dir/backupDB/` 하위에 생성. 검증 스크립트가
     parent 디렉토리만 검사 → 패치: `os.path.join(tmp_dir, 'backupDB')` 검사로 수정.

### Code Review 1차에서 발견된 결함 (코드 패치 적용됨)

1. **`cmd_fix_projects` apply 모드에서 `BEGIN IMMEDIATE` 트랜잭션 충돌**
   - 원인: default `isolation_level=""`이 DML 직전 자동 BEGIN을 emit.
   - 패치: `conn.isolation_level = None` 추가. QA D4가 정상 동작 확인.
2. **`_diagnose_users_teams`의 GROUP_CONCAT(name) 콤마 split 깨짐**
   - 원인: 이름에 콤마 들어가면 split 결과가 망가짐.
   - 패치: 별도 SELECT로 name dict 매핑. QA D2가 `'kim, a'` 케이스로 회귀 방지 확인.

## 정적 검증

4파일 모두 `ast.parse` 통과:
- `database.py` — 헬퍼 3종 + phase 5a 본문 + PHASES.append 추가.
- `main.py` — `_is_doctor_invocation` / `_run_doctor` + `__main__` 분기 추가.
- `tools/migration_doctor.py` — 신규 파일.
- `WhatUdoin.spec` — datas / hiddenimports 추가.

## 한계 / 미검증 항목

- **실제 PyInstaller 빌드** — 사양 §S3 명시: 사용자가 직접 실행. spec 파일 정합성만
  점검 (구문 + 추가 항목 위치 적절). frozen 환경 동작은 사용자 빌드 후 확인 필요.
- **운영 DB 직접 실행** — VSCode 디버깅 모드 + 서버 재시작 불가 제약으로 운영 DB(현재 메모리에서
  돌아가는 인스턴스)에는 접근 X. 합성 DB 7+5 시나리오로 모든 분기 커버.
- **users/teams.name_norm 자동 정리** — 사양 §"진행 방식"에 따라 본 사이클 범위 외.
  도구 진단 + SQL 템플릿 출력만 검증 (D2).

## 종합

**판정**: 통과. 자동 dedup phase + migration_doctor 양쪽 모두 모든 시나리오 PASS.
서버 재시작 없이 다음 init_db()부터 phase 5a가 자동 적용된다 (마커 `migration_phase:team_phase_5a_projects_dedup_safe_v1`이 settings 테이블에 등록됨).
