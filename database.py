import sqlite3
from contextlib import contextmanager

DB_PATH = "events.db"


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
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
                source TEXT DEFAULT 'manual'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT DEFAULT 'viewer'
            )
        """)
        # 기존 DB 마이그레이션 (컬럼이 없으면 추가)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
        for col, definition in [
            ("project",  "TEXT"),
            ("assignee", "TEXT"),
            ("all_day",  "INTEGER DEFAULT 0"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {definition}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_all_events():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY start_datetime").fetchall()
    return [dict(r) for r in rows]


def get_event(event_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def create_event(data: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (title, project, description, location, assignee, all_day, start_datetime, end_datetime, created_by, source)
               VALUES
               (:title, :project, :description, :location, :assignee, :all_day, :start_datetime, :end_datetime, :created_by, :source)""",
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
                updated_at     = CURRENT_TIMESTAMP
               WHERE id = :id""",
            data,
        )


def delete_event(event_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def get_projects() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' ORDER BY project"
        ).fetchall()
    return [row[0] for row in rows]
