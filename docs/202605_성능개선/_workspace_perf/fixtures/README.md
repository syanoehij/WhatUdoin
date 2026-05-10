# M1a-2: fixture seed / cleanup 절차

## 개요

`seed_users.py`와 `cleanup.py`는 WhatUdoin 부하 측정 전용 fixture 스크립트다.
운영 코드(`app.py`, `database.py` 등)를 수정하지 않고, 측정 전용 계정 50개를 개발 DB에 직접 INSERT한다.

## 운영 코드 커플링 (변경 추적용)

| 항목 | 위치 | 현재 값/방식 |
|------|------|-------------|
| 비밀번호 저장 방식 | `database.get_user_by_password()` | 평문 (WHERE password = ?) |
| sessions 컬럼 | `database.py` CREATE TABLE sessions | id TEXT PK, user_id, created_at, expires_at |
| expires_at 포맷 | `database.create_session()` | `%Y-%m-%d %H:%M:%S` UTC |
| session cookie 이름 | `auth.SESSION_COOKIE` | `"session_id"` |
| users 컬럼 | `database.py` CREATE TABLE users | name, password, role, team_id, is_active |
| teams 컬럼 | `database.py` CREATE TABLE teams | id, name, created_at |

**주의**: `database.py`가 hashed password 방식으로 변경되면 `seed_users.py`의 INSERT도 동일하게 수정해야 한다.

---

## 환경 변수

| 변수 | 필수 | 기본값 | 설명 |
|------|------|--------|------|
| `WHATUDOIN_PERF_FIXTURE` | **필수** | (없음) | `allow`로 설정해야 실행 허용 |
| `WHATUDOIN_DB_PATH` | 선택 | `D:\Github\WhatUdoin\whatudoin.db` | DB 파일 경로 override |

---

## 실행 절차 (권장 순서)

```
1. 서버 종료 (VSCode 디버그 중지)
2. DB 백업 (M1a-3에서 도입 예정)
3. seed 실행
4. 서버 시작
5. 부하 측정 (Locust — M1a-5 ~ M1a-7)
6. 서버 종료
7. cleanup 실행
```

**seed 실행 (Windows):**
```
set WHATUDOIN_PERF_FIXTURE=allow
python _workspace/perf/fixtures/seed_users.py
```

**seed 실행 (bash/PowerShell):**
```bash
WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/fixtures/seed_users.py
# PowerShell:
$env:WHATUDOIN_PERF_FIXTURE="allow"; python _workspace\perf\fixtures\seed_users.py
```

**cleanup 실행:**
```bash
WHATUDOIN_PERF_FIXTURE=allow python _workspace/perf/fixtures/cleanup.py
# PowerShell:
$env:WHATUDOIN_PERF_FIXTURE="allow"; python _workspace\perf\fixtures\cleanup.py
```

---

## session_cookies.json

seed 완료 후 `_workspace/perf/fixtures/session_cookies.json`에 기록된다.

형식:
```json
{
  "test_perf_001": {
    "session_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "expires_at": "2026-05-16 12:34:56"
  },
  ...
}
```

**Locust 시나리오(M1a-5)에서의 사용 예고:**
- `on_start()`에서 `session_cookies.json`을 읽어 자신의 VU 번호에 해당하는 `session_id` 로드
- `self.client.cookies.set("session_id", session_id)` 로 cookie 주입
- `/api/login` 호출 없이 rate limit 우회 가능 (§15 세션 쿠키 사전 생성 권장 방식)
- session 유효기간: 7일 (측정 윈도우 동안 만료 없음)

---

## 안전 가드

두 스크립트 모두 다음 가드를 실행 시작 즉시 검사한다:

1. **환경변수 가드**: `WHATUDOIN_PERF_FIXTURE=allow` 미설정 시 ABORT
2. **WAL 파일 가드**: `whatudoin.db-wal` 또는 `whatudoin.db-shm` 존재 시 ABORT (서버 실행 중 의심)

`cleanup.py`의 모든 DELETE 문은 `WHERE name LIKE 'test_perf_%'` 또는 동등한 조건을 포함한다.
WHERE 누락 DELETE 0건 — 운영 데이터 오염 방지.

---

## 실패 시 복구

cleanup이 실패하거나 측정 중 데이터 손상이 의심되면:

1. 서버 종료
2. `_workspace/perf/baseline_<날짜>/db_snapshot/`에서 DB 파일 세트 복원
   - `whatudoin.db` 덮어쓰기
   - `whatudoin.db-wal`, `whatudoin.db-shm` 존재 시 함께 복원 (세트 단위 유지)
3. 복원 후 `PRAGMA integrity_check;`로 검증

**M1a-3에서 DB snapshot 백업 가드가 도입 예정**이며, 복원 절차 상세는 해당 step README에 명시된다.

---

## 멱등성

- `seed_users.py`: 이미 존재하는 `test_perf_` 계정은 INSERT 생략, 카운트 보고
- 세션은 항상 삭제 후 재생성 (stale expires_at 방지)
- `test_perf_team` 팀: `INSERT OR IGNORE` 로 중복 방지

---

## M1a-2 scope 외 TODO

다음 fixture는 M1a-2 범위 외이며 cleanup.py에 placeholder + TODO 주석으로 표시:

- `events` (test_perf_ 접두어 프로젝트/제목 패턴)
- `checklists`
- `meetings`
- `attachments` (파일 시스템 포함)
