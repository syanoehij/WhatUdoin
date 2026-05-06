import os
import secrets
import sqlite3
import string
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

# PyInstaller 번들 시 exe 옆 디렉토리, 개발 시 소스 파일 디렉토리
_RUN_DIR = Path(os.environ.get("WHATUDOIN_RUN_DIR", Path(__file__).parent))
DB_PATH  = str(_RUN_DIR / "whatudoin.db")


def init_db():
    with get_conn() as conn:
        # ── events ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                team_id INTEGER,
                project TEXT,
                description TEXT,
                location TEXT,
                assignee TEXT,
                all_day INTEGER DEFAULT 0,
                start_datetime TEXT NOT NULL,
                end_datetime TEXT,
                created_by TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                source TEXT DEFAULT 'manual',
                meeting_id INTEGER,
                bound_checklist_id INTEGER
            )
        """)
        # ── teams ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── users ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                password TEXT NOT NULL DEFAULT '',
                role TEXT DEFAULT 'editor',
                team_id INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── meetings ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                team_id INTEGER,
                created_by INTEGER NOT NULL,
                is_team_doc INTEGER DEFAULT 1,
                is_public INTEGER DEFAULT 0,
                team_share INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 기존 DB에 누락 컬럼 추가
        for _col, _def in [
            ("is_team_doc", "INTEGER DEFAULT 1"),
            ("is_public",   "INTEGER DEFAULT 0"),
            ("team_share",  "INTEGER DEFAULT 0"),
        ]:
            try:
                conn.execute(f"ALTER TABLE meetings ADD COLUMN {_col} {_def}")
            except Exception:
                pass
        # ── meeting_histories ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_histories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                edited_by INTEGER NOT NULL,
                edited_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── checklist_histories ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checklist_histories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checklist_id INTEGER NOT NULL,
                content TEXT NOT NULL,
                edited_by TEXT NOT NULL,
                edited_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── projects ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                color TEXT,
                start_date TEXT,
                end_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _migrate(conn, "projects", [
            ("start_date", "TEXT"),
            ("end_date",   "TEXT"),
            ("is_active",  "INTEGER DEFAULT 1"),
            ("memo",       "TEXT"),
            ("is_private", "INTEGER DEFAULT 0"),
        ])
        # ── pending_users ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                password TEXT NOT NULL,
                memo TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── user_ips ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_ips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                ip_address TEXT NOT NULL,
                type TEXT DEFAULT 'history',
                last_seen TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── sessions ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── settings (마이그레이션 체크에서 사용하므로 먼저 생성) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # ── 기존 테이블 마이그레이션 ──
        _migrate(conn, "events", [
            ("project",              "TEXT"),
            ("assignee",             "TEXT"),
            ("all_day",              "INTEGER DEFAULT 0"),
            ("team_id",              "INTEGER"),
            ("meeting_id",           "INTEGER"),
            ("kanban_status",        "TEXT"),
            ("priority",             "TEXT DEFAULT 'normal'"),
            ("is_active",            "INTEGER DEFAULT 1"),
            ("kanban_hidden",        "INTEGER DEFAULT 0"),
            ("done_at",              "TEXT DEFAULT NULL"),
            ("event_type",           "TEXT DEFAULT 'schedule'"),
            ("recurrence_rule",      "TEXT DEFAULT NULL"),
            ("recurrence_end",       "TEXT DEFAULT NULL"),
            ("recurrence_parent_id", "INTEGER DEFAULT NULL"),
            ("parent_event_id",      "INTEGER DEFAULT NULL"),
            ("bound_checklist_id",   "INTEGER DEFAULT NULL"),
        ])
        # 기존 done 상태 일정에 done_at 백필
        if _table_exists(conn, "events"):
            conn.execute(
                "UPDATE events SET done_at = updated_at WHERE kanban_status = 'done' AND done_at IS NULL"
            )
            # 기존 이벤트에 event_type 백필 (NULL → 'schedule')
            conn.execute(
                "UPDATE events SET event_type = 'schedule' WHERE event_type IS NULL"
            )
        _migrate(conn, "users", [
            ("password",            "TEXT NOT NULL DEFAULT ''"),
            ("team_id",             "INTEGER"),
            ("is_active",           "INTEGER DEFAULT 1"),
            ("created_at",          "TEXT DEFAULT CURRENT_TIMESTAMP"),
            ("avr_enabled",         "INTEGER DEFAULT 0"),
            ("mcp_token_hash",      "TEXT"),
            ("mcp_token_created_at", "TEXT"),
        ])
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_mcp_token_hash "
            "ON users(mcp_token_hash) WHERE mcp_token_hash IS NOT NULL"
        )
        _migrate(conn, "sessions", [
            ("expires_at", "TEXT"),
        ])
        _migrate(conn, "meetings", [
            ("meeting_date", "TEXT"),
        ])
        # ── 휴지통 soft-delete 컬럼 마이그레이션 ──
        _migrate(conn, "events", [
            ("deleted_at", "TEXT DEFAULT NULL"),
            ("deleted_by", "TEXT DEFAULT NULL"),
            ("is_public",  "INTEGER DEFAULT NULL"),
        ])
        # 기존 이벤트 is_public=1(마이그레이션 기본값) → NULL(프로젝트 연동)으로 1회 초기화
        if _table_exists(conn, "events") and not conn.execute("SELECT 1 FROM settings WHERE key='ev_is_pub_reset_v1'").fetchone():
            conn.execute("UPDATE events SET is_public = NULL WHERE deleted_at IS NULL")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ev_is_pub_reset_v1', '1')")
        # 완료된 일정(is_active=0)은 항상 외부 비공개(is_public=0) — 1회 초기화
        if _table_exists(conn, "events") and not conn.execute("SELECT 1 FROM settings WHERE key='ev_done_pub_reset_v1'").fetchone():
            conn.execute("UPDATE events SET is_public = 0 WHERE is_active = 0 AND deleted_at IS NULL")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ev_done_pub_reset_v1', '1')")
        _migrate(conn, "checklists", [
            ("deleted_at", "TEXT DEFAULT NULL"),
            ("deleted_by", "TEXT DEFAULT NULL"),
            ("team_id",    "INTEGER DEFAULT NULL"),
            ("is_public",  "INTEGER DEFAULT 0"),
            ("is_locked",  "INTEGER NOT NULL DEFAULT 0"),
            ("is_active",  "INTEGER DEFAULT 1"),
        ])
        # 미지정 체크리스트(project 없음)는 외부 비공개 고정 — 1회 초기화
        if _table_exists(conn, "checklists") and not conn.execute("SELECT 1 FROM settings WHERE key='ck_unset_priv_reset_v1'").fetchone():
            conn.execute("UPDATE checklists SET is_public = 0 WHERE (project IS NULL OR project = '') AND deleted_at IS NULL")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ck_unset_priv_reset_v1', '1')")
        # '미지정' 문자열로 잘못 저장된 project 값을 빈 문자열로 정리 — 1회성
        if not conn.execute("SELECT 1 FROM settings WHERE key='ck_fix_mijijeong_project_v1'").fetchone():
            conn.execute("UPDATE checklists SET project = '' WHERE project = '미지정'")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ck_fix_mijijeong_project_v1', '1')")
        _migrate(conn, "meetings", [
            ("deleted_at", "TEXT DEFAULT NULL"),
            ("deleted_by", "TEXT DEFAULT NULL"),
        ])
        _migrate(conn, "projects", [
            ("deleted_at", "TEXT DEFAULT NULL"),
            ("deleted_by", "TEXT DEFAULT NULL"),
            ("team_id",    "INTEGER DEFAULT NULL"),
        ])
        # 삭제된 프로젝트를 참조하는 활성 체크리스트/이벤트의 project 필드 정리
        # (프로젝트 삭제 시 체크리스트 이동 로직 추가 이전 데이터 호환)
        if _table_exists(conn, "checklists"):
            conn.execute("""
                UPDATE checklists SET project = ''
                WHERE project != '' AND deleted_at IS NULL
                  AND project IN (SELECT name FROM projects WHERE deleted_at IS NOT NULL)
            """)
        if _table_exists(conn, "events"):
            conn.execute("""
                UPDATE events SET project = NULL
                WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL
                  AND project IN (SELECT name FROM projects WHERE deleted_at IS NOT NULL)
            """)
        # 레거시 soft-delete 이벤트 team_id=NULL 보강 (deleted_by → users.team_id 매핑)
        if _table_exists(conn, "events") and not conn.execute("SELECT 1 FROM settings WHERE key='ev_trash_teamid_backfill_v1'").fetchone():
            conn.execute("""
                UPDATE events
                   SET team_id = (SELECT u.team_id FROM users u WHERE u.name = events.deleted_by)
                 WHERE deleted_at IS NOT NULL
                   AND team_id IS NULL
                   AND deleted_by IS NOT NULL
                   AND EXISTS (SELECT 1 FROM users u WHERE u.name = events.deleted_by AND u.team_id IS NOT NULL)
            """)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ev_trash_teamid_backfill_v1', '1')")
        # ── 휴지통 프로젝트 그룹 컬럼 마이그레이션 ──
        _migrate(conn, "events", [
            ("trash_project_id", "INTEGER NULL"),
        ])
        _migrate(conn, "checklists", [
            ("trash_project_id", "INTEGER NULL"),
        ])
        _migrate(conn, "meetings", [
            ("trash_project_id", "INTEGER NULL"),
        ])
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_trash_project "
            "ON events(trash_project_id, deleted_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checklists_trash_project "
            "ON checklists(trash_project_id, deleted_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_meetings_trash_project "
            "ON meetings(trash_project_id, deleted_at)"
        )
        # 1회성 백필: 기존 휴지통 이벤트/체크리스트를 삭제된 프로젝트에 연결
        if not conn.execute("SELECT 1 FROM settings WHERE key='trash_project_backfill_v1'").fetchone():
            conn.execute("""
                UPDATE events
                   SET trash_project_id = (
                       SELECT p.id FROM projects p
                        WHERE p.name = events.project
                          AND p.deleted_at IS NOT NULL
                          AND ABS(strftime('%s', p.deleted_at) - strftime('%s', events.deleted_at)) <= 5
                        LIMIT 1)
                 WHERE deleted_at IS NOT NULL AND trash_project_id IS NULL
            """)
            conn.execute("""
                UPDATE checklists
                   SET trash_project_id = (
                       SELECT p.id FROM projects p
                        WHERE p.name = checklists.project
                          AND p.deleted_at IS NOT NULL
                          AND ABS(strftime('%s', p.deleted_at) - strftime('%s', checklists.deleted_at)) <= 5
                        LIMIT 1)
                 WHERE deleted_at IS NOT NULL AND trash_project_id IS NULL
            """)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trash_project_backfill_v1', '1')")
        # ── settings ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # ── team_notices ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS team_notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── notifications ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                event_id INTEGER,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_locks (
                meeting_id INTEGER PRIMARY KEY,
                user_name  TEXT NOT NULL,
                locked_at  TEXT NOT NULL
            )
        """)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS checklists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL DEFAULT '',
                title      TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                is_public  INTEGER NOT NULL DEFAULT 0,
                is_locked  INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT DEFAULT NULL,
                deleted_by TEXT DEFAULT NULL,
                team_id    INTEGER DEFAULT NULL,
                is_active  INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS checklist_locks (
                checklist_id INTEGER PRIMARY KEY,
                user_name    TEXT NOT NULL,
                locked_at    TEXT NOT NULL
            );
        """)
        # tab_token 컬럼 마이그레이션 (기존 DB 대응)
        for _tbl, _col, _def in [
            ("meeting_locks",   "tab_token", "TEXT NOT NULL DEFAULT ''"),
            ("checklist_locks", "tab_token", "TEXT NOT NULL DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {_tbl} ADD COLUMN {_col} {_def}")
            except Exception:
                pass

        # ── links ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT NOT NULL,
                description TEXT DEFAULT '',
                scope TEXT NOT NULL DEFAULT 'personal',
                team_id INTEGER,
                created_by TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (team_id) REFERENCES teams(id)
            )
        """)

        # ── project_milestones ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_milestones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_project_date ON project_milestones(project_id, date)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uniq_pm_project_date ON project_milestones(project_id, date)")

        # ── 인덱스 ──
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_deleted_start "
                     "ON events(deleted_at, start_datetime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_team_deleted_start "
                     "ON events(team_id, deleted_at, start_datetime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_parent_deleted "
                     "ON events(parent_event_id, deleted_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_meeting_deleted_start "
                     "ON events(meeting_id, deleted_at, start_datetime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project_deleted_start "
                     "ON events(project, deleted_at, start_datetime)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checklists_deleted_updated "
                     "ON checklists(deleted_at, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checklists_project_deleted_updated "
                     "ON checklists(project, deleted_at, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_meetings_deleted_updated "
                     "ON meetings(deleted_at, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_unread "
                     "ON notifications(user_name, is_read, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_meeting_histories_meeting "
                     "ON meeting_histories(meeting_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_checklist_histories_checklist "
                     "ON checklist_histories(checklist_id, id)")

        # ── 시드 데이터 ──
        if not conn.execute("SELECT 1 FROM teams LIMIT 1").fetchone():
            conn.execute("INSERT INTO teams (name) VALUES ('관리팀')")
        if not conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            team_id = conn.execute("SELECT id FROM teams LIMIT 1").fetchone()[0]
            init_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
            conn.execute(
                "INSERT INTO users (name, password, role, team_id, is_active) VALUES (?,?,'admin',?,1)",
                ("admin", init_pw, team_id)
            )
            print(f"[WhatUdoin] 초기 관리자 비밀번호: {init_pw}  (최초 1회만 표시, 즉시 변경 권장)")


def _recurrence_dates(rule: str, start_date_str: str, end_limit_str: str | None) -> list:
    """rule에 따라 start_date 이후의 반복 날짜 목록 반환 (start_date 자체 제외)

    rule 형식: 'weekly:0,2,4'  (0=월, 1=화, 2=수, 3=목, 4=금)
    """
    from datetime import date as _date, timedelta as _td

    if not rule or not rule.startswith('weekly:'):
        return []

    try:
        days = sorted(set(int(d) for d in rule.split(':')[1].split(',') if d.strip().isdigit()))
    except Exception:
        return []
    if not days:
        return []

    start = _date.fromisoformat(start_date_str[:10])
    if end_limit_str:
        end_limit = _date.fromisoformat(end_limit_str[:10])
    else:
        end_limit = start + _td(days=365)

    MAX = 200
    results = []
    current = start + _td(days=1)   # start 자체는 부모이므로 다음 날부터

    while current <= end_limit and len(results) < MAX:
        wd = current.weekday()
        if wd in days:
            results.append(current.isoformat())
            current = current + _td(days=1)
        else:
            # 이번 주/다음 주에서 다음으로 가장 가까운 선택 요일로 점프
            next_days_this_cycle = [d for d in days if d > wd]
            if next_days_this_cycle:
                jump = next_days_this_cycle[0] - wd
            else:
                jump = 7 - wd + days[0]
            current = current + _td(days=jump)

    return results


def _generate_recurrence_children(conn, parent_id: int, parent_data: dict):
    """parent_data 기준으로 반복 자식 이벤트 생성 (conn 트랜잭션 내에서 호출)"""
    rule = parent_data.get("recurrence_rule")
    if not rule:
        return
    start_str = parent_data.get("start_datetime", "")
    end_str   = parent_data.get("end_datetime") or start_str
    if not start_str:
        return

    # 이벤트 지속 시간 계산
    from datetime import datetime as _dt
    def _to_dt(s):
        s = s[:16]
        return _dt.fromisoformat(s) if 'T' in s else _dt.fromisoformat(s + 'T00:00')
    start_dt = _to_dt(start_str)
    end_dt   = _to_dt(end_str)
    duration = end_dt - start_dt

    dates = _recurrence_dates(rule, start_str[:10], parent_data.get("recurrence_end"))
    time_part = start_str[10:]  # 'T09:00' or ''

    for date_str in dates:
        new_start = date_str + time_part
        new_end_dt = _dt.fromisoformat(new_start[:16] if 'T' in new_start else new_start + 'T00:00') + duration
        new_end = new_end_dt.strftime('%Y-%m-%dT%H:%M') if duration.total_seconds() > 0 else None

        child = {
            "title":               parent_data["title"],
            "team_id":             parent_data.get("team_id"),
            "project":             parent_data.get("project"),
            "description":         parent_data.get("description"),
            "location":            parent_data.get("location"),
            "assignee":            parent_data.get("assignee"),
            "all_day":             parent_data.get("all_day", 0),
            "start_datetime":      new_start,
            "end_datetime":        new_end,
            "created_by":          parent_data.get("created_by"),
            "source":              parent_data.get("source", "manual"),
            "meeting_id":          None,
            "kanban_status":       None,   # 자식은 칸반 미등록
            "priority":            parent_data.get("priority", "normal"),
            "event_type":          parent_data.get("event_type", "schedule"),
            "is_active":           1,
            "is_public":           None,  # 프로젝트 공개 연동
            "recurrence_rule":     None,
            "recurrence_end":      None,
            "recurrence_parent_id": parent_id,
            "bound_checklist_id":  parent_data.get("bound_checklist_id"),
        }
        conn.execute(
            """INSERT INTO events
               (title, team_id, project, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority, event_type, is_active, is_public,
                recurrence_rule, recurrence_end, recurrence_parent_id,
                bound_checklist_id)
               VALUES
               (:title, :team_id, :project, :description, :location, :assignee, :all_day,
                :start_datetime, :end_datetime, :created_by, :source, :meeting_id,
                :kanban_status, :priority, :event_type, :is_active, :is_public,
                :recurrence_rule, :recurrence_end, :recurrence_parent_id,
                :bound_checklist_id)""",
            child,
        )


def _table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone())


def _migrate(conn, table: str, columns: list):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if not existing:
        # 테이블이 아직 없으면 CREATE TABLE IF NOT EXISTS가 최신 스키마로 생성하므로 건너뜀
        return
    for col, definition in columns:
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Events ──────────────────────────────────────────────

def get_all_events():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM events
               WHERE deleted_at IS NULL
                 AND (project IS NULL OR project = ''
                      OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
               ORDER BY start_datetime"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_events_by_project_range(project: str, start_date: str, end_date: str, include_subtasks: bool = False) -> list[dict]:
    """특정 프로젝트의 날짜 범위 일정 조회 (반복 원본만, 완료 프로젝트 제외)"""
    type_filter = "('schedule', 'subtask')" if include_subtasks else "('schedule')"
    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT e.* FROM events e
               LEFT JOIN projects p ON p.name = e.project AND p.deleted_at IS NULL
               WHERE e.project = ?
                 AND e.deleted_at IS NULL
                 AND (e.event_type IS NULL OR e.event_type IN {type_filter})
                 AND e.recurrence_parent_id IS NULL
                 AND date(e.start_datetime) BETWEEN ? AND ?
                 AND (e.is_active IS NULL OR e.is_active != 0)
                 AND (p.id IS NULL OR p.is_active = 1)
               ORDER BY e.start_datetime""",
            (project, start_date, end_date)
        ).fetchall()
    return [dict(r) for r in rows]


def get_event(event_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        # 자식 인스턴스인 경우 부모의 recurrence_rule / recurrence_end 를 포함
        if d.get("recurrence_parent_id"):
            parent = conn.execute(
                "SELECT recurrence_rule, recurrence_end FROM events WHERE id = ?",
                (d["recurrence_parent_id"],)
            ).fetchone()
            if parent:
                d["recurrence_rule"] = parent["recurrence_rule"]
                d["recurrence_end"]  = parent["recurrence_end"]
        # 바인딩된 체크리스트 정보 (삭제된 체크는 None으로 폴백)
        if d.get("bound_checklist_id"):
            chk = conn.execute(
                "SELECT title, content FROM checklists WHERE id = ? AND deleted_at IS NULL",
                (d["bound_checklist_id"],)
            ).fetchone()
            d["bound_checklist_title"]   = chk["title"]   if chk else None
            d["bound_checklist_content"] = chk["content"] if chk else None
        else:
            d["bound_checklist_title"]   = None
            d["bound_checklist_content"] = None
        return d


def get_subtasks(parent_id: int) -> list[dict]:
    """특정 부모 이벤트의 하위 업무 목록 (삭제 제외)"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE parent_event_id = ? AND deleted_at IS NULL ORDER BY start_datetime",
            (parent_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def complete_subtasks(parent_id: int):
    """부모 이벤트 완료 시 하위 업무 일괄 완료 처리"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET is_active = 0, updated_at = CURRENT_TIMESTAMP WHERE parent_event_id = ? AND deleted_at IS NULL AND is_active != 0",
            (parent_id,)
        )


def has_subtasks(event_id: int) -> bool:
    """이벤트가 하위 업무를 하나 이상 보유하고 있는지 확인"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE parent_event_id = ? AND deleted_at IS NULL LIMIT 1",
            (event_id,)
        ).fetchone()
    return row is not None


def create_event(data: dict) -> int:
    data.setdefault("team_id", None)
    data.setdefault("meeting_id", None)
    data.setdefault("kanban_status", None)
    data.setdefault("priority", "normal")
    data.setdefault("event_type", "schedule")
    data.setdefault("recurrence_rule", None)
    data.setdefault("recurrence_end", None)
    data.setdefault("recurrence_parent_id", None)
    data.setdefault("parent_event_id", None)
    data.setdefault("bound_checklist_id", None)
    data.setdefault("is_public", None)  # 기본: 프로젝트 공개 연동
    # 회의·일지·하위 업무 타입은 칸반 등록 안 함
    if data.get("event_type") in ("meeting", "journal", "subtask"):
        data["kanban_status"] = None
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (title, team_id, project, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority, event_type, is_public,
                recurrence_rule, recurrence_end, recurrence_parent_id, parent_event_id,
                bound_checklist_id)
               VALUES
               (:title, :team_id, :project, :description, :location, :assignee, :all_day,
                :start_datetime, :end_datetime, :created_by, :source, :meeting_id,
                :kanban_status, :priority, :event_type, :is_public,
                :recurrence_rule, :recurrence_end, :recurrence_parent_id, :parent_event_id,
                :bound_checklist_id)""",
            data,
        )
        parent_id = cur.lastrowid
        if data.get("recurrence_rule"):
            data["id"] = parent_id
            _generate_recurrence_children(conn, parent_id, data)
    return parent_id


def _apply_event_update(conn, event_id: int, data: dict):
    """단일 이벤트 행 업데이트 (conn 트랜잭션 내 공통 로직)"""
    data["id"] = event_id
    new_status = data.get("kanban_status")
    if new_status == 'done':
        data["done_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elif new_status is not None:
        data["done_at"] = None
    else:
        data.setdefault("done_at", None)
    data.setdefault("event_type", "schedule")
    if data.get("event_type") in ("meeting", "journal", "subtask"):
        data["kanban_status"] = None
        data["done_at"] = None
    data.setdefault("recurrence_rule", None)
    data.setdefault("recurrence_end", None)
    data.setdefault("parent_event_id", None)
    data.setdefault("bound_checklist_id", None)
    conn.execute(
        """UPDATE events SET
            title              = :title,
            project            = :project,
            description        = :description,
            location           = :location,
            assignee           = :assignee,
            all_day            = :all_day,
            start_datetime     = :start_datetime,
            end_datetime       = :end_datetime,
            kanban_status      = :kanban_status,
            priority           = :priority,
            done_at            = :done_at,
            event_type         = :event_type,
            recurrence_rule    = :recurrence_rule,
            recurrence_end     = :recurrence_end,
            parent_event_id    = :parent_event_id,
            bound_checklist_id = :bound_checklist_id,
            updated_at         = CURRENT_TIMESTAMP
           WHERE id = :id""",
        data,
    )


def update_event(event_id: int, data: dict):
    with get_conn() as conn:
        existing = conn.execute("SELECT project, event_type FROM events WHERE id = ?", (event_id,)).fetchone()
        _apply_event_update(conn, event_id, data)
        # 업무 일정의 프로젝트가 바뀌면 하위 업무들도 함께 이동
        if (existing and data.get("event_type", "schedule") == "schedule"
                and data.get("project") != (existing["project"] if existing else None)):
            conn.execute(
                "UPDATE events SET project = ?, updated_at = CURRENT_TIMESTAMP WHERE parent_event_id = ? AND deleted_at IS NULL",
                (data.get("project"), event_id)
            )


def update_event_recurring_this(event_id: int, data: dict):
    """이것만 수정: 해당 이벤트만 변경 (반복 시리즈 유지)"""
    with get_conn() as conn:
        # 기존 recurrence 정보 보존
        existing = conn.execute("SELECT recurrence_rule, recurrence_end, recurrence_parent_id FROM events WHERE id = ?", (event_id,)).fetchone()
        if existing:
            data.setdefault("recurrence_rule", existing["recurrence_rule"])
            data.setdefault("recurrence_end", existing["recurrence_end"])
        _apply_event_update(conn, event_id, data)


def update_event_recurring_all(event_id: int, data: dict):
    """전체 수정: 부모 + 모든 자식 재생성"""
    with get_conn() as conn:
        # 부모 ID 확인
        existing = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not existing:
            return
        existing = dict(existing)
        parent_id = existing.get("recurrence_parent_id") or event_id

        # 부모 업데이트
        parent_data = dict(data)
        parent_data.setdefault("recurrence_rule", existing.get("recurrence_rule"))
        parent_data.setdefault("recurrence_end", existing.get("recurrence_end"))
        # 부모의 start/end는 유지 (전체 수정이지만 날짜는 parent 기준)
        parent_row = conn.execute("SELECT * FROM events WHERE id = ?", (parent_id,)).fetchone()
        if parent_row:
            parent_row = dict(parent_row)
            parent_data["start_datetime"] = parent_row["start_datetime"]
            parent_data["end_datetime"] = parent_row["end_datetime"]
            parent_data["all_day"] = parent_row["all_day"]
        _apply_event_update(conn, parent_id, parent_data)

        # 기존 자식 전체 삭제 후 재생성
        conn.execute("DELETE FROM events WHERE recurrence_parent_id = ?", (parent_id,))
        final_parent = conn.execute("SELECT * FROM events WHERE id = ?", (parent_id,)).fetchone()
        if final_parent:
            _generate_recurrence_children(conn, parent_id, dict(final_parent))


def update_event_recurring_from_here(event_id: int, data: dict):
    """이후 전체 수정: 이 이벤트 날짜부터 새 시리즈로 분리"""
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not existing:
            return
        existing = dict(existing)
        parent_id = existing.get("recurrence_parent_id") or event_id
        this_start = existing["start_datetime"][:10]

        # 기존 부모의 반복 종료일을 이 이벤트 하루 전으로 설정
        from datetime import date as _date, timedelta as _td
        day_before = (_date.fromisoformat(this_start) - _td(days=1)).isoformat()
        conn.execute(
            "UPDATE events SET recurrence_end = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (day_before, parent_id)
        )
        # 이 이벤트 이후(포함) 자식 모두 삭제
        conn.execute(
            "DELETE FROM events WHERE recurrence_parent_id = ? AND start_datetime >= ?",
            (parent_id, this_start + 'T00:00')
        )
        # 이 이벤트 자체도 삭제 (새 부모로 만들 것이므로)
        if existing.get("recurrence_parent_id"):
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

        # 기존 부모에서 rule/type 정보 가져오기
        old_parent = conn.execute("SELECT * FROM events WHERE id = ?", (parent_id,)).fetchone()
        rule = data.get("recurrence_rule") or (dict(old_parent)["recurrence_rule"] if old_parent else None)

        # 새 부모(이 이벤트) 생성
        new_parent = dict(data)
        new_parent.setdefault("recurrence_rule", rule)
        new_parent.setdefault("recurrence_end", None)
        new_parent["recurrence_parent_id"] = None
        new_parent.setdefault("event_type", existing.get("event_type", "schedule"))
        new_parent.setdefault("team_id", existing.get("team_id"))
        new_parent.setdefault("created_by", existing.get("created_by"))
        new_parent.setdefault("source", "manual")
        new_parent.setdefault("meeting_id", None)
        new_parent.setdefault("kanban_status", existing.get("kanban_status"))
        new_parent.setdefault("priority", existing.get("priority", "normal"))
        new_parent.setdefault("is_active", 1)
        new_parent.setdefault("bound_checklist_id", existing.get("bound_checklist_id"))
        if new_parent.get("event_type") in ("meeting", "journal"):
            new_parent["kanban_status"] = None

        cur = conn.execute(
            """INSERT INTO events
               (title, team_id, project, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority, event_type, is_active,
                recurrence_rule, recurrence_end, recurrence_parent_id,
                bound_checklist_id)
               VALUES
               (:title, :team_id, :project, :description, :location, :assignee, :all_day,
                :start_datetime, :end_datetime, :created_by, :source, :meeting_id,
                :kanban_status, :priority, :event_type, :is_active,
                :recurrence_rule, :recurrence_end, :recurrence_parent_id,
                :bound_checklist_id)""",
            new_parent,
        )
        new_parent_id = cur.lastrowid
        if rule:
            new_parent["id"] = new_parent_id
            _generate_recurrence_children(conn, new_parent_id, new_parent)


def update_event_project(event_id: int, project: str | None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET project = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (project or None, event_id),
        )


def update_event_datetime(event_id: int, start_datetime: str, end_datetime: str | None, all_day: int):
    with get_conn() as conn:
        conn.execute(
            """UPDATE events SET
                start_datetime = ?,
                end_datetime   = ?,
                all_day        = ?,
                updated_at     = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (start_datetime, end_datetime, all_day, event_id),
        )


def delete_event(event_id: int, delete_mode: str = 'this', deleted_by: str = None, team_id: int = None):
    """
    soft-delete 방식으로 이벤트 휴지통 이동.
    delete_mode:
      'this'      - 이것만 (자식이면 이 인스턴스만, 부모면 부모+모든자식 soft-delete)
      'all'       - 부모 + 모든 자식 soft-delete
      'from_here' - 이 이벤트 날짜 이후(포함) hard-delete (series 조각 정리)
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return
        row = dict(row)
        parent_id = row.get("recurrence_parent_id") or event_id

        if delete_mode == 'all':
            conn.execute(
                "UPDATE events SET deleted_at = ?, deleted_by = ?, team_id = COALESCE(team_id, ?) "
                "WHERE id = ? OR recurrence_parent_id = ? OR parent_event_id = ?",
                (now, deleted_by, team_id, parent_id, parent_id, parent_id)
            )
        elif delete_mode == 'from_here':
            # 반복 series 조각 정리는 hard-delete 유지
            from datetime import date as _date, timedelta as _td
            this_start = row["start_datetime"][:10]
            day_before = (_date.fromisoformat(this_start) - _td(days=1)).isoformat()
            conn.execute(
                "UPDATE events SET recurrence_end = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (day_before, parent_id)
            )
            conn.execute(
                "DELETE FROM events WHERE recurrence_parent_id = ? AND start_datetime >= ?",
                (parent_id, this_start + 'T00:00')
            )
            if row.get("recurrence_parent_id"):
                conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        else:  # 'this'
            if row.get("recurrence_parent_id"):
                conn.execute(
                    "UPDATE events SET deleted_at = ?, deleted_by = ?, team_id = COALESCE(team_id, ?) WHERE id = ?",
                    (now, deleted_by, team_id, event_id)
                )
            else:
                # 부모 삭제 → 자식(반복 인스턴스 + 하위 업무)도 soft-delete
                conn.execute(
                    "UPDATE events SET deleted_at = ?, deleted_by = ?, team_id = COALESCE(team_id, ?) "
                    "WHERE id = ? OR recurrence_parent_id = ? OR parent_event_id = ?",
                    (now, deleted_by, team_id, event_id, event_id, event_id)
                )


def get_kanban_events(team_id: int = None, viewer=None) -> list[dict]:
    # 조건:
    #   - kanban_status가 설정된 일정, 또는
    #   - 프로젝트 없는(미지정) 일정 (kanban_status 없어도 Backlog로 표시)
    # 제외:
    #   - 종료된 프로젝트 소속 일정
    #   - 완료 처리된 미지정 일정 (is_active = 0)
    private_clause = """
        AND (
          e.is_public = 1
          OR (
            e.is_public IS NULL
            AND e.project IS NOT NULL AND e.project != ''
            AND e.project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
          )
        )
    """ if viewer is None else ""
    base_filter = f"""
        AND (
            e.kanban_status IS NOT NULL
            OR (e.project IS NULL OR e.project = '')
        )
        AND (e.project IS NULL OR e.project = '' OR e.project NOT IN (
            SELECT name FROM projects WHERE is_active = 0
        ))
        AND (e.is_active IS NULL OR e.is_active = 1)
        AND (e.kanban_hidden IS NULL OR e.kanban_hidden = 0)
        AND (e.done_at IS NULL OR e.done_at > datetime('now', '-7 days'))
        AND (e.event_type IS NULL OR e.event_type = 'schedule')
        AND e.recurrence_parent_id IS NULL
        AND e.deleted_at IS NULL
        {private_clause}
    """
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                f"SELECT * FROM events e WHERE e.team_id = ? {base_filter} ORDER BY e.start_datetime",
                (team_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM events e WHERE 1=1 {base_filter} ORDER BY e.start_datetime"
            ).fetchall()
    return [dict(r) for r in rows]


_MISSING = object()

def update_kanban_status(event_id: int, kanban_status=_MISSING, priority=_MISSING):
    sets, params = [], []
    if kanban_status is not _MISSING:
        sets.append("kanban_status = ?")
        params.append(kanban_status)
        # done으로 변경 시 done_at 기록, 다른 상태로 변경 시 초기화
        if kanban_status == 'done':
            sets.append("done_at = CURRENT_TIMESTAMP")
        else:
            sets.append("done_at = NULL")
    if priority is not _MISSING:
        sets.append("priority = ?")
        params.append(priority)
    if not sets:
        return
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(event_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE events SET {', '.join(sets)} WHERE id = ?",
            params
        )


def get_project_timeline(team_id: int = None, viewer=None) -> list[dict]:
    """팀 → 프로젝트 2단계 그룹으로 일정 반환 (프로젝트 없는 일정은 '미지정'으로 묶음)"""
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE e.team_id = ? AND e.deleted_at IS NULL
                   ORDER BY e.start_datetime""",
                (team_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE e.deleted_at IS NULL
                   ORDER BY e.start_datetime"""
            ).fetchall()
        # projects 테이블에서 메타 조회
        proj_meta_rows = conn.execute(
            "SELECT id, name, color, start_date, end_date, is_active, is_private FROM projects WHERE deleted_at IS NULL"
        ).fetchall()
        ms_rows = conn.execute("""
            SELECT pm.project_id, pm.title, pm.date
              FROM project_milestones pm
             ORDER BY pm.project_id, pm.sort_order
        """).fetchall()
    proj_meta = {r["name"]: dict(r) for r in proj_meta_rows}
    ms_by_pid = {}
    for r in ms_rows:
        ms_by_pid.setdefault(r["project_id"], []).append({"title": r["title"], "date": r["date"]})
    # 비활성(종료) 프로젝트 이름 집합
    inactive = {name for name, m in proj_meta.items() if m.get("is_active") == 0}
    # 비공개 프로젝트 이름 집합 (비로그인 시 제외)
    private_projs = {name for name, m in proj_meta.items() if m.get("is_private") == 1} if viewer is None else set()

    # team_name → project → events (비활성 프로젝트 제외)
    teams: dict[str, dict[str, list]] = {}
    for row in rows:
        d = dict(row)
        tname = d.get("team_name") or "미분류"
        p = d["project"] if d.get("project") and d["project"].strip() else "미지정"
        if p in inactive:
            continue  # 종료된 프로젝트 건너뜀
        if viewer is None:
            ep_public = d.get("is_public")
            if ep_public == 0:
                continue  # 명시적 비공개
            elif ep_public is None:
                # 프로젝트 연동: 미지정이거나 비공개 프로젝트면 숨김
                if p in private_projs or p == "미지정":
                    continue
            # ep_public == 1: 프로젝트 공개 여부 무관하게 항상 노출
        if p == "미지정" and d.get("is_active") == 0:
            continue  # 완료 처리된 미지정 일정 건너뜀
        if d.get("kanban_hidden") == 1:
            continue  # 칸반/간트 숨김 처리된 일정 건너뜀
        if d.get("recurrence_parent_id"):
            continue  # 반복 자식 인스턴스는 간트에서 제외
        done_at = d.get("done_at")
        if done_at:
            from datetime import datetime as _dt, timedelta as _td
            try:
                done_dt = _dt.fromisoformat(done_at.replace("Z", "+00:00").split("+")[0])
                if _dt.now() - done_dt > _td(days=7):
                    continue  # done 후 7일 초과 → 간트에서 숨김
            except Exception:
                pass
        if tname not in teams:
            teams[tname] = {}
        if p not in teams[tname]:
            teams[tname][p] = []
        teams[tname][p].append(d)
    result = []
    for tname, projs in sorted(teams.items()):
        # 일반 프로젝트 정렬 후 미지정을 맨 뒤로
        normal = sorted((k, v) for k, v in projs.items() if k != "미지정")
        unset  = [("미지정", projs["미지정"])] if "미지정" in projs else []
        proj_list = []
        for pname, evs in normal + unset:
            meta = proj_meta.get(pname, {})
            proj_list.append({
                "name":       pname,
                "events":     evs,
                "color":      meta.get("color"),
                "start_date": meta.get("start_date"),
                "end_date":   meta.get("end_date"),
                "milestones": ms_by_pid.get(meta.get("id"), []),
            })
        result.append({"team_name": tname, "projects": proj_list})
    return result


def get_upcoming_meetings(assignee_name: str = None, limit: int = 7) -> list[dict]:
    """event_type='meeting'인 일정 중 오늘 이후 최대 limit개 반환 (담당자 필터 가능)"""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM events
               WHERE event_type = 'meeting'
               AND (is_active IS NULL OR is_active = 1)
               AND deleted_at IS NULL
               AND start_datetime >= ?
               ORDER BY start_datetime""",
            (today,)
        ).fetchall()
    all_rows = [dict(r) for r in rows]
    if not assignee_name:
        return all_rows[:limit]
    # 콤마 구분 담당자 필드를 split+trim 후 정확 비교 (공백 포함 케이스 커버)
    name_lower = assignee_name.strip().lower()
    result = []
    for row in all_rows:
        assignees = [s.strip().lower() for s in (row.get('assignee') or '').split(',')]
        if name_lower in assignees:
            result.append(row)
        if len(result) >= limit:
            break
    return result


def _get_user_projects(conn, user_name: str) -> set:
    """events 테이블에서 user_name이 assignee에 포함된 프로젝트명 집합 반환 (Python-side 필터).
    get_upcoming_meetings 패턴과 동일하게 콤마 split+trim 후 정확 비교."""
    rows = conn.execute(
        """SELECT project, assignee FROM events
           WHERE project IS NOT NULL AND project != ''
           AND assignee IS NOT NULL AND assignee != ''
           AND (is_active IS NULL OR is_active = 1)
           AND deleted_at IS NULL"""
    ).fetchall()
    name_lower = user_name.strip().lower()
    matched = set()
    for row in rows:
        parts = [s.strip().lower() for s in (row["assignee"] or "").split(",")]
        if name_lower in parts:
            matched.add(row["project"])
    return matched


def get_calendar_milestones(user_name: str) -> list:
    """캘린더 이벤트 소스용. 로그인 사용자가 assignee인 프로젝트의 모든 milestone 반환."""
    with get_conn() as conn:
        user_projects = _get_user_projects(conn, user_name)
        if not user_projects:
            return []
        placeholders = ",".join("?" * len(user_projects))
        rows = conn.execute(
            f"""SELECT pm.id, pm.title, pm.date, p.name AS project_name, p.color
                  FROM project_milestones pm
                  JOIN projects p ON p.id = pm.project_id
                 WHERE p.name IN ({placeholders})
                   AND (p.is_active IS NULL OR p.is_active = 1)
                 ORDER BY pm.date ASC, pm.sort_order ASC""",
            tuple(user_projects)
        ).fetchall()
    result = []
    for row in rows:
        ev = {
            "id": f"ms-{row['id']}",
            "title": f"{row['title']} - {row['project_name']}",
            "start": row["date"],
            "end": row["date"],
            "allDay": True,
            "extendedProps": {
                "type": "milestone",
                "project": row["project_name"],
                "milestone_title": row["title"],
                "date": row["date"],
            },
            "classNames": ["ev-milestone"],
            "editable": False,
            "startEditable": False,
            "durationEditable": False,
        }
        if row["color"]:
            ev["backgroundColor"] = row["color"]
        result.append(ev)
    return result


def get_upcoming_milestones(user_name: str, limit: int = 5) -> list:
    """내 스케줄용. 오늘 이후 사용자 프로젝트의 milestone + 종료 예정을 합산해 limit개 반환."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        user_projects = _get_user_projects(conn, user_name)
        if not user_projects:
            return []
        placeholders = ",".join("?" * len(user_projects))
        ms_rows = conn.execute(
            f"""SELECT pm.title, pm.date, p.name AS project_name
                  FROM project_milestones pm
                  JOIN projects p ON p.id = pm.project_id
                 WHERE p.name IN ({placeholders})
                   AND (p.is_active IS NULL OR p.is_active = 1)
                   AND pm.date >= ?
                 ORDER BY pm.date ASC""",
            (*tuple(user_projects), today)
        ).fetchall()
        end_rows = conn.execute(
            f"""SELECT name AS project_name, end_date AS date
                  FROM projects
                 WHERE name IN ({placeholders})
                   AND (is_active IS NULL OR is_active = 1)
                   AND deleted_at IS NULL
                   AND end_date IS NOT NULL
                   AND end_date >= ?
                 ORDER BY end_date ASC""",
            (*tuple(user_projects), today)
        ).fetchall()
    items = [{"project": r["project_name"], "title": r["title"], "date": r["date"]} for r in ms_rows]
    items += [{"project": r["project_name"], "title": "종료 예정", "date": r["date"]} for r in end_rows]
    items.sort(key=lambda x: x["date"])
    return items[:limit]


def create_notification(user_name: str, type_: str, message: str, event_id: int = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO notifications (user_name, type, message, event_id) VALUES (?,?,?,?)",
            (user_name, type_, message, event_id)
        )


def create_notification_for_all(type_: str, message: str, event_id: int = None, exclude_user: str = None):
    """모든 활성 유저에게 알림 생성 (exclude_user 제외)"""
    with get_conn() as conn:
        users = conn.execute(
            "SELECT name FROM users WHERE is_active = 1"
        ).fetchall()
        for u in users:
            if exclude_user and u["name"] == exclude_user:
                continue
            conn.execute(
                "INSERT INTO notifications (user_name, type, message, event_id) VALUES (?,?,?,?)",
                (u["name"], type_, message, event_id)
            )


def get_notification_count(user_name: str) -> int:
    """미읽은 알림 수 반환 (읽음 처리 없음)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE user_name = ? AND is_read = 0",
            (user_name,)
        ).fetchone()
    return row["cnt"] if row else 0


def get_pending_notifications(user_name: str) -> list[dict]:
    """미읽은 알림 반환 (읽음 처리 없음)"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_name = ? AND is_read = 0 ORDER BY id DESC",
            (user_name,)
        ).fetchall()
    return [dict(r) for r in rows]


def mark_all_notifications_read(user_name: str):
    """모든 미읽은 알림을 읽음 처리"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE user_name = ? AND is_read = 0",
            (user_name,)
        )


def check_upcoming_event_alarms():
    """15분 후 시작하는 일정/회의에 대한 알림 생성 (APScheduler에서 호출)"""
    now = datetime.now()
    window_start = (now + timedelta(minutes=14)).strftime("%Y-%m-%dT%H:%M")
    window_end   = (now + timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, start_datetime, assignee, event_type, created_by
               FROM events
               WHERE (is_active IS NULL OR is_active = 1)
               AND deleted_at IS NULL
               AND (event_type IS NULL OR event_type != 'journal')
               AND start_datetime BETWEEN ? AND ?
               AND recurrence_parent_id IS NULL""",
            (window_start, window_end)
        ).fetchall()
        for row in rows:
            label = "회의" if row["event_type"] == "meeting" else "업무"
            time_str = row["start_datetime"][11:16] if row["start_datetime"] else ""
            message = f"15분 후 {label} 시작: {row['title']} ({time_str})"
            # 담당자가 있으면 담당자에게, 없으면 생성자에게
            if row["assignee"]:
                targets = [a.strip() for a in row["assignee"].split(",") if a.strip()]
            elif row["created_by"]:
                # created_by는 user id(숫자) — 이름으로 변환
                creator = conn.execute(
                    "SELECT name FROM users WHERE id = ?", (row["created_by"],)
                ).fetchone()
                targets = [creator["name"]] if creator else []
            else:
                targets = []
            for name in targets:
                # 중복 방지: 같은 event_id + user_name 조합이 오늘 이미 있으면 스킵
                exists = conn.execute(
                    """SELECT 1 FROM notifications
                       WHERE event_id = ? AND user_name = ? AND type = 'upcoming'
                       AND created_at >= date('now')""",
                    (row["id"], name)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO notifications (user_name, type, message, event_id) VALUES (?,?,?,?)",
                        (name, "upcoming", message, row["id"])
                    )


def get_latest_notice() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM team_notices ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def save_notice(content: str, created_by: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO team_notices (content, created_by) VALUES (?, ?)",
            (content, created_by)
        )
        # 30일 이전 이력 자동 삭제
        conn.execute(
            "DELETE FROM team_notices WHERE created_at < datetime('now', '-30 days')"
        )
        # 100개 초과분 삭제 (가장 오래된 것부터)
        conn.execute(
            "DELETE FROM team_notices WHERE id NOT IN "
            "(SELECT id FROM team_notices ORDER BY id DESC LIMIT 100)"
        )
    return cur.lastrowid


def get_notice_history(limit: int = 100) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM team_notices ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_projects() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL ORDER BY project"
        ).fetchall()
    return [row[0] for row in rows]


def get_unified_project_list(active_only: bool = True) -> list[dict]:
    """모든 페이지에서 일관되게 사용할 통합 프로젝트 목록.

    projects 테이블(삭제 안 된 것) + events.project + checklists.project 를 합산하여
    [{name, color, is_active, id}] 형태로 반환. 이름 기준 중복 제거 후 이름순 정렬.
    active_only=True(기본값)이면 is_active=1인 항목만 반환.
    """
    with get_conn() as conn:
        # 1. projects 테이블 (삭제 안 된 것)
        proj_rows = conn.execute(
            "SELECT id, name, color, is_active, is_private FROM projects WHERE deleted_at IS NULL"
        ).fetchall()
        # 2. events.project 에서 프로젝트 이름 수집 (삭제 안 된 것)
        ev_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
        ).fetchall()
        # 3. checklists.project 에서 프로젝트 이름 수집 (삭제 안 된 것)
        ck_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM checklists WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
        ).fetchall()

    proj_map: dict[str, dict] = {}
    for r in proj_rows:
        proj_map[r["name"]] = {
            "id": r["id"],
            "name": r["name"],
            "color": r["color"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "is_private": r["is_private"] if r["is_private"] is not None else 0,
        }

    # events/checklists 에만 있는 프로젝트 이름도 포함 (orphan — is_active 기본 1)
    for rows in (ev_proj_rows, ck_proj_rows):
        for r in rows:
            name = r[0]
            if name and name not in proj_map:
                proj_map[name] = {"id": None, "name": name, "color": None, "is_active": 1, "is_private": 0}

    result = sorted(proj_map.values(), key=lambda x: x["name"])
    if active_only:
        result = [p for p in result if p.get("is_active", 1)]
    return result


# ── Project Management ───────────────────────────────────

def get_all_projects_with_events() -> list[dict]:
    """프로젝트 목록 + 각 프로젝트의 일정 반환 (projects 테이블 + events.project + checklists.project 합산)"""
    with get_conn() as conn:
        # projects 테이블의 프로젝트 (삭제되지 않은 것만)
        proj_rows = conn.execute(
            "SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY is_active DESC, name"
        ).fetchall()
        # events에서 project 이름 목록 (projects 테이블에 없는 것도 포함)
        ev_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
        ).fetchall()
        # checklists에서 project 이름 목록 (projects 테이블에도 events에도 없는 것 보완)
        ck_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM checklists WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
        ).fetchall()
        # 이벤트들 (삭제되지 않은 것만)
        ev_rows = conn.execute(
            """SELECT e.*, t.name as team_name
               FROM events e LEFT JOIN teams t ON e.team_id = t.id
               WHERE e.deleted_at IS NULL
               ORDER BY e.start_datetime"""
        ).fetchall()
        # milestone 단일 SELECT (N+1 회피)
        ms_rows = conn.execute(
            "SELECT project_id, title, date FROM project_milestones ORDER BY project_id, sort_order"
        ).fetchall()

    ms_by_pid: dict = {}
    for r in ms_rows:
        ms_by_pid.setdefault(r["project_id"], []).append({"title": r["title"], "date": r["date"]})

    # projects 테이블 기반 dict
    proj_map: dict[str, dict] = {}
    for r in proj_rows:
        proj_map[r["name"]] = {
            "id": r["id"], "name": r["name"], "color": r["color"],
            "start_date": r["start_date"], "end_date": r["end_date"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "is_private": r["is_private"] if r["is_private"] is not None else 0,
            "memo": r["memo"],
            "events": [],
            "milestones": ms_by_pid.get(r["id"], []),
        }

    # events.project / checklists.project에만 있는 orphan 프로젝트도 추가
    for r in (*ev_proj_rows, *ck_proj_rows):
        name = r[0]
        if name and name not in proj_map:
            proj_map[name] = {"id": None, "name": name, "color": None,
                              "start_date": None, "end_date": None, "is_active": 1,
                              "is_private": 0, "memo": None, "events": []}

    # 이벤트 분류
    unset_events = []
    for r in ev_rows:
        d = dict(r)
        p = d.get("project") or ""
        if p.strip():
            if p not in proj_map:
                proj_map[p] = {"id": None, "name": p, "color": None,
                               "start_date": None, "end_date": None, "is_active": 1,
                               "is_private": 0, "memo": None, "events": []}
            proj_map[p]["events"].append(d)
        else:
            unset_events.append(d)

    active   = sorted((p for p in proj_map.values() if p.get("is_active", 1)), key=lambda x: x["name"])
    inactive = sorted((p for p in proj_map.values() if not p.get("is_active", 1)), key=lambda x: x["name"])
    result = active + inactive
    if unset_events:
        result.append({"id": None, "name": "미지정", "color": None,
                       "start_date": None, "end_date": None, "is_active": 1,
                       "memo": None, "events": unset_events})
    return result


def get_all_projects_meta() -> list[dict]:
    """프로젝트 메타 정보만 반환 (events 제외). check 페이지 등 이벤트 불필요 시 사용."""
    with get_conn() as conn:
        proj_rows = conn.execute(
            "SELECT id, name, color, is_active, is_private FROM projects WHERE deleted_at IS NULL ORDER BY is_active DESC, name"
        ).fetchall()
        ev_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
        ).fetchall()
        ck_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM checklists WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
        ).fetchall()

    proj_map: dict[str, dict] = {}
    for r in proj_rows:
        proj_map[r["name"]] = {
            "id": r["id"], "name": r["name"], "color": r["color"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "is_private": r["is_private"] if r["is_private"] is not None else 0,
        }

    for r in (*ev_proj_rows, *ck_proj_rows):
        name = r[0]
        if name and name not in proj_map:
            proj_map[name] = {"id": None, "name": name, "color": None, "is_active": 1, "is_private": 0}

    active   = sorted((p for p in proj_map.values() if p.get("is_active", 1)), key=lambda x: x["name"])
    inactive = sorted((p for p in proj_map.values() if not p.get("is_active", 1)), key=lambda x: x["name"])
    return active + inactive


def create_project(name: str, color: str = None, memo: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, color, memo) VALUES (?, ?, ?)", (name, color, memo)
        )
    return cur.lastrowid


def get_project(name: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE name = ? AND deleted_at IS NULL",
            (name,)
        ).fetchone()
    return dict(row) if row else None


def get_events_by_project(name: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE project = ? AND deleted_at IS NULL ORDER BY start_datetime",
            (name,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_unassigned_events() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE project IS NULL AND deleted_at IS NULL ORDER BY start_datetime"
        ).fetchall()
    return [dict(r) for r in rows]


def rename_project(old_name: str, new_name: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET name = ? WHERE name = ?", (new_name, old_name)
        )
        conn.execute(
            "UPDATE events SET project = ? WHERE project = ?", (new_name, old_name)
        )


def delete_project(name: str, deleted_by: str = None, team_id: int = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        # 프로젝트 id 조회 (자식 항목에 trash_project_id 연결용)
        # 활성(미삭제) 프로젝트만 대상 — team_id 조건은 라우트 레이어에서 이미 검증됨
        proj_row = conn.execute(
            "SELECT id FROM projects WHERE name = ? AND deleted_at IS NULL",
            (name,)
        ).fetchone()
        proj_id = proj_row["id"] if proj_row else None
        # 프로젝트 소속 이벤트 soft-delete (trash_project_id 연결)
        conn.execute(
            "UPDATE events SET deleted_at = ?, deleted_by = ?, trash_project_id = ? "
            "WHERE project = ? AND deleted_at IS NULL",
            (now, deleted_by, proj_id, name)
        )
        # 프로젝트 소속 체크리스트 soft-delete (trash_project_id 연결)
        conn.execute(
            "UPDATE checklists SET deleted_at = ?, deleted_by = ?, team_id = ?, trash_project_id = ? "
            "WHERE project = ? AND deleted_at IS NULL",
            (now, deleted_by, team_id, proj_id, name)
        )
        # 프로젝트 중간 일정 hard delete
        conn.execute(
            "DELETE FROM project_milestones WHERE project_id = (SELECT id FROM projects WHERE name = ?)",
            (name,)
        )
        conn.execute(
            "UPDATE projects SET deleted_at = ?, deleted_by = ?, team_id = ? WHERE name = ?",
            (now, deleted_by, team_id, name)
        )


def bulk_soft_delete_project_items(project_name: str, event_ids: list, checklist_ids: list, deleted_by: str, team_id: int):
    """프로젝트 포함 항목 선택 일괄 soft-delete (휴지통 이동)"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ev_count = 0
    ck_count = 0
    # 미지정 프로젝트: DB에서 project IS NULL 또는 ''로 저장
    is_unset = (project_name == '미지정')
    with get_conn() as conn:
        if event_ids:
            placeholders = ','.join('?' * len(event_ids))
            proj_cond = "(project IS NULL OR project = '')" if is_unset else "project=?"
            params = [now, deleted_by, team_id, *event_ids] if is_unset else [now, deleted_by, team_id, *event_ids, project_name]
            cur = conn.execute(
                f"UPDATE events SET deleted_at=?, deleted_by=?, team_id=COALESCE(team_id,?) "
                f"WHERE id IN ({placeholders}) AND {proj_cond} AND deleted_at IS NULL",
                params
            )
            ev_count = cur.rowcount
        if checklist_ids:
            placeholders = ','.join('?' * len(checklist_ids))
            proj_cond = "project=''" if is_unset else "project=?"
            params = [now, deleted_by, team_id, *checklist_ids] if is_unset else [now, deleted_by, team_id, *checklist_ids, project_name]
            cur = conn.execute(
                f"UPDATE checklists SET deleted_at=?, deleted_by=?, team_id=COALESCE(team_id,?) "
                f"WHERE id IN ({placeholders}) AND {proj_cond} AND deleted_at IS NULL",
                params
            )
            ck_count = cur.rowcount
    return ev_count, ck_count


def update_event_active_status(event_id: int, is_active: int):
    with get_conn() as conn:
        conn.execute("UPDATE events SET is_active = ? WHERE id = ?", (is_active, event_id))
        if is_active == 0:
            conn.execute("UPDATE events SET is_public = 0 WHERE id = ?", (event_id,))


def update_project_privacy(name: str, is_private: int):
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute("UPDATE projects SET is_private = ? WHERE name = ?", (is_private, name))
        else:
            conn.execute("INSERT INTO projects (name, is_private) VALUES (?, ?)", (name, is_private))


def update_project_memo(name: str, memo: str):
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute("UPDATE projects SET memo = ? WHERE name = ?", (memo or None, name))
        else:
            conn.execute("INSERT INTO projects (name, memo) VALUES (?, ?)", (name, memo or None))


def update_project_color(name: str, color: str):
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute("UPDATE projects SET color = ? WHERE name = ?", (color, name))
        else:
            conn.execute("INSERT INTO projects (name, color) VALUES (?, ?)", (name, color))


def update_project_status(name: str, is_active: int):
    """프로젝트 활성/종료 상태 변경. projects 테이블에 없으면 먼저 생성."""
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute("UPDATE projects SET is_active = ? WHERE name = ?", (is_active, name))
        else:
            conn.execute("INSERT INTO projects (name, is_active) VALUES (?, ?)", (name, is_active))


def update_project_dates(name: str, start_date: str = None, end_date: str = None):
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE projects SET start_date = ?, end_date = ? WHERE name = ?",
                (start_date or None, end_date or None, name)
            )
        else:
            conn.execute(
                "INSERT INTO projects (name, start_date, end_date) VALUES (?, ?, ?)",
                (name, start_date or None, end_date or None)
            )


def get_project_milestones(name: str) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT m.id, m.title, m.date, m.sort_order
              FROM project_milestones m
              JOIN projects p ON p.id = m.project_id
             WHERE p.name = ?
             ORDER BY m.sort_order ASC, m.date ASC
        """, (name,)).fetchall()
        return [dict(r) for r in rows]


def set_project_milestones(name: str, milestones: list) -> None:
    """전체 교체. milestones = [{title, date}, ...] 최대 10개."""
    with get_conn() as conn:
        proj = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
        if not proj:
            raise ValueError(f"프로젝트 '{name}'을 찾을 수 없습니다.")
        pid = proj["id"]
        conn.execute("DELETE FROM project_milestones WHERE project_id = ?", (pid,))
        for idx, m in enumerate(milestones):
            conn.execute(
                "INSERT INTO project_milestones (project_id, title, date, sort_order) VALUES (?, ?, ?, ?)",
                (pid, m["title"].strip(), m["date"], idx)
            )


def project_name_exists(name: str) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM projects WHERE name = ?", (name,)).fetchone()
        if row:
            return True
        row2 = conn.execute(
            "SELECT 1 FROM events WHERE project = ? LIMIT 1", (name,)
        ).fetchone()
        return bool(row2)


def check_conflicts(start_dt: str, end_dt: str, team_id: int = None, exclude_id: int = None) -> list[dict]:
    end_dt = end_dt or start_dt
    with get_conn() as conn:
        sql = """
            SELECT id, title, start_datetime, end_datetime
            FROM events
            WHERE start_datetime < ? AND (end_datetime > ? OR (end_datetime IS NULL AND start_datetime >= ?))
        """
        params = [end_dt, start_dt, start_dt]
        if team_id:
            sql += " AND team_id = ?"
            params.append(team_id)
        if exclude_id:
            sql += " AND id != ?"
            params.append(exclude_id)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Teams ──────────────────────────────────────────────

def get_all_teams():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM teams ORDER BY name").fetchall()
    return [dict(r) for r in rows]


def create_team(name: str) -> int:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO teams (name) VALUES (?)", (name,))
    return cur.lastrowid


def update_team(team_id: int, name: str):
    with get_conn() as conn:
        conn.execute("UPDATE teams SET name = ? WHERE id = ?", (name, team_id))


def delete_team(team_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))


# ── Users ──────────────────────────────────────────────

def get_all_users():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.*, t.name as team_name
               FROM users u LEFT JOIN teams t ON u.team_id = t.id
               ORDER BY u.id"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_user(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_password(password: str):
    """에디터 로그인: 비밀번호로 사용자 조회"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE password = ? AND is_active = 1",
            (password,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_credentials(name: str, password: str):
    """관리자 로그인: 이름 + 비밀번호"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE name = ? AND password = ? AND role = 'admin' AND is_active = 1",
            (name, password)
        ).fetchone()
    return dict(row) if row else None


def update_user(user_id: int, data: dict):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET team_id = :team_id, is_active = :is_active WHERE id = :id",
            {**data, "id": user_id}
        )


def reset_user_password(user_id: int, new_password: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET password = ? WHERE id = ?", (new_password, user_id))


# ── Sessions ────────────────────────────────────────────

def create_session(user_id: int, role: str = "editor") -> str:
    session_id = str(uuid.uuid4())
    if role == "admin":
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES (?, ?, ?)",
            (session_id, user_id, expires_at)
        )
    return session_id


def get_session_user(session_id: str):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.*, s.expires_at FROM sessions s
               JOIN users u ON s.user_id = u.id
               WHERE s.id = ? AND u.is_active = 1""",
            (session_id,)
        ).fetchone()
    if not row:
        return None
    row = dict(row)
    if row.get("expires_at"):
        expires = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            delete_session(session_id)
            return None
    return row


def delete_session(session_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def cleanup_expired_sessions():
    """만료된 세션 및 레거시 expires_at=NULL 세션 일괄 삭제"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM sessions WHERE expires_at IS NULL OR expires_at < strftime('%Y-%m-%d %H:%M:%S', 'now')"
        )


# ── IP Management ────────────────────────────────────────

def get_user_by_whitelist_ip(ip: str):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.* FROM user_ips ui
               JOIN users u ON ui.user_id = u.id
               WHERE ui.ip_address = ? AND ui.type = 'whitelist' AND u.is_active = 1""",
            (ip,)
        ).fetchone()
    return dict(row) if row else None


def record_ip(user_id: int, ip: str):
    """수동 로그인 IP 기록 (최대 5개 유지, 중복 허용)"""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, ?, 'history')",
            (user_id, ip)
        )
        # history 5개 초과 시 가장 오래된 것 삭제
        history = conn.execute(
            "SELECT id FROM user_ips WHERE user_id = ? AND type = 'history' ORDER BY last_seen ASC",
            (user_id,)
        ).fetchall()
        if len(history) > 5:
            for row in history[:-5]:
                conn.execute("DELETE FROM user_ips WHERE id = ?", (row["id"],))


def get_user_ips(user_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM user_ips WHERE user_id = ? ORDER BY last_seen DESC",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def toggle_ip_whitelist(ip_id: int, enable: bool):
    new_type = "whitelist" if enable else "history"
    with get_conn() as conn:
        conn.execute("UPDATE user_ips SET type = ? WHERE id = ?", (new_type, ip_id))


# ── Pending Users ────────────────────────────────────────

def check_register_duplicate(name: str, password: str) -> str | None:
    """중복 여부 확인. 문제 있으면 에러 메시지 반환, 없으면 None."""
    with get_conn() as conn:
        # 이름 중복: 기존 활성 유저
        if conn.execute("SELECT 1 FROM users WHERE name = ? AND is_active = 1", (name,)).fetchone():
            return "이미 사용 중인 이름입니다."
        # 이름 중복: 대기 중인 신청자
        if conn.execute("SELECT 1 FROM pending_users WHERE name = ? AND status = 'pending'", (name,)).fetchone():
            return "이미 가입 신청 중인 이름입니다."
        # 비밀번호 중복: 기존 활성 유저
        if conn.execute("SELECT 1 FROM users WHERE password = ? AND is_active = 1", (password,)).fetchone():
            return "이미 사용 중인 비밀번호입니다. 다른 비밀번호를 사용하세요."
        # 비밀번호 중복: 대기 중인 신청자
        if conn.execute("SELECT 1 FROM pending_users WHERE password = ? AND status = 'pending'", (password,)).fetchone():
            return "이미 가입 신청에 사용된 비밀번호입니다. 다른 비밀번호를 사용하세요."
    return None


def create_pending_user(name: str, password: str, memo: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pending_users (name, password, memo) VALUES (?, ?, ?)",
            (name, password, memo)
        )
    return cur.lastrowid


def get_pending_users():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_users WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def approve_pending_user(pending_id: int, team_id: int):
    with get_conn() as conn:
        pending = conn.execute(
            "SELECT * FROM pending_users WHERE id = ?", (pending_id,)
        ).fetchone()
        if not pending:
            return None
        cur = conn.execute(
            "INSERT INTO users (name, password, role, team_id, is_active) VALUES (?,?,'editor',?,1)",
            (pending["name"], pending["password"], team_id)
        )
        conn.execute(
            "UPDATE pending_users SET status = 'approved' WHERE id = ?", (pending_id,)
        )
    return cur.lastrowid


def reject_pending_user(pending_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_users SET status = 'rejected' WHERE id = ?", (pending_id,)
        )


# ── Meetings ────────────────────────────────────────────

def get_all_meetings(viewer=None):
    """viewer: None=비로그인, dict=로그인 사용자. 가시성 규칙을 SQL에서 처리."""
    base = """SELECT m.*, u.name as author_name, u.id as author_id, t.name as team_name,
               (SELECT COUNT(*) FROM events e WHERE e.meeting_id = m.id AND e.deleted_at IS NULL) as event_count
               FROM meetings m
               LEFT JOIN users u ON m.created_by = u.id
               LEFT JOIN teams t ON m.team_id = t.id
               WHERE m.deleted_at IS NULL"""
    with get_conn() as conn:
        if viewer is None:
            rows = conn.execute(
                base + " AND m.is_public = 1 ORDER BY m.updated_at DESC"
            ).fetchall()
        elif viewer.get("role") == "admin":
            rows = conn.execute(
                base + " ORDER BY m.updated_at DESC"
            ).fetchall()
        else:
            uid = viewer["id"]
            tid = viewer.get("team_id")
            rows = conn.execute(
                base + """
                  AND (
                    m.created_by = ?
                    OR m.is_public = 1
                    OR (m.is_team_doc = 1 AND m.team_id = ?)
                    OR (m.is_team_doc = 0 AND m.team_share = 1 AND m.team_id = ?)
                  )
                  ORDER BY m.updated_at DESC""",
                (uid, tid, tid)
            ).fetchall()
    return [dict(r) for r in rows]


def get_meeting(meeting_id: int):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT m.*, u.name as author_name, t.name as team_name
               FROM meetings m
               LEFT JOIN users u ON m.created_by = u.id
               LEFT JOIN teams t ON m.team_id = t.id
               WHERE m.id = ? AND m.deleted_at IS NULL""",
            (meeting_id,)
        ).fetchone()
    return dict(row) if row else None


def update_meeting_visibility(meeting_id: int, is_team_doc: int, is_public: int, team_share: int) -> None:
    _team_share = 0 if is_team_doc else team_share
    with get_conn() as conn:
        conn.execute(
            "UPDATE meetings SET is_team_doc = ?, is_public = ?, team_share = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (is_team_doc, is_public, _team_share, meeting_id)
        )


def create_meeting(title: str, content: str, team_id, created_by: int,
                   meeting_date: str = None, is_team_doc: int = 1,
                   is_public: int = 0, team_share: int = 0) -> int:
    _team_share = 0 if is_team_doc else team_share
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO meetings (title, content, team_id, created_by, meeting_date, is_team_doc, is_public, team_share) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, content, team_id, created_by, meeting_date, is_team_doc, is_public, _team_share)
        )
    return cur.lastrowid


def update_meeting(meeting_id: int, title: str, content: str, edited_by: int,
                   meeting_date: str = None, is_team_doc: int = 1,
                   is_public: int = 0, team_share: int = 0):
    _team_share = 0 if is_team_doc else team_share
    with get_conn() as conn:
        current = conn.execute(
            "SELECT content FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if current:
            conn.execute(
                "INSERT INTO meeting_histories (meeting_id, content, edited_by) VALUES (?, ?, ?)",
                (meeting_id, current["content"], edited_by)
            )
            # 최근 3개만 유지
            conn.execute(
                "DELETE FROM meeting_histories WHERE meeting_id = ? AND id NOT IN "
                "(SELECT id FROM meeting_histories WHERE meeting_id = ? ORDER BY id DESC LIMIT 3)",
                (meeting_id, meeting_id)
            )
        conn.execute(
            "UPDATE meetings SET title = ?, content = ?, meeting_date = ?, is_team_doc = ?, "
            "is_public = ?, team_share = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, content, meeting_date, is_team_doc, is_public, _team_share, meeting_id)
        )


def delete_meeting(meeting_id: int, deleted_by: str = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE meetings SET deleted_at = ?, deleted_by = ? WHERE id = ?",
            (now, deleted_by, meeting_id)
        )


def get_meeting_histories(meeting_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT mh.*, u.name as editor_name
               FROM meeting_histories mh
               LEFT JOIN users u ON mh.edited_by = u.id
               WHERE mh.meeting_id = ?
               ORDER BY mh.edited_at DESC
               LIMIT 3""",
            (meeting_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def restore_meeting_from_history(meeting_id: int, history_id: int, restored_by: int) -> bool:
    with get_conn() as conn:
        hist = conn.execute(
            "SELECT * FROM meeting_histories WHERE id = ? AND meeting_id = ?",
            (history_id, meeting_id)
        ).fetchone()
        if not hist:
            return False
        # 현재 내용을 이력에 저장
        current = conn.execute("SELECT content FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
        if current:
            conn.execute(
                "INSERT INTO meeting_histories (meeting_id, content, edited_by) VALUES (?, ?, ?)",
                (meeting_id, current["content"], restored_by)
            )
        # 이력 내용으로 복원
        conn.execute(
            "UPDATE meetings SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (hist["content"], meeting_id)
        )
        # 최근 3개만 유지
        conn.execute(
            "DELETE FROM meeting_histories WHERE meeting_id = ? AND id NOT IN "
            "(SELECT id FROM meeting_histories WHERE meeting_id = ? ORDER BY id DESC LIMIT 3)",
            (meeting_id, meeting_id)
        )
        return True


def get_events_by_meeting(meeting_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE meeting_id = ? AND deleted_at IS NULL ORDER BY start_datetime",
            (meeting_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_done_project_names() -> list[str]:
    """완료(is_active=0) 처리된 프로젝트 이름 목록."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL"
        ).fetchall()
    return [r["name"] for r in rows]


def get_events_for_conflict_check(team_id: int | None = None) -> list[dict]:
    """중복 감지용: 과거 3개월 ~ 미래 12개월 이벤트 반환.

    team_id 전달 시 현재 팀 + 공용 일정(team_id IS NULL)을 포함.
    """
    with get_conn() as conn:
        if team_id is not None:
            rows = conn.execute(
                """SELECT id, title, start_datetime, end_datetime, all_day,
                          assignee, project, location, event_type
                   FROM events
                   WHERE date(start_datetime) >= date('now', '-3 months')
                     AND date(start_datetime) <= date('now', '+12 months')
                     AND deleted_at IS NULL
                     AND (event_type IS NULL OR event_type IN ('schedule', 'journal'))
                     AND (team_id = ? OR team_id IS NULL)
                   ORDER BY start_datetime""",
                (team_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT id, title, start_datetime, end_datetime, all_day,
                          assignee, project, location, event_type
                   FROM events
                   WHERE date(start_datetime) >= date('now', '-3 months')
                     AND date(start_datetime) <= date('now', '+12 months')
                     AND deleted_at IS NULL
                     AND (event_type IS NULL OR event_type IN ('schedule', 'journal'))
                   ORDER BY start_datetime"""
            ).fetchall()
    return [dict(r) for r in rows]

def get_events_by_date_range(start_date: str, end_date: str, team_id: int = None) -> list[dict]:
    """날짜 범위와 겹치는 이벤트 조회 (시작일·진행 중·종료일 모두 포함)"""
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE date(e.start_datetime) <= ?
                   AND COALESCE(date(e.end_datetime), date(e.start_datetime)) >= ?
                   AND e.team_id = ? AND e.deleted_at IS NULL
                   AND (e.event_type IS NULL OR e.event_type IN ('schedule', 'journal'))
                   ORDER BY e.start_datetime""",
                (end_date, start_date, team_id)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE date(e.start_datetime) <= ?
                   AND COALESCE(date(e.end_datetime), date(e.start_datetime)) >= ?
                   AND e.deleted_at IS NULL
                   AND (e.event_type IS NULL OR e.event_type IN ('schedule', 'journal'))
                   ORDER BY e.start_datetime""",
                (end_date, start_date)
            ).fetchall()
    return [dict(r) for r in rows]


def get_meetings_by_date_range(start_date: str, end_date: str,
                                team_id: int = None, created_by: int = None) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, content, meeting_date, created_at, created_by, team_id
               FROM meetings
               WHERE meeting_date BETWEEN ? AND ?
                 AND deleted_at IS NULL
                 AND title NOT LIKE '주간 업무 보고 (%)'
                 AND (
                   (is_team_doc = 1 AND team_id = ?)
                   OR created_by = ?
                 )
               ORDER BY meeting_date""",
            (start_date, end_date, team_id, created_by)
        ).fetchall()
    return [dict(r) for r in rows]


def get_checklists_by_date_range(start_date: str, end_date: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, project, title, content, created_by, updated_at
               FROM checklists
               WHERE substr(updated_at, 1, 10) BETWEEN ? AND ?
                 AND deleted_at IS NULL
               ORDER BY updated_at DESC""",
            (start_date, end_date)
        ).fetchall()
    return [dict(r) for r in rows]


def get_previous_weekly_report(base_date: str, team_id: int, created_by: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT id, title, content, meeting_date
               FROM meetings
               WHERE title LIKE '주간 업무 보고 (%)'
                 AND team_id = ?
                 AND created_by = ?
                 AND meeting_date < ?
                 AND deleted_at IS NULL
               ORDER BY meeting_date DESC
               LIMIT 1""",
            (team_id, created_by, base_date)
        ).fetchone()
    return dict(row) if row else None


# ── Project Colors ──────────────────────────────────────

def get_project_colors() -> dict:
    """projects 테이블에서 color가 설정된 {name: color} 딕셔너리 반환"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, color FROM projects WHERE color IS NOT NULL AND color != ''"
        ).fetchall()
    return {r["name"]: r["color"] for r in rows}


# ── User name change ─────────────────────────────────────

def update_user_name(user_id: int, new_name: str):
    with get_conn() as conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (new_name, user_id))


def count_active_admins() -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
        ).fetchone()
    return row[0] if row else 0


# ── Settings ─────────────────────────────────────────────


def update_event_visibility(event_id: int, is_public) -> None:
    # is_public: None=프로젝트 연동, 0=비공개, 1=공개
    with get_conn() as conn:
        conn.execute(
            "UPDATE events SET is_public = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (is_public, event_id)
        )


def get_setting(key: str, default: str = None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value)
        )


def delete_setting(key: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


# ── AVR ──────────────────────────────────────────────────

def set_user_avr_enabled(user_id: int, enabled: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET avr_enabled = ? WHERE id = ?",
            (1 if enabled else 0, user_id)
        )


def list_users_with_avr():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, role, avr_enabled FROM users WHERE role != 'admin' ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


# ── Meeting Locks ─────────────────────────────────────────

LOCK_TIMEOUT_MINUTES = 5


def acquire_meeting_lock(meeting_id: int, user_name: str, tab_token: str) -> bool:
    """잠금 획득. 이미 다른 탭이 유효한 잠금을 가지면 False 반환."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT tab_token FROM meeting_locks WHERE meeting_id = ? AND locked_at > ?",
            (meeting_id, threshold)
        ).fetchone()
        if existing and existing["tab_token"] != tab_token:
            return False
        conn.execute(
            "INSERT INTO meeting_locks (meeting_id, user_name, locked_at, tab_token) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(meeting_id) DO UPDATE SET user_name = excluded.user_name, locked_at = excluded.locked_at, tab_token = excluded.tab_token",
            (meeting_id, user_name, now, tab_token)
        )
    return True


def heartbeat_meeting_lock(meeting_id: int, tab_token: str) -> bool:
    """잠금 보유자가 heartbeat로 locked_at 갱신. 잠금 보유자 탭이 아니면 False."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT tab_token FROM meeting_locks WHERE meeting_id = ? AND locked_at > ?",
            (meeting_id, threshold)
        ).fetchone()
        if not existing or existing["tab_token"] != tab_token:
            return False
        conn.execute(
            "UPDATE meeting_locks SET locked_at = ? WHERE meeting_id = ?",
            (now, meeting_id)
        )
    return True


def release_meeting_lock(meeting_id: int, tab_token: str | None = None):
    """잠금 해제. tab_token 지정 시 해당 탭 것만, None이면 강제 해제."""
    with get_conn() as conn:
        if tab_token:
            conn.execute(
                "DELETE FROM meeting_locks WHERE meeting_id = ? AND tab_token = ?",
                (meeting_id, tab_token)
            )
        else:
            conn.execute("DELETE FROM meeting_locks WHERE meeting_id = ?", (meeting_id,))


def get_meeting_lock(meeting_id: int) -> dict | None:
    """현재 유효한 잠금 반환. 없으면 None."""
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_name, locked_at, tab_token FROM meeting_locks WHERE meeting_id = ? AND locked_at > ?",
            (meeting_id, threshold)
        ).fetchone()
    return dict(row) if row else None


# ── Checklists ────────────────────────────────────────────

def create_checklist(project: str, title: str, content: str, created_by: str, is_public: int = 0, team_id: int = None) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO checklists (project, title, content, created_by, created_at, updated_at, is_public, team_id) VALUES (?,?,?,?,?,?,?,?)",
            (project, title, content, created_by, now, now, is_public, team_id)
        )
    return cur.lastrowid


def get_checklists(project: str = None, viewer=None, active_only: bool | None = None, include_done_projects: bool = False) -> list:
    if include_done_projects:
        inactive_filter = ""
    else:
        inactive_filter = """
        AND (project IS NULL OR project = ''
             OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
    """
    # 3상태: is_public=1 항상 공개, is_public=NULL 프로젝트 연동, is_public=0 항상 비공개
    public_filter = """
        AND (
          is_public = 1
          OR (
            is_public IS NULL
            AND project IS NOT NULL AND project != ''
            AND project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
          )
        )
    """ if viewer is None else ""
    active_filter = ""
    if active_only is True:
        active_filter = " AND COALESCE(is_active, 1) = 1"
    elif active_only is False:
        active_filter = " AND COALESCE(is_active, 1) = 0"
    private_proj_filter = ""  # public_filter에 통합됨
    is_done_project_col = """
        CASE WHEN (project IS NOT NULL AND project != ''
             AND project IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
        THEN 1 ELSE 0 END as is_done_project"""
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"SELECT id, project, title, created_by, created_at, updated_at, is_public, is_locked, COALESCE(is_active,1) as is_active, {is_done_project_col} FROM checklists WHERE deleted_at IS NULL {inactive_filter}{public_filter}{private_proj_filter}{active_filter} ORDER BY updated_at DESC"
            ).fetchall()
        elif project == "":
            # 미지정 (project가 NULL 또는 빈 문자열인 항목)
            rows = conn.execute(
                f"SELECT id, project, title, created_by, created_at, updated_at, is_public, is_locked, COALESCE(is_active,1) as is_active, {is_done_project_col} FROM checklists WHERE (project IS NULL OR project = '') AND deleted_at IS NULL {public_filter}{private_proj_filter}{active_filter} ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, project, title, created_by, created_at, updated_at, is_public, is_locked, COALESCE(is_active,1) as is_active, {is_done_project_col} FROM checklists WHERE project = ? AND deleted_at IS NULL {inactive_filter}{public_filter}{private_proj_filter}{active_filter} ORDER BY updated_at DESC",
                (project,)
            ).fetchall()
    return [dict(r) for r in rows]


def set_checklist_active(checklist_id: int, is_active: int):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE checklists SET is_active = ?, updated_at = ? WHERE id = ?",
            (is_active, now, checklist_id)
        )


def set_checklist_is_locked(checklist_id: int, locked: int) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE checklists SET is_locked = ?, updated_at = ? WHERE id = ?",
            (1 if locked else 0, now, checklist_id)
        )


def update_checklist_visibility(checklist_id: int, is_public) -> None:
    # is_public: None=프로젝트 연동, 0=비공개, 1=공개
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE checklists SET is_public = ?, updated_at = ? WHERE id = ?",
            (is_public, now, checklist_id)
        )


def bulk_update_checklist_visibility(project: str | None, is_public: int, is_active: int | None = None, team_id: int | None = None) -> int:
    """특정 프로젝트(또는 미지정) 체크리스트 전체의 is_public을 일괄 변경. 변경된 행 수 반환."""
    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    active_clause = " AND is_active = ?" if is_active is not None else ""
    team_clause = " AND team_id = ?" if team_id is not None else ""
    with get_conn() as conn:
        if project:
            params = (is_public, now, project) + ((is_active,) if is_active is not None else ())
            cur = conn.execute(
                f"UPDATE checklists SET is_public = ?, updated_at = ? WHERE project = ? AND deleted_at IS NULL{active_clause}",
                params,
            )
        else:
            params = (is_public, now) + ((is_active,) if is_active is not None else ()) + ((team_id,) if team_id is not None else ())
            cur = conn.execute(
                f"UPDATE checklists SET is_public = ?, updated_at = ? WHERE (project IS NULL OR project = '') AND deleted_at IS NULL{active_clause}{team_clause}",
                params,
            )
    return cur.rowcount


def bulk_update_event_visibility(project: str | None, is_public: int, is_active: int | None = None, team_id: int | None = None) -> int:
    """특정 프로젝트(또는 미지정) 일정 전체의 is_public을 일괄 변경. 변경된 행 수 반환."""
    active_clause = " AND is_active = ?" if is_active is not None else ""
    team_clause = " AND team_id = ?" if team_id is not None else ""
    with get_conn() as conn:
        if project:
            params = (is_public, project) + ((is_active,) if is_active is not None else ())
            cur = conn.execute(
                f"UPDATE events SET is_public = ?, updated_at = CURRENT_TIMESTAMP WHERE project = ? AND deleted_at IS NULL{active_clause}",
                params,
            )
        else:
            params = (is_public,) + ((is_active,) if is_active is not None else ()) + ((team_id,) if team_id is not None else ())
            cur = conn.execute(
                f"UPDATE events SET is_public = ?, updated_at = CURRENT_TIMESTAMP WHERE (project IS NULL OR project = '') AND deleted_at IS NULL{active_clause}{team_clause}",
                params,
            )
    return cur.rowcount


def get_unassigned_checklists() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, project, title, created_by, created_at, updated_at "
            "FROM checklists WHERE (project IS NULL OR project = '') "
            "AND deleted_at IS NULL ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_checklist(checklist_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM checklists WHERE id = ? AND deleted_at IS NULL", (checklist_id,)
        ).fetchone()
    return dict(row) if row else None


def update_checklist(checklist_id: int, title: str, project: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE checklists SET title = ?, project = ?, updated_at = ? WHERE id = ?",
            (title, project, now, checklist_id)
        )


def update_checklist_content(checklist_id: int, content: str, edited_by: str = '', save_history: bool = True):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        if save_history:
            current = conn.execute(
                "SELECT content FROM checklists WHERE id = ?", (checklist_id,)
            ).fetchone()
            if current:
                conn.execute(
                    "INSERT INTO checklist_histories (checklist_id, content, edited_by) VALUES (?, ?, ?)",
                    (checklist_id, current["content"], edited_by)
                )
                # 최근 3개만 유지
                conn.execute(
                    "DELETE FROM checklist_histories WHERE checklist_id = ? AND id NOT IN "
                    "(SELECT id FROM checklist_histories WHERE checklist_id = ? ORDER BY id DESC LIMIT 3)",
                    (checklist_id, checklist_id)
                )
        conn.execute(
            "UPDATE checklists SET content = ?, updated_at = ? WHERE id = ?",
            (content, now, checklist_id)
        )


def get_checklist_histories(checklist_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM checklist_histories
               WHERE checklist_id = ?
               ORDER BY edited_at DESC
               LIMIT 3""",
            (checklist_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def restore_checklist_from_history(checklist_id: int, history_id: int, restored_by: str) -> bool:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        hist = conn.execute(
            "SELECT * FROM checklist_histories WHERE id = ? AND checklist_id = ?",
            (history_id, checklist_id)
        ).fetchone()
        if not hist:
            return False
        current = conn.execute("SELECT content FROM checklists WHERE id = ?", (checklist_id,)).fetchone()
        if current:
            conn.execute(
                "INSERT INTO checklist_histories (checklist_id, content, edited_by) VALUES (?, ?, ?)",
                (checklist_id, current["content"], restored_by)
            )
        conn.execute(
            "UPDATE checklists SET content = ?, updated_at = ? WHERE id = ?",
            (hist["content"], now, checklist_id)
        )
        conn.execute(
            "DELETE FROM checklist_histories WHERE checklist_id = ? AND id NOT IN "
            "(SELECT id FROM checklist_histories WHERE checklist_id = ? ORDER BY id DESC LIMIT 3)",
            (checklist_id, checklist_id)
        )
        return True


def delete_checklist(checklist_id: int, deleted_by: str = None, team_id: int = None):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE checklists SET deleted_at = ?, deleted_by = ?, team_id = ? WHERE id = ?",
            (now, deleted_by, team_id, checklist_id)
        )


def get_checklist_projects() -> list:
    """체크리스트에 사용된 프로젝트 목록 반환."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM checklists WHERE project != '' AND deleted_at IS NULL ORDER BY project"
        ).fetchall()
    return [r[0] for r in rows]


# ── Checklist Locks ───────────────────────────────────────

def acquire_checklist_lock(checklist_id: int, user_name: str, tab_token: str) -> bool:
    """잠금 획득. 이미 다른 탭이 유효한 잠금을 가지면 False 반환."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT tab_token FROM checklist_locks WHERE checklist_id = ? AND locked_at > ?",
            (checklist_id, threshold)
        ).fetchone()
        if existing and existing["tab_token"] != tab_token:
            return False
        conn.execute(
            "INSERT INTO checklist_locks (checklist_id, user_name, locked_at, tab_token) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(checklist_id) DO UPDATE SET user_name = excluded.user_name, locked_at = excluded.locked_at, tab_token = excluded.tab_token",
            (checklist_id, user_name, now, tab_token)
        )
    return True


def heartbeat_checklist_lock(checklist_id: int, tab_token: str) -> bool:
    """잠금 보유자가 heartbeat로 locked_at 갱신. 잠금 보유자 탭이 아니면 False."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT tab_token FROM checklist_locks WHERE checklist_id = ? AND locked_at > ?",
            (checklist_id, threshold)
        ).fetchone()
        if not existing or existing["tab_token"] != tab_token:
            return False
        conn.execute(
            "UPDATE checklist_locks SET locked_at = ? WHERE checklist_id = ?",
            (now, checklist_id)
        )
    return True


def release_checklist_lock(checklist_id: int, tab_token: str | None = None):
    """잠금 해제. tab_token 지정 시 해당 탭 것만, None이면 강제 해제."""
    with get_conn() as conn:
        if tab_token:
            conn.execute(
                "DELETE FROM checklist_locks WHERE checklist_id = ? AND tab_token = ?",
                (checklist_id, tab_token)
            )
        else:
            conn.execute("DELETE FROM checklist_locks WHERE checklist_id = ?", (checklist_id,))


def get_checklist_lock(checklist_id: int) -> dict | None:
    """현재 유효한 잠금 반환. 없으면 None."""
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=LOCK_TIMEOUT_MINUTES)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_name, locked_at, tab_token FROM checklist_locks WHERE checklist_id = ? AND locked_at > ?",
            (checklist_id, threshold)
        ).fetchone()
    return dict(row) if row else None


# ── Links ────────────────────────────────────────────────

def get_links(user_name: str, team_id):
    """개인 링크(본인) + 팀 링크(소속 팀) 반환"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, title, url, description, scope, team_id, created_by, created_at
            FROM links
            WHERE (scope = 'personal' AND created_by = ?)
               OR (scope = 'team' AND team_id = ?)
            ORDER BY scope DESC, created_at ASC
        """, (user_name, team_id)).fetchall()
    return [dict(r) for r in rows]


def create_link(title: str, url: str, description: str, scope: str, team_id, created_by: str) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO links (title, url, description, scope, team_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (title, url, description, scope, team_id, created_by))
        return cur.lastrowid


def update_link(link_id: int, title: str, url: str, description: str, user_name: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("""
            UPDATE links SET title=?, url=?, description=?
            WHERE id=? AND created_by=?
        """, (title, url, description, link_id, user_name))
        return cur.rowcount > 0


def delete_link(link_id: int, user_name: str, role: str) -> bool:
    with get_conn() as conn:
        if role == 'admin':
            cur = conn.execute("DELETE FROM links WHERE id=?", (link_id,))
        else:
            cur = conn.execute("DELETE FROM links WHERE id=? AND created_by=?", (link_id, user_name))
        return cur.rowcount > 0


# ── Trash ────────────────────────────────────────────────

def get_trash_items(team_id: int = None) -> dict:
    """휴지통 아이템 반환 — groups(프로젝트별 묶음) + unassigned(미지정)"""
    team_filter = "AND team_id = ?" if team_id else ""
    team_args = (team_id,) if team_id else ()

    with get_conn() as conn:
        # 삭제된 프로젝트 목록
        pj_rows = conn.execute(
            f"SELECT id, name, color, deleted_at, deleted_by, team_id "
            f"FROM projects WHERE deleted_at IS NOT NULL {team_filter} ORDER BY deleted_at DESC",
            team_args
        ).fetchall()

        groups = []
        for pj in pj_rows:
            proj_id = pj["id"]
            # 해당 프로젝트에 묶인 이벤트
            ev_items = conn.execute(
                """SELECT id, title, project, description, deleted_at, deleted_by, team_id,
                          start_datetime, end_datetime, event_type, trash_project_id
                   FROM events
                   WHERE deleted_at IS NOT NULL AND trash_project_id = ?
                     AND recurrence_parent_id IS NULL
                     AND (parent_event_id IS NULL
                          OR NOT EXISTS (SELECT 1 FROM events p WHERE p.id = events.parent_event_id AND p.deleted_at IS NOT NULL))
                   ORDER BY deleted_at DESC""",
                (proj_id,)
            ).fetchall()
            # 해당 프로젝트에 묶인 체크리스트
            cl_items = conn.execute(
                """SELECT id, title, content, project, deleted_at, deleted_by, team_id, trash_project_id
                   FROM checklists
                   WHERE deleted_at IS NOT NULL AND trash_project_id = ?
                   ORDER BY deleted_at DESC""",
                (proj_id,)
            ).fetchall()
            # 해당 프로젝트에 묶인 회의록
            mt_items = conn.execute(
                """SELECT id, title, content, deleted_at, deleted_by, team_id, trash_project_id
                   FROM meetings
                   WHERE deleted_at IS NOT NULL AND trash_project_id = ?
                   ORDER BY deleted_at DESC""",
                (proj_id,)
            ).fetchall()

            items = (
                [dict(r) | {"type": "event"}     for r in ev_items] +
                [dict(r) | {"type": "checklist"} for r in cl_items] +
                [dict(r) | {"type": "meeting"}   for r in mt_items]
            )
            items.sort(key=lambda x: x["deleted_at"] or "", reverse=True)

            groups.append({
                "project": dict(pj),
                "items":   items,
            })

        # 미지정 항목 (trash_project_id IS NULL)
        ev_unassigned = conn.execute(
            f"""SELECT id, title, project, description, deleted_at, deleted_by, team_id,
                       start_datetime, end_datetime, event_type, trash_project_id
                FROM events
                WHERE deleted_at IS NOT NULL AND trash_project_id IS NULL {team_filter}
                  AND recurrence_parent_id IS NULL
                  AND (parent_event_id IS NULL
                       OR NOT EXISTS (SELECT 1 FROM events p WHERE p.id = events.parent_event_id AND p.deleted_at IS NOT NULL))
                ORDER BY deleted_at DESC""",
            team_args
        ).fetchall()
        cl_unassigned = conn.execute(
            f"""SELECT id, title, content, project, deleted_at, deleted_by, team_id, trash_project_id
                FROM checklists
                WHERE deleted_at IS NOT NULL AND trash_project_id IS NULL {team_filter}
                ORDER BY deleted_at DESC""",
            team_args
        ).fetchall()
        mt_unassigned = conn.execute(
            f"""SELECT id, title, content, deleted_at, deleted_by, team_id, trash_project_id
                FROM meetings
                WHERE deleted_at IS NOT NULL AND trash_project_id IS NULL {team_filter}
                ORDER BY deleted_at DESC""",
            team_args
        ).fetchall()

    unassigned = (
        [dict(r) | {"type": "event"}     for r in ev_unassigned] +
        [dict(r) | {"type": "checklist"} for r in cl_unassigned] +
        [dict(r) | {"type": "meeting"}   for r in mt_unassigned]
    )
    unassigned.sort(key=lambda x: x["deleted_at"] or "", reverse=True)

    return {
        "groups":     groups,
        "unassigned": unassigned,
    }


def get_trash_item_team(item_type: str, item_id: int):
    """휴지통 항목의 team_id 반환 (권한 검사용). 항목 없으면 None."""
    table_map = {"event": "events", "meeting": "meetings", "checklist": "checklists", "project": "projects"}
    table = table_map.get(item_type)
    if not table:
        return None
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT team_id FROM {table} WHERE id = ? AND deleted_at IS NOT NULL",
            (item_id,)
        ).fetchone()
    return row["team_id"] if row else None


def restore_trash_item(item_type: str, item_id: int) -> bool:
    """휴지통에서 복원 (deleted_at = NULL)"""
    with get_conn() as conn:
        if item_type == "event":
            row = conn.execute("SELECT recurrence_parent_id FROM events WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return False
            # 부모 이벤트면 자식(반복 인스턴스 + 하위 업무)도 함께 복원
            conn.execute(
                "UPDATE events SET deleted_at = NULL, deleted_by = NULL, trash_project_id = NULL "
                "WHERE id = ? OR recurrence_parent_id = ? OR parent_event_id = ?",
                (item_id, item_id, item_id)
            )
        elif item_type == "meeting":
            conn.execute(
                "UPDATE meetings SET deleted_at = NULL, deleted_by = NULL, trash_project_id = NULL WHERE id = ?",
                (item_id,)
            )
        elif item_type == "checklist":
            conn.execute(
                "UPDATE checklists SET deleted_at = NULL, deleted_by = NULL, trash_project_id = NULL WHERE id = ?",
                (item_id,)
            )
        elif item_type == "project":
            row = conn.execute("SELECT id, name FROM projects WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return False
            proj_id = row["id"]
            # 프로젝트 엔티티 복원 (team_id 유지)
            conn.execute(
                "UPDATE projects SET deleted_at = NULL, deleted_by = NULL WHERE id = ?",
                (item_id,)
            )
            # trash_project_id로 연결된 이벤트 복원
            conn.execute(
                "UPDATE events SET deleted_at = NULL, deleted_by = NULL, trash_project_id = NULL "
                "WHERE trash_project_id = ? AND deleted_at IS NOT NULL",
                (proj_id,)
            )
            # trash_project_id로 연결된 체크리스트 복원
            conn.execute(
                "UPDATE checklists SET deleted_at = NULL, deleted_by = NULL, trash_project_id = NULL "
                "WHERE trash_project_id = ? AND deleted_at IS NOT NULL",
                (proj_id,)
            )
            # trash_project_id로 연결된 회의록 복원
            conn.execute(
                "UPDATE meetings SET deleted_at = NULL, deleted_by = NULL, trash_project_id = NULL "
                "WHERE trash_project_id = ? AND deleted_at IS NOT NULL",
                (proj_id,)
            )
        else:
            return False
    return True


def finalize_expired_done():
    """done 상태로 7일 경과한 일정을 is_active=0 으로 자동 완료 처리 (APScheduler에서 호출)"""
    with get_conn() as conn:
        conn.execute("""
            UPDATE events
            SET is_active = 0, is_public = 0
            WHERE kanban_status = 'done'
              AND (is_active IS NULL OR is_active = 1)
              AND done_at IS NOT NULL
              AND done_at <= datetime('now', '-7 days')
              AND deleted_at IS NULL
        """)


def cleanup_old_trash():
    """90일 초과 휴지통 항목 영구 삭제 (APScheduler에서 호출)"""
    threshold = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE deleted_at IS NOT NULL AND deleted_at < ?", (threshold,))
        conn.execute("DELETE FROM meetings WHERE deleted_at IS NOT NULL AND deleted_at < ?", (threshold,))
        conn.execute("DELETE FROM checklists WHERE deleted_at IS NOT NULL AND deleted_at < ?", (threshold,))
        conn.execute("DELETE FROM projects WHERE deleted_at IS NOT NULL AND deleted_at < ?", (threshold,))


# ── MCP 토큰 ─────────────────────────────────────────────

def get_user_by_mcp_token_hash(token_hash: str):
    """SHA-256 해시로 활성 사용자 조회 (is_active=1 필수)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE mcp_token_hash = ? AND is_active = 1",
            (token_hash,)
        ).fetchone()
    return dict(row) if row else None


def set_mcp_token_hash(user_id: int, token_hash: str, created_at: str) -> None:
    """MCP 토큰 해시 저장. UNIQUE 충돌 시 sqlite3.IntegrityError 발생 (호출자가 1회 재시도)"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET mcp_token_hash = ?, mcp_token_created_at = ? WHERE id = ?",
            (token_hash, created_at, user_id)
        )


def clear_mcp_token(user_id: int) -> None:
    """MCP 토큰 삭제 (hash와 created_at 모두 NULL)"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET mcp_token_hash = NULL, mcp_token_created_at = NULL WHERE id = ?",
            (user_id,)
        )


def get_mcp_token_meta(user_id: int) -> dict:
    """토큰 존재 여부와 발급 시각 반환. 평문 토큰 절대 반환 금지."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT mcp_token_hash, mcp_token_created_at FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    if not row:
        return {"has_token": False, "created_at": None}
    return {
        "has_token": bool(row["mcp_token_hash"]),
        "created_at": row["mcp_token_created_at"],
    }


def get_event_for_mcp(event_id: int) -> dict | None:
    """MCP용 이벤트 조회: ① visibility check → ② get_event() 호출.
    종료 프로젝트(is_active=0) 소속 이벤트, 삭제된 이벤트는 None 반환."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT 1 FROM events
               WHERE id = ?
                 AND deleted_at IS NULL
                 AND (project IS NULL OR project = ''
                      OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))""",
            (event_id,)
        ).fetchone()
    if not row:
        return None
    return get_event(event_id)


def get_projects_for_mcp(conn, include_inactive: bool = False) -> list[dict]:
    """MCP용 프로젝트 목록 조회.
    deleted_at IS NULL인 프로젝트만 반환.
    include_inactive=False(기본값)이면 is_active=1 조건 추가.
    반환 필드: name, color, is_active, start_date, end_date
    """
    query = "SELECT name, color, is_active, start_date, end_date FROM projects WHERE deleted_at IS NULL"
    if not include_inactive:
        query += " AND is_active = 1"
    query += " ORDER BY name"
    rows = conn.execute(query).fetchall()
    return [dict(r) for r in rows]


def get_events_filtered(
    conn,
    project: str | None = None,
    start_after: str | None = None,
    end_before: str | None = None,
) -> list[dict]:
    """MCP용 필터링된 이벤트 조회.
    기존 get_all_events()의 조건(deleted_at IS NULL, 비활성 프로젝트 제외)을 유지하면서
    추가 필터를 적용한다.
    - project: events.project = ? 조건
    - start_after: events.start_datetime >= ? 조건 (ISO 8601 문자열 비교)
    - end_before: events.end_datetime <= ? 조건
    파라미터가 None이면 해당 조건 생략.
    """
    query = """SELECT id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type
               FROM events
               WHERE deleted_at IS NULL
                 AND (project IS NULL OR project = ''
                      OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))"""
    params: list = []
    if project is not None:
        query += " AND project = ?"
        params.append(project)
    if start_after is not None:
        query += " AND start_datetime >= ?"
        params.append(start_after)
    if end_before is not None:
        query += " AND (end_datetime IS NULL OR end_datetime <= ?)"
        params.append(end_before)
    query += " ORDER BY start_datetime"
    rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_all_meetings_summary(viewer=None):
    """MCP list_documents용 경량 조회 — content 제외.
    가시성 로직은 get_all_meetings()와 동일.
    """
    base = """SELECT m.id, m.title, u.name as author_name, t.name as team_name, m.updated_at,
               (SELECT COUNT(*) FROM events e WHERE e.meeting_id = m.id AND e.deleted_at IS NULL) as event_count
               FROM meetings m
               LEFT JOIN users u ON m.created_by = u.id
               LEFT JOIN teams t ON m.team_id = t.id
               WHERE m.deleted_at IS NULL"""
    with get_conn() as conn:
        if viewer is None:
            rows = conn.execute(
                base + " AND m.is_public = 1 ORDER BY m.updated_at DESC"
            ).fetchall()
        elif viewer.get("role") == "admin":
            rows = conn.execute(
                base + " ORDER BY m.updated_at DESC"
            ).fetchall()
        else:
            uid = viewer["id"]
            tid = viewer.get("team_id")
            rows = conn.execute(
                base + """
                  AND (
                    m.created_by = ?
                    OR m.is_public = 1
                    OR (m.is_team_doc = 1 AND m.team_id = ?)
                    OR (m.is_team_doc = 0 AND m.team_share = 1 AND m.team_id = ?)
                  )
                  ORDER BY m.updated_at DESC""",
                (uid, tid, tid)
            ).fetchall()
    return [dict(r) for r in rows]


def get_checklists_summary(project: str = None, viewer=None) -> list:
    """MCP list_checklists용 경량 조회 — content 대신 item_count, done_count 계산.
    가시성 로직은 get_checklists()와 동일 (active_only/include_done_projects 미사용).
    content는 마크다운 형식이며 '- [ ]'(미완료), '- [x]'(완료) 패턴 사용.
    """
    import re
    inactive_filter = """
        AND (project IS NULL OR project = ''
             OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
    """
    public_filter = """
        AND (
          is_public = 1
          OR (
            is_public IS NULL
            AND project IS NOT NULL AND project != ''
            AND project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
          )
        )
    """ if viewer is None else ""
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"SELECT id, project, title, content, updated_at FROM checklists WHERE deleted_at IS NULL {inactive_filter}{public_filter} ORDER BY updated_at DESC"
            ).fetchall()
        elif project == "":
            rows = conn.execute(
                f"SELECT id, project, title, content, updated_at FROM checklists WHERE (project IS NULL OR project = '') AND deleted_at IS NULL {public_filter} ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, project, title, content, updated_at FROM checklists WHERE project = ? AND deleted_at IS NULL {inactive_filter}{public_filter} ORDER BY updated_at DESC",
                (project,)
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        content = d.pop("content") or ""
        item_count = len(re.findall(r'(?m)^\s*-\s+\[[ xX]\]', content))
        done_count = len(re.findall(r'(?m)^\s*-\s+\[[xX]\]', content))
        d["item_count"] = item_count
        d["done_count"] = done_count
        result.append(d)
    return result


def search_all(query: str, type: str = None, viewer=None,
               start_after: str | None = None, end_before: str | None = None) -> list[dict]:
    """이벤트·문서·체크리스트 통합 키워드 검색 (MCP search 도구용).
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - type: "event"|"document"|"checklist"|None(전체)
    - viewer: None=비로그인, dict=로그인 사용자
    - start_after/end_before: 이벤트 날짜 범위 (겹치는 이벤트 포함). 문서·체크리스트는 미적용.
    반환: 경량 필드 + type 필드 포함 (content 미포함)
    정렬: event → document → checklist 순
    """
    if not query or not query.strip():
        return []
    type_filter = type
    like = f"%{query}%"
    results: list[dict] = []

    with get_conn() as conn:
        # ── events ──────────────────────────────────────────────
        if type_filter is None or type_filter == "event":
            sql = """SELECT id, title, project, start_datetime, end_datetime, assignee, kanban_status
                   FROM events
                   WHERE deleted_at IS NULL
                     AND title LIKE ?
                     AND (project IS NULL OR project = ''
                          OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))"""
            params: list = [like]
            if start_after is not None:
                # end_datetime이 NULL이면 start_datetime을 종료로 간주
                sql += " AND (end_datetime IS NULL OR end_datetime >= ?)"
                params.append(start_after)
            if end_before is not None:
                sql += " AND start_datetime <= ?"
                params.append(end_before)
            sql += " ORDER BY start_datetime"
            rows = conn.execute(sql, params).fetchall()
            for r in rows:
                d = dict(r)
                d["type"] = "event"
                results.append(d)

        # ── meetings (documents) ────────────────────────────────
        if type_filter is None or type_filter == "document":
            base = """SELECT m.id, m.title, u.name as author_name, t.name as team_name, m.updated_at
                      FROM meetings m
                      LEFT JOIN users u ON m.created_by = u.id
                      LEFT JOIN teams t ON m.team_id = t.id
                      WHERE m.deleted_at IS NULL
                        AND (m.title LIKE ? OR m.content LIKE ?)"""
            like2 = (like, like)
            if viewer is None:
                rows = conn.execute(
                    base + " AND m.is_public = 1 ORDER BY m.updated_at DESC",
                    like2
                ).fetchall()
            elif viewer.get("role") == "admin":
                rows = conn.execute(
                    base + " ORDER BY m.updated_at DESC",
                    like2
                ).fetchall()
            else:
                uid = viewer["id"]
                tid = viewer.get("team_id")
                rows = conn.execute(
                    base + """
                      AND (
                        m.created_by = ?
                        OR m.is_public = 1
                        OR (m.is_team_doc = 1 AND m.team_id = ?)
                        OR (m.is_team_doc = 0 AND m.team_share = 1 AND m.team_id = ?)
                      )
                      ORDER BY m.updated_at DESC""",
                    (*like2, uid, tid, tid)
                ).fetchall()
            for r in rows:
                d = dict(r)
                d["type"] = "document"
                results.append(d)

        # ── checklists ──────────────────────────────────────────
        if type_filter is None or type_filter == "checklist":
            inactive_filter = """
                AND (project IS NULL OR project = ''
                     OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
            """
            public_filter = """
                AND (
                  is_public = 1
                  OR (
                    is_public IS NULL
                    AND project IS NOT NULL AND project != ''
                    AND project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
                  )
                )
            """ if viewer is None else ""
            rows = conn.execute(
                f"""SELECT id, title, project, updated_at
                    FROM checklists
                    WHERE deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {inactive_filter}{public_filter}
                    ORDER BY updated_at DESC""",
                (like, like)
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["type"] = "checklist"
                results.append(d)

    return results


def search_events_mcp(query: str, start_after: str | None = None,
                      end_before: str | None = None) -> list[dict]:
    """MCP search_events 도구용 이벤트 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - start_after/end_before: 날짜 겹침 조건 적용 (search_all events 부분과 동일 로직).
    반환: 경량 필드 목록 (id, title, project, start_datetime, end_datetime,
                          assignee, kanban_status, event_type)
    """
    if not query or not query.strip():
        return []
    like = f"%{query}%"
    sql = """SELECT id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type
             FROM events
             WHERE deleted_at IS NULL
               AND title LIKE ?
               AND (project IS NULL OR project = ''
                    OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))"""
    params: list = [like]
    if start_after is not None:
        sql += " AND (end_datetime IS NULL OR end_datetime >= ?)"
        params.append(start_after)
    if end_before is not None:
        sql += " AND start_datetime <= ?"
        params.append(end_before)
    sql += " ORDER BY start_datetime"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_kanban_summary(project: str | None = None, viewer=None) -> list[dict]:
    """MCP list_kanban 도구용 칸반 항목 경량 조회.
    get_kanban_events()의 필터 조건을 재사용하되 경량 필드만 SELECT.
    - project: None=전체, ""=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
    - viewer: None=공개 항목만, dict=인증 사용자(비공개 프로젝트 포함)
    반환 필드: id, title, project, kanban_status, priority, assignee, start_datetime, end_datetime
    """
    private_clause = """
        AND (
          e.is_public = 1
          OR (
            e.is_public IS NULL
            AND e.project IS NOT NULL AND e.project != ''
            AND e.project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
          )
        )
    """ if viewer is None else ""
    base_filter = f"""
        AND (
            e.kanban_status IS NOT NULL
            OR (e.project IS NULL OR e.project = '')
        )
        AND (e.project IS NULL OR e.project = '' OR e.project NOT IN (
            SELECT name FROM projects WHERE is_active = 0
        ))
        AND (e.is_active IS NULL OR e.is_active = 1)
        AND (e.kanban_hidden IS NULL OR e.kanban_hidden = 0)
        AND (e.done_at IS NULL OR e.done_at > datetime('now', '-7 days'))
        AND (e.event_type IS NULL OR e.event_type = 'schedule')
        AND e.recurrence_parent_id IS NULL
        AND e.deleted_at IS NULL
        {private_clause}
    """
    select = "SELECT e.id, e.title, e.project, e.kanban_status, e.priority, e.assignee, e.start_datetime, e.end_datetime FROM events e"
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"{select} WHERE 1=1 {base_filter} ORDER BY e.start_datetime"
            ).fetchall()
        elif project == "":
            rows = conn.execute(
                f"{select} WHERE (e.project IS NULL OR e.project = '') {base_filter} ORDER BY e.start_datetime"
            ).fetchall()
        else:
            rows = conn.execute(
                f"{select} WHERE e.project = ? {base_filter} ORDER BY e.start_datetime",
                (project,)
            ).fetchall()
    return [dict(r) for r in rows]


def search_kanban_mcp(query: str, project: str | None = None, viewer=None) -> list[dict]:
    """MCP search_kanban 도구용 칸반 항목 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - project: None=전체, ""=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
    - viewer: None=공개 항목만, dict=인증 사용자(비공개 프로젝트 포함)
    반환 필드: id, title, project, kanban_status, priority, assignee
    """
    if not query or not query.strip():
        return []
    like = f"%{query}%"
    private_clause = """
        AND (
          e.is_public = 1
          OR (
            e.is_public IS NULL
            AND e.project IS NOT NULL AND e.project != ''
            AND e.project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
          )
        )
    """ if viewer is None else ""
    base_filter = f"""
        AND (
            e.kanban_status IS NOT NULL
            OR (e.project IS NULL OR e.project = '')
        )
        AND (e.project IS NULL OR e.project = '' OR e.project NOT IN (
            SELECT name FROM projects WHERE is_active = 0
        ))
        AND (e.is_active IS NULL OR e.is_active = 1)
        AND (e.kanban_hidden IS NULL OR e.kanban_hidden = 0)
        AND (e.done_at IS NULL OR e.done_at > datetime('now', '-7 days'))
        AND (e.event_type IS NULL OR e.event_type = 'schedule')
        AND e.recurrence_parent_id IS NULL
        AND e.deleted_at IS NULL
        {private_clause}
    """
    select = "SELECT e.id, e.title, e.project, e.kanban_status, e.priority, e.assignee FROM events e"
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"{select} WHERE e.title LIKE ? {base_filter} ORDER BY e.start_datetime",
                (like,)
            ).fetchall()
        elif project == "":
            rows = conn.execute(
                f"{select} WHERE e.title LIKE ? AND (e.project IS NULL OR e.project = '') {base_filter} ORDER BY e.start_datetime",
                (like,)
            ).fetchall()
        else:
            rows = conn.execute(
                f"{select} WHERE e.title LIKE ? AND e.project = ? {base_filter} ORDER BY e.start_datetime",
                (like, project)
            ).fetchall()
    return [dict(r) for r in rows]


def search_documents_mcp(query: str, viewer=None) -> list[dict]:
    """MCP search_documents 도구용 문서 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - viewer: None=공개 문서만, dict=인증 사용자(열람 가능 문서)
    가시성 로직은 search_all의 meetings 부분과 동일.
    반환 필드: id, title, author_name, team_name, updated_at
    """
    if not query or not query.strip():
        return []
    like = f"%{query}%"
    base = """SELECT m.id, m.title, u.name as author_name, t.name as team_name, m.updated_at
              FROM meetings m
              LEFT JOIN users u ON m.created_by = u.id
              LEFT JOIN teams t ON m.team_id = t.id
              WHERE m.deleted_at IS NULL
                AND (m.title LIKE ? OR m.content LIKE ?)"""
    with get_conn() as conn:
        if viewer is None:
            rows = conn.execute(
                base + " AND m.is_public = 1 ORDER BY m.updated_at DESC",
                (like, like)
            ).fetchall()
        elif viewer.get("role") == "admin":
            rows = conn.execute(
                base + " ORDER BY m.updated_at DESC",
                (like, like)
            ).fetchall()
        else:
            uid = viewer["id"]
            tid = viewer.get("team_id")
            rows = conn.execute(
                base + """
                  AND (
                    m.created_by = ?
                    OR m.is_public = 1
                    OR (m.is_team_doc = 1 AND m.team_id = ?)
                    OR (m.is_team_doc = 0 AND m.team_share = 1 AND m.team_id = ?)
                  )
                  ORDER BY m.updated_at DESC""",
                (like, like, uid, tid, tid)
            ).fetchall()
    return [dict(r) for r in rows]


def search_checklists_mcp(query: str, project: str | None = None, viewer=None) -> list[dict]:
    """MCP search_checklists 도구용 체크리스트 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - project: None=전체, ""=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
    - viewer: None=공개 항목만, dict=인증 사용자
    가시성·필터 로직은 search_all의 checklists 부분과 동일.
    반환 필드: id, title, project, updated_at, item_count, done_count
    """
    import re
    if not query or not query.strip():
        return []
    like = f"%{query}%"
    inactive_filter = """
        AND (project IS NULL OR project = ''
             OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
    """
    public_filter = """
        AND (
          is_public = 1
          OR (
            is_public IS NULL
            AND project IS NOT NULL AND project != ''
            AND project NOT IN (SELECT name FROM projects WHERE is_private = 1 AND deleted_at IS NULL)
          )
        )
    """ if viewer is None else ""
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"""SELECT id, title, project, content, updated_at FROM checklists
                    WHERE deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {inactive_filter}{public_filter}
                    ORDER BY updated_at DESC""",
                (like, like)
            ).fetchall()
        elif project == "":
            rows = conn.execute(
                f"""SELECT id, title, project, content, updated_at FROM checklists
                    WHERE (project IS NULL OR project = '')
                      AND deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {public_filter}
                    ORDER BY updated_at DESC""",
                (like, like)
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT id, title, project, content, updated_at FROM checklists
                    WHERE project = ?
                      AND deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {inactive_filter}{public_filter}
                    ORDER BY updated_at DESC""",
                (project, like, like)
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        content = d.pop("content") or ""
        item_count = len(re.findall(r'(?m)^\s*-\s+\[[ xX]\]', content))
        done_count = len(re.findall(r'(?m)^\s*-\s+\[[xX]\]', content))
        d["item_count"] = item_count
        d["done_count"] = done_count
        result.append(d)
    return result
