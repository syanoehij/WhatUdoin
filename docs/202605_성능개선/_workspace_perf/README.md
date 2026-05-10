# WhatUdoin 성능 측정 인프라

M1a~M1d 부하 측정 및 최적화 절차 전체를 관리하는 문서.
개별 fixture 단위 절차는 `_workspace/perf/fixtures/README.md` 참조.

---

## 디렉터리 구조

```
_workspace/perf/
├── README.md                        <- 이 문서 (전체 측정 라이프사이클)
├── locust/                          <- locust 시나리오 파일 (M1a-5)
├── fixtures/
│   ├── README.md                    <- fixture 단위 절차 (seed/cleanup 상세)
│   ├── seed_users.py                <- test_perf_001~050 계정 + 세션 생성
│   ├── cleanup.py                   <- test_perf_ 접두어 데이터 정리
│   └── session_cookies.json         <- seed 후 생성 (locust 쿠키 주입용)
├── scripts/
│   ├── snapshot_db.py               <- 측정 전 DB 오프라인 snapshot (M1a-3)
│   └── restore_db.py                <- snapshot에서 운영 DB 복원 (M1a-3)
└── baseline_2026-05-09/
    └── db_snapshot/                 <- whatudoin.db snapshot (snapshot_db.py 출력)
```

baseline 디렉터리 이름은 step 시작 시점(`2026-05-09`) 고정.
실제 측정 시점이 다를 경우 `baseline_<측정일>/` 추가 운용 가능.

---

## 환경 변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `WHATUDOIN_PERF_FIXTURE` | **필수** | (없음) | `allow`로 설정해야 모든 fixture/snapshot/restore 스크립트 실행 허용 |
| `WHATUDOIN_DB_PATH` | 선택 | `D:\Github\WhatUdoin\whatudoin.db` | 운영 DB 파일 경로 override |
| `WHATUDOIN_PERF_BASELINE_DIR` | 선택 | `_workspace/perf/baseline_2026-05-09/` | baseline 디렉터리 override |

---

## 전체 측정 절차 (M1a baseline)

```
1. 서버 종료 (VSCode 디버그 중지)
2. snapshot_db.py     — 운영 DB 오프라인 백업 (안전판)
3. seed_users.py      — test_perf_001~050 계정 + 세션 쿠키 생성
4. 서버 시작
5. 부하 측정          — locust (M1a-5 ~ M1a-7)
6. 서버 종료
7. cleanup.py         — test_perf_ 접두어 데이터 삭제
8. (필요 시) restore_db.py --confirm-overwrite
```

**Windows (PowerShell) 실행 예:**

```powershell
# 2. snapshot
$env:WHATUDOIN_PERF_FIXTURE="allow"
python _workspace/perf/scripts/snapshot_db.py

# 3. seed
python _workspace/perf/fixtures/seed_users.py

# 서버 시작 후 측정 (사용자 수동)

# 7. cleanup
python _workspace/perf/fixtures/cleanup.py
```

**bash 실행 예:**

```bash
WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/scripts/snapshot_db.py
WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/fixtures/seed_users.py
# ... 측정 ...
WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/fixtures/cleanup.py
```

---

## snapshot_db.py — 측정 전 DB 백업

`_workspace/perf/scripts/snapshot_db.py`

### 동작

1. `WHATUDOIN_PERF_FIXTURE=allow` 환경변수 확인 — 미설정 시 ABORT
2. 서버 종료 상태 전제 확인 — WAL 모드에서 `whatudoin.db-wal` / `whatudoin.db-shm`이 남아 있으면 세트로 함께 복사
3. target 디렉터리 결정
   - 기본: `baseline_2026-05-09/db_snapshot/`
   - 이미 존재하면 timestamp suffix 자동 생성: `db_snapshot_<HHMMSS>/`
   - **무조건 덮어쓰기 금지** — 기존 snapshot 보호
4. `shutil.copy2()` 로 `whatudoin.db` / `whatudoin.db-wal` / `whatudoin.db-shm` 세트 복사
5. `PRAGMA integrity_check;` 결과 `ok` 확인

### 주의

서버가 완전히 종료된 상태에서만 사용한다. WAL 모드에서는 서버 종료 후에도
`whatudoin.db-wal` / `whatudoin.db-shm`이 남을 수 있으므로, 존재하는 sidecar는
본문 DB와 같은 snapshot 디렉터리에 함께 복사한다. sidecar가 없으면 복사 대상
"세트"가 `.db` 하나로 줄어드는 것은 정상 동작이다.

### 사용법

```powershell
$env:WHATUDOIN_PERF_FIXTURE="allow"
python _workspace/perf/scripts/snapshot_db.py

# baseline 디렉터리 override
$env:WHATUDOIN_PERF_BASELINE_DIR="D:/Github/WhatUdoin/_workspace/perf/baseline_2026-05-10"
python _workspace/perf/scripts/snapshot_db.py
```

---

## restore_db.py — 운영 DB 복원

`_workspace/perf/scripts/restore_db.py`

### 동작

1. `WHATUDOIN_PERF_FIXTURE=allow` 환경변수 확인 — 미설정 시 ABORT
2. 서버 종료 상태 전제 확인
3. `--confirm-overwrite` 인자 확인 — 없으면 ABORT
4. 현재 운영 DB 세트를 사이드카로 보관: `*.before-restore-<timestamp>`
   (우발적 손실 방지 — 복원 전 현재 상태 보존)
5. snapshot에서 `whatudoin.db` / `whatudoin.db-wal` / `whatudoin.db-shm` 세트 복사
6. `PRAGMA integrity_check;` 결과 `ok` 확인

### 복원 절차

```powershell
# 1. 서버 종료 확인
# 2. 기본 snapshot 경로에서 복원
$env:WHATUDOIN_PERF_FIXTURE="allow"
python _workspace/perf/scripts/restore_db.py --confirm-overwrite

# 특정 snapshot 디렉터리 지정
python _workspace/perf/scripts/restore_db.py --confirm-overwrite --snapshot-dir _workspace/perf/baseline_2026-05-09/db_snapshot_142030

# 3. 서버 시작 후 정상 동작 확인
```

### 사이드카 위치

복원 전 현재 DB는 운영 DB와 같은 디렉터리에 보관된다:
`whatudoin.db.before-restore-<YYYYMMDD_HHMMSS>`

---

## 안전 정책 요약

모든 스크립트는 실행 즉시 다음 가드를 순서대로 통과해야 동작한다:

1. **환경변수 가드**: `WHATUDOIN_PERF_FIXTURE=allow` 미설정 시 exit code 1 ABORT
2. **서버 종료 전제**: WAL sidecar가 있어도 abort하지 않고 세트로 복사/복원한다. 실행 전 서버 종료 여부를 확인한다.
3. **overwrite 가드** (restore 전용): `--confirm-overwrite` 없으면 ABORT

추가 안전판:
- `cleanup.py` — `test_perf_` 접두어 매칭 외 DELETE 0건
- `restore_db.py` — 덮어쓰기 전 현재 DB를 사이드카로 보관
- `snapshot_db.py` — 기존 snapshot 있으면 timestamp suffix로 별도 생성 (기존 보호)

---

## snapshot vs backup.py (M1b) 의 차이

### M1a-3 snapshot (이 스크립트)

- **방식**: 오프라인 `shutil.copy2` — 서버 종료 상태에서만 실행
- **목적**: M1a baseline 측정의 안전판 — fixture seed/cleanup 실패, 측정 중 데이터 손상 대비
- **WAL 상태**: sidecar가 있으면 `.db`/`.db-wal`/`.db-shm` 세트 복사, 없으면 단일 `.db` 파일 복사
- **시점**: M1a baseline 측정 시작 직전 (fixture seed 전)

### M1b WAL 적용 전 백업 (backup.py)

- **방식**: `sqlite3` backup API (`run_backup()`) — 온라인/오프라인 모두 가능
- **목적**: WAL 모드 활성화(`PRAGMA journal_mode=WAL`) 직전 별도 가드
- **WAL 상태**: WAL 활성화 전이므로 Journal 모드 상태의 DB 백업
- **시점**: M1b-8 WAL 모드 활성화 직전

두 절차는 별개의 안전판이다. M1a에도 fixture 데이터가 들어가기 때문에 M1a baseline 
측정 자체에도 동일 수준의 백업이 필요하다 (계획서 §15 "측정 전 DB 백업 가드" 항목).

---

## fixtures/README.md 와 이 문서의 역할 구분

| 문서 | 범위 |
|------|------|
| `fixtures/README.md` | fixture 단위 절차 — seed_users.py / cleanup.py 상세, session_cookies.json 형식, 운영 코드 커플링 추적 |
| `perf/README.md` (이 문서) | 전체 측정 라이프사이클 — 측정 절차 순서, snapshot/restore 사용법, 환경변수 표, 안전 정책, M1b backup과의 관계 |

---

## 후속 TODO

- M1b-15 WAL 복원 drill: 이 README 에 결과 갱신 예정
