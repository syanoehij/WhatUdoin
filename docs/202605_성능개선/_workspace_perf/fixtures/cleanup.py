"""
M1a-2: 부하 측정용 fixture cleanup 스크립트

목적:
  - test_perf_ 접두어 사용자의 sessions 삭제
  - test_perf_ 접두어 사용자 삭제
  - test_perf_team 팀 삭제
  - session_cookies.json 삭제

안전 가드:
  - 모든 DELETE 문의 WHERE 절에 test_perf_ 접두어 또는 그에 준하는 조건 필수
  - WHERE 누락 DELETE 0건
  - 실패 시 트랜잭션 전체 ROLLBACK (부분 commit 없음)
  - WHATUDOIN_PERF_FIXTURE=allow 환경변수 필수
  - WhatUdoin 서버 종료 상태에서만 실행 (WAL 파일 없음)

events 회수 (M1a-7 보완):
  - events.created_by는 app.py:1740에서 str(user.id)로 server-side 덮어쓰므로
    LIKE 'test_perf_%' 패턴 불가 — locustfile이 부여한 title 접두어로 매칭.
  - DELETE FROM events WHERE title LIKE 'test_perf_evt_%'

TODO (범위 외, 향후 fixture 추가 시 구현):
  - checklists WHERE project LIKE 'test_perf_%'
  - meetings WHERE created_by IN (test_perf_ user ids)  [created_by는 INTEGER FK]
  - attachments (이벤트/체크리스트 연결)
  현재는 해당 영역 fixture가 없으므로 placeholder만 둠

운영 코드 커플링:
  - database.py `users.name` 컬럼 (LIKE 'test_perf_%' 매칭)
  - database.py `sessions.user_id` → users.id FK
  - database.py `teams.name` = 'test_perf_team'

사용법:
  WHATUDOIN_PERF_FIXTURE=allow python cleanup.py
  WHATUDOIN_PERF_FIXTURE=allow WHATUDOIN_DB_PATH=D:/path/to/whatudoin.db python cleanup.py
"""

import os
import sys
from pathlib import Path

PERF_PREFIX    = "test_perf_"
PERF_TEAM_NAME = "test_perf_team"

DEFAULT_DB_PATH = str(Path(__file__).parents[3] / "whatudoin.db")  # D:/Github/WhatUdoin/whatudoin.db
OUTPUT_COOKIES  = Path(__file__).parent / "session_cookies.json"

# baseline 디렉터리는 절대 접근/삭제하지 않음 (코드 흐름상 참조 없음)


# ── 가드 ──────────────────────────────────────────────────────────────────────

def _check_guards() -> str:
    """환경 변수 + WAL 파일 검사. 통과하면 DB 경로 반환, 실패 시 sys.exit."""

    if os.environ.get("WHATUDOIN_PERF_FIXTURE") != "allow":
        print(
            "[ABORT] WHATUDOIN_PERF_FIXTURE=allow 환경변수가 설정되지 않았습니다.\n"
            "  운영 DB 오염 방지를 위해 이 스크립트는 명시적 승인 없이 실행되지 않습니다.\n"
            "  사용 예: WHATUDOIN_PERF_FIXTURE=allow python cleanup.py",
            file=sys.stderr,
        )
        sys.exit(1)

    db_path = os.environ.get("WHATUDOIN_DB_PATH", DEFAULT_DB_PATH)

    wal = Path(db_path + "-wal")
    shm = Path(db_path + "-shm")
    found = [str(p) for p in (wal, shm) if p.exists()]
    if found:
        print(
            f"[ABORT] WAL/SHM 파일이 존재합니다: {found}\n"
            "  WhatUdoin 서버가 실행 중일 가능성이 있습니다.\n"
            "  서버를 종료한 후 다시 실행하세요.",
            file=sys.stderr,
        )
        sys.exit(1)

    return db_path


# ── cleanup 로직 ──────────────────────────────────────────────────────────────

def _cleanup(db_path: str):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")

        # 1. sessions: test_perf_ 사용자 세션만 삭제
        r_sessions = conn.execute(
            "DELETE FROM sessions WHERE user_id IN "
            "(SELECT id FROM users WHERE name LIKE ?)",
            (PERF_PREFIX + "%",),
        )
        print(f"[cleanup] sessions 삭제: {r_sessions.rowcount}행")

        # events: title 접두어 기반 회수 (M1a-7 보완)
        # events.created_by는 app.py:1740에서 str(user.id)로 덮어써짐 → LIKE 불가.
        # locustfile _perf_event_title()이 항상 'test_perf_evt_' 접두어를 부여함.
        # UPDATE payload도 '{title}_updated' 로 prefix 유지 → 어떤 단계에서 멈춰도 회수됨.
        r_events = conn.execute(
            "DELETE FROM events WHERE title LIKE ?",
            ("test_perf_evt_%",),
        )
        print(f"[cleanup] events 삭제: {r_events.rowcount}행")

        # TODO: checklists (test_perf_ fixture가 추가되면 구현)
        # r_checklists = conn.execute(
        #     "DELETE FROM checklists WHERE project LIKE ?", (PERF_PREFIX + "%",)
        # )

        # TODO: meetings (test_perf_ fixture가 추가되면 구현)
        # r_meetings = conn.execute(
        #     "DELETE FROM meetings WHERE created_by IN "
        #     "(SELECT id FROM users WHERE name LIKE ?)", (PERF_PREFIX + "%",)
        # )

        # TODO: attachments (M1a-2 범위 외)

        # 2. users: test_perf_ 접두어 사용자만 삭제
        r_users = conn.execute(
            "DELETE FROM users WHERE name LIKE ?",
            (PERF_PREFIX + "%",),
        )
        print(f"[cleanup] users 삭제: {r_users.rowcount}행")

        # 3. team: test_perf_team만 삭제
        r_team = conn.execute(
            "DELETE FROM teams WHERE name = ?",
            (PERF_TEAM_NAME,),
        )
        print(f"[cleanup] teams 삭제: {r_team.rowcount}행")

        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        conn.close()
        print(f"[ERROR] 트랜잭션 롤백 — 운영 데이터 변경 없음: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        conn.close()


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    db_path = _check_guards()
    print(f"[cleanup] DB 경로: {db_path}")

    _cleanup(db_path)

    # session_cookies.json 삭제 (측정 결과/baseline 파일은 접근하지 않음)
    if OUTPUT_COOKIES.exists():
        OUTPUT_COOKIES.unlink()
        print(f"[cleanup] session_cookies.json 삭제: {OUTPUT_COOKIES}")
    else:
        print("[cleanup] session_cookies.json 없음 (이미 삭제됨)")

    print("[cleanup] 완료. 운영 데이터 오염 없음.")


if __name__ == "__main__":
    main()
