import os
import sqlite3
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
                is_team_doc INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # 기존 DB에 is_team_doc 컬럼이 없는 경우 추가
        try:
            conn.execute("ALTER TABLE meetings ADD COLUMN is_team_doc INTEGER DEFAULT 1")
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

        # ── 기존 테이블 마이그레이션 ──
        _migrate(conn, "events", [
            ("project",        "TEXT"),
            ("assignee",       "TEXT"),
            ("all_day",        "INTEGER DEFAULT 0"),
            ("team_id",        "INTEGER"),
            ("meeting_id",     "INTEGER"),
            ("kanban_status",  "TEXT"),
            ("priority",       "TEXT DEFAULT 'normal'"),
            ("is_active",      "INTEGER DEFAULT 1"),
            ("kanban_hidden",  "INTEGER DEFAULT 0"),
            ("done_at",        "TEXT DEFAULT NULL"),
        ])
        # 기존 done 상태 일정에 done_at 백필
        conn.execute(
            "UPDATE events SET done_at = updated_at WHERE kanban_status = 'done' AND done_at IS NULL"
        )
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
        # ── settings ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

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
    # kanban_status 변경에 따른 done_at 관리
    new_status = data.get("kanban_status")
    if new_status == 'done':
        data["done_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    elif new_status is not None:
        data["done_at"] = None
    else:
        # kanban_status가 None이면 기존 done_at 유지 (SELECT 후 판단 불필요, 그냥 NULL)
        data.setdefault("done_at", None)
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
                done_at        = :done_at,
                updated_at     = CURRENT_TIMESTAMP
               WHERE id = :id""",
            data,
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


def delete_event(event_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM events WHERE id = ?", (event_id,))


def get_kanban_events(team_id: int = None) -> list[dict]:
    # 조건:
    #   - kanban_status가 설정된 일정, 또는
    #   - 프로젝트 없는(미지정) 일정 (kanban_status 없어도 Backlog로 표시)
    # 제외:
    #   - 종료된 프로젝트 소속 일정
    #   - 완료 처리된 미지정 일정 (is_active = 0)
    base_filter = """
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


def get_project_timeline(team_id: int = None) -> list[dict]:
    """팀 → 프로젝트 2단계 그룹으로 일정 반환 (프로젝트 없는 일정은 '미지정'으로 묶음)"""
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE e.team_id = ?
                   ORDER BY e.start_datetime""",
                (team_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   ORDER BY e.start_datetime"""
            ).fetchall()
        # projects 테이블에서 메타 조회
        proj_meta_rows = conn.execute(
            "SELECT name, color, start_date, end_date, is_active FROM projects"
        ).fetchall()
    proj_meta = {r["name"]: dict(r) for r in proj_meta_rows}
    # 비활성(종료) 프로젝트 이름 집합
    inactive = {name for name, m in proj_meta.items() if m.get("is_active") == 0}

    # team_name → project → events (비활성 프로젝트 제외)
    teams: dict[str, dict[str, list]] = {}
    for row in rows:
        d = dict(row)
        tname = d.get("team_name") or "미분류"
        p = d["project"] if d.get("project") and d["project"].strip() else "미지정"
        if p in inactive:
            continue  # 종료된 프로젝트 건너뜀
        if p == "미지정" and d.get("is_active") == 0:
            continue  # 완료 처리된 미지정 일정 건너뜀
        if d.get("kanban_hidden") == 1:
            continue  # 칸반/간트 숨김 처리된 일정 건너뜀
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
            })
        result.append({"team_name": tname, "projects": proj_list})
    return result


def get_projects() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' ORDER BY project"
        ).fetchall()
    return [row[0] for row in rows]


# ── Project Management ───────────────────────────────────

def get_all_projects_with_events() -> list[dict]:
    """프로젝트 목록 + 각 프로젝트의 일정 반환 (projects 테이블 + events.project 합산)"""
    with get_conn() as conn:
        # projects 테이블의 프로젝트
        proj_rows = conn.execute("SELECT * FROM projects ORDER BY is_active DESC, name").fetchall()
        # events에서 project 이름 목록 (projects 테이블에 없는 것도 포함)
        ev_proj_rows = conn.execute(
            "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != ''"
        ).fetchall()
        # 이벤트들
        ev_rows = conn.execute(
            """SELECT e.*, t.name as team_name
               FROM events e LEFT JOIN teams t ON e.team_id = t.id
               ORDER BY e.start_datetime"""
        ).fetchall()

    # projects 테이블 기반 dict
    proj_map: dict[str, dict] = {}
    for r in proj_rows:
        proj_map[r["name"]] = {
            "id": r["id"], "name": r["name"], "color": r["color"],
            "start_date": r["start_date"], "end_date": r["end_date"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "memo": r["memo"],
            "events": [],
        }

    # events.project에만 있는 프로젝트도 추가
    for r in ev_proj_rows:
        name = r["project"]
        if name not in proj_map:
            proj_map[name] = {"id": None, "name": name, "color": None,
                              "start_date": None, "end_date": None, "is_active": 1,
                              "memo": None, "events": []}

    # 이벤트 분류
    unset_events = []
    for r in ev_rows:
        d = dict(r)
        p = d.get("project") or ""
        if p.strip():
            if p not in proj_map:
                proj_map[p] = {"id": None, "name": p, "color": None,
                               "start_date": None, "end_date": None, "is_active": 1,
                               "memo": None, "events": []}
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


def create_project(name: str, color: str = None, memo: str = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, color, memo) VALUES (?, ?, ?)", (name, color, memo)
        )
    return cur.lastrowid


def rename_project(old_name: str, new_name: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET name = ? WHERE name = ?", (new_name, old_name)
        )
        conn.execute(
            "UPDATE events SET project = ? WHERE project = ?", (new_name, old_name)
        )


def delete_project(name: str, delete_events: bool = False):
    with get_conn() as conn:
        if delete_events:
            conn.execute("DELETE FROM events WHERE project = ?", (name,))
        else:
            conn.execute("UPDATE events SET project = NULL WHERE project = ?", (name,))
        conn.execute("DELETE FROM projects WHERE name = ?", (name,))


def update_event_active_status(event_id: int, is_active: int):
    with get_conn() as conn:
        conn.execute("UPDATE events SET is_active = ? WHERE id = ?", (is_active, event_id))


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
            """SELECT m.*, u.name as author_name, u.id as author_id, t.name as team_name,
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


def create_meeting(title: str, content: str, team_id, created_by: int, meeting_date: str = None, is_team_doc: int = 1) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO meetings (title, content, team_id, created_by, meeting_date, is_team_doc) VALUES (?, ?, ?, ?, ?, ?)",
            (title, content, team_id, created_by, meeting_date, is_team_doc)
        )
    return cur.lastrowid


def update_meeting(meeting_id: int, title: str, content: str, edited_by: int, meeting_date: str = None, is_team_doc: int = 1):
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
            "UPDATE meetings SET title = ?, content = ?, meeting_date = ?, is_team_doc = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, content, meeting_date, is_team_doc, meeting_id)
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


def get_events_for_conflict_check() -> list[dict]:
    """중복 감지용: 과거 3개월 ~ 미래 12개월 이벤트 title/date/assignee 반환"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, title, start_datetime, assignee
               FROM events
               WHERE date(start_datetime) >= date('now', '-3 months')
                 AND date(start_datetime) <= date('now', '+12 months')
               ORDER BY start_datetime"""
        ).fetchall()
    return [dict(r) for r in rows]

def get_events_by_date_range(start_date: str, end_date: str, team_id: int = None) -> list[dict]:
    """날짜 범위로 이벤트 조회 (start_date ~ end_date 포함)"""
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE date(e.start_datetime) >= ? AND date(e.start_datetime) <= ?
                   AND e.team_id = ?
                   ORDER BY e.start_datetime""",
                (start_date, end_date, team_id)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE date(e.start_datetime) >= ? AND date(e.start_datetime) <= ?
                   ORDER BY e.start_datetime""",
                (start_date, end_date)
            ).fetchall()
    return [dict(r) for r in rows]


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

def update_event_kanban_hidden(event_id: int, hidden: bool):
    with get_conn() as conn:
        conn.execute("UPDATE events SET kanban_hidden = ? WHERE id = ?", (1 if hidden else 0, event_id))


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
