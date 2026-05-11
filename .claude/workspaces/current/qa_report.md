## QA 보고서 — 마이그레이션 dedup phase ordering 버그 수정

### 검증 방식
실서버 E2E 불가(서버 꺼짐 + VSCode 디버깅 모드). 버그가 `init_db()` 마이그레이션 러너 순서에 있으므로
합성 임시 DB(`tempfile.mkdtemp` — 루트에 .db 미생성) + `database.DB_PATH`/`_RUN_DIR` monkeypatch +
`database._run_phase_migrations()` 직접 호출로 검증. Playwright 대신 Python 검증 스크립트 사용.

스크립트: `.claude/workspaces/current/scripts/verify_dedup_phase_ordering.py`
실행 로그: `.claude/workspaces/current/scripts/verify_dedup_phase_ordering.log`
실행: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "D:\Program Files\Python\Python312\python.exe" .claude/workspaces/current/scripts/verify_dedup_phase_ordering.py` → EXIT 0, **PASS 25 / FAIL 0**

각 케이스는 "phase 1·2 적용 후" 상태의 최소 합성 스키마(`projects`: team_id·name_norm 컬럼 포함, `events`: project_id)를 만들고,
5a/5 *외* 모든 PHASES 마커를 미리 set(그 phase 본문이 합성 DB에서 안 돌게)한 뒤 러너를 호출한다.

### 통과 ✅
- [x] **case 1 — safe-only 충돌**: 같은 `(team_id=7, name_norm='alpha')`에 참조 0건 중복 row 2개(id 101·102), Beta(103)는 events에서 참조.
  → 5a가 102 hard DELETE(MIN id 101 보존) → preflight 통과 → #5가 `idx_projects_team_name` 생성.
  검증: 예외 없음 / `migration_phase:team_phase_5a_...` set / `migration_phase:team_phase_5_...` set / `idx_projects_team_name` 존재 / projects=[101,103] / `team_migration_warnings`에 `dedup_projects_auto` / `preflight_projects_team_name` 없음.
- [x] **case 2 — unsafe 충돌 (discriminator)**: 같은 `(team_id=9, name_norm='gamma')` 2 row(201·202) 모두 `events.project_id`가 참조 → `_classify_projects_dedup_group`이 `delete=[]` → `safe=False` → 5a 노옵, 둘 다 보존 → preflight가 2건 충돌 감지.
  검증: `RuntimeError` 발생 / **`migration_phase:team_phase_5a_...` set** (5a가 돌긴 돌았음 — pre-preflight 마커가 preflight 실패에도 커밋됨) / `migration_phase:team_phase_5_...` **미set** / projects=[201,202] 둘 다 보존 / `idx_projects_team_name` 미생성 / `team_migration_warnings`에 `preflight_projects_team_name`.
  ※ 이 케이스가 수정의 핵심 검증: 5a가 preflight *앞*에서 실행되고, 그 마커가 preflight 실패와 무관하게 영속화됨.
- [x] **case 3 — 충돌 0건 회귀**: 중복 없는 projects(301·302) → 5a 노옵 → preflight 통과 → #5 인덱스 생성.
  검증: 예외 없음 / 5a·#5 마커 set / 인덱스 존재 / projects 전부 보존 / `dedup_projects_auto` 없음 / preflight 충돌 없음.
- [x] **case 4 — 재호출 skip**: case 3 DB로 `_run_phase_migrations()` 재호출 → `_pending_phases()` 빈 리스트.
  검증: 예외 없음 / 마커 불변 / projects 데이터 불변 / `backupDB/`에 새 백업 파일 0개(pending=0이라 백업 skip).

### 회귀 확인
- `import database, app, tools.migration_doctor` → 모두 OK.
- `database.py` `ast.parse` → OK.
- `pytest tests/test_project_rename.py tests/test_html_table_to_gfm.py` → **12 passed, 2 failed**.
  2건 실패(`test_rename_project_*`)는 **사전 결함** — `git stash` 후 master HEAD(`ac98650`)에서 동일하게 2 failed 확인. 옛 픽스처 DB에 `projects.team_id` 컬럼이 없어 `sqlite3.OperationalError: no such column: team_id`. 본 수정과 무관.
- `tests/`의 phaseN_*.spec.js / *.py는 마이그레이션 러너를 import하지 않음. 과거 verify 스크립트(`.claude/workspaces/archive/.../verify_dedup_phase.py` 등)는 phase 본문을 자체 `_run_phase` 래퍼로 직접 호출(`_run_phase_migrations` 미경유) → 영향 없음, 또한 archive라 active 회귀 대상 아님.

### 실패 ❌
없음.

### 서버 재시작
**필요.** `database.py`(마이그레이션 러너) 코드 변경 — 코드 reload용 재시작. 스키마 변경·새 phase 본문 추가 없음.
운영 DB는 이미 migration_doctor로 정리됨(충돌 0건) + 모든 phase 마커 set → `_pending_phases()` 빈 리스트라
재시작 시 본 수정 경로(백업/preflight/phase 본문)에 진입조차 안 함 — 운영 DB 기동을 막을 위험 없음.

### 최종 판정
**통과.** 25/25 PASS. 사전 결함 2건 외 회귀 없음.
