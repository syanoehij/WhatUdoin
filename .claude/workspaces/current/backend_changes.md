# backend_changes — 마이그레이션 dedup phase ordering 버그 수정

## 문제
`database.py:_run_phase_migrations()`는 preflight 검사를 **모든 phase 본문보다 먼저 일괄** 실행한다.
`team_phase_5a_projects_dedup_safe_v1`("재시작만으로 안전 dedup → 그 다음 #5 preflight 통과 → #5 인덱스 생성")는
단순히 `PHASES.append` 순서상 #5보다 앞에 등록돼 있을 뿐이라, #5 preflight(`_check_projects_team_name_unique`)가
abort시키면 5a 본문은 실행 기회를 못 얻는다 → "자동 dedup 안전망"이 사실상 죽은 코드.

## 수정 (Candidate 1 — `_PRE_PREFLIGHT_PHASES` 허용 리스트, advisor 확인)
새 phase 본문 추가 없음. `_phase_5a_projects_dedup_safe` 본문 변경 없음. 러너 실행 순서만 고침.

### 변경 파일/함수
**`database.py`만 변경.**

1. **`_PRE_PREFLIGHT_PHASES` 추가** (`_PREFLIGHT_CHECKS` 정의 직후, 기존 L736 부근):
   - `frozenset({"team_phase_5a_projects_dedup_safe_v1"})`.
   - docstring 주석: preflight보다 먼저 실행되는 phase 집합. 계약 — (a) idempotent, (b) preflight가 강제하는 UNIQUE invariant에 의존 금지(오히려 그 invariant 충족이 목적).

2. **`_run_phase_body(name, body)` 헬퍼 추출** (`_run_phase_migrations` 바로 위 신규 함수):
   - 기존 `_run_phase_migrations` 안에 있던 per-phase 격리 트랜잭션 루프 본문(`with get_conn() / isolation_level=None / BEGIN IMMEDIATE / body / _mark_phase_done / COMMIT`, 실패 시 `ROLLBACK` + `RuntimeError`)을 그대로 함수로 옮김. 동작·로그 메시지 동일.
   - pre-preflight 단계와 나머지 단계에서 공유.

3. **`_run_phase_migrations` 분할** (기존 동작 보존 + 단계 삽입):
   - ① `pending = _pending_phases()`; 비면 즉시 반환 — **불변**.
   - ② 백업 1회 (`if not pending: return` 직후·모든 phase 본문 전) — **위치·로직 불변**.
   - ②.5 `pending`을 PHASES 순서 유지하며 분할: `pre_preflight = [(n,b) for (n,b) in pending if n in _PRE_PREFLIGHT_PHASES]`, `rest = [...if n not in...]`. (필터링만, 재정렬 없음.)
   - ②.6 `for name,body in pre_preflight: _run_phase_body(name, body)` — 각자 독립 `with get_conn()` 블록·독립 트랜잭션이라 **마커가 preflight 전에 커밋**됨 (preflight 실패가 5a 마커를 롤백시키지 않음).
   - ③ preflight 일괄 실행 — **불변**. 이제 5a dedup 후 상태에서 돌므로 남은(unsafe) 충돌만 잡음. 충돌 시 경고 누적 + `RuntimeError`.
   - ④ `for name,body in rest: _run_phase_body(name, body)` — 나머지 phase 순차 실행.
   - docstring "동작 순서" 1~5단계로 갱신.

4. **주석 갱신** (`database.py` L1605~1607 부근 — 5a 등록 블록):
   - 기존: "PHASES.append 순서 = 실행 순서. 본 5a 등록은 #5 등록 *위*에 위치해 같은 init_db()에서 dedup → preflight → 인덱스 생성 순서가 보장된다." → 이제 거짓.
   - 신규: 5a는 `_PRE_PREFLIGHT_PHASES`에 등록되어 preflight 앞에서 실행됨이 순서를 보장한다. 단순 PHASES 순서로는 보장 안 됨(러너가 preflight를 모든 phase 본문보다 먼저 일괄 실행하므로). + unsafe 충돌로 preflight가 거부해도 5a 마커는 set 상태로 남고, 운영자가 unsafe row를 수동/migration_doctor로 정리 후 재시작하면 preflight 통과 → #5 진행 — 5a 재실행 불필요(버그 아님).

### 안 건드린 것 (불변 확인)
- 백업 타이밍/로직 — `if not pending: return` 직후 1회, 그대로.
- `_mark_phase_done`, `_append_team_migration_warning`, `team_migration_warnings` 누적 — 그대로.
- per-phase 트랜잭션 래퍼 시맨틱 (`isolation_level=None` + `BEGIN IMMEDIATE` + 수동 COMMIT/ROLLBACK) — `_run_phase_body`로 옮겼을 뿐 동작 동일.
- preflight 실행부 (`_run_preflight_checks`, default isolation_level, 경고 commit 후 raise) — 그대로.
- unsafe 그룹(살아남는 row ≥1) 보존 + `_check_projects_team_name_unique`가 잡아 RuntimeError — 그대로.
- pending=0 → 백업·preflight·phase 본문 전부 skip — 그대로.
- `_phase_5a_projects_dedup_safe` 본문·`_classify_projects_dedup_group`·`_projects_duplicate_groups` — 미변경.

### migration_doctor 영향
없음. `tools/migration_doctor.py`는 `_projects_duplicate_groups` / `_classify_projects_dedup_group` 헬퍼만 직접 호출(L88~92). `_run_phase_migrations`·`_run_phase_body`·`_pending_phases`·`_run_preflight_checks`·`_PRE_PREFLIGHT_PHASES` 어느 것도 참조 안 함 (grep 확인). 별개 진입점.

### 기존 verify 스크립트 회귀
- `scripts/`(active 테스트 디렉토리 = `tests/`)에는 dedup/migration 관련 verify 스크립트 없음. `tests/`의 phaseN_*.spec.js / *.py는 마이그레이션 러너를 import하지 않음.
- 과거 verify 스크립트(`.claude/workspaces/archive/.../verify_dedup_phase.py` 등)는 모두 archive에 있고, phase 본문을 자체 `_run_phase` 래퍼로 직접 호출하지 `_run_phase_migrations`를 안 거침 → 이번 변경과 무관. (archive라 active 회귀 대상도 아님.)

### 스모크
- `python -c "import ast; ast.parse(...)"` → parse OK.
- `python -c "import database; ..."` → import OK, `_PRE_PREFLIGHT_PHASES == frozenset({'team_phase_5a_projects_dedup_safe_v1'})`, `_run_phase_body`/`_run_phase_migrations` callable, 5a가 PHASES에 있고 #5보다 앞 인덱스.

## 서버 재시작
필요. **러너 코드(`database.py`) 변경 — 코드 reload용 재시작.** 스키마 변경·새 phase 본문 추가 없음. 운영 DB는 이미 migration_doctor로 정리됐고(충돌 0건) 모든 phase 마커가 찍혀 있어 `_pending_phases()`가 빈 리스트 → 이 변경이 운영 DB 기동 경로에 닿지 않음. (그래도 QA에 "충돌 0건 DB에서도 정상" 케이스 포함 요청.)

## QA 검증 시나리오 (합성 임시 DB — 실서버 X)
1. **safe-only 충돌 DB**: phase 1·2가 적용된 합성 스키마 + 같은 `(team_id, name_norm)` 그룹에 참조 0건 중복 row → `init_db()`(또는 `_run_phase_migrations` 직접) → `settings`에 `migration_phase:team_phase_5a_...` AND `migration_phase:team_phase_5_...` 마커, `idx_projects_team_name_norm`(또는 실제 인덱스명) 존재, `team_migration_warnings`에 `dedup_projects_auto` 항목. 예외 없음.
2. **unsafe 충돌 DB** (discriminator): 같은 그룹에 `events.project_id`(deleted_at IS NULL)가 2건 이상 참조하는 중복 row → `init_db()` → `RuntimeError`, `migration_phase:team_phase_5a_...` 마커는 **set**(5a가 돌았으나 안전 정리할 게 없어 cleanly return), `migration_phase:team_phase_5_...` 마커는 **미set**, `team_migration_warnings`에 `preflight_projects_team_name` 항목. ※ 5a 마커가 set이 아니면 러너 버그(pre-preflight가 preflight 실패로 롤백된 것).
3. **충돌 0건 DB**: 정상 마이그레이션 → pending이면 백업 → pre-preflight 작업 없음(또는 5a 노옵) → preflight 통과 → 나머지 phase 실행. 회귀 없음.
4. **재호출**: 같은 DB로 `init_db()` 재호출 → `_pending_phases()` 빈 리스트 → 즉시 반환, 백업·preflight·phase 본문 전부 skip. 마커·데이터 불변.
- 기존 `tests/` spec이 깨지지 않는지(특히 import 단계). archive verify 스크립트는 회귀 대상 아님.
