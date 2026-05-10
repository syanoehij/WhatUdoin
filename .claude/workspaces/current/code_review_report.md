# 코드 리뷰 — 그룹 A 보강 사이클

리뷰 범위: 사양 §"reviewer 항목" — (a) phase 순서 (b) 안전 조건 정밀도 (c) 도구
dry-run 보호 (d) main.py sub-command 분기.

## (a) phase 순서

**검증 대상**: `team_phase_5a_projects_dedup_safe_v1`이 `team_phase_5_projects_unique_v1`
*앞에* 등록되어 같은 init_db() 호출에서 5a → 5 순서로 실행되는지.

**확인**: `database.py` 라인 순서 기준
- L1820 `PHASES.append(("team_phase_5a_projects_dedup_safe_v1", ...))`
- L1907 `PHASES.append(("team_phase_5_projects_unique_v1", ...))`

`_pending_phases()`는 `for name, body in PHASES`로 등록 순서를 보존하므로 5a → 5
순서 보장. `_run_phase_migrations` → preflight 검사는 **모든 phase 본문보다 앞**에서
한 번 돌고, 5a가 진입 후에는 conn 트랜잭션이 커밋된 상태로 5에 진입(같은 init_db
콜 안에서). preflight은 5a *전*에 한 번만 돌기 때문에, 5a가 정리한 그룹은 같은 콜의
다음 init_db()까지는 preflight에 노출되지 않으나, **본 사이클은 5a가 먼저 돌고 그 후
5의 인덱스 생성이 충돌 row 없이 성공**하는 것이 핵심 — 이 시나리오는 보장됨.

> 단, 만약 회사 운영 DB가 처음부터 unsafe 그룹(참조 ≥2)을 가지면, preflight이 5a보다
> 먼저 돌기 때문에 5a가 안전 정리할 기회 없이 거부된다. 사양 §"unsafe 그룹"이 명시한
> 정책 그대로이며, 운영자는 도구로 진단 후 처리. **기능 의도와 일치**.

**판정**: PASS.

## (b) 안전 조건 정밀도

### 거짓 양성(잘못 정리) 방지

`_project_reference_count`가 카운트하는 9가지:
1. `events.project_id` (deleted_at IS NULL)
2. `checklists.project_id` (deleted_at IS NULL)
3. `events.project = name AND project_id IS NULL` (deleted_at IS NULL)
4. `checklists.project = name AND project_id IS NULL` (deleted_at IS NULL)
5. `events.trash_project_id` — 컬럼 존재 시 무조건 카운트
6. `checklists.trash_project_id`
7. `meetings.trash_project_id`
8. `project_members.project_id`
9. `project_milestones.project_id`

각 카운트는 컬럼 존재 여부(`_has_col`) 사전 검사 — 합성 DB에서 누락된 컬럼이 있어도
`OperationalError` 안 발생. 안전.

> trash_project_id는 휴지통 메타이지만 사양 §"안전 조건"이 보수적 카운트 요구.
> 코드도 `deleted_at` 가드 없이 무조건 카운트 → **사양 일치**.

### 거짓 음성(정리 안 됨) 가능성

- `events.project = name`은 정확 일치. NFC casefold가 적용된 `name_norm`은 비교 안 함
  → 대소문자 차이로 잔존 문자열 참조가 안 잡힐 수 있음. 그러나 phase 1 백필 이후
  `events.project`도 원본 그대로 유지되므로 `name == projects.name` 정확 일치가 정상
  운영. **현재 운영 정책과 부합**.

### 분류 규칙

`_classify_projects_dedup_group`:
- `referenced` 비어있지 않으면 → `keep = sorted(referenced)`, `delete = sorted(unreferenced)`,
  `safe = bool(delete)`. 참조 0건 row만 자동 정리. 참조 row가 1건이든 2건이든 모두 보존.
- `referenced` 비어있으면 → `keep = [min(ids)]`, 나머지 모두 DELETE → safe = True.

> 참조 row가 2+개 + 참조 0건 row가 1+개인 경우: 5a가 참조 0건만 정리하지만, 참조 row
> 2개로 인덱스 충돌은 그대로 남음 → 이후 #5 preflight이 거부. 사양 §"unsafe 그룹"의
> 의도된 동작.

**판정**: PASS.

## (c) 도구 dry-run 보호

### read-only mode

`cmd_check`, `cmd_fix_projects(apply=False)` 모두 `file:...?mode=ro` URI로 연결.
SQLite read-only 모드는 INSERT/UPDATE/DELETE/CREATE 거부. 코드 실수로 DML이 섞여도
`OperationalError: attempt to write a readonly database`로 즉시 차단.

### --apply 백업 가드

`run_migration_backup` 호출 후 백업 파일 경로를 stdout 출력. 백업 실패 시 exit 2로
정리 거부.

### 트랜잭션 처리 (수정 적용됨)

리뷰 1차에서 발견된 **블로커 결함**: `cmd_fix_projects` apply 모드에서 default
`isolation_level=""`이 DML 직전 자동 BEGIN을 emit하므로 직접 `BEGIN IMMEDIATE` 호출 시
`OperationalError: cannot start a transaction within a transaction` 발생 가능.

**패치 적용**:
```python
conn = sqlite3.connect(db_path, timeout=5)
conn.isolation_level = None  # 매뉴얼 트랜잭션
```

이로써 `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`이 명시적으로 동작. phase 러너의
패턴과 동일.

### 충돌 그룹 표시

`_diagnose_users_teams`는 1차에서 `GROUP_CONCAT(name)`을 콤마로 split — name 안에
콤마가 있으면 깨짐. **패치 적용**: GROUP_CONCAT으로 ids만 받고, 이름은 별도
`SELECT id, name FROM {tbl} WHERE id IN (?,?,...)`로 정확히 받아 dict 매핑. NULL
name도 빈 문자열로 대체.

**판정**: PASS (수정 후).

## (d) main.py sub-command 분기

### 분기 위치

`if __name__ == "__main__":` 블록 진입 직후, `multiprocessing.freeze_support()` 다음
줄에서 `_is_doctor_invocation(sys.argv)` 검사. 이전 모든 콘솔/sidecar 토글 검사보다 앞.

### 일반 모드 영향

분기 통과(`--doctor` 미설정) 시 기존 코드 100% 그대로 실행. import 추가도 함수 안에
지연 import (`from tools.migration_doctor import main as doctor_main`)이라 일반 모드
시작 시간 영향 0.

### 도구 모드 부작용

`_run_doctor`는:
- `WHATUDOIN_BASE_DIR`, `WHATUDOIN_RUN_DIR` 환경변수만 주입.
- 콘솔 인코딩만 처리(UTF-8).
- frozen 모드에서 `AllocConsole`은 호출하지만 `_HWND_REF` 갱신·트레이 생성·
  watcher 스레드 모두 호출 안 함.
- `app.py` import 안 함 → DB 자동 init 안 함, FastAPI 라우트 등록 안 함, lifespan 실행 안 함.

도구는 자체 sqlite3.connect로 진입.

### argv 인식

- `python main.py` → False, 일반 모드.
- `python main.py --doctor` → True, args=`[]` → 기본 `check`.
- `python main.py --doctor check` → True.
- `python main.py --doctor fix-projects --apply` → True.
- `python main.py doctor check` → True (alias).
- `python main.py somethingelse --doctor` → False (의도된 — 도구 인자는 첫 위치만 인정).

**판정**: PASS.

## 종합

| 항목 | 1차 | 패치 후 |
|------|----|--------|
| (a) phase 순서 | PASS | PASS |
| (b) 안전 조건 정밀도 | PASS | PASS |
| (c) 도구 dry-run 보호 | **블로커: BEGIN IMMEDIATE 트랜잭션 충돌** + 마이너: GROUP_CONCAT split 깨짐 | PASS (패치 적용) |
| (d) main.py sub-command | PASS | PASS |

**최종 판정**: 통과 (블로커 1건 수정 후).

## 패치 변경 파일

- `tools/migration_doctor.py`:
  - `cmd_fix_projects`: `conn.isolation_level = None` 추가.
  - `_diagnose_users_teams`: name 추출을 별도 SELECT로 분리.
