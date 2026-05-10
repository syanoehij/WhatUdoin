## QA 보고서 — 팀 기능 그룹 A #1 (DB 마이그레이션 인프라)

본 사이클은 백엔드 인프라만 추가하고 라우트·UI 변경이 없으므로 Playwright E2E는 비대상.
사용자 지시한 (a) verify_phase_infra.py 재실행, (b) 운영 DB 복사본 회귀 smoke,
(c) Phase 실패 주입 검증 3종을 핀포인트로 수행한다.

서버 재시작 필요 없음 — 모든 검증은 임시 디렉토리에서 import-time으로 수행.

### 검증 환경
- Python: `D:\Program Files\Python\Python312\python.exe`
- 작업 디렉토리: `D:\Github\WhatUdoin`
- 운영 DB 보호: 모든 검증은 `tempfile.mkdtemp()`로 격리된 임시 디렉토리에서 수행.
  운영 `whatudoin.db`는 (b)에서 `shutil.copy2`로 사본만 사용, 원본 무수정.

---

### (a) verify_phase_infra.py 재실행 — 통과 ✅

```
"D:\Program Files\Python\Python312\python.exe" .claude/workspaces/current/scripts/verify_phase_infra.py
```

**결과**: ALL PASS: 8/8

stdout 전문:

```
[case 1] OK: empty DB, no phases → no backup, no error
[WhatUdoin][migration] pre-migration backup: C:\Users\Lumen\AppData\Local\Temp\wud_phase_test_1zo3augk\backupDB\whatudoin-migrate-20260510T215748230739.db
[WhatUdoin][migration] phase 'demo_phase_1' OK
[case 2] OK: marker persists, phase not re-run, no extra backup
[WhatUdoin][migration] pre-migration backup: C:\Users\Lumen\AppData\Local\Temp\wud_phase_test_q29w9rmp\backupDB\whatudoin-migrate-20260510T215748262090.db
[WhatUdoin][migration] phase 'phase_failing' FAILED: RuntimeError('intentional failure for test')
[case 3] OK: phase failed → RuntimeError, marker not set, DDL rolled back, backup exists
[WhatUdoin][migration] pre-migration backup: ...whatudoin-migrate-20260510T215748280122.db
[WhatUdoin][migration] phase 'idem_phase' OK
[WhatUdoin][migration] pre-migration backup: ...whatudoin-migrate-20260510T215748309597.db
[WhatUdoin][migration] phase 'idem_phase' OK
[case 4] OK: marker deletion → phase safely re-runs (idempotent)
[case 5] OK: warnings dedup works (2 entries after 3 appends with 1 dup)
[case 6] OK: normalize_name handles NFC + casefold + None
[WhatUdoin][migration] pre-migration backup: ...whatudoin-migrate-20260510T215748344155.db
[WhatUdoin][migration] preflight conflict: fake users.name_norm collision: 'alice' vs 'Alice'
[case 7] OK: preflight conflict → startup refused + warning persisted
[WhatUdoin][migration] pre-migration backup: ...whatudoin-migrate-20260510T215748370698.db
[WhatUdoin][migration] phase 'noop_only' OK
[case 8] OK: empty _PREFLIGHT_CHECKS passes through

ALL PASS: 8/8
```

**case → exit criteria 매핑 재확인**:

| case | exit criterion | 결과 |
|------|----------------|------|
| 1 | 빈 DB + PHASES=[] → 백업 0개, RuntimeError 없음 | PASS |
| 2 | 재시작 시 마커 그대로, phase 미재실행, 백업 추가 없음 | PASS |
| 3 | phase 실패 → RuntimeError + 마커 미기록 + DDL 롤백 + 백업 존재 | PASS |
| 4 | 마커 강제 삭제 후 재실행 idempotent | PASS |
| 5 | 경고 누적 dedup (3 append → 2 entries) | PASS |
| 6 | normalize_name NFC + casefold + None | PASS |
| 7 | preflight 충돌 → 서버 시작 거부 + 경고 영속화 | PASS |
| 8 | _PREFLIGHT_CHECKS=[] → 정상 통과 | PASS |

**stdout 로그 사양 적합성**:
- `[WhatUdoin][migration] pre-migration backup: {path}` — 백업 1회 명확
- `[WhatUdoin][migration] phase {name!r} OK` — 성공 명확
- `[WhatUdoin][migration] phase {name!r} FAILED: {exc!r}` — 실패한 phase 이름 + 원인 명확 (사양서 exit criterion #3 만족)
- `[WhatUdoin][migration] preflight conflict: {msg}` — preflight 충돌 메시지 명확

---

### (b) 운영 DB 복사본 회귀 smoke test — 통과 ✅

운영 DB(`whatudoin.db`)를 임시 디렉토리로 복사 후 PHASES=[]·_PREFLIGHT_CHECKS=[]
상태에서 init_db()가 진정한 no-op인지 검증.

스크립트: `.claude/workspaces/current/scripts/smoke_prod_db_noop.py` (신규)

```
"D:\Program Files\Python\Python312\python.exe" .claude/workspaces/current/scripts/smoke_prod_db_noop.py
```

stdout:

```
[SMOKE] init_db() succeeded: True
[SMOKE] migration backups created: []
[SMOKE] new settings keys: []
[SMOKE] deleted settings keys: []
[SMOKE] migration-related new keys: []
[SMOKE] PHASES = []
[SMOKE] _PREFLIGHT_CHECKS = []

[SMOKE] PASS: PHASES=[] truly no-op against production DB copy
```

**검증 항목**:
- [x] init_db() RuntimeError 없이 정상 종료
- [x] backupDB/whatudoin-migrate-*.db 파일 0개 (PHASES=[]면 백업 안 떠야 함 → 만족)
- [x] settings 테이블에 `migration_phase:*` / `team_migration_warnings` 키 미생성
- [x] 기존 settings 키 손상·삭제 0개 (PRE/POST 비교)
- [x] PHASES, _PREFLIGHT_CHECKS 모듈 전역 상태가 빈 리스트 유지 (다른 테스트가 오염시키지 않음)

→ **본 #1 인프라가 운영 DB에서 진정한 no-op임이 확인됨**. 실제 서버를 재시작해도 PHASES=[]이므로 무동작이다.

---

### (c) Phase 실패 주입 검증 — 통과 ✅

사용자 요청: "가짜 phase 1개를 PHASES에 등록하고 일부러 raise → 트랜잭션 롤백 + 서버 시작 거부 + stdout 로그가 사양 exit criteria를 만족하는지"

이는 verify_phase_infra.py의 case 3로 정확히 커버되어 (a)에서 함께 검증됨.

**case 3 stdout (재인용)**:
```
[WhatUdoin][migration] pre-migration backup: ...whatudoin-migrate-20260510T215748262090.db
[WhatUdoin][migration] phase 'phase_failing' FAILED: RuntimeError('intentional failure for test')
[case 3] OK: phase failed → RuntimeError, marker not set, DDL rolled back, backup exists
```

**case 3 코드 검증 항목** (verify_phase_infra.py L87~123):
- [x] init_db()가 RuntimeError raise (서버 시작 거부 = uvicorn boot 실패)
- [x] RuntimeError 메시지에 `'phase_failing'` 포함 (어느 phase 실패인지 명확)
- [x] 백업 파일은 1개 생성됨 (실패 *전*에 미리 떴어야 함 → 데이터 보호)
- [x] phase 마커 미기록 (`SELECT FROM settings WHERE key = 'migration_phase:phase_failing'` → None)
- [x] phase 본문의 `CREATE TABLE _will_be_rolled_back`이 sqlite_master에 없음 → **DDL 롤백 검증** (advisor 권고가 정확히 반영되었음을 의미)

**사양서 exit criterion #3 매핑**:
> 인위적 phase 실패 주입 시 트랜잭션 롤백 + 서버 시작 거부 + stdout에 어느 phase에서 실패했는지 명확

→ 3가지 모두 만족.

---

### 회귀 확인

본 사이클은 라우트/UI 변경 없음 → Playwright phase1~phase4 회귀 테스트 비대상.
운영 DB 복사본에 대한 init_db() smoke가 회귀를 대신한다 — (b)에서 PASS 확인.

---

### 산출물

- `.claude/workspaces/current/scripts/verify_phase_infra.py` (기존, 8 case 모두 PASS)
- `.claude/workspaces/current/scripts/smoke_prod_db_noop.py` (qa 신규 추가, prod DB no-op 검증)
- 본 보고서: `.claude/workspaces/current/qa_report.md`

### 서버 재시작 요청

**요청 없음**. 본 사이클은 PHASES=[]이므로 실 서버 재시작 시 phase 인프라가 처음 가동되더라도 무동작 (smoke test로 확인 완료). 사용자가 서버를 재시작해도 동작 변화 없음.

다만 **#2 이후 사이클에서 실제 phase 본문을 등록**하면, 그 사이클의 qa는 import-time 검증과 함께 실 서버 재시작도 사용자에게 요청해야 한다 (phase 인프라가 실 부트 경로를 처음 거치게 되므로).

---

### 최종 판정

**통과** (3/3 검증 항목 모두 PASS, 차단 없음, 회귀 없음).

사양서 exit criteria 6개 모두 검증 완료:
1. 빈 DB + phase 0개 → 인프라만 정상, 서버 시작 OK (case 1, smoke)
2. 재시작 시 마커 그대로, phase 미재실행 (case 2)
3. Phase 실패 주입 → 트랜잭션 롤백 + 서버 시작 거부 + stdout 명확 (case 3)
4. 백업 파일이 정해진 명명, 미적용 마이그 있을 때만 생성 (case 1 + case 2)
5. preflight 골격 호출, 검사 함수 0개여도 정상 통과 (case 8)
6. 마커 강제 삭제 후 재실행 안전 (case 4)
