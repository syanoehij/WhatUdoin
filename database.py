import json
import os
import secrets
import sqlite3
import string
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import passwords

# PyInstaller 번들 시 exe 옆 디렉토리, 개발 시 소스 파일 디렉토리
_RUN_DIR = Path(os.environ.get("WHATUDOIN_RUN_DIR", Path(__file__).parent))
DB_PATH  = str(_RUN_DIR / "whatudoin.db")

_SQLITE_TIMEOUT_SECONDS = 5
_SQLITE_BUSY_TIMEOUT_MS = 5000
_SQLITE_CACHE_SIZE = -8000
_SQLITE_SYNCHRONOUS_DEFAULT = "NORMAL"
_SQLITE_SYNCHRONOUS_ENV = "WHATUDOIN_SYNCHRONOUS_MODE"
_SQLITE_SYNCHRONOUS_ALLOWED = {"NORMAL", "FULL"}

_WAL_MODE_READY = False
_WAL_MODE_LOCK = threading.Lock()


def init_db():
    with get_conn() as conn:
        # ── events ──
        # 팀 기능 그룹 A #2: project_id 컬럼 추가 (NULL 유지, 백필은 #5 책임).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                team_id INTEGER,
                project TEXT,
                project_id INTEGER,
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
        # 팀 기능 그룹 A #2: name_norm(NFC+casefold), deleted_at(소프트 삭제) 추가.
        # name UNIQUE는 #5/#7 후속 사이클에서 name_norm UNIQUE로 대체될 예정 — 현 단계에서는 유지.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                name_norm TEXT,
                deleted_at TEXT DEFAULT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── users ──
        # 팀 기능 그룹 A #2: name_norm, password_hash 컬럼 추가(컬럼만 — 백필/UNIQUE는 후속 사이클).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                name_norm TEXT,
                password TEXT NOT NULL DEFAULT '',
                password_hash TEXT,
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
        # ── checklists ──
        # 팀 기능 그룹 A #2: 후행 코드(line ~245의 UPDATE checklists)와 인덱스
        # (line ~360의 CREATE INDEX … ON checklists)가 빈 DB에서도 깨지지 않도록
        # checklists CREATE를 위로 끌어올렸다. 본 정의는 후행 executescript의
        # checklists 정의와 동기화되어야 하며, 후행은 CREATE TABLE IF NOT EXISTS
        # 라 노옵으로 끝난다 (checklist_locks만 실제 생성).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS checklists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL DEFAULT '',
                project_id INTEGER,
                title      TEXT NOT NULL,
                content    TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                is_public  INTEGER NOT NULL DEFAULT 0,
                is_locked  INTEGER NOT NULL DEFAULT 0,
                deleted_at TEXT DEFAULT NULL,
                deleted_by TEXT DEFAULT NULL,
                team_id    INTEGER DEFAULT NULL,
                is_active  INTEGER DEFAULT 1,
                attachments TEXT DEFAULT '[]',
                trash_project_id INTEGER NULL
            )
        """)
        # ── projects ──
        # 팀 기능 그룹 A #2: 빈 DB 신규 생성 시 name UNIQUE 제거 + name_norm 추가.
        # 기존 DB의 name UNIQUE 제거는 phase 1 본문에서 테이블 재구성으로 처리.
        # projects(team_id, name_norm) UNIQUE는 #5 후속 사이클 책임 — 본 사이클에서는 미생성.
        # 추가: 후행 _migrate가 ALTER로 더하던 컬럼들을 CREATE에 흡수해, 빈 DB에서도
        # 후행 UPDATE 문(line 263 deleted_at 백필 등)이 컬럼 미존재로 실패하지 않도록 한다.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                name_norm TEXT,
                color TEXT,
                start_date TEXT,
                end_date TEXT,
                is_active INTEGER DEFAULT 1,
                memo TEXT,
                is_private INTEGER DEFAULT 0,
                deleted_at TEXT DEFAULT NULL,
                deleted_by TEXT DEFAULT NULL,
                team_id INTEGER,
                is_hidden INTEGER DEFAULT 0,
                owner_id INTEGER,
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
        if _table_exists(conn, "checklists") and not conn.execute("SELECT 1 FROM settings WHERE key='ck_fix_mijijeong_project_v1'").fetchone():
            conn.execute("UPDATE checklists SET project = '' WHERE project = '미지정'")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('ck_fix_mijijeong_project_v1', '1')")
        # events의 '미지정' project 값 NULL로 정리, 프로젝트 테이블에서 '미지정' 삭제
        if not conn.execute("SELECT 1 FROM settings WHERE key='fix_mijijeong_all_v1'").fetchone():
            conn.execute("UPDATE events SET project = NULL WHERE project = '미지정' AND deleted_at IS NULL")
            if _table_exists(conn, "checklists"):
                conn.execute("UPDATE checklists SET project = '' WHERE project = '미지정' AND deleted_at IS NULL")
            if _table_exists(conn, "projects"):
                conn.execute("UPDATE projects SET deleted_at = datetime('now') WHERE name = '미지정' AND deleted_at IS NULL")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fix_mijijeong_all_v1', '1')")
        _migrate(conn, "meetings", [
            ("deleted_at", "TEXT DEFAULT NULL"),
            ("deleted_by", "TEXT DEFAULT NULL"),
        ])
        _migrate(conn, "projects", [
            ("deleted_at", "TEXT DEFAULT NULL"),
            ("deleted_by", "TEXT DEFAULT NULL"),
            ("team_id",    "INTEGER DEFAULT NULL"),
            ("is_hidden",  "INTEGER DEFAULT 0"),
            ("owner_id",   "INTEGER"),
        ])
        if _table_exists(conn, "projects") and _table_exists(conn, "users"):
            conn.execute("""
                UPDATE projects
                   SET team_id = (SELECT u.team_id FROM users u WHERE u.id = projects.owner_id)
                 WHERE is_hidden = 1
                   AND team_id IS NULL
                   AND owner_id IS NOT NULL
                   AND EXISTS (
                       SELECT 1 FROM users u
                        WHERE u.id = projects.owner_id
                          AND u.team_id IS NOT NULL
                   )
            """)
        # ── project_members (히든 프로젝트 멤버) ──
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_members (
                project_id INTEGER NOT NULL,
                user_id    INTEGER NOT NULL,
                added_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (project_id, user_id)
            )
        """)
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
        _migrate(conn, "meetings", [
            ("attachments", "TEXT DEFAULT '[]'"),
        ])
        _migrate(conn, "checklists", [
            ("attachments", "TEXT DEFAULT '[]'"),
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
        # 팀 기능 그룹 A #2: team_id 추가 (NULL=전체 공지로 해석, 백필은 #4 책임).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS team_notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER,
                content TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── notifications ──
        # 팀 기능 그룹 A #2: team_id 추가 (NULL=비-팀 컨텍스트, 백필은 #4 책임).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL,
                team_id INTEGER,
                type TEXT NOT NULL,
                message TEXT NOT NULL,
                event_id INTEGER,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── user_teams ── (팀 기능 그룹 A #2 신규)
        # 멀티-팀 멤버십. admin은 전 팀 슈퍼유저 정책이라 row 없음.
        # status: 'pending' | 'approved' (#8 가입 흐름에서 사용).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                team_id INTEGER NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                status TEXT NOT NULL DEFAULT 'approved',
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # ── team_menu_settings ── (팀 기능 그룹 A #2 신규)
        # 팀별 메뉴 표시 토글. 기본값 시드는 #19 책임.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS team_menu_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                menu_key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS meeting_locks (
                meeting_id INTEGER PRIMARY KEY,
                user_name  TEXT NOT NULL,
                locked_at  TEXT NOT NULL
            )
        """)
        # 팀 기능 그룹 A #2: project_id 컬럼 추가 (NULL 유지, 백필은 #5 책임).
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS checklists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL DEFAULT '',
                project_id INTEGER,
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
        # 팀 기능 그룹 A #3:
        #   - 관리팀 자동 생성 제거 (시스템 관리자는 어떤 팀에도 소속되지 않는다).
        #   - admin은 team_id=NULL 로 시드 → Phase 3 본문이 빈 DB에서 진정으로 노옵.
        if not conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
            init_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
            conn.execute(
                "INSERT INTO users (name, name_norm, password, role, team_id, is_active) "
                "VALUES (?,?,?,'admin',NULL,1)",
                ("admin", normalize_name("admin"), init_pw)
            )
            print(f"[WhatUdoin] 초기 관리자 비밀번호: {init_pw}  (최초 1회만 표시, 즉시 변경 권장)")

    # with get_conn() 블록 종료 후, phase 마이그레이션 인프라 진입점.
    # 본 #1에서는 PHASES가 비어 있어 즉시 반환된다.
    # #2 이후에서 PHASES/_PREFLIGHT_CHECKS에 phase 본문을 등록하면 자동으로 실행된다.
    _run_phase_migrations()


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
            # #6: 부모와 동일한 project_id 상속 (없으면 conn으로 해석).
            "project_id":          parent_data.get("project_id") if parent_data.get("project_id") is not None
                                   else _resolve_project_id_for_write(
                                       conn, parent_data.get("team_id"), parent_data.get("project")
                                   ),
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
               (title, team_id, project, project_id, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority, event_type, is_active, is_public,
                recurrence_rule, recurrence_end, recurrence_parent_id,
                bound_checklist_id)
               VALUES
               (:title, :team_id, :project, :project_id, :description, :location, :assignee, :all_day,
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


# ── Phase 마이그레이션 인프라 ─────────────────────────────────────
# 팀 기능 그룹 A #1. _migrate()보다 상위 layer에서 phase 단위로
# 백업·마커·트랜잭션·preflight를 묶어 실행한다. 실제 phase 본문 SQL은
# 본 파일이 아닌 후속 사이클(#2~)에서 PHASES / _PREFLIGHT_CHECKS에 등록한다.

_MIGRATION_LOG_PREFIX = "[WhatUdoin][migration]"
_PHASE_MARKER_KEY_PREFIX = "migration_phase:"
_TEAM_MIGRATION_WARNINGS_KEY = "team_migration_warnings"

# (phase_name, body_callable) 목록. body_callable: (conn) -> None.
# 본 #1에서는 비어 있다. #2 이후에서 실제 phase를 등록한다.
PHASES: list = []

# preflight 검사 함수 목록. 각 함수: (conn) -> list[tuple[str, str]]
#   각 튜플은 (warning_category, message) — 카테고리는 settings.team_migration_warnings에
#   누적될 때 dedup 단위로 쓰인다(예: 'preflight_projects_team_name').
# 검사 함수가 raise하면 러너가 ('preflight', '<repr>') 단일 튜플로 변환한다.
# 본 #1에서는 비어 있다. UNIQUE 제약 추가 시 검사 함수를 등록한다.
_PREFLIGHT_CHECKS: list = []

# preflight 검사보다 *먼저* 실행되어야 하는 phase 이름 집합.
# 러너(_run_phase_migrations)는 preflight를 모든 phase 본문보다 앞에서 일괄 실행한다.
# 따라서 단순히 PHASES.append 순서를 앞당기는 것만으로는 "dedup → preflight → 인덱스 생성"
# 순서를 보장할 수 없다. 여기 등록된 phase는 같은 init_db() 호출에서 preflight 앞에서
# 본문 실행 + 마커 커밋되고, 그 뒤 preflight → 나머지 phase 순으로 진행된다.
# 계약:
#   (a) idempotent — 재실행 안전, clean state면 노옵.
#   (b) preflight가 강제하는 UNIQUE invariant에 의존하지 않을 것
#       (오히려 그 invariant를 만족시키기 위한 안전 정리 작업이 목적).
# 의도: team_phase_5a가 안전 dedup → 그 결과를 #5 preflight(_check_projects_team_name_unique)가
#       검증 → 통과하면 #5가 (team_id, name_norm) UNIQUE 인덱스 생성.
_PRE_PREFLIGHT_PHASES: frozenset = frozenset({"team_phase_5a_projects_dedup_safe_v1"})


def _is_phase_done(conn, name: str) -> bool:
    """phase 마커 존재 여부. set_setting/get_setting을 거치지 않고
    호출자의 conn(트랜잭션)을 그대로 사용한다."""
    row = conn.execute(
        "SELECT 1 FROM settings WHERE key = ?",
        (_PHASE_MARKER_KEY_PREFIX + name,),
    ).fetchone()
    return bool(row)


def _mark_phase_done(conn, name: str) -> None:
    """phase 마커 기록. 반드시 phase 본문과 동일한 트랜잭션(conn)에서 호출.
    set_setting()을 쓰면 별도 connection을 열어 본문↔마커 드리프트가 생긴다."""
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_PHASE_MARKER_KEY_PREFIX + name, datetime.now(timezone.utc).isoformat()),
    )


def _pending_phases() -> list:
    """아직 마커가 없는 phase만 반환. 별도 read-only connection 사용."""
    if not PHASES:
        return []
    pending = []
    with get_conn() as conn:
        for name, body in PHASES:
            if not _is_phase_done(conn, name):
                pending.append((name, body))
    return pending


def normalize_name(s: str) -> str:
    """이름 비교용 정규화. NFC + casefold.

    팀 기능 그룹 A에서 user/team 이름 중복·검색 비교 시 사용 예정.
    Unicode 합성/대소문자 차이만 흡수한다(공백·특수문자는 보존).
    """
    if s is None:
        return ""
    import unicodedata
    return unicodedata.normalize("NFC", str(s)).casefold()


def _append_team_migration_warning(conn, category: str, message: str) -> None:
    """settings.team_migration_warnings(JSON 배열)에 race-safe append.

    호출자의 conn 트랜잭션 안에서 read → append → write를 수행하므로
    같은 카테고리 중복 메시지를 삽입하지 않는다.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE key = ?",
        (_TEAM_MIGRATION_WARNINGS_KEY,),
    ).fetchone()
    try:
        arr = json.loads(row["value"]) if row and row["value"] else []
        if not isinstance(arr, list):
            arr = []
    except (json.JSONDecodeError, TypeError):
        arr = []

    # 같은 카테고리+메시지 중복 방지
    for entry in arr:
        if (
            isinstance(entry, dict)
            and entry.get("category") == category
            and entry.get("message") == message
        ):
            return

    arr.append({
        "category": category,
        "message": message,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    payload = json.dumps(arr, ensure_ascii=False)
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_TEAM_MIGRATION_WARNINGS_KEY, payload),
    )


# ── 팀 기능 그룹 A #2 — phase 본문 등록 ─────────────────────────
# 위 PHASES / _PREFLIGHT_CHECKS 인프라(#1) 위에 phase 1·2·4 본문을 등록한다.
# 각 본문은 호출자가 BEGIN IMMEDIATE 트랜잭션으로 감싸 호출하므로 conn.commit() 호출 금지.
# idempotency 가드(WHERE NULL/INSERT OR IGNORE)는 마커 강제 삭제 후 재실행에 대비한 추가 안전망.

# projects 테이블 재구성 시 보존할 명시적 컬럼 목록 (사양서 §주의사항 마지막).
# id 보존 필수 — events.project_id 등의 FK 참조 회피.
_PROJECTS_REBUILD_COLUMNS = [
    "id",
    "team_id",
    "name",
    "name_norm",
    "color",
    "start_date",
    "end_date",
    "is_active",
    "is_private",
    "is_hidden",
    "owner_id",
    "memo",
    "deleted_at",
    "deleted_by",
    "created_at",
]


def _column_set(conn, table: str) -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _phase_1_team_columns(conn):
    """Phase 1: 컬럼/테이블 추가 + projects 테이블 재구성.

    빈 DB에서는 init_db()의 CREATE TABLE이 최신 스키마를 만들고,
    이 본문은 모든 ALTER가 idempotent guard를 통해 노옵으로 끝난다.
    기존 DB에서는 누락 컬럼·테이블을 따라잡고, projects의 name UNIQUE를 제거한다.
    """
    # 1. users 컬럼 추가 (name_norm, password_hash)
    users_cols = _column_set(conn, "users")
    if "name_norm" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN name_norm TEXT")
    if "password_hash" not in users_cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

    # 2. teams 컬럼 추가 (deleted_at, name_norm)
    teams_cols = _column_set(conn, "teams")
    if "deleted_at" not in teams_cols:
        conn.execute("ALTER TABLE teams ADD COLUMN deleted_at TEXT DEFAULT NULL")
    if "name_norm" not in teams_cols:
        conn.execute("ALTER TABLE teams ADD COLUMN name_norm TEXT")

    # 3. events.project_id, checklists.project_id 추가
    if _table_exists(conn, "events"):
        if "project_id" not in _column_set(conn, "events"):
            conn.execute("ALTER TABLE events ADD COLUMN project_id INTEGER")
    if _table_exists(conn, "checklists"):
        if "project_id" not in _column_set(conn, "checklists"):
            conn.execute("ALTER TABLE checklists ADD COLUMN project_id INTEGER")

    # 4. notifications.team_id, team_notices.team_id 추가
    if _table_exists(conn, "notifications"):
        if "team_id" not in _column_set(conn, "notifications"):
            conn.execute("ALTER TABLE notifications ADD COLUMN team_id INTEGER")
    if _table_exists(conn, "team_notices"):
        if "team_id" not in _column_set(conn, "team_notices"):
            conn.execute("ALTER TABLE team_notices ADD COLUMN team_id INTEGER")

    # 5. user_teams, team_menu_settings 테이블 생성
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            team_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'approved',
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_menu_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL,
            menu_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 6. projects 테이블 재구성 — name UNIQUE 제거 + name_norm 추가 + id 보존.
    #    SQLite는 컬럼 제약 변경에 ALTER 미지원 → 새 테이블 만들고 INSERT … SELECT.
    #    sqlite_master.sql에 'name TEXT NOT NULL UNIQUE'가 남아 있을 때만 재구성.
    if _table_exists(conn, "projects"):
        sql_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='projects'"
        ).fetchone()
        existing_sql = (sql_row[0] if sql_row else "") or ""
        # UNIQUE가 'name' 컬럼 정의에 붙어 있는 경우만 재구성 (느슨한 정규화 매칭).
        normalized = " ".join(existing_sql.split()).lower()
        needs_rebuild = "name text not null unique" in normalized

        if needs_rebuild:
            existing_cols = _column_set(conn, "projects")
            # 백업: 재구성에 필요한 모든 컬럼이 실제 존재해야 한다(없으면 NULL로 채움).
            select_exprs = []
            for col in _PROJECTS_REBUILD_COLUMNS:
                if col == "name_norm":
                    # 재구성과 동시에 name_norm 채움 (사양서 주의사항: 같은 phase에서 채워도 됨)
                    # NFC+casefold는 SQL로 표현 불가 → 일단 NULL 삽입 후 Python 백필.
                    select_exprs.append("NULL AS name_norm")
                elif col in existing_cols:
                    select_exprs.append(col)
                else:
                    select_exprs.append(f"NULL AS {col}")

            old_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            old_max_id_row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM projects").fetchone()
            old_max_id = old_max_id_row[0] if old_max_id_row else 0

            conn.execute("DROP TABLE IF EXISTS projects_new")
            conn.execute("""
                CREATE TABLE projects_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    team_id INTEGER,
                    name TEXT NOT NULL,
                    name_norm TEXT,
                    color TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    is_active INTEGER DEFAULT 1,
                    is_private INTEGER DEFAULT 0,
                    is_hidden INTEGER DEFAULT 0,
                    owner_id INTEGER,
                    memo TEXT,
                    deleted_at TEXT DEFAULT NULL,
                    deleted_by TEXT DEFAULT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cols_csv = ", ".join(_PROJECTS_REBUILD_COLUMNS)
            select_csv = ", ".join(select_exprs)
            conn.execute(
                f"INSERT INTO projects_new ({cols_csv}) SELECT {select_csv} FROM projects"
            )

            new_count = conn.execute("SELECT COUNT(*) FROM projects_new").fetchone()[0]
            if new_count != old_count:
                raise RuntimeError(
                    f"projects rebuild row count mismatch: old={old_count} new={new_count}"
                )

            conn.execute("DROP TABLE projects")
            conn.execute("ALTER TABLE projects_new RENAME TO projects")

            # AUTOINCREMENT seq 보존 (id 재발급 방지).
            # sqlite_sequence는 PRIMARY KEY/UNIQUE 제약이 없는 시스템 테이블이라
            # ON CONFLICT 절을 쓸 수 없다 — UPDATE-or-INSERT로 처리.
            seq_exists = conn.execute(
                "SELECT 1 FROM sqlite_sequence WHERE name = 'projects'"
            ).fetchone()
            if seq_exists:
                conn.execute(
                    "UPDATE sqlite_sequence SET seq = ? WHERE name = 'projects'",
                    (old_max_id,),
                )
            else:
                conn.execute(
                    "INSERT INTO sqlite_sequence (name, seq) VALUES ('projects', ?)",
                    (old_max_id,),
                )

            # 재구성 직후 같은 phase에서 name_norm 채움 (Python 정규화 사용).
            rows = conn.execute("SELECT id, name FROM projects WHERE name_norm IS NULL").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE projects SET name_norm = ? WHERE id = ?",
                    (normalize_name(row["name"] if isinstance(row, sqlite3.Row) else row[1]),
                     row["id"] if isinstance(row, sqlite3.Row) else row[0]),
                )


PHASES.append(("team_phase_1_columns_v1", _phase_1_team_columns))


def _phase_2_team_backfill(conn):
    """Phase 2: name_norm/role/user_teams 백필.

    가드:
      - name_norm: WHERE name_norm IS NULL
      - role: WHERE role = 'editor'
      - user_teams: INSERT OR IGNORE + UNIQUE(user_id, team_id) 인덱스 의존 (Phase 4에서 생성)
        → 인덱스가 아직 없을 수 있으므로 WHERE NOT EXISTS 가드 병행.
    """
    # 1. users.name_norm 백필 (admin 포함)
    rows = conn.execute(
        "SELECT id, name FROM users WHERE name_norm IS NULL"
    ).fetchall()
    for row in rows:
        uid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        nm = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        conn.execute(
            "UPDATE users SET name_norm = ? WHERE id = ?",
            (normalize_name(nm), uid),
        )

    # teams.name_norm도 같이 백필 (phase 1에서 컬럼만 추가, 백필은 #2 범위로 묶음)
    if _table_exists(conn, "teams"):
        team_cols = _column_set(conn, "teams")
        if "name_norm" in team_cols:
            t_rows = conn.execute(
                "SELECT id, name FROM teams WHERE name_norm IS NULL"
            ).fetchall()
            for row in t_rows:
                tid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
                tn = row["name"] if isinstance(row, sqlite3.Row) else row[1]
                conn.execute(
                    "UPDATE teams SET name_norm = ? WHERE id = ?",
                    (normalize_name(tn), tid),
                )

    # 2. users.role: 'editor' → 'member' 일괄 갱신 (admin 유지)
    conn.execute(
        "UPDATE users SET role = 'member' WHERE role = 'editor'"
    )

    # 3. user_teams 백필: team_id NOT NULL AND role != 'admin' 사용자
    #    INSERT … SELECT … WHERE NOT EXISTS로 idempotent.
    conn.execute("""
        INSERT INTO user_teams (user_id, team_id, role, status, joined_at)
        SELECT u.id, u.team_id, 'member', 'approved', u.created_at
          FROM users u
         WHERE u.team_id IS NOT NULL
           AND u.role != 'admin'
           AND NOT EXISTS (
               SELECT 1 FROM user_teams ut
                WHERE ut.user_id = u.id AND ut.team_id = u.team_id
           )
    """)


PHASES.append(("team_phase_2_backfill_v1", _phase_2_team_backfill))


def _phase_4_team_indexes(conn):
    """Phase 4: 본 사이클 범위의 UNIQUE 인덱스 2개 + #6 비-UNIQUE 인덱스 2개 생성.

    - idx_user_teams_user_team: user_teams(user_id, team_id) UNIQUE
    - idx_team_menu_settings:    team_menu_settings(team_id, menu_key) UNIQUE
    - idx_events_project_id:     events(project_id) — #6 추가, 비-UNIQUE
    - idx_checklists_project_id: checklists(project_id) — #6 추가, 비-UNIQUE

    users.name_norm UNIQUE / teams.name_norm UNIQUE / projects(team_id, name_norm) UNIQUE는
    각각 #7/#5 후속 사이클 책임이므로 본 사이클에서 만들지 않는다.
    """
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_teams_user_team "
        "ON user_teams(user_id, team_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_team_menu_settings "
        "ON team_menu_settings(team_id, menu_key)"
    )
    # #6: project_id 기준 조회/JOIN 가속용 비-UNIQUE 인덱스.
    # 컬럼 존재 가드 — Phase 1이 추가하지만 빈 DB 첫 init_db()에서 phase 4가
    # phase 1 뒤에 실행되므로 정상적으로 존재. 방어적 가드만 둔다.
    if _table_exists(conn, "events") and "project_id" in _column_set(conn, "events"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_project_id "
            "ON events(project_id)"
        )
    if _table_exists(conn, "checklists") and "project_id" in _column_set(conn, "checklists"):
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_checklists_project_id "
            "ON checklists(project_id)"
        )


PHASES.append(("team_phase_4_indexes_v1", _phase_4_team_indexes))


# 팀 기능 그룹 A #3 — 시스템 관리자 분리 + 관리팀 시드 처리.
#
# 본문은 한 트랜잭션 안에서 다음을 수행한다(모두 idempotent 가드 포함):
#   1) admin users.team_id → NULL, admin users.mcp_token_hash·created_at → NULL
#   2) admin user_ips.type='whitelist' → 'history' (row 보존)
#   3) admin user_teams row 정리 (정상 흐름에선 노옵)
#   4) name='관리팀' 팀에 대해: admin 외 참조가 1건이라도 있으면 'AdminTeam'으로 rename,
#      0건이면 DELETE. 사양서가 정의한 8+2개 테이블의 team_id 컬럼을 모두 검사.
#
# 가드:
#   - 모든 UPDATE는 WHERE 절에 NULL/IS NOT NULL/!= 비교를 두어 마커 강제 삭제 후
#     재실행해도 추가 변경 없이 종료된다.
#   - 참조 검사 대상 테이블의 team_id 컬럼 존재 여부는 PRAGMA table_info로 가드.
#
# 사양서 §주의사항: cascade 외래키 없음 → 참조 0건 검사 후 DELETE 안전.
_ADMIN_TEAM_REF_TABLES = [
    # (table_name, column_name) — 사양서가 정의한 참조 데이터 8+2개.
    # users.team_id는 admin team_id NULL 처리 직후 검사하므로 admin은 자연스럽게 제외된다.
    ("users",              "team_id"),
    ("user_teams",         "team_id"),
    ("events",             "team_id"),
    ("checklists",         "team_id"),
    ("meetings",           "team_id"),
    ("projects",           "team_id"),
    ("notifications",      "team_id"),
    ("team_notices",       "team_id"),
    ("links",              "team_id"),
    ("team_menu_settings", "team_id"),
]


def _team_has_external_refs(conn, team_id: int) -> bool:
    """사양서 §13의 참조 데이터 목록을 모두 훑어 1건이라도 가리키면 True."""
    for table, column in _ADMIN_TEAM_REF_TABLES:
        if not _table_exists(conn, table):
            continue
        if column not in _column_set(conn, table):
            continue
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
            (team_id,),
        ).fetchone()
        if row:
            return True
    return False


def _phase_3_admin_separation(conn):
    """Phase 3: 시스템 관리자(admin) 데이터 분리 + 관리팀 처리.

    본문 자체가 idempotent — 마커 강제 삭제 후 재실행해도 변화 없음.
    빈 DB 첫 init_db()에서는 admin 시드가 team_id=NULL이고 관리팀이 없으므로 노옵.
    """
    # 1. admin users.team_id → NULL  (이미 NULL이면 0행 영향)
    conn.execute(
        "UPDATE users SET team_id = NULL "
        "WHERE role = 'admin' AND team_id IS NOT NULL"
    )

    # admin mcp_token_hash·created_at → NULL  (컬럼 존재 가드)
    users_cols = _column_set(conn, "users")
    if "mcp_token_hash" in users_cols:
        conn.execute(
            "UPDATE users SET mcp_token_hash = NULL "
            "WHERE role = 'admin' AND mcp_token_hash IS NOT NULL"
        )
    if "mcp_token_created_at" in users_cols:
        conn.execute(
            "UPDATE users SET mcp_token_created_at = NULL "
            "WHERE role = 'admin' AND mcp_token_created_at IS NOT NULL"
        )

    # 2. admin user_ips.type='whitelist' → 'history' (row 삭제 X — 이력 보존)
    if _table_exists(conn, "user_ips"):
        conn.execute(
            "UPDATE user_ips SET type = 'history' "
            "WHERE type = 'whitelist' "
            "  AND user_id IN (SELECT id FROM users WHERE role = 'admin')"
        )

    # 3. 안전 보강: admin이 user_teams에 row를 가지면 안 되므로 정리.
    #    #2 백필이 admin 제외 가드를 가지지만, 본 사이클에서 명시적으로 한 번 더 보강.
    if _table_exists(conn, "user_teams"):
        conn.execute(
            "DELETE FROM user_teams "
            "WHERE user_id IN (SELECT id FROM users WHERE role = 'admin')"
        )

    # 4. name='관리팀' 팀 처리.
    if not _table_exists(conn, "teams"):
        return
    teams_cols = _column_set(conn, "teams")
    admin_team = conn.execute(
        "SELECT id FROM teams WHERE name = ? LIMIT 1",
        ("관리팀",),
    ).fetchone()
    if not admin_team:
        return  # 관리팀 자체 없음 → 노옵
    admin_team_id = admin_team["id"] if isinstance(admin_team, sqlite3.Row) else admin_team[0]

    if _team_has_external_refs(conn, admin_team_id):
        # 참조 ≥1건 → 'AdminTeam'으로 rename (name·name_norm 동시 갱신).
        # 단, 운영자가 미리 'AdminTeam' 팀을 만들어둔 환경(name UNIQUE 제약 충돌)에서는
        # IntegrityError로 phase가 ROLLBACK되어 서버가 시작되지 않으므로,
        # 사전 SELECT로 충돌을 감지하면 fallback 이름('관리팀_legacy_<id>')으로 rename하고
        # 운영자가 후속 정리할 수 있도록 team_migration_warnings에 기록한다.
        target_name = "AdminTeam"
        conflict_row = conn.execute(
            "SELECT 1 FROM teams WHERE name = ? AND id != ? LIMIT 1",
            (target_name, admin_team_id),
        ).fetchone()
        if conflict_row:
            target_name = f"관리팀_legacy_{admin_team_id}"
            _append_team_migration_warning(
                conn,
                "admin_separation",
                f"AdminTeam 이름 충돌, '관리팀'을 '{target_name}'(id={admin_team_id})로 rename",
            )

        # name_norm 컬럼 존재 가드 (Phase 1이 추가하지만 방어적).
        if "name_norm" in teams_cols:
            conn.execute(
                "UPDATE teams SET name = ?, name_norm = ? WHERE id = ?",
                (target_name, normalize_name(target_name), admin_team_id),
            )
        else:
            conn.execute(
                "UPDATE teams SET name = ? WHERE id = ?",
                (target_name, admin_team_id),
            )
    else:
        # 참조 0건 → DELETE
        conn.execute("DELETE FROM teams WHERE id = ?", (admin_team_id,))


PHASES.append(("team_phase_3_admin_separation_v1", _phase_3_admin_separation))


# ── 팀 기능 그룹 A #4 — 데이터 백필 phase 본문 ──────────────────
# 이름은 todo의 항목 번호 기반(`team_phase_4_data_backfill_v1`).
# 위 `team_phase_4_indexes_v1`(L~1073)과 별개의 phase이며 등록 순서상 마지막에 실행된다.
#
# 책임:
#   1) events.team_id 백필                — created_by(TEXT) 기반
#   2) checklists.team_id 백필             — created_by(TEXT) 기반
#   3) meetings.team_id 백필 (4분기)       — created_by(INTEGER) + is_team_doc 분기
#   4) projects.team_id 백필 (단계 1/2/4)  — owner_id 기반 (자동 생성은 #6)
#   5) notifications.team_id 백필          — events.team_id 의존
#   6) links.team_id 백필                  — scope='team' 한정, created_by(TEXT)
#   7) team_notices.team_id 백필           — created_by(TEXT)
#   8) pending_users 자동 삭제              — 가드 불필요
#
# 모든 UPDATE에 `WHERE team_id IS NULL` 가드 → 마커 강제 삭제 후 재실행해도
# 이미 채워진 row를 다시 건드리지 않는다.
#
# 헬퍼 `_resolve_user_single_team`은 phase 본문 내부 전용(__phase4 prefix). 라우트·UI에서
# 쓰지 말 것 — 런타임 헬퍼는 #15의 `resolve_work_team` 책임.
#
# warning 카테고리 5종:
#   - data_backfill_events
#   - data_backfill_meetings_team_doc_no_owner
#   - data_backfill_projects
#   - data_backfill_links
#   - data_backfill_team_notices


def __phase4_resolve_user_single_team(conn, user_name_or_id, _cache: dict):
    """작성자 → 단일 팀 결정 헬퍼 (phase 4 데이터 백필 전용).

    우선순위 (사양서 §13 확정안):
      1) user_teams approved row가 정확히 1건이면 그 팀
      2) ≥2건이면 joined_at 최선(가장 이른) 팀 — 대표 팀
      3) 0건이면 legacy users.team_id (admin이거나 미배정이면 NULL)
      4) 사용자 매칭 실패 → None

    입력 타입:
      - INTEGER  → users.id 직접 조회 (예: meetings.created_by)
      - TEXT     → users.name 매칭 (대소문자 그대로; name_norm 매칭은 후속 사이클 책임)

    매 row마다 호출되므로 dict 캐시로 중복 조회 회피. 캐시 키는 ('id', uid) 또는 ('name', name).
    None/빈문자열 입력은 즉시 None 반환.
    """
    if user_name_or_id is None:
        return None
    if isinstance(user_name_or_id, str) and not user_name_or_id.strip():
        return None

    if isinstance(user_name_or_id, int):
        cache_key = ("id", user_name_or_id)
    else:
        cache_key = ("name", str(user_name_or_id))

    if cache_key in _cache:
        return _cache[cache_key]

    # 사용자 row 조회
    if cache_key[0] == "id":
        user_row = conn.execute(
            "SELECT id, team_id, role FROM users WHERE id = ?",
            (cache_key[1],),
        ).fetchone()
    else:
        user_row = conn.execute(
            "SELECT id, team_id, role FROM users WHERE name = ?",
            (cache_key[1],),
        ).fetchone()

    if not user_row:
        _cache[cache_key] = None
        return None

    uid = user_row["id"] if isinstance(user_row, sqlite3.Row) else user_row[0]
    legacy_team_id = user_row["team_id"] if isinstance(user_row, sqlite3.Row) else user_row[1]

    # user_teams approved 멤버십 조회 (joined_at ASC = 가장 이른 가입)
    ut_rows = conn.execute(
        "SELECT team_id FROM user_teams "
        "WHERE user_id = ? AND status = 'approved' "
        "ORDER BY joined_at ASC, id ASC",
        (uid,),
    ).fetchall()

    if len(ut_rows) >= 1:
        # 1건이면 그 팀, ≥2건이면 가장 이른 팀(대표 팀)
        first = ut_rows[0]
        team_id = first["team_id"] if isinstance(first, sqlite3.Row) else first[0]
        _cache[cache_key] = team_id
        return team_id

    # 0건 → legacy users.team_id (admin은 이미 #3에서 NULL 처리됨)
    _cache[cache_key] = legacy_team_id
    return legacy_team_id


def _phase_4_data_backfill(conn):
    """Phase 4 데이터 백필 (#4 사이클): 7개 테이블 team_id 백필 + pending_users 삭제.

    전체 본문이 idempotent — 마커 강제 삭제 후 재실행해도 결과 동일:
      - 모든 UPDATE에 `WHERE team_id IS NULL` 가드
      - DELETE FROM pending_users 는 빈 테이블이면 노옵
      - 결정 불가 row는 NULL 유지 + warning 누적 (dedup은 _append_team_migration_warning 내장)
    """
    cache: dict = {}

    # ─── 1) events.team_id 백필 ─────────────────────────────────────
    # (1번) project_id → projects.team_id: project_id 백필은 #6 책임이라
    #        본 사이클에서는 매칭 0건이 정상이지만, #6 PHASES.append 후를 대비해 코드는 둔다.
    #        가드 컬럼 존재 확인 + WHERE team_id IS NULL.
    if _table_exists(conn, "events"):
        events_cols = _column_set(conn, "events")
        if "project_id" in events_cols and _table_exists(conn, "projects"):
            conn.execute(
                "UPDATE events "
                "   SET team_id = (SELECT p.team_id FROM projects p "
                "                   WHERE p.id = events.project_id) "
                " WHERE events.team_id IS NULL "
                "   AND events.project_id IS NOT NULL "
                "   AND EXISTS (SELECT 1 FROM projects p "
                "                WHERE p.id = events.project_id "
                "                  AND p.team_id IS NOT NULL)"
            )

        # (2번) 작성자 단일 팀
        rows = conn.execute(
            "SELECT id, created_by FROM events WHERE team_id IS NULL"
        ).fetchall()
        for row in rows:
            row_id = row["id"]
            created_by = row["created_by"]
            resolved = __phase4_resolve_user_single_team(conn, created_by, cache)
            if resolved is not None:
                conn.execute(
                    "UPDATE events SET team_id = ? WHERE id = ? AND team_id IS NULL",
                    (resolved, row_id),
                )
            else:
                _append_team_migration_warning(
                    conn,
                    "data_backfill_events",
                    f"events id={row_id} created_by={created_by!r} resolution failed",
                )

    # ─── 2) checklists.team_id 백필 (events와 동일 규칙) ────────────
    if _table_exists(conn, "checklists"):
        rows = conn.execute(
            "SELECT id, created_by FROM checklists WHERE team_id IS NULL"
        ).fetchall()
        for row in rows:
            row_id = row["id"]
            created_by = row["created_by"]
            resolved = __phase4_resolve_user_single_team(conn, created_by, cache)
            if resolved is not None:
                conn.execute(
                    "UPDATE checklists SET team_id = ? WHERE id = ? AND team_id IS NULL",
                    (resolved, row_id),
                )
            else:
                _append_team_migration_warning(
                    conn,
                    "data_backfill_events",  # 사양서 §exit criteria: 5개 카테고리만 사용 → checklists는 events 카테고리에 합산
                    f"checklists id={row_id} created_by={created_by!r} resolution failed",
                )

    # ─── 3) meetings.team_id 백필 (4분기) ────────────────────────────
    # created_by는 INTEGER NOT NULL.
    # 분기:
    #   (A) is_team_doc=1 + 작성자 admin/팀미배정  → NULL 유지 + warning(team_doc_no_owner)
    #   (B) is_team_doc=0 + 작성자 admin/팀미배정  → NULL 유지 (정상, 개인 문서)
    #   (C) is_team_doc=1 + 정상                    → 작성자 단일 팀으로 백필
    #   (D) is_team_doc=0 + 정상                    → 동일 (작성자 본인 가시성 + 팀 컨텍스트)
    if _table_exists(conn, "meetings"):
        rows = conn.execute(
            "SELECT id, created_by, is_team_doc FROM meetings WHERE team_id IS NULL"
        ).fetchall()
        for row in rows:
            row_id = row["id"]
            created_by = row["created_by"]
            is_team_doc = row["is_team_doc"]

            resolved = __phase4_resolve_user_single_team(conn, created_by, cache)
            if resolved is not None:
                # 분기 (C)/(D): 정상 백필
                conn.execute(
                    "UPDATE meetings SET team_id = ? WHERE id = ? AND team_id IS NULL",
                    (resolved, row_id),
                )
            else:
                # 작성자 admin이거나 팀 미배정
                if is_team_doc == 1:
                    # 분기 (A): 팀 문서인데 소유자 없음 → 명시 warning
                    _append_team_migration_warning(
                        conn,
                        "data_backfill_meetings_team_doc_no_owner",
                        f"meetings id={row_id} created_by={created_by!r} is_team_doc=1 but no team",
                    )
                # 분기 (B): 개인 문서 + 팀 미배정 → 정상, warning 안 함

    # ─── 4) projects.team_id 백필 (단계 1/2/4) ──────────────────────
    # 단계 3(자동 프로젝트 생성)은 #6 책임 — 본 사이클에서 시도 X.
    # 단계 1(기존 team_id 사용)은 가드(`WHERE team_id IS NULL`)로 자연 skip.
    # deleted_at 컬럼 존재 가드 — Phase 1이 추가하지만 방어적.
    if _table_exists(conn, "projects"):
        proj_cols = _column_set(conn, "projects")
        guard_deleted = "AND deleted_at IS NULL" if "deleted_at" in proj_cols else ""
        rows = conn.execute(
            f"SELECT id, name, owner_id FROM projects "
            f" WHERE team_id IS NULL {guard_deleted}"
        ).fetchall()
        for row in rows:
            row_id = row["id"]
            name = row["name"]
            owner_id = row["owner_id"]

            resolved = None
            if owner_id is not None:
                # 단계 2: owner의 user_teams 단일 팀 / legacy users.team_id
                resolved = __phase4_resolve_user_single_team(conn, owner_id, cache)

            if resolved is not None:
                conn.execute(
                    "UPDATE projects SET team_id = ? WHERE id = ? AND team_id IS NULL",
                    (resolved, row_id),
                )
            else:
                # 단계 4: 결정 불가 → NULL 유지 + warning
                _append_team_migration_warning(
                    conn,
                    "data_backfill_projects",
                    f"projects id={row_id} name={name!r} owner_id={owner_id!r} resolution failed",
                )

    # ─── 5) notifications.team_id 백필 ──────────────────────────────
    # event_id가 있고 events.team_id가 있으면 그 값으로. 없으면 NULL 유지(warning 안 함).
    # 알림은 transient 데이터라 사양서가 noise 회피를 위해 warning 생략을 명시.
    if _table_exists(conn, "notifications") and _table_exists(conn, "events"):
        conn.execute(
            "UPDATE notifications "
            "   SET team_id = (SELECT e.team_id FROM events e "
            "                   WHERE e.id = notifications.event_id) "
            " WHERE notifications.team_id IS NULL "
            "   AND notifications.event_id IS NOT NULL "
            "   AND EXISTS (SELECT 1 FROM events e "
            "                WHERE e.id = notifications.event_id "
            "                  AND e.team_id IS NOT NULL)"
        )

    # ─── 6) links.team_id 백필 (scope='team' 한정) ──────────────────
    # scope='personal'은 team_id NULL이 정상.
    if _table_exists(conn, "links"):
        rows = conn.execute(
            "SELECT id, title, created_by FROM links "
            " WHERE scope = 'team' AND team_id IS NULL"
        ).fetchall()
        for row in rows:
            row_id = row["id"]
            title = row["title"]
            created_by = row["created_by"]
            resolved = __phase4_resolve_user_single_team(conn, created_by, cache)
            if resolved is not None:
                conn.execute(
                    "UPDATE links SET team_id = ? "
                    "WHERE id = ? AND scope = 'team' AND team_id IS NULL",
                    (resolved, row_id),
                )
            else:
                _append_team_migration_warning(
                    conn,
                    "data_backfill_links",
                    f"links id={row_id} created_by={created_by!r} title={title!r} resolution failed",
                )

    # ─── 7) team_notices.team_id 백필 ───────────────────────────────
    if _table_exists(conn, "team_notices"):
        rows = conn.execute(
            "SELECT id, created_by FROM team_notices WHERE team_id IS NULL"
        ).fetchall()
        for row in rows:
            row_id = row["id"]
            created_by = row["created_by"]
            resolved = __phase4_resolve_user_single_team(conn, created_by, cache)
            if resolved is not None:
                conn.execute(
                    "UPDATE team_notices SET team_id = ? WHERE id = ? AND team_id IS NULL",
                    (resolved, row_id),
                )
            else:
                _append_team_migration_warning(
                    conn,
                    "data_backfill_team_notices",
                    f"team_notices id={row_id} created_by={created_by!r} resolution failed",
                )

    # ─── 8) pending_users 자동 삭제 ─────────────────────────────────
    # status 무관 전체 삭제. 빈 테이블이면 0행 영향 (노옵).
    if _table_exists(conn, "pending_users"):
        conn.execute("DELETE FROM pending_users")


PHASES.append(("team_phase_4_data_backfill_v1", _phase_4_data_backfill))


# ── 팀 기능 그룹 A #9 — user_ips whitelist 부분 UNIQUE 인덱스 ────────
# 본 사이클 본문이 수행하는 일:
#   user_ips(ip_address) 위에 `WHERE type='whitelist'` 부분 UNIQUE 인덱스 생성.
#   → "1 IP = 1 사용자" 강제. history row는 인덱스 면제(중복 허용 — 접속 이력).
#
# 충돌(같은 IP가 2명 이상에게 whitelist)은 본문 진입 전 preflight
# (`_check_user_ips_whitelist_unique`)가 잡아 서버 시작을 거부한다. Phase 3가 admin
# whitelist를 history로 강등하므로 여기 도달하는 충돌은 일반 사용자 간 충돌뿐이며,
# 안전한 자동 선택 기준이 없으므로 자동 정리(phase 5a 같은) 없이 abort + 경고만 한다.
# 운영자가 `tools/migration_doctor.py` 또는 수동 SQL로 정리한 뒤 재시작한다.

def _phase_4b_user_ips_whitelist_unique(conn):
    """Phase: user_ips whitelist 부분 UNIQUE 인덱스 등록.

    빈 DB나 두 번째 init_db()에서는 인덱스 노옵(IF NOT EXISTS)으로 끝난다.
    """
    if not _table_exists(conn, "user_ips"):
        return
    cols = _column_set(conn, "user_ips")
    if "ip_address" not in cols or "type" not in cols:
        # 구조적 오류 — 본문 실패로 ROLLBACK.
        raise RuntimeError("phase 4b: user_ips.ip_address/type column missing")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_ips_whitelist_unique "
        "ON user_ips(ip_address) WHERE type = 'whitelist'"
    )


PHASES.append(("team_phase_4b_user_ips_whitelist_unique_v1", _phase_4b_user_ips_whitelist_unique))


# ── 팀 기능 그룹 A #5a — projects 자동 dedup (안전 그룹만) ────────
# 본 사이클(보강) 본문이 수행하는 일:
#   같은 (team_id, name_norm) 그룹에서 "참조 0건 + 빈 row"만 자동 hard DELETE.
#   - 그룹 모든 row가 참조 0건이면 MIN(id) 1개를 살리고 나머지 DELETE.
#   - 참조가 있는 row는 보존, unsafe 그룹(살아남는 row가 1건 이상 보존)은 그대로 둠
#     → 이후 #5 preflight(`_check_projects_team_name_unique`)가 거부.
#
# 참조 카운트 (모두 deleted_at IS NULL 또는 NULL 무관 보수적 카운트):
#   - events.project_id, checklists.project_id (deleted_at IS NULL)
#   - events.project = projects.name AND project_id IS NULL (문자열 잔존)
#   - checklists.project = projects.name AND project_id IS NULL
#   - events.trash_project_id, checklists.trash_project_id, meetings.trash_project_id
#   - project_members.project_id, project_milestones.project_id
#
# Idempotency:
#   - 두 번째 init_db() → phase 마커가 있어 미실행.
#   - 마커 강제 삭제 후 재실행 → 이미 row 수가 1이라 GROUP BY HAVING COUNT(*) > 1 자체가
#     0건 매칭. 본문은 사실상 노옵.
#
# 실행 순서:
#   본 5a는 `_PRE_PREFLIGHT_PHASES`에 등록되어 같은 init_db() 호출에서 preflight *앞*에서
#   실행된다 — 이게 dedup → preflight → 인덱스 생성 순서를 보장한다. (단순 PHASES.append
#   순서로는 보장되지 않는다: 러너 `_run_phase_migrations`가 preflight를 모든 phase 본문보다
#   먼저 일괄 실행하므로, _PRE_PREFLIGHT_PHASES에 없는 phase는 preflight 통과 후에야 돈다.)
#   pre-preflight 단계에서 5a 마커는 별도 트랜잭션으로 커밋되므로, 직후 preflight가 unsafe
#   충돌로 RuntimeError를 던져 서버 시작이 거부되더라도 5a 마커는 set 상태로 남는다.
#   운영자가 unsafe row를 수동/migration_doctor로 정리한 뒤 재시작하면 preflight가 통과하고
#   #5가 인덱스를 만든다 — 5a는 "고려됐고 안전하게 더 할 게 없음" 상태라 재실행하지 않는다
#   (버그 아님).

# 도구(migration_doctor)와 phase 본문이 공유하는 안전 정리 헬퍼.
# (a) 그룹 식별  (b) 참조 카운트  (c) 안전 그룹 정리.
# 도구는 read-only/dry-run에서 (a)+(b)만, --apply에서 (c)까지 호출한다.

def _projects_duplicate_groups(conn):
    """projects (team_id, name_norm) 충돌 그룹 목록.

    반환: list[dict{team_id, name_norm, ids: list[int]}] — 그룹당 id 오름차순.
    빈 테이블/컬럼 미존재면 [].
    """
    if not _table_exists(conn, "projects"):
        return []
    proj_cols = _column_set(conn, "projects")
    if "team_id" not in proj_cols or "name_norm" not in proj_cols:
        return []

    rows = conn.execute(
        "SELECT team_id, name_norm, GROUP_CONCAT(id) AS ids "
        "  FROM projects "
        " WHERE team_id IS NOT NULL "
        "   AND name_norm IS NOT NULL "
        " GROUP BY team_id, name_norm "
        "HAVING COUNT(*) > 1 "
        "ORDER BY team_id, name_norm"
    ).fetchall()

    groups = []
    for row in rows:
        team_id = row["team_id"] if isinstance(row, sqlite3.Row) else row[0]
        name_norm = row["name_norm"] if isinstance(row, sqlite3.Row) else row[1]
        ids_str = row["ids"] if isinstance(row, sqlite3.Row) else row[2]
        ids = sorted(int(x) for x in (ids_str or "").split(",") if x.strip())
        groups.append({"team_id": team_id, "name_norm": name_norm, "ids": ids})
    return groups


def _project_reference_count(conn, project_id: int, project_name: str | None) -> int:
    """주어진 projects.id가 다른 테이블에서 참조되는 총 건수.

    카운트되는 항목 (deleted_at 컬럼이 있으면 NULL 만):
      - events.project_id, checklists.project_id
      - events.project = name AND project_id IS NULL (문자열 잔존)
      - checklists.project = name AND project_id IS NULL
      - events.trash_project_id, checklists.trash_project_id, meetings.trash_project_id
      - project_members.project_id, project_milestones.project_id

    참조 0건이어야 안전 정리 대상이 된다. 카운트가 1+면 보존.
    """
    total = 0

    def _has_col(table: str, col: str) -> bool:
        if not _table_exists(conn, table):
            return False
        return col in _column_set(conn, table)

    def _count(sql: str, params: tuple) -> int:
        try:
            row = conn.execute(sql, params).fetchone()
        except sqlite3.OperationalError:
            return 0
        if not row:
            return 0
        return int(row[0] or 0)

    # events.project_id (deleted_at IS NULL)
    if _has_col("events", "project_id"):
        if _has_col("events", "deleted_at"):
            total += _count(
                "SELECT COUNT(*) FROM events "
                " WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            )
        else:
            total += _count(
                "SELECT COUNT(*) FROM events WHERE project_id = ?",
                (project_id,),
            )

    # checklists.project_id (deleted_at IS NULL)
    if _has_col("checklists", "project_id"):
        if _has_col("checklists", "deleted_at"):
            total += _count(
                "SELECT COUNT(*) FROM checklists "
                " WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            )
        else:
            total += _count(
                "SELECT COUNT(*) FROM checklists WHERE project_id = ?",
                (project_id,),
            )

    # 문자열 잔존: events.project = name AND project_id IS NULL
    if project_name is not None and _has_col("events", "project"):
        if _has_col("events", "project_id"):
            if _has_col("events", "deleted_at"):
                total += _count(
                    "SELECT COUNT(*) FROM events "
                    " WHERE project = ? AND project_id IS NULL "
                    "   AND deleted_at IS NULL",
                    (project_name,),
                )
            else:
                total += _count(
                    "SELECT COUNT(*) FROM events "
                    " WHERE project = ? AND project_id IS NULL",
                    (project_name,),
                )

    # 문자열 잔존: checklists.project = name AND project_id IS NULL
    if project_name is not None and _has_col("checklists", "project"):
        if _has_col("checklists", "project_id"):
            if _has_col("checklists", "deleted_at"):
                total += _count(
                    "SELECT COUNT(*) FROM checklists "
                    " WHERE project = ? AND project_id IS NULL "
                    "   AND deleted_at IS NULL",
                    (project_name,),
                )
            else:
                total += _count(
                    "SELECT COUNT(*) FROM checklists "
                    " WHERE project = ? AND project_id IS NULL",
                    (project_name,),
                )

    # trash_project_id (휴지통 메타이지만 참조 보존)
    for tbl in ("events", "checklists", "meetings"):
        if _has_col(tbl, "trash_project_id"):
            total += _count(
                f"SELECT COUNT(*) FROM {tbl} WHERE trash_project_id = ?",
                (project_id,),
            )

    # project_members / project_milestones
    for tbl in ("project_members", "project_milestones"):
        if _has_col(tbl, "project_id"):
            total += _count(
                f"SELECT COUNT(*) FROM {tbl} WHERE project_id = ?",
                (project_id,),
            )

    return total


def _classify_projects_dedup_group(conn, group: dict) -> dict:
    """그룹 안에서 어떤 id를 살리고 어떤 id를 자동 DELETE할지 분류.

    반환: dict{
        team_id, name_norm, ids,
        ref_counts: dict[id, int],
        keep: list[int],         # 보존 (참조 ≥1 또는 그룹 내 MIN id 대표)
        delete: list[int],       # 자동 DELETE 대상 (참조 0건)
        safe: bool,              # 본 사이클 자동 정리 가능 여부 (delete가 1건 이상)
        unsafe_reason: str|None  # safe=False일 때 이유
    }
    """
    ref_counts: dict[int, int] = {}
    rows = conn.execute(
        "SELECT id, name FROM projects WHERE id IN (%s)"
        % ",".join("?" * len(group["ids"])),
        tuple(group["ids"]),
    ).fetchall()
    name_by_id: dict[int, str | None] = {}
    for row in rows:
        rid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        nm = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        name_by_id[rid] = nm

    for pid in group["ids"]:
        ref_counts[pid] = _project_reference_count(conn, pid, name_by_id.get(pid))

    referenced = [pid for pid in group["ids"] if ref_counts[pid] > 0]
    unreferenced = [pid for pid in group["ids"] if ref_counts[pid] == 0]

    if referenced:
        # 참조 있는 row 모두 보존, 참조 0건 row는 모두 자동 DELETE.
        keep = sorted(referenced)
        delete = sorted(unreferenced)
        # 참조 row가 2개 이상이면 인덱스 충돌은 여전히 unsafe — phase는 dedup만 하고
        # 이후 #5 preflight이 충돌을 잡는다. 단, delete가 1건 이상이면 정리는 진행.
        safe = bool(delete)
        unsafe_reason = (
            None if safe
            else f"all {len(group['ids'])} rows referenced — manual decision needed"
        )
    else:
        # 모두 참조 0건 → MIN(id) 1개 살리고 나머지 DELETE.
        keep = [min(group["ids"])]
        delete = sorted(pid for pid in group["ids"] if pid != keep[0])
        safe = bool(delete)
        unsafe_reason = None

    return {
        "team_id": group["team_id"],
        "name_norm": group["name_norm"],
        "ids": list(group["ids"]),
        "ref_counts": ref_counts,
        "keep": keep,
        "delete": delete,
        "safe": safe,
        "unsafe_reason": unsafe_reason,
    }


def _phase_5a_projects_dedup_safe(conn):
    """Phase 5a: projects (team_id, name_norm) 안전 dedup.

    참조 0건 + 빈 row만 자동 hard DELETE. 메타데이터 동일성 검사는 안 한다
    (참조 없는 row는 메타가 무엇이든 운영 영향 0).

    빈 DB나 충돌 0건이면 본문 사실상 노옵.
    """
    if not _table_exists(conn, "projects"):
        return

    proj_cols = _column_set(conn, "projects")
    if "team_id" not in proj_cols or "name_norm" not in proj_cols:
        # Phase 1이 적용되지 않은 단계 — 본문 skip(노옵).
        return

    groups = _projects_duplicate_groups(conn)
    if not groups:
        return

    cleaned_total = 0
    for group in groups:
        plan = _classify_projects_dedup_group(conn, group)
        if not plan["safe"]:
            # 자동 정리 불가 그룹은 그대로 두고 #5 preflight에 위임.
            continue
        if not plan["delete"]:
            continue

        # hard DELETE — phase 시작 전 자동 백업이 떠 있어 복구 가능.
        # 같은 conn 트랜잭션이라 이 phase가 ROLLBACK되면 모두 되돌아온다.
        placeholders = ",".join("?" * len(plan["delete"]))
        conn.execute(
            f"DELETE FROM projects WHERE id IN ({placeholders})",
            tuple(plan["delete"]),
        )

        cleaned_total += len(plan["delete"])
        _append_team_migration_warning(
            conn,
            "dedup_projects_auto",
            f"projects (team_id={plan['team_id']}, "
            f"name_norm={plan['name_norm']!r}) "
            f"kept_ids={plan['keep']} deleted_ids={plan['delete']}",
        )

    if cleaned_total:
        print(
            f"{_MIGRATION_LOG_PREFIX} phase 5a: auto-deleted {cleaned_total} "
            f"unreferenced duplicate project row(s)"
        )


PHASES.append(("team_phase_5a_projects_dedup_safe_v1", _phase_5a_projects_dedup_safe))


# ── 팀 기능 그룹 A #5 — projects (team_id, name_norm) UNIQUE ─────
# 본 사이클 본문이 수행하는 일:
#   1) projects.name_norm 잔존 NULL row 방어용 백필 (Phase 1 본문이 이미 채웠으나
#      마커 강제 삭제·중간 실패 시나리오에 대비). `WHERE name_norm IS NULL` 가드.
#   2) (team_id, name_norm) 부분 UNIQUE 인덱스 생성. NULL team_id row는 인덱스 면제
#      (운영자 #10 후속 정리 영역). `IF NOT EXISTS`로 idempotent.
#
# 인덱스 충돌(`team_id IS NOT NULL`인데 동일 (team_id, name_norm) 2+건)은 본문 진입
# 전 preflight(`_check_projects_team_name_unique`)가 잡아 서버 시작을 거부한다.
# 운영자가 충돌 row를 정리한 뒤 재시작하면 정상 진입한다.
# Phase 5a가 앞서 안전 그룹을 자동 정리하므로, 여기에 도달하는 충돌은 unsafe(참조 ≥2)뿐.

def _phase_5_projects_unique(conn):
    """Phase 5: projects (team_id, name_norm) 부분 UNIQUE 인덱스 등록.

    잔존 name_norm NULL row 방어 백필 → 부분 UNIQUE 인덱스(IF NOT EXISTS).
    빈 DB나 두 번째 init_db()에서는 백필 0건 + 인덱스 노옵으로 끝난다.
    """
    if not _table_exists(conn, "projects"):
        return

    proj_cols = _column_set(conn, "projects")
    if "name_norm" not in proj_cols:
        # Phase 1이 컬럼을 추가했어야 한다. 구조적 오류 — 본문 실패로 ROLLBACK.
        raise RuntimeError("phase 5: projects.name_norm column missing")

    # 1) 잔존 name_norm NULL row 방어 백필.
    rows = conn.execute(
        "SELECT id, name FROM projects WHERE name_norm IS NULL"
    ).fetchall()
    for row in rows:
        rid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        nm = row["name"] if isinstance(row, sqlite3.Row) else row[1]
        conn.execute(
            "UPDATE projects SET name_norm = ? WHERE id = ? AND name_norm IS NULL",
            (normalize_name(nm), rid),
        )

    # 2) 부분 UNIQUE 인덱스. SQLite 3.8.0+ partial index 사용.
    #    team_id IS NULL인 row는 인덱스에서 제외 — 운영자 정리 영역.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_team_name "
        "ON projects(team_id, name_norm) WHERE team_id IS NOT NULL"
    )


PHASES.append(("team_phase_5_projects_unique_v1", _phase_5_projects_unique))


# ── 팀 기능 그룹 A #6 — events/checklists.project_id 백필 ────────────
# 본 사이클 본문이 수행하는 일:
#   1) events.project_id 백필                — (team_id, name_norm) 매칭 + 자동 생성
#   2) checklists.project_id 백필             — 동일 정책
#   3) project_milestones / project_members / *_trash_project_id dangling 검증 (warning만)
#
# 매칭 정책 (사양서 §13):
#   - row.team_id 있고 row.project 비어있지 않을 때만 시도.
#   - (team_id, normalize_name(project)) 매칭. deleted_at IS NULL 우선, 없으면 deleted_at 포함.
#   - 매칭 0건 + team_id 있음 → 자동 프로젝트 생성 후 연결.
#   - team_id NULL → project_id NULL + warning.
#   - project 비어있음 → 그대로 NULL.
#
# 자동 생성 캐시:
#   - 같은 phase 안에서 동일 (team_id, name_norm)에 대해 1번만 INSERT.
#   - events·checklists 양쪽이 같은 (team_id, name)을 자동 생성해도 1개만 생성.
#   - (team_id, name_norm) 부분 UNIQUE 인덱스(#5) 덕분에 race-safe.
#
# Idempotent 가드:
#   - 모든 UPDATE는 `WHERE project_id IS NULL`. 마커 강제 삭제 후 재실행 시에도 노옵.
#   - 자동 생성도 같은 phase 안 캐시로 중복 없음.
#
# warning 카테고리 (4종):
#   - project_id_backfill_no_team       (row.team_id NULL → 연결 불가, row id + project)
#   - project_id_backfill_auto_created  (자동 생성 row 카운트만 — 노이즈 우려)
#   - project_id_backfill_dangling_trash       (events/checklists/meetings.trash_project_id 무효 참조)
#   - project_id_backfill_dangling_milestone   (project_milestones.project_id 무효 참조)
#   - project_id_backfill_dangling_member      (project_members.project_id 무효 참조)


def _phase_6_lookup_or_create_project(
    conn,
    team_id: int,
    project_name: str,
    cache: dict,
    auto_created_ids: set,
) -> int:
    """주어진 (team_id, project_name)에 대응하는 projects.id를 찾거나 생성.

    검색 우선순위:
      1) cache[(team_id, name_norm)]
      2) projects 매칭 — deleted_at IS NULL 우선, 없으면 deleted_at 포함 가장 작은 id
      3) 매칭 0건 → 자동 INSERT (is_active=1, is_hidden=0, is_private=0,
         owner_id=NULL, color=NULL, memo=NULL, name_norm 동시 채움)

    auto_created_ids에는 자동 생성된 projects.id가 추가된다 (set 누적).

    반환: projects.id (정수) — caller가 events/checklists에 UPDATE.
    """
    norm = normalize_name(project_name)
    cache_key = (team_id, norm)
    if cache_key in cache:
        return cache[cache_key]

    # 활성 프로젝트(deleted_at IS NULL) 우선 매칭.
    row = conn.execute(
        "SELECT id FROM projects "
        " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL "
        " ORDER BY id LIMIT 1",
        (team_id, norm),
    ).fetchone()
    if row is None:
        # 활성 0건 → 휴지통 포함 매칭.
        row = conn.execute(
            "SELECT id FROM projects "
            " WHERE team_id = ? AND name_norm = ? "
            " ORDER BY id LIMIT 1",
            (team_id, norm),
        ).fetchone()

    if row is not None:
        proj_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        cache[cache_key] = proj_id
        return proj_id

    # 매칭 0건 → 자동 생성. (team_id, name_norm) 부분 UNIQUE 인덱스(#5) 덕분에
    # 동시성 문제는 phase 본문 안 단일 트랜잭션에서 발생할 수 없음.
    cur = conn.execute(
        "INSERT INTO projects "
        "    (team_id, name, name_norm, is_active, is_hidden, is_private, "
        "     owner_id, color, memo, created_at) "
        "VALUES (?, ?, ?, 1, 0, 0, NULL, NULL, NULL, CURRENT_TIMESTAMP)",
        (team_id, project_name, norm),
    )
    new_id = cur.lastrowid
    cache[cache_key] = new_id
    auto_created_ids.add(new_id)
    return new_id


def _phase_6_backfill_table_project_id(
    conn,
    table: str,
    cache: dict,
    auto_created_ids: set,
) -> None:
    """events 또는 checklists의 project_id를 일괄 백필.

    가드: WHERE project_id IS NULL 만 대상으로 한다. 이미 채워진 row는 노옵.
    매칭 정책은 _phase_6_lookup_or_create_project 참고.
    """
    if not _table_exists(conn, table):
        return

    cols = _column_set(conn, table)
    if "project_id" not in cols or "team_id" not in cols or "project" not in cols:
        # Phase 1이 컬럼을 추가했어야 한다. 구조적 오류 — 본문 실패로 ROLLBACK.
        raise RuntimeError(f"phase 6: {table} missing project_id/team_id/project column")

    rows = conn.execute(
        f"SELECT id, team_id, project FROM {table} "
        f" WHERE project_id IS NULL"
    ).fetchall()

    for row in rows:
        rid = row["id"]
        team_id = row["team_id"]
        project = row["project"]

        # 1) project 문자열 비어있음 → NULL 유지 (공백만도 빈 것으로 간주).
        if project is None or str(project).strip() == "":
            continue

        # 2) team_id NULL → 연결 불가 + warning.
        if team_id is None:
            _append_team_migration_warning(
                conn,
                "project_id_backfill_no_team",
                f"{table} id={rid} project={project!r} team_id=NULL → project_id 미설정",
            )
            continue

        # 3) 매칭 또는 자동 생성.
        proj_id = _phase_6_lookup_or_create_project(
            conn, team_id, project, cache, auto_created_ids
        )

        # idempotent 가드 보강: WHERE project_id IS NULL.
        conn.execute(
            f"UPDATE {table} SET project_id = ? "
            f" WHERE id = ? AND project_id IS NULL",
            (proj_id, rid),
        )


def _phase_6_check_dangling_refs(conn) -> None:
    """project_milestones / project_members / *_trash_project_id의 무효 참조 검증.

    데이터 변경 X — warning만 누적. cleanup은 별도 운영 작업.
    """
    # 1) project_milestones.project_id 무효 참조
    if _table_exists(conn, "project_milestones"):
        rows = conn.execute(
            "SELECT pm.id, pm.project_id "
            "  FROM project_milestones pm "
            "  LEFT JOIN projects p ON p.id = pm.project_id "
            " WHERE p.id IS NULL"
        ).fetchall()
        for row in rows:
            mid = row["id"]
            pid = row["project_id"]
            _append_team_migration_warning(
                conn,
                "project_id_backfill_dangling_milestone",
                f"project_milestones id={mid} project_id={pid} → projects 매칭 없음",
            )

    # 2) project_members.project_id 무효 참조 — (project_id, user_id) 복합 PK라 id 컬럼 없음
    if _table_exists(conn, "project_members"):
        rows = conn.execute(
            "SELECT pm.project_id, pm.user_id "
            "  FROM project_members pm "
            "  LEFT JOIN projects p ON p.id = pm.project_id "
            " WHERE p.id IS NULL"
        ).fetchall()
        for row in rows:
            pid = row["project_id"]
            uid = row["user_id"]
            _append_team_migration_warning(
                conn,
                "project_id_backfill_dangling_member",
                f"project_members project_id={pid} user_id={uid} → projects 매칭 없음",
            )

    # 3) events / checklists / meetings의 trash_project_id 무효 참조
    for table in ("events", "checklists", "meetings"):
        if not _table_exists(conn, table):
            continue
        if "trash_project_id" not in _column_set(conn, table):
            continue
        rows = conn.execute(
            f"SELECT t.id, t.trash_project_id "
            f"  FROM {table} t "
            f"  LEFT JOIN projects p ON p.id = t.trash_project_id "
            f" WHERE t.trash_project_id IS NOT NULL AND p.id IS NULL"
        ).fetchall()
        for row in rows:
            rid = row["id"]
            tpid = row["trash_project_id"]
            _append_team_migration_warning(
                conn,
                "project_id_backfill_dangling_trash",
                f"{table} id={rid} trash_project_id={tpid} → projects 매칭 없음",
            )


def _phase_6_project_id_backfill(conn):
    """Phase 6: events/checklists.project_id 백필 + 자동 프로젝트 생성 + dangling 검증.

    빈 DB 첫 init_db()에서는 events·checklists 모두 빈 상태이므로 노옵.
    두 번째 init_db()에서는 마커가 있어 _pending_phases가 이 phase를 제외.
    마커 강제 삭제 후 재실행 시에도 `WHERE project_id IS NULL` 가드로 노옵.
    """
    if not _table_exists(conn, "projects"):
        # 구조적 전제 위반 — phase 1·5가 먼저 실행되어야 한다.
        raise RuntimeError("phase 6: projects table missing")

    # 1) events / checklists 백필 — 자동 생성 캐시 + 자동 생성 id 집합 공유.
    cache: dict = {}
    auto_created_ids: set = set()

    _phase_6_backfill_table_project_id(conn, "events", cache, auto_created_ids)
    _phase_6_backfill_table_project_id(conn, "checklists", cache, auto_created_ids)

    # 자동 생성 row 카운트만 누적 (이름 목록은 노이즈 우려 — 사양서 §주의사항).
    if auto_created_ids:
        _append_team_migration_warning(
            conn,
            "project_id_backfill_auto_created",
            f"auto-created projects count={len(auto_created_ids)} (운영자 후속 정리 권장)",
        )

    # 2) dangling 검증 — warning만 누적, 데이터 변경 X.
    _phase_6_check_dangling_refs(conn)


PHASES.append(("team_phase_6_project_id_backfill_v1", _phase_6_project_id_backfill))


# ── 팀 기능 그룹 A #7 — 비밀번호 hash 변환 + name_norm UNIQUE ──────
#
# 본 phase 본문은 한 트랜잭션 안에서 다음을 수행한다:
#   1) 평문 password 보유 사용자(빈 문자열·NULL 제외, admin 포함)에 대해
#      `password_hash = hash_password(password)` 갱신 + 같은 row의 `password = ''`.
#   2) sanity check: 변환 후 무작위(SQL ORDER BY id LIMIT 1) row의 hash를
#      본문 시작 시점에 캡처해 둔 평문으로 verify_password하여 True 확인.
#      실패 시 raise → 러너가 ROLLBACK으로 원본 평문 보존.
#   3) `users.name_norm` 전역 UNIQUE 인덱스, `teams.name_norm` UNIQUE 인덱스 생성.
#
# spec deviation: spec은 "password = NULL"로 명시하지만 `users.password` 컬럼이
#   `NOT NULL DEFAULT ''`이라 NULL 처리가 IntegrityError를 낸다. 빈 문자열로 저장하면
#   기존 가드(`password != ''`)가 NULL/빈 문자열을 동일 취급하므로 의미적으로 동등하다.
#   `password` 컬럼 drop은 Phase 5(별도 릴리스) 책임이므로 본 사이클은 평문 제거만 보장.
#
# 인덱스 생성을 본 phase 7에 둔 이유:
#   - 이미 phase 4 마커가 찍힌 환경에서는 phase 4 본문 변경이 무효 (재실행 안 됨).
#   - 인덱스를 phase 7에 두면 신규 환경(phase 4 → phase 7 순서) / 기존 환경 모두에서
#     실행된다. CREATE UNIQUE INDEX IF NOT EXISTS 가드로 idempotent.
#
# 가드:
#   - WHERE password_hash IS NULL AND password IS NOT NULL AND password != ''
#   - 마커 강제 삭제 후 재실행 시 변환 대상 0건이면 sanity check도 skip(노옵).
def _phase_7_password_hash(conn):
    """Phase 7: 평문 비밀번호 → hash 변환 + name_norm UNIQUE 인덱스 생성.

    팀 기능 그룹 A #7 사양:
      - 빈 password row는 변환 안 함 (가드).
      - sanity check 실패 시 raise → 러너 ROLLBACK으로 원본 평문 보존.
      - name_norm UNIQUE: users 전역(is_active 무관), teams 전역.
    """
    if not _table_exists(conn, "users"):
        return  # 신규 init_db 시 users는 phase 1 이전 단계에서 이미 생성된다.

    user_cols = _column_set(conn, "users")
    if "password_hash" not in user_cols:
        # phase 1이 password_hash 컬럼을 추가하므로 phase 7 시점에는 반드시 존재.
        # 방어적 가드: 없으면 노옵으로 종료(러너가 RuntimeError 내지 않게).
        return

    # 1) 변환 대상 추출 — 평문 보유 + hash 미보유.
    targets = conn.execute(
        "SELECT id, password FROM users "
        " WHERE password_hash IS NULL "
        "   AND password IS NOT NULL "
        "   AND password != ''"
    ).fetchall()

    converted_count = 0
    sanity_id = None
    sanity_plain = None  # 본문 종료 후 GC 대상. 절대 log/print 금지.

    for row in targets:
        uid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        plaintext = row["password"] if isinstance(row, sqlite3.Row) else row[1]
        new_hash = passwords.hash_password(plaintext)
        # 같은 트랜잭션에서 hash 저장 + 평문 제거 ('' — 컬럼 NOT NULL 제약 회피).
        conn.execute(
            "UPDATE users SET password_hash = ?, password = '' WHERE id = ?",
            (new_hash, uid),
        )
        converted_count += 1
        # 첫 번째 변환 row를 sanity 검사 대상으로 캡처.
        if sanity_id is None:
            sanity_id = uid
            sanity_plain = plaintext

    # 2) sanity check — 변환 row가 1건 이상이면 반드시 검증.
    if sanity_id is not None:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (sanity_id,)
        ).fetchone()
        stored = row["password_hash"] if isinstance(row, sqlite3.Row) else (row[0] if row else None)
        if not stored or not passwords.verify_password(sanity_plain, stored):
            raise RuntimeError(
                f"phase 7 sanity check failed: verify_password mismatch for user_id={sanity_id}"
            )

    # 평문은 더 이상 필요 없음 — 명시적으로 None 할당하여 참조 끊는다.
    sanity_plain = None

    # 3) UNIQUE 인덱스 생성 — users.name_norm 전역, teams.name_norm 전역.
    #    phase 4 본문이 이미 적용된 환경에서도 이 phase가 처음 돌면서 인덱스가 생성된다.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name_norm "
        "ON users(name_norm)"
    )
    if _table_exists(conn, "teams"):
        teams_cols = _column_set(conn, "teams")
        if "name_norm" in teams_cols:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_name_norm "
                "ON teams(name_norm)"
            )

    print(f"{_MIGRATION_LOG_PREFIX} phase 7: converted {converted_count} plaintext password(s) to hash")


PHASES.append(("team_phase_7_password_hash_v1", _phase_7_password_hash))


def _check_projects_team_name_unique(conn):
    """Preflight: projects (team_id, name_norm) 충돌 검사.

    team_id IS NOT NULL AND name_norm IS NOT NULL 안에서 GROUP BY로 동일 키 2+건이면
    부분 UNIQUE 인덱스 생성이 실패하므로 사전에 막는다.

    반환: list[(category, message)] — 카테고리는 'preflight_projects_team_name'.
    충돌 0건이면 빈 리스트 반환(정상 통과).
    """
    if not _table_exists(conn, "projects"):
        return []
    proj_cols = _column_set(conn, "projects")
    if "team_id" not in proj_cols or "name_norm" not in proj_cols:
        # Phase 1·5가 적용되기 전 단계에서는 검사 자체를 skip(노옵).
        return []

    rows = conn.execute(
        "SELECT team_id, name_norm, COUNT(*) AS dup, GROUP_CONCAT(id) AS ids "
        "  FROM projects "
        " WHERE team_id IS NOT NULL "
        "   AND name_norm IS NOT NULL "
        " GROUP BY team_id, name_norm "
        "HAVING COUNT(*) > 1"
    ).fetchall()

    conflicts: list = []
    for row in rows:
        team_id = row["team_id"] if isinstance(row, sqlite3.Row) else row[0]
        name_norm = row["name_norm"] if isinstance(row, sqlite3.Row) else row[1]
        dup = row["dup"] if isinstance(row, sqlite3.Row) else row[2]
        ids = row["ids"] if isinstance(row, sqlite3.Row) else row[3]
        conflicts.append((
            "preflight_projects_team_name",
            f"projects (team_id={team_id}, name_norm={name_norm!r}) "
            f"duplicates={dup} ids=[{ids}]",
        ))
    return conflicts


_PREFLIGHT_CHECKS.append(_check_projects_team_name_unique)


def _check_users_name_norm_unique(conn):
    """Preflight: users.name_norm 전역 충돌 검사 (#7).

    name_norm IS NOT NULL 안에서 GROUP BY로 동일 키 2+건이면 UNIQUE 인덱스
    생성이 실패한다. is_active 무관 (휴면 계정 포함 전수 검사).

    반환: list[(category, message)] — 카테고리는 'preflight_users_name_norm'.
    """
    if not _table_exists(conn, "users"):
        return []
    if "name_norm" not in _column_set(conn, "users"):
        # phase 1 이전엔 컬럼이 없어 검사 자체 skip(노옵).
        return []

    rows = conn.execute(
        "SELECT name_norm, COUNT(*) AS dup, GROUP_CONCAT(id) AS ids "
        "  FROM users "
        " WHERE name_norm IS NOT NULL "
        " GROUP BY name_norm "
        "HAVING COUNT(*) > 1"
    ).fetchall()

    conflicts: list = []
    for row in rows:
        name_norm = row["name_norm"] if isinstance(row, sqlite3.Row) else row[0]
        dup = row["dup"] if isinstance(row, sqlite3.Row) else row[1]
        ids = row["ids"] if isinstance(row, sqlite3.Row) else row[2]
        conflicts.append((
            "preflight_users_name_norm",
            f"users (name_norm={name_norm!r}) duplicates={dup} ids=[{ids}]",
        ))
    return conflicts


_PREFLIGHT_CHECKS.append(_check_users_name_norm_unique)


def _check_teams_name_norm_unique(conn):
    """Preflight: teams.name_norm 전역 충돌 검사 (#7).

    deleted_at 무관 — 같은 정규 이름이 여러 row에 살아 있으면 UNIQUE 실패한다.
    soft delete 정책 변경이 필요하면 후속 사이클에서 부분 인덱스로 재정의한다.

    반환: list[(category, message)] — 카테고리는 'preflight_teams_name_norm'.
    """
    if not _table_exists(conn, "teams"):
        return []
    if "name_norm" not in _column_set(conn, "teams"):
        return []

    rows = conn.execute(
        "SELECT name_norm, COUNT(*) AS dup, GROUP_CONCAT(id) AS ids "
        "  FROM teams "
        " WHERE name_norm IS NOT NULL "
        " GROUP BY name_norm "
        "HAVING COUNT(*) > 1"
    ).fetchall()

    conflicts: list = []
    for row in rows:
        name_norm = row["name_norm"] if isinstance(row, sqlite3.Row) else row[0]
        dup = row["dup"] if isinstance(row, sqlite3.Row) else row[1]
        ids = row["ids"] if isinstance(row, sqlite3.Row) else row[2]
        conflicts.append((
            "preflight_teams_name_norm",
            f"teams (name_norm={name_norm!r}) duplicates={dup} ids=[{ids}]",
        ))
    return conflicts


_PREFLIGHT_CHECKS.append(_check_teams_name_norm_unique)


def _check_user_ips_whitelist_unique(conn):
    """Preflight: user_ips 의 type='whitelist' ip_address 전역 충돌 검사 (#9).

    같은 ip_address가 2명 이상에게 whitelist면 부분 UNIQUE 인덱스 생성이 실패하므로
    사전에 막는다. Phase 3가 admin whitelist를 history로 강등한 뒤 실행되므로 여기
    걸리는 충돌은 일반 사용자 간 충돌이다. 자동 정리 없이 abort — 운영자가 정리한다.

    반환: list[(category, message)] — 카테고리는 'preflight_user_ips_whitelist'.
    """
    if not _table_exists(conn, "user_ips"):
        return []
    cols = _column_set(conn, "user_ips")
    if "ip_address" not in cols or "type" not in cols:
        return []

    rows = conn.execute(
        "SELECT ip_address, COUNT(*) AS dup, GROUP_CONCAT(user_id) AS uids "
        "  FROM user_ips "
        " WHERE type = 'whitelist' "
        " GROUP BY ip_address "
        "HAVING COUNT(*) > 1"
    ).fetchall()

    conflicts: list = []
    for row in rows:
        ip = row["ip_address"] if isinstance(row, sqlite3.Row) else row[0]
        dup = row["dup"] if isinstance(row, sqlite3.Row) else row[1]
        uids = row["uids"] if isinstance(row, sqlite3.Row) else row[2]
        conflicts.append((
            "preflight_user_ips_whitelist",
            f"user_ips (ip_address={ip!r}) whitelisted to {dup} users uids=[{uids}]",
        ))
    return conflicts


_PREFLIGHT_CHECKS.append(_check_user_ips_whitelist_unique)


def _run_preflight_checks(conn) -> list:
    """등록된 모든 preflight 검사를 돌려 (카테고리, 메시지) 튜플 목록을 모은다.

    각 검사 함수는 list[tuple[str, str]]을 반환한다 — (category, message).
    예: ('preflight_projects_team_name', "team_id=1 name_norm='abc' duplicates=2").
    검사 함수가 raise하면 ('preflight', f'<...> raised: <repr>') 단일 튜플로 변환된다.
    검사 함수가 0개면 빈 리스트 반환(정상 통과)."""
    conflicts: list = []
    for check in _PREFLIGHT_CHECKS:
        try:
            result = check(conn) or []
        except Exception as exc:
            # 검사 자체 실패도 충돌로 본다(서버 시작 거부) — 일반 카테고리로 기록.
            result = [(
                "preflight",
                f"preflight check {getattr(check, '__name__', check)!r} raised: {exc!r}",
            )]
        if result:
            conflicts.extend(result)
    return conflicts


def _run_phase_body(name: str, body) -> None:
    """phase 1개를 격리 트랜잭션으로 실행.

    Python sqlite3의 default isolation_level=""는 DDL(CREATE/ALTER/DROP) 직전에
    자동 COMMIT을 호출하여, body가 raise해도 DDL은 이미 영속화되는 함정이 있다.
    phase 본문이 DDL+DML 혼합일 때 부분 적용을 막기 위해 isolation_level=None으로
    자동 트랜잭션을 끄고 BEGIN IMMEDIATE/COMMIT/ROLLBACK을 수동으로 관리한다.
    이 변경은 이 conn에만 적용되며, 코드베이스 전역 get_conn() 시맨틱은 건드리지 않는다.

    본문 성공 시 같은 conn으로 마커 기록 → COMMIT(get_conn 컨텍스트 매니저 종료).
    본문 실패 시 ROLLBACK → stdout 로그 + RuntimeError 재발생으로 서버 시작 거부.
    """
    try:
        with get_conn() as conn:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                body(conn)
                _mark_phase_done(conn, name)
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        print(f"{_MIGRATION_LOG_PREFIX} phase {name!r} OK")
    except Exception as exc:
        print(f"{_MIGRATION_LOG_PREFIX} phase {name!r} FAILED: {exc!r}")
        raise RuntimeError(f"migration phase {name!r} failed: {exc!r}") from exc


def _run_phase_migrations() -> None:
    """init_db()의 with get_conn() 블록 종료 직후 호출되는 진입점.

    동작 순서:
      1. PHASES 중 미적용분(_pending_phases)이 0개면 즉시 반환(백업·preflight 모두 skip).
      2. 미적용분이 1개라도 있으면 자동 백업 1회.
      3. pre-preflight phase(_PRE_PREFLIGHT_PHASES에 등록된 것) 먼저 실행 — 각자 독립
         트랜잭션으로 본문 + 마커 커밋. preflight가 강제하는 invariant를 만족시키기 위한
         안전 정리(예: projects 자동 dedup)가 여기서 일어나고, 그 결과를 4)가 검증한다.
      4. preflight 검사 실행 — (3)이 정리하고도 남은 충돌 1개 이상이면 경고 누적 후
         RuntimeError로 서버 시작 거부.
      5. 나머지 pending phase를 각자 독립 트랜잭션으로 순차 실행.
    """
    pending = _pending_phases()
    if not pending:
        return

    # 1) 미적용 마이그레이션이 1개라도 있을 때만 백업 1회
    try:
        from backup import run_migration_backup
        backup_path = run_migration_backup(DB_PATH, _RUN_DIR)
        print(f"{_MIGRATION_LOG_PREFIX} pre-migration backup: {backup_path}")
    except Exception as exc:
        # 백업 실패 시 마이그레이션 진행 거부 — 데이터 보호 우선
        print(f"{_MIGRATION_LOG_PREFIX} backup FAILED, aborting migration: {exc!r}")
        raise RuntimeError(f"migration backup failed: {exc!r}") from exc

    # PHASES 등록 순서를 유지하면서 preflight 앞/뒤로 분할 (재정렬 금지 — 필터링만).
    pre_preflight = [(n, b) for (n, b) in pending if n in _PRE_PREFLIGHT_PHASES]
    rest = [(n, b) for (n, b) in pending if n not in _PRE_PREFLIGHT_PHASES]

    # 2) pre-preflight phase 실행 — 각자 독립 트랜잭션. 여기서 마커가 커밋되므로
    #    3)의 preflight가 실패해 RuntimeError가 나도 이 단계 마커는 롤백되지 않는다.
    for name, body in pre_preflight:
        _run_phase_body(name, body)

    # 3) preflight 검사 — 충돌 시 경고 누적 후 거부
    # 여기서는 default isolation_level("")을 그대로 둔다. preflight 자체는 SELECT만 돌고
    # 경고 누적은 INSERT/UPDATE(DML)이라 implicit BEGIN이 깔끔하게 동작한다. phase 러너에서만
    # isolation_level=None을 쓰는 것은 거기서만 DDL이 섞이기 때문이다 (asymmetry 의도적).
    with get_conn() as conn:
        conflicts = _run_preflight_checks(conn)
        if conflicts:
            for category, msg in conflicts:
                _append_team_migration_warning(conn, category, msg)
                print(f"{_MIGRATION_LOG_PREFIX} preflight conflict[{category}]: {msg}")
            # conn.commit()은 get_conn 종료 시 자동 — 경고는 영속화하고 종료
        # conflicts가 있으면 with 블록 종료 후 raise (경고가 먼저 commit 되도록)
    if conflicts:
        raise RuntimeError(
            f"migration preflight failed with {len(conflicts)} conflict(s); see settings.team_migration_warnings"
        )

    # 4) 나머지 phase 각자 격리 트랜잭션 실행
    for name, body in rest:
        _run_phase_body(name, body)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=_SQLITE_TIMEOUT_SECONDS)
    try:
        _ensure_wal_mode(conn)
        _apply_sqlite_pragmas(conn)
        conn.row_factory = sqlite3.Row
        yield conn
        conn.commit()
    finally:
        conn.close()


def _sqlite_synchronous_mode() -> str:
    mode = os.environ.get(_SQLITE_SYNCHRONOUS_ENV, _SQLITE_SYNCHRONOUS_DEFAULT)
    mode = mode.strip().upper()
    if mode in _SQLITE_SYNCHRONOUS_ALLOWED:
        return mode
    return _SQLITE_SYNCHRONOUS_DEFAULT


def _ensure_wal_mode(conn) -> None:
    global _WAL_MODE_READY
    if _WAL_MODE_READY:
        return
    with _WAL_MODE_LOCK:
        if _WAL_MODE_READY:
            return
        row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(row[0]).lower() if row else ""
        if mode != "wal":
            raise sqlite3.OperationalError(
                f"failed to enable WAL journal mode: {mode or 'unknown'}"
            )
        _WAL_MODE_READY = True


def _apply_sqlite_pragmas(conn) -> None:
    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute(f"PRAGMA synchronous={_sqlite_synchronous_mode()}")
    conn.execute(f"PRAGMA cache_size={_SQLITE_CACHE_SIZE}")
    conn.execute("PRAGMA temp_store=MEMORY")


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


def get_blocked_hidden_project_names(viewer) -> set[str]:
    """viewer가 접근할 수 없는 히든 프로젝트 이름 집합.
    viewer=None(비로그인): 모든 히든 프로젝트 차단.
    viewer=admin: 빈 셋(전체 접근).
    viewer=일반 로그인: 멤버가 아닌 히든 프로젝트 차단.
    """
    if viewer and viewer.get("role") == "admin":
        return set()
    with get_conn() as conn:
        if viewer is None:
            rows = conn.execute(
                "SELECT name FROM projects WHERE is_hidden = 1 AND deleted_at IS NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT name FROM projects
                   WHERE is_hidden = 1 AND deleted_at IS NULL
                   AND id NOT IN (SELECT project_id FROM project_members WHERE user_id = ?)""",
                (viewer["id"],)
            ).fetchall()
    return {r[0] for r in rows}


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
        # #6: project_id 동반. caller가 명시 지정한 값이 있으면 우선 사용.
        if "project_id" not in data or data.get("project_id") is None:
            data["project_id"] = _resolve_project_id_for_write(
                conn, data.get("team_id"), data.get("project")
            )
        cur = conn.execute(
            """INSERT INTO events
               (title, team_id, project, project_id, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority, event_type, is_public,
                recurrence_rule, recurrence_end, recurrence_parent_id, parent_event_id,
                bound_checklist_id)
               VALUES
               (:title, :team_id, :project, :project_id, :description, :location, :assignee, :all_day,
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
    # #6: project 변경 시 project_id 동기화. team_id는 기존 row에서 가져온다.
    row = conn.execute(
        "SELECT team_id FROM events WHERE id = ?", (event_id,)
    ).fetchone()
    row_team_id = (row["team_id"] if row else None) if isinstance(row, sqlite3.Row) else (row[0] if row else None)
    data["project_id"] = _resolve_project_id_for_write(
        conn, row_team_id, data.get("project")
    )
    conn.execute(
        """UPDATE events SET
            title              = :title,
            project            = :project,
            project_id         = :project_id,
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
        existing = conn.execute("SELECT project, team_id, event_type FROM events WHERE id = ?", (event_id,)).fetchone()
        _apply_event_update(conn, event_id, data)
        # 업무 일정의 프로젝트가 바뀌면 하위 업무들도 함께 이동
        if (existing and data.get("event_type", "schedule") == "schedule"
                and data.get("project") != (existing["project"] if existing else None)):
            # #6: 하위 업무도 project_id 동기화. existing.team_id 사용 (자식은 부모와 같은 팀).
            existing_team_id = existing["team_id"] if existing else None
            child_pid = _resolve_project_id_for_write(
                conn, existing_team_id, data.get("project")
            )
            conn.execute(
                "UPDATE events SET project = ?, project_id = ?, updated_at = CURRENT_TIMESTAMP "
                " WHERE parent_event_id = ? AND deleted_at IS NULL",
                (data.get("project"), child_pid, event_id)
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

        # #6: 새 부모 INSERT에 project_id 동반.
        new_parent["project_id"] = _resolve_project_id_for_write(
            conn, new_parent.get("team_id"), new_parent.get("project")
        )
        cur = conn.execute(
            """INSERT INTO events
               (title, team_id, project, project_id, description, location, assignee, all_day,
                start_datetime, end_datetime, created_by, source, meeting_id,
                kanban_status, priority, event_type, is_active,
                recurrence_rule, recurrence_end, recurrence_parent_id,
                bound_checklist_id)
               VALUES
               (:title, :team_id, :project, :project_id, :description, :location, :assignee, :all_day,
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
        # #6: project 문자열 + project_id 동시 갱신.
        # 기존 row의 team_id를 기준으로 (team_id, name_norm) 매칭.
        row = conn.execute(
            "SELECT team_id FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        row_team_id = (row["team_id"] if row else None) if isinstance(row, sqlite3.Row) else (row[0] if row else None)
        new_project_id = _resolve_project_id_for_write(conn, row_team_id, project)
        conn.execute(
            "UPDATE events SET project = ?, project_id = ?, updated_at = CURRENT_TIMESTAMP "
            " WHERE id = ?",
            (project or None, new_project_id, event_id),
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
            AND e.project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
          )
        )
        AND (
          e.project IS NULL OR e.project = ''
          OR e.project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
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
        # visible_hidden_ids: 로그인 사용자가 접근 가능한 히든 프로젝트 ID 집합
        visible_hidden_ids: set[int] = set()
        if viewer:
            if viewer.get("role") == "admin":
                hidden_rows = conn.execute(
                    "SELECT id FROM projects WHERE is_hidden = 1 AND deleted_at IS NULL"
                ).fetchall()
                visible_hidden_ids = {r[0] for r in hidden_rows}
            else:
                member_rows = conn.execute(
                    "SELECT project_id FROM project_members WHERE user_id = ?", (viewer["id"],)
                ).fetchall()
                visible_hidden_ids = {r[0] for r in member_rows}
        sql = """
            SELECT e.*, COALESCE(p.is_hidden, 0) AS project_is_hidden, p.id AS _proj_id
            FROM events e
            LEFT JOIN projects p ON e.project = p.name AND p.deleted_at IS NULL
        """
        if team_id:
            rows = conn.execute(
                f"{sql} WHERE e.team_id = ? {base_filter} ORDER BY e.start_datetime",
                (team_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                f"{sql} WHERE 1=1 {base_filter} ORDER BY e.start_datetime"
            ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # 로그인했지만 히든 프로젝트 비멤버는 제외
        if d.get("project_is_hidden") and viewer is not None:
            if d.get("_proj_id") not in visible_hidden_ids:
                continue
        d.pop("_proj_id", None)
        result.append(d)
    return result


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


def get_project_timeline(team_id: int = None, viewer=None, work_team_ids=None) -> list[dict]:
    """팀 → 프로젝트 2단계 그룹으로 일정 반환 (프로젝트 없는 일정은 '미지정'으로 묶음).

    work_team_ids — 팀 기능 그룹 A #10: team_id 미지정 시 비admin 사용자에게 보이는 팀 집합 제한
      (NULL team 일정은 작성자 본인만). admin·비로그인은 무관(기존 동작). team_id 명시 시 그 팀만.
    """
    is_scoped = not (viewer is None or viewer.get("role") == "admin")
    with get_conn() as conn:
        if team_id:
            rows = conn.execute(
                """SELECT e.*, t.name as team_name
                   FROM events e LEFT JOIN teams t ON e.team_id = t.id
                   WHERE e.team_id = ? AND e.deleted_at IS NULL
                   ORDER BY e.start_datetime""",
                (team_id,)
            ).fetchall()
        elif is_scoped:
            scope = list(work_team_ids) if work_team_ids is not None else list(_viewer_team_ids(viewer))
            auth_sql, auth_params = _author_in_sql(viewer, "e.created_by")
            if scope:
                _ph = ",".join("?" for _ in scope)
                rows = conn.execute(
                    f"""SELECT e.*, t.name as team_name
                       FROM events e LEFT JOIN teams t ON e.team_id = t.id
                       WHERE e.deleted_at IS NULL
                         AND (e.team_id IN ({_ph}) OR (e.team_id IS NULL AND {auth_sql}))
                       ORDER BY e.start_datetime""",
                    (*scope, *auth_params),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT e.*, t.name as team_name
                       FROM events e LEFT JOIN teams t ON e.team_id = t.id
                       WHERE e.deleted_at IS NULL AND e.team_id IS NULL AND {auth_sql}
                       ORDER BY e.start_datetime""",
                    tuple(auth_params),
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
            "SELECT id, name, color, start_date, end_date, is_active, is_private, is_hidden FROM projects WHERE deleted_at IS NULL"
        ).fetchall()
        ms_rows = conn.execute("""
            SELECT pm.project_id, pm.title, pm.date
              FROM project_milestones pm
             ORDER BY pm.project_id, pm.sort_order
        """).fetchall()
        # 히든 프로젝트 멤버 pre-fetch
        visible_hidden_ids: set[int] = set()
        if viewer and viewer.get("role") == "admin":
            visible_hidden_ids = {r["id"] for r in proj_meta_rows if r["is_hidden"]}
        elif viewer:
            member_rows = conn.execute(
                "SELECT project_id FROM project_members WHERE user_id = ?", (viewer["id"],)
            ).fetchall()
            visible_hidden_ids = {r[0] for r in member_rows}
    proj_meta = {r["name"]: dict(r) for r in proj_meta_rows}
    ms_by_pid = {}
    for r in ms_rows:
        ms_by_pid.setdefault(r["project_id"], []).append({"title": r["title"], "date": r["date"]})
    # 비활성(종료) 프로젝트 이름 집합
    inactive = {name for name, m in proj_meta.items() if m.get("is_active") == 0}
    # 비공개 프로젝트 이름 집합 (비로그인 시 제외)
    private_projs = {name for name, m in proj_meta.items() if m.get("is_private") == 1} if viewer is None else set()
    # 히든 프로젝트 이름 집합 (접근 불가한 것)
    hidden_projs = {name for name, m in proj_meta.items()
                    if m.get("is_hidden") and m.get("id") not in visible_hidden_ids}

    # team_name → project → events (비활성 프로젝트 제외)
    teams: dict[str, dict[str, list]] = {}
    for row in rows:
        d = dict(row)
        tname = d.get("team_name") or "미분류"
        p = d["project"] if d.get("project") and d["project"].strip() else "미지정"
        if p in inactive:
            continue  # 종료된 프로젝트 건너뜀
        if p in hidden_projs:
            continue  # 접근 불가 히든 프로젝트 건너뜀
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


def _get_user_projects(conn, user_name: str, viewer=None) -> set:
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
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        matched = {project for project in matched if project not in blocked}
    return matched


def get_calendar_milestones(user_name: str, viewer=None) -> list:
    """캘린더 이벤트 소스용. 로그인 사용자가 assignee인 프로젝트의 모든 milestone 반환."""
    with get_conn() as conn:
        user_projects = _get_user_projects(conn, user_name, viewer=viewer)
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


def get_upcoming_milestones(user_name: str, limit: int = 5, viewer=None) -> list:
    """내 스케줄용. 오늘 이후 사용자 프로젝트의 milestone + 종료 예정을 합산해 limit개 반환."""
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        user_projects = _get_user_projects(conn, user_name, viewer=viewer)
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


def _notification_visible_to_viewer(conn, notification: dict, viewer=None) -> bool:
    event_id = notification.get("event_id")
    if not event_id:
        return True
    event = conn.execute(
        "SELECT id, project, trash_project_id, deleted_at FROM events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if not event:
        return False
    return _trash_item_visible_to_viewer(conn, event, viewer)


def get_notification_count(user_name: str, viewer=None) -> int:
    """Return unread notifications count."""
    if viewer is not None:
        return len(get_pending_notifications(user_name, viewer=viewer))
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE user_name = ? AND is_read = 0",
            (user_name,)
        ).fetchone()
    return row["cnt"] if row else 0


def get_pending_notifications(user_name: str, viewer=None) -> list[dict]:
    """미읽은 알림 반환 (읽음 처리 없음)"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE user_name = ? AND is_read = 0 ORDER BY id DESC",
            (user_name,)
        ).fetchall()
        result = [dict(r) for r in rows]
        if viewer is not None:
            result = [r for r in result if _notification_visible_to_viewer(conn, r, viewer)]
    return result


def find_upload_references(url: str) -> list[dict]:
    """Return documents/checklists that reference an uploaded file URL."""
    like = f"%{url}%"
    refs: list[dict] = []
    with get_conn() as conn:
        for row in conn.execute(
            """SELECT id, deleted_at FROM meetings
               WHERE content LIKE ? OR attachments LIKE ?""",
            (like, like),
        ).fetchall():
            refs.append({"type": "document", "id": row["id"], "deleted": row["deleted_at"] is not None})
        for row in conn.execute(
            """SELECT id, deleted_at FROM checklists
               WHERE content LIKE ? OR attachments LIKE ?""",
            (like, like),
        ).fetchall():
            refs.append({"type": "checklist", "id": row["id"], "deleted": row["deleted_at"] is not None})
    return refs


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


def _author_token_set(viewer):
    """events/checklists.created_by 비교용 토큰 집합 — 신규 쓰기는 str(user.id), legacy 는 사용자 이름.

    팀 기능 그룹 A #10: NULL-team row 의 작성자 본인 판정에 사용. None/admin 입력은 빈 집합.
    """
    if not viewer:
        return set()
    s = {str(viewer.get("id"))}
    if viewer.get("name"):
        s.add(viewer.get("name"))
    return s


def _author_in_sql(viewer, col):
    """팀 기능 그룹 A #10: `col IN (?,?)` 형태 fragment + 파라미터 (작성자 토큰 집합 기준).

    빈 토큰이면 ("0", []) 반환 (항상 거짓).
    """
    tokens = list(_author_token_set(viewer))
    if not tokens:
        return "0", []
    ph = ",".join("?" for _ in tokens)
    return f"{col} IN ({ph})", tokens


def _project_team_filter_sql(work_team_ids, viewer, alias=""):
    """팀 기능 그룹 A #10: projects 테이블 행에 적용할 작업 팀 필터 SQL + 파라미터.

    반환: (where_fragment, params). viewer=None/admin 또는 work_team_ids 미적용 시 ("1=1", []).
    work_team_ids 가 None 이면 viewer 소속 팀 전체로 fallback.
    a.team_id ∈ 집합 OR (a.team_id IS NULL AND a.owner_id = viewer.id) 만 통과.
    """
    if viewer is None or viewer.get("role") == "admin":
        return "1=1", []
    scope = list(work_team_ids) if work_team_ids is not None else list(_viewer_team_ids(viewer))
    p = (alias + ".") if alias else ""
    vid = viewer.get("id")
    if scope:
        ph = ",".join("?" for _ in scope)
        return f"({p}team_id IN ({ph}) OR ({p}team_id IS NULL AND {p}owner_id = ?))", [*scope, vid]
    return f"({p}team_id IS NULL AND {p}owner_id = ?)", [vid]


def _events_checklists_team_name_set(conn, work_team_ids, viewer):
    """팀 기능 그룹 A #10: events/checklists 중 작업 팀 컨텍스트에 보이는 row 의 project 이름 집합.

    orphan 프로젝트(projects 테이블엔 없고 events/checklists.project 로만 존재)를 작업 팀별로
    제한하기 위한 보조 — viewer=None/admin 이면 None 반환(필터 안 함).
    """
    if viewer is None or viewer.get("role") == "admin":
        return None
    scope = list(work_team_ids) if work_team_ids is not None else list(_viewer_team_ids(viewer))
    auth_sql, auth_params = _author_in_sql(viewer, "created_by")
    names: set[str] = set()
    for table in ("events", "checklists"):
        if scope:
            ph = ",".join("?" for _ in scope)
            rows = conn.execute(
                f"SELECT DISTINCT project FROM {table} "
                f"WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL "
                f"AND (team_id IN ({ph}) OR (team_id IS NULL AND {auth_sql}))",
                (*scope, *auth_params),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT DISTINCT project FROM {table} "
                f"WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL "
                f"AND team_id IS NULL AND {auth_sql}",
                tuple(auth_params),
            ).fetchall()
        names.update(r[0] for r in rows if r[0])
    return names


def get_unified_project_list(active_only: bool = True, viewer=None, work_team_ids=None) -> list[dict]:
    """모든 페이지에서 일관되게 사용할 통합 프로젝트 목록.

    projects 테이블(삭제 안 된 것) + events.project + checklists.project 를 합산하여
    [{name, color, is_active, id}] 형태로 반환. 이름 기준 중복 제거 후 이름순 정렬.
    active_only=True(기본값)이면 is_active=1인 항목만 반환.
    viewer=None: 비로그인 — is_hidden=1 제외
    viewer=user_dict: 로그인 사용자 — admin이거나 project_members에 포함된 히든만 포함
    work_team_ids — 팀 기능 그룹 A #10: 작업 팀 필터. team_id ∈ 집합 OR (NULL & owner 본인)만,
      orphan 프로젝트 이름도 작업 팀 컨텍스트의 events/checklists 것만 포함. None 이면 소속 팀 전체 fallback.
    """
    team_where, team_params = _project_team_filter_sql(work_team_ids, viewer, alias="p")
    with get_conn() as conn:
        # 1. projects 테이블 (삭제 안 된 것 + 작업 팀 필터)
        proj_rows = conn.execute(
            f"SELECT p.id, p.name, p.color, p.is_active, p.is_private, p.is_hidden, p.owner_id, p.team_id "
            f"FROM projects p WHERE p.deleted_at IS NULL AND {team_where}",
            team_params,
        ).fetchall()
        # 2~3. orphan 프로젝트 이름 집합 (작업 팀 컨텍스트 제한)
        orphan_names = _events_checklists_team_name_set(conn, work_team_ids, viewer)
        if orphan_names is None:
            ev_proj_rows = conn.execute(
                "SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
            ).fetchall()
            ck_proj_rows = conn.execute(
                "SELECT DISTINCT project FROM checklists WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL"
            ).fetchall()
            orphan_iter = [r[0] for r in ev_proj_rows] + [r[0] for r in ck_proj_rows]
        else:
            orphan_iter = list(orphan_names)
        # 4. 히든 프로젝트 멤버 pre-fetch (N+1 방지)
        visible_hidden_ids: set[int] = set()
        if viewer and viewer.get("role") == "admin":
            # admin은 모든 히든 프로젝트 가시
            visible_hidden_ids = {r["id"] for r in proj_rows if r["is_hidden"]}
        elif viewer:
            member_rows = conn.execute(
                "SELECT project_id FROM project_members WHERE user_id = ?", (viewer["id"],)
            ).fetchall()
            visible_hidden_ids = {r[0] for r in member_rows}

    proj_map: dict[str, dict] = {}
    hidden_blocked: set[str] = set()  # 접근 불가 히든 프로젝트 이름 — orphan 재추가 방지
    for r in proj_rows:
        is_hidden = r["is_hidden"] if r["is_hidden"] is not None else 0
        if is_hidden:
            if r["id"] not in visible_hidden_ids:
                hidden_blocked.add(r["name"])
                continue
        proj_map[r["name"]] = {
            "id": r["id"],
            "name": r["name"],
            "color": r["color"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "is_private": r["is_private"] if r["is_private"] is not None else 0,
            "is_hidden": is_hidden,
        }

    # events/checklists 에만 있는 프로젝트 이름도 포함 (orphan — is_active 기본 1)
    for name in orphan_iter:
        if name and name not in proj_map and name not in hidden_blocked:
            proj_map[name] = {"id": None, "name": name, "color": None, "is_active": 1, "is_private": 0, "is_hidden": 0}

    result = sorted(proj_map.values(), key=lambda x: x["name"])
    if active_only:
        result = [p for p in result if p.get("is_active", 1)]
    return result


# ── Project Management ───────────────────────────────────

def get_all_projects_with_events(viewer=None, work_team_ids=None) -> list[dict]:
    """프로젝트 목록 + 각 프로젝트의 일정 반환 (projects 테이블 + events.project + checklists.project 합산)
    viewer=None: 비로그인 — is_hidden=1 제외
    viewer=user_dict: 로그인 사용자 — admin이거나 project_members에 포함된 히든만 포함
    work_team_ids — 팀 기능 그룹 A #10: 작업 팀 필터 (projects: team_id ∈ 집합 OR NULL&owner본인,
      events: team_id ∈ 집합 OR NULL&작성자본인, orphan 이름도 작업 팀 컨텍스트 것만). None 이면 소속 팀 전체.
    """
    team_where, team_params = _project_team_filter_sql(work_team_ids, viewer, alias="projects")
    is_scoped = not (viewer is None or viewer.get("role") == "admin")
    if is_scoped:
        ev_scope = list(work_team_ids) if work_team_ids is not None else list(_viewer_team_ids(viewer))
        ev_auth_sql, ev_auth_params = _author_in_sql(viewer, "e.created_by")
    with get_conn() as conn:
        # projects 테이블의 프로젝트 (삭제되지 않은 것 + 작업 팀 필터)
        proj_rows = conn.execute(
            f"SELECT * FROM projects WHERE deleted_at IS NULL AND {team_where} ORDER BY is_active DESC, name",
            team_params,
        ).fetchall()
        # orphan 프로젝트 이름 (작업 팀 컨텍스트 제한)
        orphan_names = _events_checklists_team_name_set(conn, work_team_ids, viewer)
        if orphan_names is None:
            _ev_pr = conn.execute("SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL").fetchall()
            _ck_pr = conn.execute("SELECT DISTINCT project FROM checklists WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL").fetchall()
            orphan_iter = [r[0] for r in _ev_pr] + [r[0] for r in _ck_pr]
        else:
            orphan_iter = list(orphan_names)
        # 이벤트들 (삭제되지 않은 것 + 작업 팀 필터)
        if is_scoped:
            if ev_scope:
                _ph = ",".join("?" for _ in ev_scope)
                ev_rows = conn.execute(
                    f"""SELECT e.*, t.name as team_name
                       FROM events e LEFT JOIN teams t ON e.team_id = t.id
                       WHERE e.deleted_at IS NULL
                         AND (e.team_id IN ({_ph}) OR (e.team_id IS NULL AND {ev_auth_sql}))
                       ORDER BY e.start_datetime""",
                    (*ev_scope, *ev_auth_params),
                ).fetchall()
            else:
                ev_rows = conn.execute(
                    f"""SELECT e.*, t.name as team_name
                       FROM events e LEFT JOIN teams t ON e.team_id = t.id
                       WHERE e.deleted_at IS NULL AND e.team_id IS NULL AND {ev_auth_sql}
                       ORDER BY e.start_datetime""",
                    tuple(ev_auth_params),
                ).fetchall()
        else:
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
        # 히든 프로젝트 멤버 pre-fetch (N+1 방지)
        visible_hidden_ids: set[int] = set()
        if viewer and viewer.get("role") == "admin":
            visible_hidden_ids = {r["id"] for r in proj_rows if r["is_hidden"]}
        elif viewer:
            member_rows = conn.execute(
                "SELECT project_id FROM project_members WHERE user_id = ?", (viewer["id"],)
            ).fetchall()
            visible_hidden_ids = {r[0] for r in member_rows}

    ms_by_pid: dict = {}
    for r in ms_rows:
        ms_by_pid.setdefault(r["project_id"], []).append({"title": r["title"], "date": r["date"]})

    # projects 테이블 기반 dict (히든 필터 적용)
    proj_map: dict[str, dict] = {}
    hidden_blocked: set[str] = set()  # 접근 불가 히든 프로젝트 이름 — orphan/이벤트 재추가 방지
    for r in proj_rows:
        is_hidden = r["is_hidden"] if r["is_hidden"] is not None else 0
        if is_hidden and r["id"] not in visible_hidden_ids:
            hidden_blocked.add(r["name"])
            continue
        proj_map[r["name"]] = {
            "id": r["id"], "name": r["name"], "color": r["color"],
            "start_date": r["start_date"], "end_date": r["end_date"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "is_private": r["is_private"] if r["is_private"] is not None else 0,
            "is_hidden": is_hidden,
            "owner_id": r["owner_id"],
            "memo": r["memo"],
            "events": [],
            "milestones": ms_by_pid.get(r["id"], []),
        }

    # events.project / checklists.project에만 있는 orphan 프로젝트도 추가 ('미지정' 제외)
    for name in orphan_iter:
        if name and name != '미지정' and name not in proj_map and name not in hidden_blocked:
            proj_map[name] = {"id": None, "name": name, "color": None,
                              "start_date": None, "end_date": None, "is_active": 1,
                              "is_private": 0, "is_hidden": 0, "owner_id": None, "memo": None, "events": []}

    # 이벤트 분류
    unset_events = []
    for r in ev_rows:
        d = dict(r)
        p = d.get("project") or ""
        if p.strip():
            if p in hidden_blocked:
                continue  # 접근 불가 히든 프로젝트 이벤트 누출 방지
            if p not in proj_map:
                proj_map[p] = {"id": None, "name": p, "color": None,
                               "start_date": None, "end_date": None, "is_active": 1,
                               "is_private": 0, "is_hidden": 0, "owner_id": None, "memo": None, "events": []}
            proj_map[p]["events"].append(d)
        else:
            unset_events.append(d)

    active   = sorted((p for p in proj_map.values() if p.get("is_active", 1)), key=lambda x: x["name"])
    inactive = sorted((p for p in proj_map.values() if not p.get("is_active", 1)), key=lambda x: x["name"])
    result = active + inactive
    if unset_events:
        result.append({"id": None, "name": "미지정", "color": None,
                       "start_date": None, "end_date": None, "is_active": 1,
                       "is_hidden": 0, "owner_id": None, "memo": None, "events": unset_events})
    return result


def get_all_projects_meta(viewer=None, work_team_ids=None) -> list[dict]:
    """프로젝트 메타 정보만 반환 (events 제외). check 페이지 등 이벤트 불필요 시 사용.
    viewer=None: 비로그인 — is_hidden=1 제외
    viewer=user_dict: 로그인 사용자 — admin이거나 project_members에 포함된 히든만 포함
    work_team_ids — 팀 기능 그룹 A #10: 작업 팀 필터 (get_unified_project_list 와 동일 의미).
    """
    team_where, team_params = _project_team_filter_sql(work_team_ids, viewer, alias="projects")
    with get_conn() as conn:
        proj_rows = conn.execute(
            f"SELECT id, name, color, is_active, is_private, is_hidden FROM projects WHERE deleted_at IS NULL AND {team_where} ORDER BY is_active DESC, name",
            team_params,
        ).fetchall()
        orphan_names = _events_checklists_team_name_set(conn, work_team_ids, viewer)
        if orphan_names is None:
            _ev_pr = conn.execute("SELECT DISTINCT project FROM events WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL").fetchall()
            _ck_pr = conn.execute("SELECT DISTINCT project FROM checklists WHERE project IS NOT NULL AND project != '' AND deleted_at IS NULL").fetchall()
            orphan_iter = [r[0] for r in _ev_pr] + [r[0] for r in _ck_pr]
        else:
            orphan_iter = list(orphan_names)
        # 히든 프로젝트 멤버 pre-fetch
        visible_hidden_ids: set[int] = set()
        if viewer and viewer.get("role") == "admin":
            visible_hidden_ids = {r["id"] for r in proj_rows if r["is_hidden"]}
        elif viewer:
            member_rows = conn.execute(
                "SELECT project_id FROM project_members WHERE user_id = ?", (viewer["id"],)
            ).fetchall()
            visible_hidden_ids = {r[0] for r in member_rows}

    proj_map: dict[str, dict] = {}
    hidden_blocked: set[str] = set()  # 접근 불가 히든 프로젝트 이름 — orphan 재추가 방지
    for r in proj_rows:
        is_hidden = r["is_hidden"] if r["is_hidden"] is not None else 0
        if is_hidden and r["id"] not in visible_hidden_ids:
            hidden_blocked.add(r["name"])
            continue
        proj_map[r["name"]] = {
            "id": r["id"], "name": r["name"], "color": r["color"],
            "is_active": r["is_active"] if r["is_active"] is not None else 1,
            "is_private": r["is_private"] if r["is_private"] is not None else 0,
            "is_hidden": is_hidden,
        }

    for name in orphan_iter:
        if name and name not in proj_map and name not in hidden_blocked:
            proj_map[name] = {"id": None, "name": name, "color": None, "is_active": 1, "is_private": 0, "is_hidden": 0}

    active   = sorted((p for p in proj_map.values() if p.get("is_active", 1)), key=lambda x: x["name"])
    inactive = sorted((p for p in proj_map.values() if not p.get("is_active", 1)), key=lambda x: x["name"])
    return active + inactive


def create_project(name: str, color: str = None, memo: str = None,
                   team_id: int | None = None) -> int:
    """프로젝트 생성. team_id가 주어지면 (team_id, name_norm) 안에서 중복 사전 검사.

    team_id=None 호출은 호환 경로 — 운영자 정리 영역에 신규 row를 만든다.
    팀 컨텍스트 필수인 라우트(`POST /api/manage/projects`)는 항상 team_id 명시.
    """
    norm = normalize_name(name)
    with get_conn() as conn:
        if team_id is not None:
            existing = conn.execute(
                "SELECT 1 FROM projects "
                " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL",
                (team_id, norm),
            ).fetchone()
            if existing:
                raise sqlite3.IntegrityError(
                    f"project name_norm={norm!r} already exists in team_id={team_id}"
                )
        cur = conn.execute(
            "INSERT INTO projects (name, name_norm, color, memo, team_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, norm, color, memo, team_id),
        )
    return cur.lastrowid


def create_hidden_project(name: str, color: str, memo: str, owner_id: int,
                          team_id: int | None = None) -> dict | None:
    """히든 프로젝트 생성. (team_id, name_norm) 안에서 중복 시 None 반환.

    team_id는 현재 작업 팀(`resolve_work_team`) 기준으로 라우트가 명시 전달한다 (#15-1).
    None이면 NULL row를 만들지 않고 ValueError — 다중 팀 모델에서 owner의 users.team_id
    fallback은 더 이상 사용하지 않는다.
    """
    if team_id is None:
        raise ValueError("히든 프로젝트는 team_id가 필요합니다")
    norm = normalize_name(name)
    with get_conn() as conn:
        # (team_id, name_norm) 중복 검사.
        exists = conn.execute(
            "SELECT 1 FROM projects "
            " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL",
            (team_id, norm),
        ).fetchone()
        if exists:
            return None

        cur = conn.execute(
            "INSERT INTO projects "
            "    (name, name_norm, color, memo, is_hidden, owner_id, team_id) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (name, norm, color or None, (memo or "").strip() or None,
             owner_id, team_id),
        )
        project_id = cur.lastrowid
        conn.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (?, ?)",
            (project_id, owner_id),
        )
    return {"id": project_id, "name": name, "color": color, "memo": memo,
            "is_hidden": 1, "owner_id": owner_id, "team_id": team_id}


def get_hidden_project_member_ids(project_id: int) -> list[int]:
    """히든 프로젝트의 멤버 user_id 목록 반환 (owner 포함)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM project_members WHERE project_id = ?", (project_id,)
        ).fetchall()
    return [r[0] for r in rows]


def is_hidden_project_visible(project_id: int, user: dict) -> bool:
    """사용자가 해당 히든 프로젝트를 볼 수 있는지 확인."""
    if not user:
        return False
    if user.get("role") == "admin":
        return True
    with get_conn() as conn:
        row = _hidden_project_visible_row(conn, project_id, user)
    return row is not None


def _hidden_project_visible_row(conn, project_id: int, user: dict):
    if not user:
        return None
    if user.get("role") == "admin":
        return {"ok": 1}
    user_id = user.get("id")
    if user_id is None:
        return None
    return conn.execute(
        """SELECT 1
           FROM project_members pm
           JOIN users u ON u.id = pm.user_id
           JOIN projects p ON p.id = pm.project_id
           WHERE pm.project_id = ?
             AND pm.user_id = ?
             AND u.is_active = 1
             AND p.team_id IS NOT NULL
             AND EXISTS (
                   SELECT 1 FROM user_teams ut
                    WHERE ut.user_id = u.id
                      AND ut.team_id = p.team_id
                      AND ut.status = 'approved'
                 )""",
        (project_id, user_id)
    ).fetchone()


def _can_view_hidden_trash_project(conn, project_row, viewer, project_deleted: bool = False) -> bool:
    """휴지통에서 히든 프로젝트 또는 그 소속 항목을 viewer에게 보여도 되는지 확인."""
    if not project_row or not project_row["is_hidden"]:
        return True
    if not viewer:
        return False
    if viewer.get("role") == "admin":
        return True
    if project_deleted:
        return project_row["owner_id"] == viewer.get("id")
    return _hidden_project_visible_row(conn, project_row["id"], viewer) is not None


def _trash_item_project_row(conn, item: dict | sqlite3.Row):
    """휴지통 항목이 연결된 프로젝트 행 반환. 없으면 None."""
    trash_project_id = item["trash_project_id"] if "trash_project_id" in item.keys() else None
    if trash_project_id is not None:
        return conn.execute(
            "SELECT id, name, is_hidden, owner_id, deleted_at FROM projects WHERE id = ?",
            (trash_project_id,)
        ).fetchone()
    project_name = (item["project"] if "project" in item.keys() else None) or ""
    if not project_name:
        return None
    return conn.execute(
        "SELECT id, name, is_hidden, owner_id, deleted_at FROM projects WHERE name = ?",
        (project_name,)
    ).fetchone()


def _trash_item_visible_to_viewer(conn, item: dict | sqlite3.Row, viewer) -> bool:
    project = _trash_item_project_row(conn, item)
    if not project or not project["is_hidden"]:
        return True
    return _can_view_hidden_trash_project(
        conn,
        project,
        viewer,
        project_deleted=project["deleted_at"] is not None,
    )


def get_project_by_name(name: str) -> dict | None:
    """이름으로 프로젝트 조회 (삭제 여부 무관)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM projects WHERE name = ?", (name,)
        ).fetchone()
    return dict(row) if row else None


def _resolve_project_id_for_write(
    conn,
    team_id: int | None,
    project_name: str | None,
) -> int | None:
    """신규 쓰기 경로에서 project 문자열 → projects.id 해석 (#6).

    범위:
      - INSERT INTO events / checklists 시 호출
      - PATCH /api/events/{id}/project (project 변경) 시 호출

    매칭 정책 (백필과 동일):
      - team_id가 None이거나 project가 비어있으면 None 반환
      - (team_id, name_norm) 매칭. deleted_at IS NULL 우선.
      - 매칭 0건이어도 자동 생성하지 않고 None 반환 (자동 생성은 phase 본문 책임).
        라우트 계층이 별도로 create_project를 호출했을 때 자연스럽게 매칭된다.

    이름이 약간 다르지만 이미 존재하는 프로젝트와 매칭되도록 normalize_name 사용.
    """
    if team_id is None or project_name is None:
        return None
    if not str(project_name).strip():
        return None
    norm = normalize_name(project_name)
    row = conn.execute(
        "SELECT id FROM projects "
        " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL "
        " ORDER BY id LIMIT 1",
        (team_id, norm),
    ).fetchone()
    if row is None:
        # 활성 매칭 없음 → 휴지통 포함 (운영 의미: 활성 복구 시점에 자연 정합).
        row = conn.execute(
            "SELECT id FROM projects "
            " WHERE team_id = ? AND name_norm = ? "
            " ORDER BY id LIMIT 1",
            (team_id, norm),
        ).fetchone()
    if row is None:
        return None
    return row["id"] if isinstance(row, sqlite3.Row) else row[0]


def get_hidden_project_members(project_id: int) -> list[dict]:
    """히든 프로젝트 멤버 목록 (owner 포함). user 정보 JOIN.

    `u.team_id`는 표시용 legacy 값일 뿐 — 멤버십·권한 판단에는 쓰지 않는다 (#15-1).
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT u.id, u.name, u.team_id,
                      CASE WHEN p.owner_id = u.id THEN 1 ELSE 0 END AS is_owner
               FROM project_members pm
               JOIN users u ON u.id = pm.user_id
               JOIN projects p ON p.id = pm.project_id
               WHERE pm.project_id = ?
               ORDER BY is_owner DESC, u.name""",
            (project_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_hidden_project_addable_members(project_id: int) -> list[dict]:
    """추가 가능한 사용자 목록 (#15-1: projects.team_id + user_teams 기준).

    프로젝트 소속 팀(`projects.team_id`)의 승인 멤버(`user_teams.status='approved'`) 중
    현재 멤버가 아니고 활성·비-admin인 사용자. owner를 참조하지 않으므로
    owner_id = NULL 복구 상황에서도 동작한다. team_id IS NULL 잔존 프로젝트는 [].
    """
    with get_conn() as conn:
        proj_row = conn.execute(
            "SELECT team_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not proj_row or proj_row["team_id"] is None:
            return []
        project_team_id = proj_row["team_id"]
        rows = conn.execute(
            """SELECT u.id, u.name FROM users u
               WHERE u.is_active = 1
               AND u.role != 'admin'
               AND u.id NOT IN (SELECT user_id FROM project_members WHERE project_id = ?)
               AND EXISTS (
                     SELECT 1 FROM user_teams ut
                      WHERE ut.user_id = u.id
                        AND ut.team_id = ?
                        AND ut.status = 'approved'
                   )
               ORDER BY u.name""",
            (project_id, project_team_id)
        ).fetchall()
    return [dict(r) for r in rows]


def add_hidden_project_member(project_id: int, user_id: int) -> bool | None:
    """멤버 추가 (#15-1: projects.team_id + user_teams 기준).

    프로젝트 소속 팀의 승인 멤버(`user_teams.status='approved'`)만 추가 가능.
    owner를 참조하지 않으므로 owner_id = NULL 복구 시 admin이 호출해도 동작한다.
    반환: True(성공), False(팀 미승인/admin/비활성/NULL팀), None(이미 멤버)
    """
    with get_conn() as conn:
        proj_row = conn.execute(
            "SELECT team_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not proj_row or proj_row["team_id"] is None:
            return False
        proj_team_id = proj_row["team_id"]
        target_row = conn.execute(
            "SELECT role, is_active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not target_row:
            return False
        if not target_row["is_active"]:
            return False
        if target_row["role"] == "admin":
            return False
        approved = conn.execute(
            "SELECT 1 FROM user_teams "
            " WHERE user_id = ? AND team_id = ? AND status = 'approved'",
            (user_id, proj_team_id)
        ).fetchone()
        if not approved:
            return False
        existing = conn.execute(
            "SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id)
        ).fetchone()
        if existing:
            return None
        conn.execute(
            "INSERT INTO project_members (project_id, user_id) VALUES (?, ?)",
            (project_id, user_id)
        )
    return True


def remove_hidden_project_member(project_id: int, user_id: int) -> bool:
    """멤버 삭제. owner 자신은 삭제 불가(False 반환)."""
    with get_conn() as conn:
        proj = conn.execute(
            "SELECT owner_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not proj:
            return False
        if proj["owner_id"] == user_id:
            return False
        conn.execute(
            "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
            (project_id, user_id)
        )
    return True


def transfer_hidden_project_owner(project_id: int, new_owner_id: int, requester_id: int) -> bool:
    """owner가 다른 멤버에게 권한 이양.
    검증: requester_id == current owner_id, new_owner_id in project_members
    처리: projects.owner_id = new_owner_id (기존 owner는 members에 유지)
    """
    with get_conn() as conn:
        proj = conn.execute(
            "SELECT owner_id FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if not proj or proj["owner_id"] != requester_id:
            return False
        member = conn.execute(
            """SELECT 1
               FROM project_members pm
               JOIN users u ON u.id = pm.user_id
               JOIN projects p ON p.id = pm.project_id
               WHERE pm.project_id = ?
                 AND pm.user_id = ?
                 AND u.is_active = 1
                 AND p.team_id IS NOT NULL
                 AND u.role != 'admin'
                 AND EXISTS (
                       SELECT 1 FROM user_teams ut
                        WHERE ut.user_id = u.id
                          AND ut.team_id = p.team_id
                          AND ut.status = 'approved'
                     )""",
            (project_id, new_owner_id)
        ).fetchone()
        if not member:
            return False
        conn.execute(
            "UPDATE projects SET owner_id = ? WHERE id = ?",
            (new_owner_id, project_id)
        )
    return True


def admin_change_hidden_project_owner(project_id: int, new_owner_id: int) -> bool:
    """admin 강제 관리자 변경.
    검증: new_owner_id in project_members + projects.team_id 승인 멤버 (#15-1)
    처리: projects.owner_id = new_owner_id (기존 owner는 members에 유지)
    """
    with get_conn() as conn:
        member = conn.execute(
            """SELECT 1
               FROM project_members pm
               JOIN users u ON u.id = pm.user_id
               JOIN projects p ON p.id = pm.project_id
               WHERE pm.project_id = ?
                 AND pm.user_id = ?
                 AND u.is_active = 1
                 AND p.team_id IS NOT NULL
                 AND u.role != 'admin'
                 AND EXISTS (
                       SELECT 1 FROM user_teams ut
                        WHERE ut.user_id = u.id
                          AND ut.team_id = p.team_id
                          AND ut.status = 'approved'
                     )""",
            (project_id, new_owner_id)
        ).fetchone()
        if not member:
            return False
        conn.execute(
            "UPDATE projects SET owner_id = ? WHERE id = ?",
            (new_owner_id, project_id)
        )
    return True


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


def rename_project(old_name: str, new_name: str, merge: bool = False):
    """프로젝트 이름 변경. 같은 팀 안에서만 (team_id, name_norm) 중복을 차단한다.

    매칭 규칙:
      - old_proj는 name=old_name로 식별 (legacy 호출부 호환).
      - target_proj는 (old_proj.team_id, normalize_name(new_name)) 안에서만 검색 →
        다른 팀의 동일 이름은 충돌이 아니다.
      - target_proj와 old_proj가 같은 팀에 있고 다른 row면 merge=True 시 합치고,
        merge=False면 IntegrityError.
    """
    if old_name == new_name:
        return
    new_norm = normalize_name(new_name)
    with get_conn() as conn:
        old_proj = conn.execute(
            "SELECT id, team_id FROM projects WHERE name = ? AND deleted_at IS NULL",
            (old_name,)
        ).fetchone()
        old_team_id = old_proj["team_id"] if old_proj else None

        # target_proj는 같은 팀 안에서만 (team_id, name_norm)로 검색.
        # team_id가 NULL인 row끼리는 (운영자 정리 영역) 충돌 무시 — old_team_id가 NULL이면
        # 같은 NULL 키들과 매칭하지 않고 None으로 둔다.
        target_proj = None
        if old_team_id is not None:
            target_proj = conn.execute(
                "SELECT id FROM projects "
                " WHERE team_id = ? AND name_norm = ? AND deleted_at IS NULL",
                (old_team_id, new_norm),
            ).fetchone()

        if target_proj and old_proj and target_proj["id"] != old_proj["id"]:
            if not merge:
                raise sqlite3.IntegrityError(f"project '{new_name}' already exists")
            # merge 분기는 항상 같은 팀(`old_team_id`) 안에서만 실행된다 — target_proj 검색이
            # team_id 한정. events/checklists의 project 라벨 갱신도 같은 팀으로 좁혀 다른 팀
            # 동일 이름이 휘말리지 않도록 한다. old_team_id가 NULL인 경우는 target_proj 자체가
            # 없으므로 이 분기에 진입하지 않는다.
            #
            # TODO #10: 본 merge 분기는 events/checklists.project 문자열만 갱신하므로
            #   project_id는 여전히 soft-deleted된 old_proj.id를 가리킨다. 사양서 §주의사항
            #   ("rename_project: project_id는 그대로")에 따라 #6 사이클은 이 비정합을
            #   유지하고, 라우트 읽기 경로 전환(#10) 시 project_id도 target_proj.id로 일괄
            #   재동기화한다. 그 전까지는 read 경로가 project_id를 신뢰하지 않으므로 안전.
            conn.execute(
                "UPDATE events SET project = ? "
                " WHERE project = ? AND team_id = ?",
                (new_name, old_name, old_team_id),
            )
            conn.execute(
                "UPDATE checklists SET project = ? "
                " WHERE project = ? AND team_id = ?",
                (new_name, old_name, old_team_id),
            )
            conn.execute("""
                DELETE FROM project_milestones
                 WHERE project_id = ?
                   AND date IN (
                       SELECT date FROM project_milestones WHERE project_id = ?
                   )
            """, (old_proj["id"], target_proj["id"]))
            conn.execute(
                "UPDATE project_milestones SET project_id = ? WHERE project_id = ?",
                (target_proj["id"], old_proj["id"])
            )
            conn.execute(
                "UPDATE projects SET deleted_at = COALESCE(deleted_at, ?) WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), old_proj["id"])
            )
            return

        # 일반 rename: 다른 팀의 동일 이름(다른 row)을 휩쓸지 않도록 id 한정.
        # old_proj가 None인 경우(orphan label rename — events/checklists에만 존재)는 기존
        # 전역 동작을 유지한다 — projects 테이블 갱신이 일어나지 않으므로 안전.
        if old_proj is not None:
            conn.execute(
                "UPDATE projects SET name = ?, name_norm = ? WHERE id = ?",
                (new_name, new_norm, old_proj["id"]),
            )
            if old_team_id is not None:
                conn.execute(
                    "UPDATE events SET project = ? "
                    " WHERE project = ? AND team_id = ?",
                    (new_name, old_name, old_team_id),
                )
                conn.execute(
                    "UPDATE checklists SET project = ? "
                    " WHERE project = ? AND team_id = ?",
                    (new_name, old_name, old_team_id),
                )
            else:
                # team_id NULL (운영자 정리 영역): 기존 전역 동작 유지 — 같은 NULL 키 안에서만
                # 의미가 있다고 가정. 실제로는 NULL 잔존 row가 거의 없으므로 노이즈도 적다.
                conn.execute(
                    "UPDATE events SET project = ? WHERE project = ?",
                    (new_name, old_name),
                )
                conn.execute(
                    "UPDATE checklists SET project = ? WHERE project = ?",
                    (new_name, old_name),
                )
        else:
            # orphan label rename: events/checklists에만 존재하는 프로젝트 이름의 일괄 변경.
            conn.execute(
                "UPDATE events SET project = ? WHERE project = ?",
                (new_name, old_name),
            )
            conn.execute(
                "UPDATE checklists SET project = ? WHERE project = ?",
                (new_name, old_name),
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


def project_name_exists(name: str, case_insensitive: bool = False) -> bool:
    with get_conn() as conn:
        if case_insensitive:
            proj_sql = "SELECT 1 FROM projects WHERE LOWER(name) = LOWER(?)"
            ev_sql   = "SELECT 1 FROM events WHERE LOWER(project) = LOWER(?) AND deleted_at IS NULL LIMIT 1"
            chk_sql  = "SELECT 1 FROM checklists WHERE LOWER(project) = LOWER(?) AND deleted_at IS NULL LIMIT 1"
        else:
            proj_sql = "SELECT 1 FROM projects WHERE name = ?"
            ev_sql   = "SELECT 1 FROM events WHERE project = ? AND deleted_at IS NULL LIMIT 1"
            chk_sql  = "SELECT 1 FROM checklists WHERE project = ? AND deleted_at IS NULL LIMIT 1"
        if conn.execute(proj_sql, (name,)).fetchone(): return True
        if conn.execute(ev_sql,   (name,)).fetchone(): return True
        return bool(conn.execute(chk_sql, (name,)).fetchone())


def check_conflicts(start_dt: str, end_dt: str, team_id: int = None, exclude_id: int = None) -> list[dict]:
    end_dt = end_dt or start_dt
    with get_conn() as conn:
        sql = """
            SELECT id, title, start_datetime, end_datetime, project
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


def get_visible_teams():
    """삭제 예정(deleted_at IS NOT NULL) 팀을 제외한 팀 목록 — 공개 화면용 (팀 기능 #11)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM teams WHERE deleted_at IS NULL ORDER BY name"
        ).fetchall()
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


def get_user_by_login(name: str, plaintext: str):
    """일반 로그인 (#7): name_norm 매칭 + password_hash 검증. admin 제외.

    동작:
      1. ``normalize_name(name)``로 name_norm 산출.
      2. role != 'admin' AND is_active = 1 AND name_norm = ? 로 SELECT.
      3. 매칭된 사용자가 있으면 ``passwords.verify_password(plaintext, password_hash)``.
      4. 매칭 실패 시 ``passwords.DUMMY_HASH``로 verify를 한 번 돌려 timing 균등화.

    반환: dict(user) | None.
    """
    norm = normalize_name(name)
    if not norm:
        # 빈 입력은 lookup miss로 취급 — 그래도 dummy verify로 시간 균등화.
        passwords.verify_password(plaintext or "", passwords.DUMMY_HASH)
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users "
            " WHERE name_norm = ? "
            "   AND role != 'admin' "
            "   AND is_active = 1",
            (norm,),
        ).fetchone()
    if not row:
        passwords.verify_password(plaintext or "", passwords.DUMMY_HASH)
        return None
    user = dict(row)
    stored = user.get("password_hash")
    if not stored or not passwords.verify_password(plaintext or "", stored):
        return None
    return user


def get_user_by_credentials(name: str, plaintext: str):
    """관리자 로그인 (#7): admin 전용 + name_norm 매칭 + hash 검증.

    외부 동작은 변경 없음 (admin이 hash로 변환된 후에도 정상 로그인).
    매칭 miss 시에도 DUMMY_HASH로 verify를 돌려 timing 균등화.
    """
    norm = normalize_name(name)
    if not norm:
        passwords.verify_password(plaintext or "", passwords.DUMMY_HASH)
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users "
            " WHERE name_norm = ? "
            "   AND role = 'admin' "
            "   AND is_active = 1",
            (norm,),
        ).fetchone()
    if not row:
        passwords.verify_password(plaintext or "", passwords.DUMMY_HASH)
        return None
    user = dict(row)
    stored = user.get("password_hash")
    if not stored or not passwords.verify_password(plaintext or "", stored):
        return None
    return user


def update_user(user_id: int, data: dict):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET team_id = :team_id, is_active = :is_active WHERE id = :id",
            {**data, "id": user_id}
        )


def reset_user_password(user_id: int, new_password: str):
    """비밀번호 리셋 (#7): hash 저장 + 평문 컬럼 비움 ('').

    `users.password` 컬럼이 NOT NULL DEFAULT '' 이라 NULL 대신 빈 문자열로 저장한다.
    Phase 7 본문과 동일한 deviation. Phase 5(컬럼 drop) 후 정리.

    호출처:
      - /api/me/change-password (자기 비밀번호 변경) — 라우트가 정책 검증 선행.
      - /api/admin/users/{id}/reset-password (admin 운영) — 정책 미적용 (admin 책임).
    """
    new_hash = passwords.hash_password(new_password)
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, password = '' WHERE id = ?",
            (new_hash, user_id),
        )


def verify_user_password(user_id: int, plaintext: str) -> bool:
    """본인 인증용 hash 검증 (#7). /api/me/change-password 등에서 사용.

    user_id 기준 단일 row의 password_hash와 비교. 사용자가 사라졌거나 hash가
    비어 있으면 False (DUMMY_HASH로 timing 균등화).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ? AND is_active = 1",
            (user_id,),
        ).fetchone()
    stored = None
    if row:
        stored = row["password_hash"] if isinstance(row, sqlite3.Row) else row[0]
    if not stored:
        passwords.verify_password(plaintext or "", passwords.DUMMY_HASH)
        return False
    return passwords.verify_password(plaintext or "", stored)


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


class IPWhitelistConflict(Exception):
    """같은 IP가 이미 다른 사용자에게 whitelist로 등록돼 있을 때.

    라우트 호출부가 409로 매핑한다. 메시지는 진단용이며, 사용자 노출 문구는
    app.py 쪽에서 별도로 정한다.
    """


def _whitelist_owner_id(conn, ip: str):
    """해당 IP의 현재 whitelist 소유 user_id (없으면 None). 같은 트랜잭션 안에서 호출."""
    row = conn.execute(
        "SELECT user_id FROM user_ips WHERE ip_address = ? AND type = 'whitelist' LIMIT 1",
        (ip,),
    ).fetchone()
    if row is None:
        return None
    return row["user_id"] if isinstance(row, sqlite3.Row) else row[0]


def find_whitelist_owner(ip: str):
    """해당 IP를 whitelist로 보유한 user_id. 없으면 None."""
    with get_conn() as conn:
        return _whitelist_owner_id(conn, ip)


def get_whitelist_status_for_ip(user_id: int, ip: str) -> dict:
    """설정 패널 초기 상태용.

    반환: {enabled, conflict, conflict_user, ip}
      - enabled: 현재 user_id가 이 IP를 whitelist로 보유 중
      - conflict: 다른 사용자가 이 IP를 whitelist로 보유 중
      - conflict_user: conflict일 때 그 사용자 이름 (아니면 None)
    """
    with get_conn() as conn:
        owner_id = _whitelist_owner_id(conn, ip)
        if owner_id is None:
            return {"enabled": False, "conflict": False, "conflict_user": None, "ip": ip}
        if owner_id == user_id:
            return {"enabled": True, "conflict": False, "conflict_user": None, "ip": ip}
        urow = conn.execute("SELECT name FROM users WHERE id = ?", (owner_id,)).fetchone()
        name = (urow["name"] if isinstance(urow, sqlite3.Row) else urow[0]) if urow else None
        return {"enabled": False, "conflict": True, "conflict_user": name, "ip": ip}


def _set_whitelist_ip_locked(conn, user_id: int, ip: str) -> None:
    """한 트랜잭션 안에서 user_id에게 ip를 whitelist로 등록.

    - 다른 사용자가 같은 IP를 whitelist로 보유 중이면 IPWhitelistConflict.
    - 같은 (user_id, ip) history row가 있으면 type='whitelist'로 승격.
    - 아무 row도 없으면 새 whitelist row INSERT.
    - 이미 같은 (user_id, ip) whitelist row면 노옵.

    INSERT/UPDATE는 부분 UNIQUE 인덱스(idx_user_ips_whitelist_unique)가 race를
    최종 방어한다. IntegrityError도 IPWhitelistConflict로 변환한다.
    """
    owner_id = _whitelist_owner_id(conn, ip)
    if owner_id is not None and owner_id != user_id:
        raise IPWhitelistConflict(f"ip {ip!r} already whitelisted to user {owner_id}")
    if owner_id == user_id:
        return  # 이미 본인 whitelist — 노옵
    try:
        # 같은 (user_id, ip) history row가 1개라도 있으면 그 중 하나(MIN id)만 승격.
        # (접속 이력 history는 중복 허용이라 여러 row가 있을 수 있다 — 부분 UNIQUE 인덱스가
        #  whitelist에만 걸리므로, 전체를 한 번에 승격하면 두 번째 row에서 IntegrityError가 난다.)
        row = conn.execute(
            "SELECT MIN(id) AS hid FROM user_ips "
            "WHERE user_id = ? AND ip_address = ? AND type = 'history'",
            (user_id, ip),
        ).fetchone()
        hid = (row["hid"] if isinstance(row, sqlite3.Row) else row[0]) if row else None
        if hid is not None:
            conn.execute("UPDATE user_ips SET type = 'whitelist' WHERE id = ?", (hid,))
        else:
            conn.execute(
                "INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, ?, 'whitelist')",
                (user_id, ip),
            )
    except sqlite3.IntegrityError as exc:
        raise IPWhitelistConflict(f"ip {ip!r} whitelist conflict: {exc!r}") from exc


def set_user_whitelist_ip(user_id: int, ip: str) -> None:
    """사용자 자체 등록 — 본인 IP를 whitelist로. 충돌 시 IPWhitelistConflict."""
    with get_conn() as conn:
        _set_whitelist_ip_locked(conn, user_id, ip)


def admin_set_whitelist_ip(target_user_id: int, ip: str) -> None:
    """admin 직접 등록 — 임의 사용자에게 임의 IP를 whitelist로.

    접속 이력 없는 IP도 새 row로 추가한다. 충돌 시 IPWhitelistConflict.
    """
    with get_conn() as conn:
        _set_whitelist_ip_locked(conn, target_user_id, ip)


def remove_user_whitelist_ip(user_id: int, ip: str) -> None:
    """본인 whitelist 해제 — 해당 (user_id, ip)의 whitelist row를 type='history'로 강등.

    row 삭제하지 않음(접속 이력 보존, 자동 로그인 효력만 제거 — 사양서 §6).
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE user_ips SET type = 'history' "
            "WHERE user_id = ? AND ip_address = ? AND type = 'whitelist'",
            (user_id, ip),
        )


def delete_ip_row(ip_id: int) -> None:
    """admin — user_ips row 삭제."""
    with get_conn() as conn:
        conn.execute("DELETE FROM user_ips WHERE id = ?", (ip_id,))


def toggle_ip_whitelist(ip_id: int, enable: bool):
    """admin — 기존 row의 whitelist/history 토글.

    enable=True로 켤 때 그 row의 IP가 이미 다른 사용자에게 whitelist면
    IPWhitelistConflict (라우트가 409 매핑). enable=False는 항상 허용.
    """
    new_type = "whitelist" if enable else "history"
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id, ip_address FROM user_ips WHERE id = ?", (ip_id,)
        ).fetchone()
        if row is None:
            return
        uid = row["user_id"] if isinstance(row, sqlite3.Row) else row[0]
        ip = row["ip_address"] if isinstance(row, sqlite3.Row) else row[1]
        if enable:
            owner_id = _whitelist_owner_id(conn, ip)
            if owner_id is not None and owner_id != uid:
                raise IPWhitelistConflict(f"ip {ip!r} already whitelisted to user {owner_id}")
        try:
            conn.execute("UPDATE user_ips SET type = ? WHERE id = ?", (new_type, ip_id))
        except sqlite3.IntegrityError as exc:
            raise IPWhitelistConflict(f"ip {ip!r} whitelist conflict: {exc!r}") from exc


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


# ── Account Registration / Team Applications (팀 기능 그룹 A #8) ──────

def create_user_account(name: str, password: str) -> dict | None:
    """계정 가입 (#8): 이름·비밀번호만으로 즉시 활성 사용자 row 생성.

    - role='member', team_id=NULL (팀 신청은 별도 흐름).
    - name_norm: ``normalize_name`` 으로 정규화하여 저장 (users.name_norm 전역 UNIQUE — #7).
    - password 컬럼은 NOT NULL DEFAULT '' 이라 빈 문자열로 두고 password_hash 에 hash 저장
      (#7 의 deviation 과 동일; Phase 5 컬럼 drop 후 정리).
    - name_norm 충돌 시(사전 SELECT 또는 IntegrityError) None 반환 — 라우트가 409 매핑.

    반환: dict(user) 신규 row | None(중복).
    """
    norm = normalize_name(name)
    pw_hash = passwords.hash_password(password)
    with get_conn() as conn:
        # 사전 중복 검사 (활성/비활성 무관 — name_norm 전역 UNIQUE 이므로).
        if conn.execute("SELECT 1 FROM users WHERE name_norm = ?", (norm,)).fetchone():
            return None
        try:
            cur = conn.execute(
                "INSERT INTO users (name, name_norm, password, password_hash, role, team_id, is_active) "
                "VALUES (?, ?, '', ?, 'member', NULL, 1)",
                (name, norm, pw_hash),
            )
        except sqlite3.IntegrityError:
            # name_norm UNIQUE race — 사전 SELECT 와 INSERT 사이에 동일 이름 가입.
            return None
        uid = cur.lastrowid
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return dict(row) if row else None


def apply_to_team(user_id: int, team_id: int) -> tuple[str, str | None]:
    """팀 신청 (#8). ``user_teams`` 에 pending row 생성/갱신.

    규칙:
      - 사용자에게 (임의 팀 대상) pending row 가 1개라도 있으면 추가 신청 차단.
        단, 차단 대상이 **바로 이 팀** 인 경우는 "이미 이 팀에 신청 중" 으로 분기.
      - 이미 approved 멤버면 차단.
      - rejected row 가 있으면 같은 row 를 status='pending' 으로 갱신 (row 추가 X — joined_at 보존).
      - row 가 없으면 신규 INSERT.

    반환: (result, detail)
      result ∈ {"created", "updated", "blocked"}
      detail: blocked 일 때 "pending_here" | "pending_other" | "already_member", 그 외 None.
    """
    with get_conn() as conn:
        # 1. 사용자의 다른 pending 존재 여부 (임의 팀).
        other_pending = conn.execute(
            "SELECT team_id FROM user_teams WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchall()
        existing = conn.execute(
            "SELECT id, status FROM user_teams WHERE user_id = ? AND team_id = ?",
            (user_id, team_id),
        ).fetchone()
        # 2. 이 팀 row 분기.
        if existing is not None:
            status = existing["status"] if isinstance(existing, sqlite3.Row) else existing[1]
            row_id = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
            if status == "approved":
                return ("blocked", "already_member")
            if status == "pending":
                return ("blocked", "pending_here")
            # rejected (또는 그 외) → pending 으로 갱신. 단 다른 팀에 pending 이 있으면 차단.
            if other_pending:
                return ("blocked", "pending_other")
            conn.execute(
                "UPDATE user_teams SET status = 'pending', role = 'member' WHERE id = ?",
                (row_id,),
            )
            return ("updated", None)
        # 3. 이 팀 row 없음 → 다른 팀 pending 있으면 차단, 없으면 신규.
        if other_pending:
            return ("blocked", "pending_other")
        conn.execute(
            "INSERT INTO user_teams (user_id, team_id, role, status) VALUES (?, ?, 'member', 'pending')",
            (user_id, team_id),
        )
        return ("created", None)


def list_team_applications(team_id: int) -> list[dict]:
    """해당 팀의 pending 신청 목록 (admin·팀 관리자 조회용). user name 동반."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ut.id, ut.user_id, ut.team_id, ut.status, ut.joined_at, "
            "       u.name AS user_name, u.name_norm AS user_name_norm "
            "  FROM user_teams ut JOIN users u ON ut.user_id = u.id "
            " WHERE ut.team_id = ? AND ut.status = 'pending' "
            " ORDER BY ut.id ASC",
            (team_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_my_team_statuses(user_id: int) -> dict:
    """팀 기능 그룹 B #12: 본인이 신청한 비-삭제 팀들의 user_teams.status.

    미배정 사용자 화면(`/`)에서 팀별 "팀 신청" / "가입 대기 중" 버튼 분기에 사용.
    approved 는 미배정 정의상 존재하지 않으므로 pending/rejected 만 포함.
    반환: {team_id: 'pending'|'rejected'}.
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ut.team_id, ut.status FROM user_teams ut "
            "JOIN teams t ON t.id = ut.team_id "
            "WHERE ut.user_id = ? AND t.deleted_at IS NULL AND ut.status IN ('pending','rejected')",
            (user_id,),
        ).fetchall()
    return {(r["team_id"] if isinstance(r, sqlite3.Row) else r[0]):
            (r["status"] if isinstance(r, sqlite3.Row) else r[1]) for r in rows}


def decide_team_application(user_id: int, team_id: int, decision: str) -> bool:
    """팀 신청 수락/거절 (#8). 대상 row 가 status='pending' 일 때만 처리.

    decision: 'approved' → status='approved' + joined_at=CURRENT_TIMESTAMP.
              'rejected'  → status='rejected'.
    반환: 처리 성공 True / 대상 없거나 pending 아니면 False.
    """
    if decision not in ("approved", "rejected"):
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM user_teams WHERE user_id = ? AND team_id = ? AND status = 'pending'",
            (user_id, team_id),
        ).fetchone()
        if not row:
            return False
        row_id = row["id"] if isinstance(row, sqlite3.Row) else row[0]
        if decision == "approved":
            conn.execute(
                "UPDATE user_teams SET status = 'approved', joined_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row_id,),
            )
        else:
            conn.execute(
                "UPDATE user_teams SET status = 'rejected' WHERE id = ?",
                (row_id,),
            )
    return True


def get_team_active(team_id: int) -> dict | None:
    """삭제되지 않은 팀 조회. team_id 유효성 검사용."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teams WHERE id = ? AND deleted_at IS NULL", (team_id,)
        ).fetchone()
    return dict(row) if row else None


def get_team_by_name_exact(name: str) -> dict | None:
    """팀 기능 그룹 B #13: 대소문자 정확 일치로 팀 조회 (`/팀이름` 공개 포털 라우트용).

    teams.name 은 SQLite 기본 BINARY collation 이라 `name = ?` 는 대소문자를 구분한다
    (`ABC` 로 생성한 팀은 `/ABC` 만 매치, `/abc` 는 None → 라우트가 404). 삭제 예정 팀
    (deleted_at IS NOT NULL)도 그대로 반환한다 — 삭제 예정 안내 페이지를 보여줘야 하므로
    deleted_at 판정은 라우트가 한다.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM teams WHERE name = ? LIMIT 1", (name,)
        ).fetchone()
    return dict(row) if row else None


# ── 작업 팀(work_team_id) 결정 헬퍼 — 팀 기능 그룹 B #15 ──────────────

def first_active_team_id() -> int | None:
    """삭제되지 않은 팀 중 가장 작은 id. admin 의 작업 팀 fallback (쿠키 없음/무효).

    계획서 §7: admin 의 기본 작업 팀은 "마지막 선택 팀(별도 저장 시) 또는 첫 번째 팀".
    '마지막 선택 팀'은 work_team_id 쿠키가 그 역할을 하므로, 쿠키가 없거나 무효일 때
    첫 번째 비삭제 팀(id 최소)으로 fallback 한다. 비삭제 팀이 하나도 없으면 None.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM teams WHERE deleted_at IS NULL ORDER BY id ASC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return row["id"] if isinstance(row, sqlite3.Row) else row[0]


def primary_team_id_for_user(user_id: int) -> int | None:
    """일반 사용자의 대표 작업 팀 — 쿠키가 없을 때 fallback.

    계획서 §7 / todo §#15: approved 소속이고 teams.deleted_at IS NULL 인 팀 중
    joined_at 이 가장 이른 팀 (동일 시 team_id 최소). approved 소속이 없으면 None.
    admin 은 user_teams row 가 없으므로 None → 호출부가 first_active_team_id 로 분기.
    """
    if user_id is None:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT ut.team_id FROM user_teams ut "
            "JOIN teams t ON t.id = ut.team_id "
            "WHERE ut.user_id = ? AND ut.status = 'approved' AND t.deleted_at IS NULL "
            "ORDER BY ut.joined_at ASC, ut.team_id ASC LIMIT 1",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return row["team_id"] if isinstance(row, sqlite3.Row) else row[0]


def user_work_teams(user_id: int) -> list[dict]:
    """프로필 "팀 변경" 드롭다운 / POST /api/me/work-team 검증용.

    사용자의 approved + 비삭제 소속 팀 목록 [{id, name}], joined_at 순(동일 시 team_id 순).
    admin 은 호출하지 않는다 (admin 은 전체 비삭제 팀 = get_visible_teams() 사용).
    """
    if user_id is None:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT t.id AS id, t.name AS name FROM user_teams ut "
            "JOIN teams t ON t.id = ut.team_id "
            "WHERE ut.user_id = ? AND ut.status = 'approved' AND t.deleted_at IS NULL "
            "ORDER BY ut.joined_at ASC, ut.team_id ASC",
            (user_id,),
        ).fetchall()
    return [{"id": r["id"], "name": r["name"]} for r in rows]


# 팀 기능 그룹 B #13: 공개 포털 메뉴 노출 기본값.
# team_menu_settings 에 행이 없을 때의 fallback (계획서 섹션 9 기본값 표).
# #19 가 team_menu_settings 시드를 추가하면 그 값이 우선한다 — 여기는 임시 기본값.
_PORTAL_MENU_DEFAULTS = {
    "kanban": True,
    "gantt": True,
    "doc": True,
    "check": True,
    "calendar": False,
}


def get_team_menu_visibility(team_id: int) -> dict:
    """팀 기능 그룹 B #13: 팀별 공개 포털 메뉴 노출 dict — `{menu_key: bool}`.

    team_menu_settings 행이 있으면 그 값, 없는 키는 _PORTAL_MENU_DEFAULTS 로 채운다.
    의미는 "공개 포털 UI 진입(탭/링크) 차단"일 뿐 데이터 차단이 아니다 (계획서 섹션 9).
    """
    vis = dict(_PORTAL_MENU_DEFAULTS)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT menu_key, enabled FROM team_menu_settings WHERE team_id = ?", (team_id,)
        ).fetchall()
    for r in rows:
        key = r["menu_key"] if isinstance(r, sqlite3.Row) else r[0]
        enabled = r["enabled"] if isinstance(r, sqlite3.Row) else r[1]
        if key in vis:
            vis[key] = bool(enabled)
    return vis


def get_public_portal_data(team_id: int) -> dict:
    """팀 기능 그룹 B #13: `/팀이름` 비로그인 공개 포털이 노출할 팀 데이터 집계.

    항상 단일 팀 기준 + viewer=None (공개 portal context — URL 은 권한 경계가 아니다,
    계획서 섹션 7). 모든 채널에서:
      - `is_public=1` 항목만 (is_public IS NULL 은 프로젝트 공개 연동, is_public=0 은 비공개)
      - 외부 비공개 프로젝트(is_private=1)·히든 프로젝트(is_hidden=1) 하위 항목은 is_public 값과
        무관하게 완전 차단 (기존 viewer=None 경로의 SQL 필터가 처리)
    반환 dict: kanban / gantt / docs / checks / calendar / menu.
    """
    # 칸반·간트·캘린더 — events 풀에서. viewer=None & team_id 명시 경로는 이미 공개 필터 내장.
    kanban = get_kanban_events(team_id, viewer=None)
    gantt = get_project_timeline(team_id, viewer=None)
    # 체크 — 전 팀 공개 항목을 받아 team_id 로 필터 (get_checklists 는 team_id 인자 없음).
    checks = [c for c in get_checklists(viewer=None) if c.get("team_id") == team_id]
    # 캘린더 탭 데이터: 칸반과 같은 events 풀 — 별도 시각화일 뿐(계획서 섹션 9).
    #   기본 메뉴 OFF 라 프론트가 탭을 안 그릴 수 있지만 데이터는 채워둔다.
    calendar = list(kanban)
    # 문서 — 팀 공개 문서만 (개인 문서 is_team_doc=0 은 팀 자료가 아니므로 포털 비노출).
    with get_conn() as conn:
        doc_rows = conn.execute(
            """SELECT m.id, m.title, m.created_by, m.updated_at, m.created_at,
                      u.name as author_name
                 FROM meetings m
                 LEFT JOIN users u ON m.created_by = u.id
                WHERE m.deleted_at IS NULL AND m.team_id = ?
                  AND m.is_public = 1 AND m.is_team_doc = 1
                ORDER BY m.updated_at DESC""",
            (team_id,),
        ).fetchall()
    docs = [dict(r) for r in doc_rows]
    return {
        "kanban": kanban,
        "gantt": gantt,
        "docs": docs,
        "checks": checks,
        "calendar": calendar,
        "menu": get_team_menu_visibility(team_id),
    }


# ── Meetings ────────────────────────────────────────────

def _meeting_team_clause(team_ids):
    """팀 기능 그룹 A #10: 작업 팀 집합에 대한 IN 절 + 파라미터 생성.

    team_ids 비어 있으면 팀 조건은 항상 거짓 (작성자 본인·공개만 통과).
    반환: (sql_fragment, params_list) — sql_fragment 는 'm.team_id IN (?,?)' 또는 '0'.
    """
    ids = [t for t in (team_ids or ()) if t is not None]
    if not ids:
        return "0", []
    placeholders = ",".join("?" for _ in ids)
    return f"m.team_id IN ({placeholders})", list(ids)


def get_all_meetings(viewer=None, work_team_ids=None):
    """viewer: None=비로그인, dict=로그인 사용자. 가시성 규칙을 SQL에서 처리.

    work_team_ids — 팀 기능 그룹 A #10. None 이면 viewer 소속 팀 전체로 fallback
    (작업 팀 쿠키 도입 전 — #15 이후 호출부에서 명시 작업 팀 1개 set 을 넘긴다). admin 은 무관.
    """
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
            team_clause, team_params = _meeting_team_clause(
                work_team_ids if work_team_ids is not None else _viewer_team_ids(viewer)
            )
            rows = conn.execute(
                base + f"""
                  AND (
                    (m.created_by = ? AND (m.is_team_doc = 0 OR m.team_id IS NULL))
                    OR m.is_public = 1
                    OR (m.is_team_doc = 1 AND m.team_id IS NOT NULL AND {team_clause})
                    OR (m.is_team_doc = 0 AND m.team_share = 1 AND {team_clause})
                  )
                  ORDER BY m.updated_at DESC""",
                (uid, *team_params, *team_params)
            ).fetchall()
    return [dict(r) for r in rows]


def get_my_personal_meetings(user_id: int) -> list[dict]:
    """팀 기능 그룹 B #12: 본인 작성 개인 문서(is_team_doc=0) 전체 — "내 자료" 영역용.

    `team_share` 값으로 거르지 않는다 (본인 화면이므로 team_share=1 이라도 본인에겐 모두 노출 —
    계획서 섹션 7·8 "자기 자료 통합 노출 목적"). team_id IS NULL 조건도 넣지 않는다
    (막 추방돼 team_id 가 남은 본인 작성 개인 문서도 "내 자료"에 포함).
    일정·체크·팀 문서(is_team_doc=1)는 제외 — 전부 팀 컨텍스트가 필요.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT m.*, t.name as team_name,
                      (SELECT COUNT(*) FROM events e WHERE e.meeting_id = m.id AND e.deleted_at IS NULL) as event_count
                 FROM meetings m
                 LEFT JOIN teams t ON m.team_id = t.id
                WHERE m.deleted_at IS NULL AND m.created_by = ? AND m.is_team_doc = 0
                ORDER BY m.updated_at DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _viewer_team_ids(viewer):
    """database.py 내부 fallback — auth.user_team_ids 와 동일 의미(approved + 비삭제 팀).

    auth 모듈을 import 하면 순환 import 위험이 있어 직접 쿼리한다.
    """
    if not viewer:
        return set()
    if viewer.get("role") == "admin":
        return set()
    uid = viewer.get("id")
    if uid is None:
        return set()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT ut.team_id FROM user_teams ut JOIN teams t ON t.id = ut.team_id "
                "WHERE ut.user_id = ? AND ut.status = 'approved' AND t.deleted_at IS NULL",
                (uid,),
            ).fetchall()
        return {r[0] for r in rows if r[0] is not None}
    except Exception:
        legacy = viewer.get("team_id")
        return {legacy} if legacy else set()


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
    if not row:
        return None
    d = dict(row)
    d["attachments"] = json.loads(d.get("attachments") or "[]")
    return d


def update_meeting_visibility(meeting_id: int, is_team_doc: int, is_public: int, team_share: int) -> None:
    _team_share = 0 if is_team_doc else team_share
    with get_conn() as conn:
        conn.execute(
            "UPDATE meetings SET is_team_doc = ?, is_public = ?, team_share = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (is_team_doc, is_public, _team_share, meeting_id)
        )


def create_meeting(title: str, content: str, team_id, created_by: int,
                   meeting_date: str = None, is_team_doc: int = 1,
                   is_public: int = 0, team_share: int = 0, attachments=None) -> int:
    _team_share = 0 if is_team_doc else team_share
    _attachments = json.dumps(attachments if isinstance(attachments, list) else [])
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO meetings (title, content, team_id, created_by, meeting_date, is_team_doc, is_public, team_share, attachments) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, content, team_id, created_by, meeting_date, is_team_doc, is_public, _team_share, _attachments)
        )
    return cur.lastrowid


def update_meeting(meeting_id: int, title: str, content: str, edited_by: int,
                   meeting_date: str = None, is_team_doc: int = 1,
                   is_public: int = 0, team_share: int = 0, attachments=None):
    _team_share = 0 if is_team_doc else team_share
    _attachments = json.dumps(attachments if isinstance(attachments, list) else [])
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
            "is_public = ?, team_share = ?, attachments = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (title, content, meeting_date, is_team_doc, is_public, _team_share, _attachments, meeting_id)
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


def is_image_url_referenced(url: str) -> bool:
    """이미지/첨부파일 URL이 어느 한 곳에서라도 참조되는지 확인 (휴지통·히스토리 포함)"""
    like = f"%{url}%"
    with get_conn() as conn:
        for table, col in (
            ("meetings",         "content"),
            ("meetings",         "attachments"),
            ("meeting_histories","content"),
            ("checklists",       "content"),
            ("checklists",       "attachments"),
            ("team_notices",     "content"),
        ):
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE {col} LIKE ? LIMIT 1", (like,)
            ).fetchone():
                return True
    return False


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

def create_checklist(project: str, title: str, content: str, created_by: str, is_public: int = 0, team_id: int = None, attachments=None) -> int:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    _attachments = json.dumps(attachments if isinstance(attachments, list) else [])
    with get_conn() as conn:
        # #6: project_id 동반.
        project_id = _resolve_project_id_for_write(conn, team_id, project)
        cur = conn.execute(
            "INSERT INTO checklists (project, project_id, title, content, created_by, created_at, updated_at, is_public, team_id, attachments) "
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (project, project_id, title, content, created_by, now, now, is_public, team_id, _attachments)
        )
    return cur.lastrowid


def get_checklists(project: str = None, viewer=None, active_only: bool | None = None, include_done_projects: bool = False, work_team_ids=None) -> list:
    """work_team_ids — 팀 기능 그룹 A #10.
      None  : 팀 필터 미적용 (admin 또는 비로그인 — 비로그인은 아래 public_filter 가 처리).
      set() : 로그인 사용자의 작업 팀 집합. team_id ∈ 집합, 또는 team_id IS NULL & 작성자 본인만 통과.
              (created_by 는 checklists 에서 이름 TEXT)
    """
    if include_done_projects:
        inactive_filter = ""
    else:
        inactive_filter = """
        AND (project IS NULL OR project = ''
             OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))
    """
    # 3상태: is_public=1 항상 공개, is_public=NULL 프로젝트 연동, is_public=0 항상 비공개
    # is_public=1이어도 프로젝트가 외부 비공개(is_private=1)면 비로그인 사용자에게 미노출
    public_filter = """
        AND (
          is_public = 1
          OR (
            is_public IS NULL
            AND project IS NOT NULL AND project != ''
            AND project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
          )
        )
        AND (
          project IS NULL OR project = ''
          OR project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
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
    select_cols = f"SELECT id, project, title, created_by, team_id, created_at, updated_at, is_public, is_locked, COALESCE(is_active,1) as is_active, {is_done_project_col}"
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"{select_cols} FROM checklists WHERE deleted_at IS NULL {inactive_filter}{public_filter}{private_proj_filter}{active_filter} ORDER BY updated_at DESC"
            ).fetchall()
        elif project == "":
            # 미지정 (project가 NULL 또는 빈 문자열인 항목)
            rows = conn.execute(
                f"{select_cols} FROM checklists WHERE (project IS NULL OR project = '') AND deleted_at IS NULL {public_filter}{private_proj_filter}{active_filter} ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                f"{select_cols} FROM checklists WHERE project = ? AND deleted_at IS NULL {inactive_filter}{public_filter}{private_proj_filter}{active_filter} ORDER BY updated_at DESC",
                (project,)
            ).fetchall()
    result = [dict(r) for r in rows]
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    # 팀 기능 그룹 A #10: 작업 팀 필터 (비로그인은 public_filter 가 이미 처리, admin/None 은 무필터)
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    return result


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
    if not row:
        return None
    d = dict(row)
    d["attachments"] = json.loads(d.get("attachments") or "[]")
    return d


def update_checklist(checklist_id: int, title: str, project: str, attachments=None):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        # #6: project 변경 시 project_id 동기화. team_id는 기존 row에서 가져온다.
        row = conn.execute(
            "SELECT team_id FROM checklists WHERE id = ?", (checklist_id,)
        ).fetchone()
        row_team_id = (row["team_id"] if row else None) if isinstance(row, sqlite3.Row) else (row[0] if row else None)
        new_project_id = _resolve_project_id_for_write(conn, row_team_id, project)
        if attachments is not None:
            _attachments = json.dumps(attachments if isinstance(attachments, list) else [])
            conn.execute(
                "UPDATE checklists SET title = ?, project = ?, project_id = ?, "
                " attachments = ?, updated_at = ? WHERE id = ?",
                (title, project, new_project_id, _attachments, now, checklist_id)
            )
        else:
            conn.execute(
                "UPDATE checklists SET title = ?, project = ?, project_id = ?, "
                " updated_at = ? WHERE id = ?",
                (title, project, new_project_id, now, checklist_id)
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

def get_trash_items(team_id: int = None, viewer=None) -> dict:
    """휴지통 아이템 반환 — groups(프로젝트별 묶음) + unassigned(미지정)
    viewer: 현재 로그인 사용자 dict. 히든 프로젝트는 admin 또는 owner만 조회 가능.
    """
    team_filter = "AND team_id = ?" if team_id else ""
    team_args = (team_id,) if team_id else ()
    is_admin = viewer and viewer.get("role") == "admin"
    viewer_id = viewer.get("id") if viewer else None

    with get_conn() as conn:
        # 삭제된 프로젝트 목록
        pj_rows = conn.execute(
            f"SELECT id, name, color, deleted_at, deleted_by, team_id, is_hidden, owner_id "
            f"FROM projects WHERE deleted_at IS NOT NULL {team_filter} ORDER BY deleted_at DESC",
            team_args
        ).fetchall()
        # 히든 프로젝트 필터: admin 또는 owner만 볼 수 있음
        pj_rows = [
            pj for pj in pj_rows
            if not pj["is_hidden"] or is_admin or (viewer_id is not None and pj["owner_id"] == viewer_id)
        ]

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
        ev_unassigned = [
            r for r in ev_unassigned
            if _trash_item_visible_to_viewer(conn, r, viewer)
        ]
        cl_unassigned = conn.execute(
            f"""SELECT id, title, content, project, deleted_at, deleted_by, team_id, trash_project_id
                FROM checklists
                WHERE deleted_at IS NOT NULL AND trash_project_id IS NULL {team_filter}
                ORDER BY deleted_at DESC""",
            team_args
        ).fetchall()
        cl_unassigned = [
            r for r in cl_unassigned
            if _trash_item_visible_to_viewer(conn, r, viewer)
        ]
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


def get_trash_hidden_project(project_id: int):
    """휴지통에 있는 프로젝트의 is_hidden, owner_id 반환 (복원 권한 검사용)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, is_hidden, owner_id, deleted_at FROM projects WHERE id = ? AND deleted_at IS NOT NULL",
            (project_id,)
        ).fetchone()
    return dict(row) if row else None


def get_trash_item_hidden_project(item_type: str, item_id: int):
    """휴지통 항목이 히든 프로젝트에 연결되어 있으면 해당 프로젝트 정보를 반환."""
    table_map = {
        "event": ("events", "project"),
        "checklist": ("checklists", "project"),
        "meeting": ("meetings", None),
        "project": ("projects", None),
    }
    if item_type not in table_map:
        return None
    if item_type == "project":
        project = get_trash_hidden_project(item_id)
        return project if project and project.get("is_hidden") else None

    table, project_col = table_map[item_type]
    with get_conn() as conn:
        row = conn.execute(
            f"""SELECT p.id, p.is_hidden, p.owner_id, p.deleted_at
                FROM {table} t
                JOIN projects p ON p.id = t.trash_project_id
                WHERE t.id = ?
                  AND t.deleted_at IS NOT NULL
                  AND t.trash_project_id IS NOT NULL
                  AND p.is_hidden = 1""",
            (item_id,)
        ).fetchone()
        if not row and project_col:
            row = conn.execute(
                f"""SELECT p.id, p.is_hidden, p.owner_id, p.deleted_at
                    FROM {table} t
                    JOIN projects p ON p.name = t.{project_col}
                    WHERE t.id = ?
                      AND t.deleted_at IS NOT NULL
                      AND t.trash_project_id IS NULL
                      AND t.{project_col} IS NOT NULL
                      AND t.{project_col} != ''
                      AND p.is_hidden = 1""",
                (item_id,)
            ).fetchone()
    return dict(row) if row else None


def get_user_owned_hidden_projects(user_id: int) -> list:
    """해당 user가 owner인 활성 히든 프로젝트 목록 반환 (C-3 팀원 제외 경고용)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name FROM projects WHERE owner_id = ? AND is_hidden = 1 AND deleted_at IS NULL",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def transfer_hidden_projects_on_removal(user_id: int, hidden_projects: list):
    """팀원 강제 제외 시 히든 프로젝트 owner 자동 이양.
    hidden_projects: get_user_owned_hidden_projects() 반환값.
    이양 대상: 해당 프로젝트 멤버 중 user 제외 added_at 오름차순 첫 번째.
    후보 조회는 `user_teams.status='approved'` + `projects.team_id` 기준 (#15-1).
    이양 불가 시 owner_id = NULL (admin만 관리 가능 상태).
    """
    with get_conn() as conn:
        for proj in hidden_projects:
            proj_id = proj["id"]
            next_owner = conn.execute(
                """SELECT pm.user_id
                   FROM project_members pm
                   JOIN users u ON u.id = pm.user_id
                   JOIN projects p ON p.id = pm.project_id
                   WHERE pm.project_id = ?
                     AND pm.user_id != ?
                     AND u.is_active = 1
                     AND p.team_id IS NOT NULL
                     AND u.role != 'admin'
                     AND EXISTS (
                           SELECT 1 FROM user_teams ut
                            WHERE ut.user_id = u.id
                              AND ut.team_id = p.team_id
                              AND ut.status = 'approved'
                         )
                   ORDER BY pm.added_at ASC
                   LIMIT 1""",
                (proj_id, user_id)
            ).fetchone()
            conn.execute(
                "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
                (proj_id, user_id)
            )
            if next_owner:
                conn.execute(
                    "UPDATE projects SET owner_id = ? WHERE id = ?",
                    (next_owner["user_id"], proj_id)
                )
            else:
                conn.execute(
                    "UPDATE projects SET owner_id = NULL WHERE id = ?",
                    (proj_id,)
                )


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


def get_event_for_mcp(event_id: int, viewer=None, work_team_ids=None) -> dict | None:
    """MCP용 이벤트 조회: ① visibility check → ② get_event() 호출.
    종료 프로젝트(is_active=0) 소속 이벤트, 삭제된 이벤트는 None 반환.
    work_team_ids — 팀 기능 그룹 A #10: 작업 팀 필터 (None 이면 viewer 소속 팀 전체)."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT project, team_id, created_by FROM events
               WHERE id = ?
                 AND deleted_at IS NULL
                 AND (project IS NULL OR project = ''
                      OR project NOT IN (SELECT name FROM projects WHERE is_active = 0 AND deleted_at IS NULL))""",
            (event_id,)
        ).fetchone()
    if not row:
        return None
    proj_name = row["project"] if isinstance(row, sqlite3.Row) else row[0]
    ev_team = row["team_id"] if isinstance(row, sqlite3.Row) else row[1]
    ev_author = row["created_by"] if isinstance(row, sqlite3.Row) else row[2]
    if proj_name:
        blocked = get_blocked_hidden_project_names(viewer)
        if proj_name in blocked:
            return None
    # 작업 팀 경계 (admin·비로그인 제외)
    if viewer is not None and viewer.get("role") != "admin":
        scope = set(work_team_ids) if work_team_ids is not None else _viewer_team_ids(viewer)
        if ev_team is not None:
            if ev_team not in scope:
                return None
        else:
            if ev_author not in _author_token_set(viewer):
                return None
    return get_event(event_id)


def get_projects_for_mcp(conn, include_inactive: bool = False, viewer=None, work_team_ids=None) -> list[dict]:
    """MCP용 프로젝트 목록 조회.
    deleted_at IS NULL인 프로젝트만 반환.
    include_inactive=False(기본값)이면 is_active=1 조건 추가.
    work_team_ids — 팀 기능 그룹 A #10: 작업 팀 필터 (None 이면 viewer 소속 팀 전체).
      team_id ∈ 집합, 또는 team_id IS NULL & owner_id == viewer.id 만 통과.
    반환 필드: name, color, is_active, start_date, end_date
    """
    query = "SELECT name, color, is_active, start_date, end_date, team_id, owner_id FROM projects WHERE deleted_at IS NULL"
    if not include_inactive:
        query += " AND is_active = 1"
    query += " ORDER BY name"
    rows = conn.execute(query).fetchall()
    result = [dict(r) for r in rows]
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if r["name"] not in blocked]
    if viewer is not None and viewer.get("role") != "admin":
        scope = set(work_team_ids) if work_team_ids is not None else _viewer_team_ids(viewer)
        vid = viewer.get("id")
        result = [
            r for r in result
            if (r.get("team_id") is not None and r.get("team_id") in scope)
            or (r.get("team_id") is None and r.get("owner_id") == vid)
        ]
    for r in result:
        r.pop("team_id", None)
        r.pop("owner_id", None)
    return result


def get_events_filtered(
    conn,
    project: str | None = None,
    start_after: str | None = None,
    end_before: str | None = None,
    viewer=None,
    work_team_ids=None,
) -> list[dict]:
    """MCP용 필터링된 이벤트 조회.
    기존 get_all_events()의 조건(deleted_at IS NULL, 비활성 프로젝트 제외)을 유지하면서
    추가 필터를 적용한다.
    - project: events.project = ? 조건
    - start_after: events.start_datetime >= ? 조건 (ISO 8601 문자열 비교)
    - end_before: events.end_datetime <= ? 조건
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체)
    파라미터가 None이면 해당 조건 생략.
    """
    query = """SELECT id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type, team_id, created_by
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
    result = [dict(r) for r in rows]
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    for r in result:
        r.pop("team_id", None)
        r.pop("created_by", None)
    return result


def get_all_meetings_summary(viewer=None, work_team_ids=None):
    """MCP list_documents용 경량 조회 — content 제외.
    가시성 로직은 get_all_meetings()와 동일 (work_team_ids 포함).
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
            team_clause, team_params = _meeting_team_clause(
                work_team_ids if work_team_ids is not None else _viewer_team_ids(viewer)
            )
            rows = conn.execute(
                base + f"""
                  AND (
                    (m.created_by = ? AND (m.is_team_doc = 0 OR m.team_id IS NULL))
                    OR m.is_public = 1
                    OR (m.is_team_doc = 1 AND m.team_id IS NOT NULL AND {team_clause})
                    OR (m.is_team_doc = 0 AND m.team_share = 1 AND {team_clause})
                  )
                  ORDER BY m.updated_at DESC""",
                (uid, *team_params, *team_params)
            ).fetchall()
    return [dict(r) for r in rows]


def get_checklists_summary(project: str = None, viewer=None, work_team_ids=None) -> list:
    """MCP list_checklists용 경량 조회 — content 대신 item_count, done_count 계산.
    가시성 로직은 get_checklists()와 동일 (active_only/include_done_projects 미사용, work_team_ids 포함).
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
            AND project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
          )
        )
        AND (
          project IS NULL OR project = ''
          OR project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
        )
    """ if viewer is None else ""
    cols = "SELECT id, project, title, content, team_id, created_by, updated_at"
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"{cols} FROM checklists WHERE deleted_at IS NULL {inactive_filter}{public_filter} ORDER BY updated_at DESC"
            ).fetchall()
        elif project == "":
            rows = conn.execute(
                f"{cols} FROM checklists WHERE (project IS NULL OR project = '') AND deleted_at IS NULL {public_filter} ORDER BY updated_at DESC"
            ).fetchall()
        else:
            rows = conn.execute(
                f"{cols} FROM checklists WHERE project = ? AND deleted_at IS NULL {inactive_filter}{public_filter} ORDER BY updated_at DESC",
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
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    # 팀 기능 그룹 A #10: 작업 팀 필터 (admin/None viewer 는 무필터)
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    # MCP 응답에 team_id/created_by 노출 불필요 — 제거
    for r in result:
        r.pop("team_id", None)
        r.pop("created_by", None)
    return result


def search_all(query: str, type: str = None, viewer=None,
               start_after: str | None = None, end_before: str | None = None,
               work_team_ids=None) -> list[dict]:
    """이벤트·문서·체크리스트 통합 키워드 검색 (MCP search 도구용).
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - type: "event"|"document"|"checklist"|None(전체)
    - viewer: None=비로그인, dict=로그인 사용자
    - start_after/end_before: 이벤트 날짜 범위 (겹치는 이벤트 포함). 문서·체크리스트는 미적용.
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체).
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
            sql = """SELECT id, title, project, start_datetime, end_datetime, assignee, kanban_status, team_id, created_by
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
            blocked_evt = get_blocked_hidden_project_names(viewer)
            evt_rows = []
            for r in rows:
                d = dict(r)
                if blocked_evt and d.get("project") and d["project"] in blocked_evt:
                    continue
                evt_rows.append(d)
            evt_rows = _filter_rows_by_work_team(evt_rows, viewer, work_team_ids, "created_by")
            for d in evt_rows:
                d.pop("team_id", None)
                d.pop("created_by", None)
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
                team_clause, team_params = _meeting_team_clause(
                    work_team_ids if work_team_ids is not None else _viewer_team_ids(viewer)
                )
                rows = conn.execute(
                    base + f"""
                      AND (
                        (m.created_by = ? AND (m.is_team_doc = 0 OR m.team_id IS NULL))
                        OR m.is_public = 1
                        OR (m.is_team_doc = 1 AND m.team_id IS NOT NULL AND {team_clause})
                        OR (m.is_team_doc = 0 AND m.team_share = 1 AND {team_clause})
                      )
                      ORDER BY m.updated_at DESC""",
                    (*like2, uid, *team_params, *team_params)
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
                    AND project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
                  )
                )
                AND (
                  project IS NULL OR project = ''
                  OR project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
                )
            """ if viewer is None else ""
            rows = conn.execute(
                f"""SELECT id, title, project, updated_at, team_id, created_by
                    FROM checklists
                    WHERE deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {inactive_filter}{public_filter}
                    ORDER BY updated_at DESC""",
                (like, like)
            ).fetchall()
            blocked_cl = get_blocked_hidden_project_names(viewer) if viewer is not None else set()
            cl_rows = []
            for r in rows:
                d = dict(r)
                if blocked_cl and d.get("project") and d["project"] in blocked_cl:
                    continue
                cl_rows.append(d)
            cl_rows = _filter_rows_by_work_team(cl_rows, viewer, work_team_ids, "created_by")
            for d in cl_rows:
                d.pop("team_id", None)
                d.pop("created_by", None)
                d["type"] = "checklist"
                results.append(d)

    return results


def _filter_rows_by_work_team(rows, viewer, work_team_ids, author_col):
    """팀 기능 그룹 A #10: events/checklists 행을 작업 팀 기준으로 필터.

    - viewer=None 또는 admin: 무필터 (호출부에서 hidden/public 필터를 별도로 처리한다)
    - 그 외: team_id ∈ 작업 팀 집합, 또는 team_id IS NULL & 작성자 본인만 통과
    work_team_ids 가 None 이면 viewer 소속 팀 전체로 fallback.
    """
    if viewer is None or viewer.get("role") == "admin":
        return rows
    scope = set(work_team_ids) if work_team_ids is not None else _viewer_team_ids(viewer)
    author_tokens = _author_token_set(viewer)
    out = []
    for r in rows:
        tid = r.get("team_id")
        if tid is not None and tid in scope:
            out.append(r)
        elif tid is None and r.get(author_col) in author_tokens:
            out.append(r)
    return out


def search_events_mcp(query: str, start_after: str | None = None,
                      end_before: str | None = None, viewer=None, work_team_ids=None) -> list[dict]:
    """MCP search_events 도구용 이벤트 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - start_after/end_before: 날짜 겹침 조건 적용 (search_all events 부분과 동일 로직).
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체).
    반환: 경량 필드 목록 (id, title, project, start_datetime, end_datetime,
                          assignee, kanban_status, event_type)
    """
    if not query or not query.strip():
        return []
    like = f"%{query}%"
    sql = """SELECT id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type, team_id, created_by
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
    result = [dict(r) for r in rows]
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    for r in result:
        r.pop("team_id", None)
        r.pop("created_by", None)
    return result


def get_kanban_summary(project: str | None = None, viewer=None, work_team_ids=None) -> list[dict]:
    """MCP list_kanban 도구용 칸반 항목 경량 조회.
    get_kanban_events()의 필터 조건을 재사용하되 경량 필드만 SELECT.
    - project: None=전체, ""=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
    - viewer: None=공개 항목만, dict=인증 사용자(비공개 프로젝트 포함)
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체)
    반환 필드: id, title, project, kanban_status, priority, assignee, start_datetime, end_datetime
    """
    private_clause = """
        AND (
          e.is_public = 1
          OR (
            e.is_public IS NULL
            AND e.project IS NOT NULL AND e.project != ''
            AND e.project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
          )
        )
        AND (
          e.project IS NULL OR e.project = ''
          OR e.project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
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
    select = "SELECT e.id, e.title, e.project, e.kanban_status, e.priority, e.assignee, e.start_datetime, e.end_datetime, e.team_id, e.created_by FROM events e"
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
    result = [dict(r) for r in rows]
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    for r in result:
        r.pop("team_id", None)
        r.pop("created_by", None)
    return result


def search_kanban_mcp(query: str, project: str | None = None, viewer=None, work_team_ids=None) -> list[dict]:
    """MCP search_kanban 도구용 칸반 항목 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - project: None=전체, ""=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
    - viewer: None=공개 항목만, dict=인증 사용자(비공개 프로젝트 포함)
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체)
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
            AND e.project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
          )
        )
        AND (
          e.project IS NULL OR e.project = ''
          OR e.project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
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
    select = "SELECT e.id, e.title, e.project, e.kanban_status, e.priority, e.assignee, e.team_id, e.created_by FROM events e"
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
    result = [dict(r) for r in rows]
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    for r in result:
        r.pop("team_id", None)
        r.pop("created_by", None)
    return result


def search_documents_mcp(query: str, viewer=None, work_team_ids=None) -> list[dict]:
    """MCP search_documents 도구용 문서 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - viewer: None=공개 문서만, dict=인증 사용자(열람 가능 문서)
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체)
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
            team_clause, team_params = _meeting_team_clause(
                work_team_ids if work_team_ids is not None else _viewer_team_ids(viewer)
            )
            rows = conn.execute(
                base + f"""
                  AND (
                    (m.created_by = ? AND (m.is_team_doc = 0 OR m.team_id IS NULL))
                    OR m.is_public = 1
                    OR (m.is_team_doc = 1 AND m.team_id IS NOT NULL AND {team_clause})
                    OR (m.is_team_doc = 0 AND m.team_share = 1 AND {team_clause})
                  )
                  ORDER BY m.updated_at DESC""",
                (like, like, uid, *team_params, *team_params)
            ).fetchall()
    return [dict(r) for r in rows]


def search_checklists_mcp(query: str, project: str | None = None, viewer=None, work_team_ids=None) -> list[dict]:
    """MCP search_checklists 도구용 체크리스트 키워드 검색.
    - query: 검색 키워드. 비어있으면 빈 리스트 반환.
    - project: None=전체, ""=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
    - viewer: None=공개 항목만, dict=인증 사용자
    - work_team_ids: 팀 기능 그룹 A #10 — 작업 팀 필터 (None 이면 viewer 소속 팀 전체)
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
            AND project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
          )
        )
        AND (
          project IS NULL OR project = ''
          OR project NOT IN (SELECT name FROM projects WHERE (is_private = 1 OR is_hidden = 1) AND deleted_at IS NULL)
        )
    """ if viewer is None else ""
    cl_cols = "SELECT id, title, project, content, updated_at, team_id, created_by"
    with get_conn() as conn:
        if project is None:
            rows = conn.execute(
                f"""{cl_cols} FROM checklists
                    WHERE deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {inactive_filter}{public_filter}
                    ORDER BY updated_at DESC""",
                (like, like)
            ).fetchall()
        elif project == "":
            rows = conn.execute(
                f"""{cl_cols} FROM checklists
                    WHERE (project IS NULL OR project = '')
                      AND deleted_at IS NULL
                      AND (title LIKE ? OR content LIKE ?)
                      {public_filter}
                    ORDER BY updated_at DESC""",
                (like, like)
            ).fetchall()
        else:
            rows = conn.execute(
                f"""{cl_cols} FROM checklists
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
    blocked = get_blocked_hidden_project_names(viewer)
    if blocked:
        result = [r for r in result if not r.get("project") or r["project"] not in blocked]
    result = _filter_rows_by_work_team(result, viewer, work_team_ids, "created_by")
    for r in result:
        r.pop("team_id", None)
        r.pop("created_by", None)
    return result
