# 팀 기능 그룹 A #1 — backend 변경 일지

본 #1은 phase 본문 SQL을 추가하지 않는다. phase를 안전하게 돌리는 인프라
(백업·마커·트랜잭션·경고·preflight 골격)만 제공한다. PHASES / _PREFLIGHT_CHECKS는
빈 리스트로 둔다 — #2 이후 사이클에서 채운다.

S2와 S3은 S1 위에 얹히는 헬퍼·확장 포인트이고 구현이 짧아 1회 backend 호출에서
함께 처리했다(사양서 §"step 분할 기준"이 허용).

---

## 핵심 결정 사항

### 1) 백업 파일 위치 및 명명
- **위치**: 기존 `backupDB/` 디렉토리 재사용 (별도 디렉토리 생성하지 않음).
- **파일명**: `whatudoin-migrate-{YYYYMMDDTHHMMSSffffff}.db`
  - `whatudoin-` prefix는 기존 일일 백업과 공유 → `cleanup_old_backups`의 glob `whatudoin-*.db`(backup.py:31)에 자연 매칭되어 90일 retention 정책에 자동 포함됨.
  - `-migrate-` infix로 일반 일일 백업(`whatudoin-{YYYYMMDD-HHMMSS}.db`)과 구분.
  - microsecond까지 포함하여, 같은 초에 두 번 실행되어도 파일명 충돌 없음 (case 4 검증 항목).
  - **콜론 미포함**: Windows 파일명 호환을 위해 ISO 8601 표기법은 압축형(콜론·하이픈 분리자 일부 생략)을 사용. docstring에 "timestamp suffix"로 명시했고 strict ISO 8601이 아니라는 사실을 적시함.
- **사유**: 사양서가 "기존 backupDB/ 재사용 또는 새 명세 둘 다 허용"이라고 기술. 새 디렉토리를 만들면 retention 로직(`cleanup_old_backups`)을 별도로 처리해야 하고, 운영자도 두 곳을 관리해야 한다. prefix 공유로 기존 운영 파이프라인을 그대로 재사용하는 것이 surgical change 원칙에 부합.

### 2) 자료구조 — 마커 / 경고
- **Phase 마커**: `settings` 테이블에 `migration_phase:{phase_name}` 키 = 완료 ISO8601 timestamp(UTC).
  - 별도 `migration_history` 테이블 신설 대신 기존 `settings` KV를 재사용. 본 인프라가 phase별로 1줄만 필요하고 기존 마커 패턴(`fix_mijijeong_all_v1` 등 line 249, 343)과 일관됨.
- **경고 누적**: `settings.team_migration_warnings` 단일 행에 JSON 배열.
  - 각 항목: `{"category": str, "message": str, "at": ISO8601}`.
  - 같은 (category, message) 쌍이 이미 있으면 append하지 않음(중복 방지).
  - read → append → write 모두 호출자의 단일 conn 트랜잭션에서 수행 → race-safe.

### 3) Preflight 검사 골격
- 모듈 전역 `_PREFLIGHT_CHECKS: list[Callable]` (확장 포인트).
  - 시그니처: `(conn) -> list[str]` (충돌 메시지 목록, 빈 리스트면 통과).
  - 0개여도 정상 통과 (exit criterion #5).
  - 1개 이상 충돌 발견 시 → 모든 메시지를 `team_migration_warnings`에 누적 → RuntimeError로 서버 시작 거부.

### 4) Phase 트랜잭션 격리 (advisor 권고 반영)
- Python sqlite3 default `isolation_level=""`는 DDL(CREATE/ALTER/DROP) 직전에 implicit COMMIT을 호출한다. 이로 인해 `body(conn)`이 raise해도 이미 영속화된 DDL이 남아 case 3(롤백 검증) 실패 + 실제 phase #2+ 부분 적용 위험.
- **해결**: phase 러너 안의 conn에만 `conn.isolation_level = None` 적용 + `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`을 수동 발행. 코드베이스 전역 `get_conn()` 시맨틱은 건드리지 않음(surgical change 원칙).
- **부수 효과**: phase body가 DDL+DML 혼합이어도 모두 단일 트랜잭션 → 부분 적용 불가능.

### 5) 마커 기록은 phase 본문과 동일 트랜잭션 (advisor 권고 반영)
- 기존 `set_setting()`은 자체적으로 `get_conn()`을 새로 열어 별도 트랜잭션이 됨 → 본문↔마커 드리프트 위험.
- 내부 헬퍼 `_mark_phase_done(conn, name)` / `_is_phase_done(conn, name)`로 호출자의 conn(트랜잭션)을 그대로 사용하도록 분리.

---

## S1: 자동 백업 + Phase 마커 + 트랜잭션 래퍼

### 변경 파일
- `backup.py` — `run_migration_backup(db_path, run_dir) -> Path` 추가 (line 28~42).
- `database.py` — Phase 인프라 섹션 추가 (line 631 ~ line 811, `_migrate()` 뒤 ~ `@contextmanager get_conn()` 앞).
  - 상수: `_MIGRATION_LOG_PREFIX`, `_PHASE_MARKER_KEY_PREFIX`, `_TEAM_MIGRATION_WARNINGS_KEY`
  - 확장 포인트: `PHASES: list = []`, `_PREFLIGHT_CHECKS: list = []`
  - 내부 헬퍼: `_is_phase_done`, `_mark_phase_done`, `_pending_phases`, `_append_team_migration_warning`, `_run_preflight_checks`, `normalize_name`
  - 진입점: `_run_phase_migrations()`
- `database.py` `init_db()` 마지막에 `_run_phase_migrations()` 호출 추가 (L498~L501, with get_conn 블록 종료 직후).
- **isolation_level asymmetry 의도적**: phase 러너만 `isolation_level=None`. preflight 블록은 DML만 다루므로 default 유지. 코드에 인라인 코멘트로 의도 명시.

### 요약
- pending phase 0개면 즉시 반환 → 백업·preflight·러너 모두 skip (exit criterion #1).
- pending phase ≥1개일 때만 백업 1회 실행 (exit criterion #4).
- 백업 실패 시 마이그레이션 진행 거부 → RuntimeError (데이터 보호 우선).
- 각 phase는 독립 connection의 격리 트랜잭션. 본문 OK → 마커 기록 → COMMIT. 본문 raise → ROLLBACK + RuntimeError → 서버 시작 거부.
- 모든 stdout 로그는 `[WhatUdoin][migration]` prefix로 통일 (기존 `[WhatUdoin]` 스타일 따름).

### 검증
`.claude/workspaces/current/scripts/verify_phase_infra.py` 8 케이스 모두 통과.

```
"D:\Program Files\Python\Python312\python.exe" .claude/workspaces/current/scripts/verify_phase_infra.py
```

S1 관련 케이스:
- case 1: PHASES=[] → 백업 0개, RuntimeError 없음 ✓
- case 2: 가짜 phase 1개 등록 → 첫 실행 시 백업 1개 + phase 본문 1회 + 마커 기록. 두 번째 init_db()에서는 phase 본문 미실행, 백업 추가 없음 ✓
- case 3: 본문이 raise → RuntimeError + 마커 미기록 + body의 CREATE TABLE이 롤백되어 sqlite_master에 없음 ✓
- case 4: 마커 강제 DELETE 후 init_db() 재호출 → phase 본문 idempotent하게 재실행, 백업 1회 더 (microsecond 분리로 파일명 충돌 없음) ✓

### 다음 step 인계
S2 작업도 동일 호출에서 처리됨 (아래).

---

## S2: 경고 누적 로그 + name normalize 헬퍼

### 변경 파일
- `database.py` — Phase 인프라 섹션 안에 추가:
  - `normalize_name(s) -> str` (NFC + casefold)
  - `_append_team_migration_warning(conn, category, message)` (race-safe append, 중복 방지)

### 요약
- `normalize_name`: `unicodedata.normalize("NFC", str(s)).casefold()`. None은 `""` 반환. 한글 NFD/NFC 차이와 알파벳/Unicode 대소문자(예: `MÜLLER` ↔ `müller`) 모두 흡수.
- `_append_team_migration_warning`: 호출자 conn 안에서 settings의 JSON을 read → list 검증 → (category, message) 중복 체크 → append → write. 별도 connection을 열지 않으므로 호출자 트랜잭션과 일관성 유지.

### 검증
- case 5: 같은 (category="preflight", message="X") 2회 + 다른 카테고리 1회 append → 결과 2건 (중복 1건 제거) ✓
- case 6: NFC vs NFD 한글 동일 결과, `Alice`/`alice`/`MÜLLER`/`müller` 모두 동일, None → "" ✓

### 다음 step 인계
S3 작업도 동일 호출에서 처리됨 (아래).

---

## S3: Phase 4 UNIQUE preflight 검사 골격

### 변경 파일
- `database.py` — Phase 인프라 섹션 안에 추가:
  - `_PREFLIGHT_CHECKS: list = []` (확장 포인트)
  - `_run_preflight_checks(conn) -> list` (등록된 모든 검사 실행, 충돌 메시지 합치기)
- `_run_phase_migrations()` 안에 preflight 호출 단계 (백업 직후, phase 실행 직전).

### 요약
- 본 사이클에서는 검사 함수 0개 등록. Phase 4 UNIQUE 제약 추가 시(예: `users.name_norm` UNIQUE) 후속 사이클이 `_PREFLIGHT_CHECKS.append(_check_users_name_norm_unique)` 형태로 함수를 추가한다.
- 검사 함수 시그니처: `(conn) -> list[str]` (충돌 메시지 목록).
- 검사 함수 자체가 raise해도 충돌로 처리(서버 시작 거부) — 진단용 메시지 함께 누적.
- 충돌 1건 이상 → 모두 `team_migration_warnings`에 카테고리 `"preflight"`로 누적 → with 블록 종료 후(경고 commit 보장) RuntimeError 발생.

### 검증
- case 7: 가짜 검사 함수 등록 + pending phase 1개 → init_db()가 RuntimeError raise, settings.team_migration_warnings에 충돌 메시지 영속화 ✓
- case 8: `_PREFLIGHT_CHECKS=[]` + pending phase 1개 → 정상 통과, phase 본문 실행 ✓

### 다음 step 인계
- 본 사이클은 여기서 종료. reviewer → qa로 인계.
- **#2 이후 사이클이 사용할 등록 패턴**:
  ```python
  # database.py 모듈 레벨 (예: PHASES 정의 직후)
  def _phase_2_add_users_name_norm(conn):
      conn.execute("ALTER TABLE users ADD COLUMN name_norm TEXT")
      conn.execute("UPDATE users SET name_norm = ...")  # 백필
  PHASES.append(("users_name_norm_v1", _phase_2_add_users_name_norm))

  def _check_users_name_norm_unique(conn):
      rows = conn.execute("...GROUP BY name_norm HAVING COUNT(*) > 1").fetchall()
      return [f"duplicate name_norm: {r['name_norm']}" for r in rows]
  _PREFLIGHT_CHECKS.append(_check_users_name_norm_unique)
  ```

---

## 사양서 exit criteria 매핑

| exit criterion | 검증 case | 결과 |
|----------------|-----------|------|
| 빈 DB 첫 시작 → phase가 아직 없으면 인프라만 정상 동작, 서버 시작 성공 | case 1 | PASS |
| 재시작 시 마커 그대로 → phase 본문 다시 안 돌고 정상 시작 | case 2 | PASS |
| 인위적 phase 실패 주입 → 트랜잭션 롤백 + 서버 시작 거부 + stdout에 어느 phase 실패인지 명확 | case 3 | PASS (`[WhatUdoin][migration] phase 'phase_failing' FAILED: ...`) |
| 백업 파일이 정해진 명명으로 미적용 마이그레이션이 있을 때만 생성 | case 1 (없을 때 0개) + case 2 (있을 때 1회) | PASS |
| preflight 골격 호출, 충돌 검사 함수 0개여도 정상 통과 | case 8 | PASS |
| Phase 마커 강제 삭제 후 재실행 — 인프라가 다시 도는 것 자체는 안전 | case 4 | PASS (idempotent 본문이므로 안전, 백업도 1회 더 떠서 흔적 남음) |

추가 보강:
- preflight 충돌 시 거부 + 경고 누적: case 7 PASS
- 경고 누적 race-safe + 중복 방지: case 5 PASS
- normalize_name NFC + casefold: case 6 PASS

---

## 알려진 사전 조건 / 본 사이클 범위 외

- **`init_db()` 자체의 빈 DB 문제(database.py:254)**: `projects.deleted_at` 컬럼이 `_migrate(projects, ...)` 호출(line 261)보다 먼저 line 254에서 사용된다. 빈 DB로 처음 init_db()를 돌리면 OperationalError. 본 사이클 범위 밖이며 reviewer가 인지만. 검증 스크립트는 운영 DB(`whatudoin.db`)를 임시 디렉토리로 복사해서 회피한다.
- **settings 테이블 정의 중복(database.py:165, 367)**: 사양서 §"코드 구조"에서 reviewer 인지 항목으로 명시. 본 사이클은 손대지 않음.
- **VSCode 디버깅 모드**: 서버 자동 재시작 불가. 본 작업은 import-time 검증으로만 종결. 실 서버 재시작 시 phase 인프라가 처음 가동되는데 PHASES=[]이므로 무동작 → 영향 없음. qa가 실서버 검증을 원하면 사용자에게 재시작 요청.

## 추가 검증: 운영 DB regression smoke test

import-time 격리 검증과 별개로, 실제 운영 DB(`whatudoin.db`)를 임시 디렉토리로
복사한 뒤 `init_db()`를 한 번 호출하여 PHASES=[]일 때 진정한 no-op인지 확인.

```
"D:\Program Files\Python\Python312\python.exe" -c "
import os, shutil, tempfile, sqlite3
from pathlib import Path
ROOT = Path('D:/Github/WhatUdoin')
tmp = Path(tempfile.mkdtemp())
shutil.copy2(ROOT / 'whatudoin.db', tmp / 'whatudoin.db')
os.environ['WHATUDOIN_RUN_DIR'] = str(tmp)
import sys; sys.path.insert(0, str(ROOT))
import database; database.init_db()
# backupDB/whatudoin-migrate-*.db 0개, settings에 migration_phase:* / team_migration_warnings 키 미생성 확인
"
```

결과: `migration backups created: []`, `new migration-related settings keys: []` →
**SMOKE TEST PASS: PHASES=[] truly no-op against production DB copy**.

## 산출물

- `backup.py` (변경): L28~42 `run_migration_backup` 추가
- `database.py` (변경): L631~811 phase 인프라, L498~501 `_run_phase_migrations()` 호출
- `.claude/workspaces/current/scripts/verify_phase_infra.py` (신규): 8 케이스 검증 스크립트, 모두 PASS
