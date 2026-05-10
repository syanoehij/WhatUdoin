## 코드 리뷰 보고서 — 팀 기능 그룹 A #1 (DB 마이그레이션 인프라)

### 리뷰 대상 파일
- `backup.py` — `run_migration_backup()` 신규 (L27~40)
- `database.py` — Phase 인프라 섹션 (L631~812), `_run_phase_migrations()` 호출 추가 (L498~501)
- `.claude/workspaces/current/scripts/verify_phase_infra.py` — 검증 스크립트 (8 case)

### 사양서 exit criteria 정합성

| 사양서 exit criterion | 코드 위치 | 검증 case | 정합성 |
|----------------------|----------|-----------|--------|
| 빈 DB + phase 0개 → 인프라만 정상, 서버 시작 OK | `_run_phase_migrations` L758~760 (early return) | case 1 | OK |
| 재시작 시 마커 그대로 → phase 미재실행 | `_is_phase_done` L649~656, `_pending_phases` L669~678 | case 2 | OK |
| Phase 실패 주입 → 트랜잭션 롤백 + 서버 시작 거부 + stdout 어느 phase 실패 명확 | L797~812 (manual BEGIN/ROLLBACK + RuntimeError + `phase {name!r} FAILED` 로그) | case 3 | OK |
| 백업 파일이 정해진 명명, 미적용 마이그 있을 때만 1회 | `run_migration_backup` (`whatudoin-migrate-{ts}.db`) + L762~770 | case 1(0개) + case 2(1개) | OK |
| preflight 골격 호출, 검사 함수 0개여도 통과 | L772~787 + L732~744 | case 8 | OK |
| 마커 강제 삭제 후 재실행 안전 | (idempotent body 가정) | case 4 | OK |

### advisor 권고 반영 검증

| 권고 | 코드 위치 | 반영 여부 |
|------|----------|-----------|
| Phase 트랜잭션 격리 — `isolation_level=None` + 수동 `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` | L799~808 | OK. 코드베이스 전역 `get_conn()` 시맨틱 미변경(러너 안의 conn에만 적용) — surgical 원칙 준수. |
| 마커 기록을 phase 본문과 동일 트랜잭션 | `_mark_phase_done(conn, name)` L659~666 + L804 | OK. `set_setting()`이 별도 conn 여는 함정 회피. |
| 경고 누적 race-safe + 중복 방지 | `_append_team_migration_warning` L693~729 | OK. read→list 검증→`(category, message)` dedup→write 모두 호출자 conn 안. |
| 백업 실패 시 마이그레이션 진행 거부 | L767~770 | OK. `RuntimeError` raise. |
| preflight 충돌 시 경고 commit 후 raise | L776~787 (with 블록 종료 후 raise) | OK. case 7에서 settings 영속화 확인. |

### 트랜잭션 매개체 정합성 검토 (핵심)

`get_conn()` 컨텍스트 매니저는 `yield conn` 후 `conn.commit()`을 호출한다(L823). phase 러너에서 `isolation_level=None` + 수동 트랜잭션 사용 시 두 경로 모두 안전한지 검증:

- **정상 경로**: `body OK` → `_mark_phase_done` → `conn.execute("COMMIT")` → with 정상 종료 → `conn.commit()` 호출. `isolation_level=None`이고 활성 트랜잭션이 없는 상태에서 `commit()`은 no-op (Python sqlite3 documented behavior). **안전**.
- **실패 경로**: `body raise` → `conn.execute("ROLLBACK")` → `raise` → with 블록이 예외 전파로 종료, `yield` 다음 줄(`conn.commit()`)은 **실행되지 않음** (contextmanager는 finally만 실행). ROLLBACK이 commit으로 무효화되지 않음. **안전**.

이는 advisor 권고가 코드에 정확히 반영되었음을 의미한다.

### 차단(Blocking) 결함

없음.

### 경고(Warning) 결함

없음.

### 인지(Acknowledged) — 본 사이클 범위 외 사전 조건

다음 두 가지는 backend가 "범위 외"로 명시했고 본 #1 인프라와 무관하지만, 다음 사이클에서 누락되지 않도록 명시 기록:

1. **`database.py:254` — `projects.deleted_at` 빈 DB 첫 시작 OperationalError**
   - `_migrate(projects, ...)` 호출(L261)보다 먼저 L254에서 `projects.deleted_at`이 참조됨.
   - 빈 DB로 처음 `init_db()`를 돌리면 OperationalError. 운영 DB가 이미 있는 환경에서는 무문제이고 본 #1 인프라(init_db 종료 후 호출)와 무관하다.
   - 검증 스크립트는 운영 DB(`whatudoin.db`)를 임시 디렉토리로 복사해서 회피.
   - **권고**: 다음 사이클(#2 이후)이 빈 DB 부트스트랩을 다룰 때 함께 수정.

2. **`database.py:165, 367` — `settings` CREATE TABLE 정의 중복**
   - 두 곳에서 `CREATE TABLE IF NOT EXISTS settings (...)`가 동일하게 선언.
   - 동작상 무해(IF NOT EXISTS) 하지만 향후 settings 스키마 변경 시 두 곳을 동시 수정해야 하는 함정.
   - **권고**: 본 사이클은 손대지 않음. 별도 정리 사이클에서 한 곳으로 통합.

### 추가 검토 메모 (advisor 지적사항)

1. **백업 retention 정합성**: `whatudoin-migrate-*.db`는 `cleanup_old_backups`의 glob `whatudoin-*.db`(backup.py:47)에 매칭되어 90일 후 삭제됨. 그 시점에 phase 마커는 `settings` 테이블에 영구 보존되므로 "이 phase가 적용된 적이 있다"는 사실은 유지된다. 백업 파일은 시점 데이터 복구용, 마커가 영구 기록 — **의도적이고 정합한 설계**.

2. **Phase body의 idempotency 요구**: 본 사이클은 "마커 강제 삭제 후 재실행 안전"을 case 4로 검증했으나, 이는 `_phase_idempotent` 본문이 `CREATE TABLE IF NOT EXISTS`로 작성되었기 때문에 성립한다. 사양서가 이미 "Phase 1 = 컬럼·테이블 추가 (idempotent)"를 요구하므로 모순 없으나, **#2 이후 phase 작성자가 idempotency를 깰 수 있는 SQL(예: `ALTER TABLE ADD COLUMN` 무조건 실행, `INSERT` without conflict resolution)을 쓰지 않도록** PHASES 등록 패턴 docstring 또는 review 시점 강제가 필요.

3. **다중 프로세스 race**: 본 앱은 단일 FastAPI 프로세스 운영이라 `_pending_phases` 검사~phase body 실행 사이에 다른 프로세스가 동일 phase를 돌릴 여지가 없다. 만약 향후 멀티프로세스/HA 구성이 도입되면 phase body의 `BEGIN IMMEDIATE`로는 부족하므로 별도 advisory lock 또는 phase 마커의 race-safe upsert가 필요. **현 시점 unblocking 사항 아님**.

### 통과 항목 (체크리스트)

- [x] DB 스키마 변경: `_migrate` 패턴과 충돌 없음 (Phase 인프라는 상위 layer로 분리, _migrate 자체 수정 없음)
- [x] 새 컬럼 추가: 본 사이클에서 컬럼 추가 없음 (인프라만)
- [x] 하위호환 유지: 기존 컬럼·테이블 변경 없음
- [x] 파일 경로: 백업은 `_RUN_DIR / "backupDB"` (기존 `_backup_dir` 재사용) — 정합
- [x] `get_conn()` 사용: 정상 (정상/실패 경로 모두 분석 완료)
- [x] SQL 파라미터화: 모든 신규 쿼리 `?` placeholder 사용 (L652~654, L662~666, L699~702, L725~729)
- [x] f-string SQL 직접 삽입 없음
- [x] 백업 실패 시 마이그레이션 거부 (데이터 보호)
- [x] stdout 로그 prefix 통일 (`[WhatUdoin][migration]`)

### 최종 판정

**통과** (차단 결함 없음, 경고 없음, 사전 조건 2건 인지 기록).

QA 진행 가능.
