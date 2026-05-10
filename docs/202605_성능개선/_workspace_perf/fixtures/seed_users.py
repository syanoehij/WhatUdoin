"""
M1a-2: 부하 측정용 fixture 사용자 seed 스크립트

목적:
  - test_perf_001 ~ test_perf_050 계정 50개를 whatudoin.db에 직접 INSERT
  - test_perf_team 팀 1개를 멱등 생성
  - 각 계정의 session을 sessions 테이블에 INSERT하고 결과를
    _workspace/perf/fixtures/session_cookies.json 에 기록

운영 코드 커플링 (변경 시 여기도 수정 필요):
  - database.py `users` 컬럼: id, name, password(평문), role, team_id, is_active, created_at
  - database.py `sessions` 컬럼: id(TEXT PK), user_id, created_at, expires_at
  - database.py `teams` 컬럼: id, name, created_at
  - 비밀번호는 현재 평문 저장 (database.get_user_by_password: WHERE password = ?)
    만약 hashed password로 전환되면 이 스크립트의 INSERT도 맞춰야 함
  - auth.SESSION_COOKIE = "session_id"
  - create_session(): expires_at = UTC + 30days (editor), format: "%Y-%m-%d %H:%M:%S"

사전 조건:
  - WHATUDOIN_PERF_FIXTURE=allow 환경변수 필수
  - WhatUdoin 서버가 종료된 상태 (WAL 파일 없음)

사용법:
  WHATUDOIN_PERF_FIXTURE=allow python seed_users.py
  WHATUDOIN_PERF_FIXTURE=allow WHATUDOIN_DB_PATH=D:/path/to/whatudoin.db python seed_users.py
"""

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 상수 ──────────────────────────────────────────────────────────────────────

PERF_PASSWORD  = "WuPerfTest!2026"
PERF_PREFIX    = "test_perf_"
PERF_TEAM_NAME = "test_perf_team"
USER_COUNT     = 50

# session 유효 기간: 부하 측정 윈도우 동안 만료되지 않도록 7일
SESSION_DAYS   = 7

DEFAULT_DB_PATH = str(Path(__file__).parents[3] / "whatudoin.db")  # D:/Github/WhatUdoin/whatudoin.db
OUTPUT_COOKIES  = Path(__file__).parent / "session_cookies.json"


# ── 가드 ──────────────────────────────────────────────────────────────────────

def _check_guards() -> str:
    """환경 변수 + WAL 파일 검사. 통과하면 DB 경로 반환, 실패 시 sys.exit."""

    # 1. 허용 환경변수 가드
    if os.environ.get("WHATUDOIN_PERF_FIXTURE") != "allow":
        print(
            "[ABORT] WHATUDOIN_PERF_FIXTURE=allow 환경변수가 설정되지 않았습니다.\n"
            "  운영 DB 오염 방지를 위해 이 스크립트는 명시적 승인 없이 실행되지 않습니다.\n"
            "  사용 예: WHATUDOIN_PERF_FIXTURE=allow python seed_users.py",
            file=sys.stderr,
        )
        sys.exit(1)

    # 2. DB 경로 결정
    db_path = os.environ.get("WHATUDOIN_DB_PATH", DEFAULT_DB_PATH)

    # 3. WAL/SHM 파일 존재 검사 (서버 종료 확인)
    wal = Path(db_path + "-wal")
    shm = Path(db_path + "-shm")
    found = [str(p) for p in (wal, shm) if p.exists()]
    if found:
        print(
            f"[ABORT] WAL/SHM 파일이 존재합니다: {found}\n"
            "  WhatUdoin 서버가 실행 중일 가능성이 있습니다.\n"
            "  서버를 종료한 후 다시 실행하세요.\n"
            "  (§4-1 read-then-write 충돌 회피 정책)",
            file=sys.stderr,
        )
        sys.exit(1)

    return db_path


# ── seed 로직 ─────────────────────────────────────────────────────────────────

def _upsert_team(conn: sqlite3.Connection) -> int:
    """test_perf_team 멱등 생성 후 id 반환"""
    conn.execute(
        "INSERT OR IGNORE INTO teams (name) VALUES (?)",
        (PERF_TEAM_NAME,),
    )
    row = conn.execute(
        "SELECT id FROM teams WHERE name = ?", (PERF_TEAM_NAME,)
    ).fetchone()
    return row[0]


def _existing_perf_users(conn: sqlite3.Connection) -> dict:
    """기존 test_perf_ 계정 {name: id} 반환"""
    rows = conn.execute(
        "SELECT name, id FROM users WHERE name LIKE ?",
        (PERF_PREFIX + "%",),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _seed_users(conn: sqlite3.Connection, team_id: int, existing: dict) -> dict:
    """
    missing 계정만 INSERT.
    반환: {username: user_id} (기존 + 신규 포함)
    """
    result = dict(existing)
    inserted = 0

    for i in range(1, USER_COUNT + 1):
        name = f"{PERF_PREFIX}{i:03d}"
        if name in existing:
            continue
        cur = conn.execute(
            "INSERT INTO users (name, password, role, team_id, is_active) VALUES (?, ?, 'editor', ?, 1)",
            (name, PERF_PASSWORD, team_id),
        )
        result[name] = cur.lastrowid
        inserted += 1

    # team_id heal: 기존 계정도 team_id 동기화
    conn.execute(
        "UPDATE users SET team_id = ? WHERE name LIKE ?",
        (team_id, PERF_PREFIX + "%"),
    )
    print(f"[seed] 사용자: 기존 {len(existing)}개, 신규 INSERT {inserted}개, 합계 {len(result)}개")
    return result


def _seed_sessions(conn: sqlite3.Connection, users: dict) -> dict:
    """
    test_perf_ 사용자의 기존 세션 삭제 후 신규 세션 50개 INSERT.
    반환: {username: {"session_id": ..., "expires_at": ...}}
    """
    # 기존 세션 정리 (항상 재생성)
    deleted = conn.execute(
        "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE ?)",
        (PERF_PREFIX + "%",),
    ).rowcount
    if deleted:
        print(f"[seed] 기존 세션 {deleted}개 삭제 후 재생성")

    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    ).strftime("%Y-%m-%d %H:%M:%S")

    cookies = {}
    for username, user_id in sorted(users.items()):
        session_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (session_id, user_id, expires_at),
        )
        cookies[username] = {
            "session_id": session_id,
            "expires_at": expires_at,
        }

    print(f"[seed] 세션 {len(cookies)}개 INSERT, expires_at={expires_at}")
    return cookies


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    db_path = _check_guards()

    print(f"[seed] DB 경로: {db_path}")
    print(f"[seed] 비밀번호: {PERF_PASSWORD}")
    print(f"[seed] 세션 유효기간: {SESSION_DAYS}일")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        conn.execute("BEGIN")

        team_id = _upsert_team(conn)
        print(f"[seed] team '{PERF_TEAM_NAME}' id={team_id}")

        existing = _existing_perf_users(conn)
        users = _seed_users(conn, team_id, existing)
        cookies = _seed_sessions(conn, users)

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        print(f"[ERROR] 트랜잭션 롤백: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()

    # session_cookies.json 기록
    OUTPUT_COOKIES.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_COOKIES, "w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2, ensure_ascii=False)
    print(f"[seed] session_cookies.json 기록: {OUTPUT_COOKIES}")
    print("[seed] 완료. 다음 단계: 서버 시작 → 부하 측정 → 서버 종료 → cleanup.py 실행")


if __name__ == "__main__":
    main()
