import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = "events.db"


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
                meeting_id INTEGER
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
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

        # ── 기존 테이블 마이그레이션 ──
        _migrate(conn, "events", [
            ("project",       "TEXT"),
            ("assignee",      "TEXT"),
            ("all_day",       "INTEGER DEFAULT 0"),
            ("team_id",       "INTEGER"),
            ("meeting_id",    "INTEGER"),
            ("kanban_status", "TEXT"),
            ("priority",      "TEXT DEFAULT 'normal'"),
        ])
        _migrate(conn, "users", [
            ("password",   "TEXT NOT NULL DEFAULT ''"),
            ("team_id",    "INTEGER"),
            ("is_active",  "INTEGER DEFAULT 1"),
            ("created_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
        ])
        _migrate(conn, "sessions", [
            ("expires_at", "TEXT"),
        ])
        _migrate(conn, "meetings", [
            ("meeting_date", "TEXT"),
        ])

        # ── 시드 데이터 ──
        if not conn.execute("SELECT 1 FROM teams LIMIT 1").fetchone():
            conn.execute("INSERT INTO teams (name) VALUES ('관리팀')")
        if not conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            team_id = conn.execute("SELECT id FROM teams LIMIT 1").fetchone()[0]
            conn.execute(
                "INSERT INTO users (name, password, role, team_id, is_active) VALUES (?,?,'admin',?,1)",
                ("admin", "admin1234", team_id)
            )


def _migrate(conn, table: str, columns: list):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
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
        rows = conn.execute("SELECT * FROM events ORDER BY start_datetime").fetchall()
    return [dict(r) for r in rows]


def get_event(event_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def create_event(data: dict) -> int:
    data.setdefault("team_id", None)
    data.setdefault("meeting_id", None)
    data.setdefault("kanban_status", None)
    data.setdefault("priority", "normal")
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (title, team_id, project, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority)
               VALUES
               (:title, :team_id, :project, :description, :location, :assignee, :all_day,
                :start_datetime, :end_datetime, :created_by, :source, :meeting_id,
                :kanban_status, :priority)""",
            data,
        )
    return cur.lastrowid


def update_event(event_id: int, data: dict):
    data["id"] = event_id
    with get_conn() as conn:
        conn.execute(
            """UPDATE events SET
                title          = :title,
                project        = :project,
                description    = :description,
                location       = :location,
                assignee       = :assignee,
                all_day        = :all_day,
                start_datetime = :start_datetime,
                end_datetime   = :end_datetime,
                kanban_status  = :kanban_status,
                priority       = :priority,
                updated_at     = CURRENT_TIMESTAMP
               WHERE id = :id""",
            data,
        )


def delete_event(event_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def get_kanban_events(team_id: int = None) -> list[dict]:
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                "SELECT * FROM events WHERE kanban_status IS NOT NULL AND team_id = ? ORDER BY start_datetime",
                (team_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM events WHERE kanban_status IS NOT NULL ORDER BY start_datetime"
            ).fetchall()
    return [dict(r) for r in rows]


def update_kanban_status(event_id: int, kanban_status, priority: str = None):
    with get_conn() as conn:
        if priority is not None:
            conn.execute(
                "UPDATE events SET kanban_status = ?, priority = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (kanban_status, priority, event_id)
            )
        else:
            conn.execute(
                "UPDATE events SET kanban_status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (kanban_status, event_id)
            )


def get_projects() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' ORDER BY project"
        ).fetchall()
    return [row[0] for row in rows]


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
    expires_at = None
    if role == "admin":
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
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

def get_all_meetings():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT m.*, u.name as author_name, t.name as team_name,
               (SELECT COUNT(*) FROM events e WHERE e.meeting_id = m.id) as event_count
               FROM meetings m
               LEFT JOIN users u ON m.created_by = u.id
               LEFT JOIN teams t ON m.team_id = t.id
               ORDER BY m.updated_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_meeting(meeting_id: int):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT m.*, u.name as author_name, t.name as team_name
               FROM meetings m
               LEFT JOIN users u ON m.created_by = u.id
               LEFT JOIN teams t ON m.team_id = t.id
               WHERE m.id = ?""",
            (meeting_id,)
        ).fetchone()
    return dict(row) if row else None


def create_meeting(title: str, content: str, team_id, created_by: int, meeting_date: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO meetings (title, content, team_id, created_by, meeting_date) VALUES (?, ?, ?, ?, ?)",
            (title, content, team_id, created_by, meeting_date)
        )
    return cur.lastrowid


def update_meeting(meeting_id: int, title: str, content: str, edited_by: int, meeting_date: str = None):
    with get_conn() as conn:
        current = conn.execute(
            "SELECT content FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if current:
            conn.execute(
                "INSERT INTO meeting_histories (meeting_id, content, edited_by) VALUES (?, ?, ?)",
                (meeting_id, current["content"], edited_by)
            )
        conn.execute(
            "UPDATE meetings SET title = ?, content = ?, meeting_date = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, content, meeting_date, meeting_id)
        )


def delete_meeting(meeting_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM meeting_histories WHERE meeting_id = ?", (meeting_id,))
        conn.execute("UPDATE events SET meeting_id = NULL WHERE meeting_id = ?", (meeting_id,))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))


def get_meeting_histories(meeting_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT mh.*, u.name as editor_name
               FROM meeting_histories mh
               LEFT JOIN users u ON mh.edited_by = u.id
               WHERE mh.meeting_id = ?
               ORDER BY mh.edited_at DESC""",
            (meeting_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_events_by_meeting(meeting_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE meeting_id = ? ORDER BY start_datetime",
            (meeting_id,)
        ).fetchall()
    return [dict(r) for r in rows]
