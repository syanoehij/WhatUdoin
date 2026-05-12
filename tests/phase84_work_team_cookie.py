"""팀 기능 그룹 B #15: 프로필 "팀 변경" UI + work_team_id 쿠키 발급/검증/Set-Cookie.

계획서 §7 (현재 작업 팀 선택) + §16 (권한 원칙). 그룹 A #10 에서 이미
`auth.resolve_work_team(request, user, explicit_id)` 헬퍼 + 팀 컨텍스트 라우트 ~20개가
`team_id` 파라미터와 `_work_scope`/`resolve_work_team`로 작업 팀을 결정한다 (쿠키 없으면 대표 팀).
#15 는 그 위에 ① work_team_id 쿠키 검증 + Set-Cookie 발급 ② `POST/GET /api/me/work-team`
③ 프로필 "팀 변경" 드롭다운 ④ 화면별 팀 드롭다운 제거 를 얹는다.

검증 (TestClient + 임시 DB — 운영 서버는 IP 자동 로그인이라 특정 사용자/다중 팀/admin 상태
브라우저 재현 불가; TestClient 는 session/work_team_id 쿠키 set·Set-Cookie 헤더 inspect 가능):
  A. 첫 로드(쿠키 없음), 2개 approved 팀(joined_at 순) → GET / → work_team_id Set-Cookie = 가장 이른 팀
  B. 첫 로드(쿠키 없음), admin → GET / → Set-Cookie = 가장 작은 id 비삭제 팀
  C. 유효 쿠키 present → 그 값 사용, Set-Cookie 갱신 없음
  D. 쿠키 present 인데 그 팀 soft-deleted → 새 대표 팀으로 Set-Cookie 갱신
  E. 쿠키 present 인데 사용자가 그 팀 멤버 아님(추방) → 새 대표 팀으로 Set-Cookie 갱신
  F. POST /api/me/work-team {team_id: 소속 팀} → 200 + Set-Cookie
  G. POST /api/me/work-team {team_id: 비소속 팀} (비admin) → 403
  H. POST /api/me/work-team {team_id: 삭제 예정 팀} → 404
  I. F 이후 /api/events / /api/kanban / /api/checklists / /api/doc / /api/project-timeline (team_id 안 보냄)가 새 팀 컨텍스트로
  J. #10 회귀: 명시 ?team_id=X (소속) 이 쿠키보다 우선
  K. phase80~83 회귀: 익명 = pre-#15 경로; 미배정 SSR GET / 는 Set-Cookie 없음
  L. admin _work_scope 는 여전히 None — /api/kanban 이 admin 에겐 전 팀 노출
  M. GET /api/me/work-team: 비admin → 소속 팀 목록 / admin → 전체 비삭제 팀
  + 정적: app.py 의 라우트·헬퍼·_ctx / templates / static — 마크업 invariant
  + import app OK

서버 재시작 불필요 — 임시 DB로 격리 실행.
"""
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORK_COOKIE = "work_team_id"


# ── 정적 invariant ────────────────────────────────────────────
def test_static_app_route_and_helpers():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    assert '@app.post("/api/me/work-team")' in src, "POST /api/me/work-team 라우트 없음"
    assert '@app.get("/api/me/work-team")' in src, "GET /api/me/work-team 라우트 없음"
    assert "def _ensure_work_team_cookie" in src
    assert "def _set_work_team_cookie" in src
    # _ctx 가 work_team_id/name 컨텍스트 제공
    m = re.search(r"def _ctx\(request: Request, \*\*kwargs\):(.*?)\ndef ", src, re.S)
    assert m and "work_team_id" in m.group(1) and "work_team_name" in m.group(1)
    # SSR 페이지가 쿠키 보정 호출
    for fn in ("def index(", "def kanban_page(", "def project_page(",
               "def docs_page(", "def check_page(", "def calendar_page("):
        i = src.index(fn)
        j = src.index("\n@app.", i + 1) if "\n@app." in src[i:] else len(src)
        assert "_ensure_work_team_cookie" in src[i:j], f"{fn} 가 _ensure_work_team_cookie 안 부름"
    # _work_scope 무변경: admin None 반환 그대로
    ws = re.search(r"def _work_scope\(.*?\):(.*?)\ndef _safe_int", src, re.S)
    assert ws and "is_admin(user)" in ws.group(1) and "return None" in ws.group(1)
    import app  # noqa: F401


def test_static_auth_resolve_work_team():
    src = (Path(ROOT) / "auth.py").read_text(encoding="utf-8")
    assert 'WORK_TEAM_COOKIE = "work_team_id"' in src
    assert "def _work_team_default" in src
    assert "def _team_is_active" in src
    m = re.search(r"def resolve_work_team\(.*?\):(.*?)\ndef admin_team_scope", src, re.S)
    assert m, "resolve_work_team 본문 추출 실패"
    body = m.group(1)
    # 쿠키 검증: user_can_access_team + _team_is_active
    assert "user_can_access_team(user, ctid)" in body and "_team_is_active(ctid)" in body
    # 명시 인자 우선 (호출부 검증) 그대로
    assert "explicit_id" in body


def test_static_db_helpers():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    assert "def first_active_team_id(" in src
    assert "def primary_team_id_for_user(" in src
    assert "def user_work_teams(" in src
    # joined_at 기준 대표 팀
    assert "ORDER BY ut.joined_at ASC" in src
    # 마이그레이션 phase 추가 없음
    assert "team_phase_15" not in src and "work_team_cookie_v1" not in src


def test_static_templates():
    base = (Path(ROOT) / "templates" / "base.html").read_text(encoding="utf-8")
    assert "팀 변경" in base and "toggleWorkTeamMenu" in base and "selectWorkTeam" in base
    assert "/api/me/work-team" in base
    assert "work_team_id" in base and "work_team_name" in base
    # admin 슈퍼유저 표시
    assert "(슈퍼유저)" in base
    # 화면별 팀 드롭다운 제거
    kanban = (Path(ROOT) / "templates" / "kanban.html").read_text(encoding="utf-8")
    assert 'id="team-filter"' not in kanban, "kanban.html 에 team-filter 드롭다운이 남아있음"
    assert "_applyInitialTeamFilter" not in kanban
    assert "CURRENT_USER.team_id" not in kanban
    assert "CURRENT_USER.work_team_id" in kanban
    project = (Path(ROOT) / "templates" / "project.html").read_text(encoding="utf-8")
    assert 'id="team-filter"' not in project, "project.html 에 team-filter 드롭다운이 남아있음"
    assert "_applyInitialTeamFilter" not in project and "proj_team_filter" not in project
    calendar = (Path(ROOT) / "templates" / "calendar.html").read_text(encoding="utf-8")
    assert "CURRENT_USER.team_id" not in calendar
    assert "CURRENT_USER.work_team_id" in calendar


def test_static_css():
    css = (Path(ROOT) / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert ".work-team-list" in css and ".work-team-item" in css


# ── TestClient fixtures ───────────────────────────────────────
def _setup(monkeypatch):
    db_dir = Path(ROOT) / ".claude" / "workspaces" / "current" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_workteam15_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("WHATUDOIN_BASE_DIR", ROOT)
    monkeypatch.setenv("WHATUDOIN_RUN_DIR", ROOT)
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    import app as app_module
    return app_module, db


def _make_user(db, name, *, admin=False):
    u = db.create_user_account(name, "pw1234")
    assert u, f"create_user_account({name}) 실패"
    if admin:
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (u["id"],))
        u = dict(u); u["role"] = "admin"
    return u


def _login(db, client, user):
    sid = db.create_session(user["id"])
    client.cookies.set("session_id", sid)


def _join(db, user_id, team_id, status="approved", joined_at=None):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO user_teams (user_id, team_id, role, status) VALUES (?, ?, 'member', ?)",
                     (user_id, team_id, status))
        if joined_at is not None:
            conn.execute("UPDATE user_teams SET joined_at = ? WHERE user_id = ? AND team_id = ?",
                         (joined_at, user_id, team_id))


def _soft_delete_team(db, team_id):
    with db.get_conn() as conn:
        conn.execute("UPDATE teams SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?", (team_id,))


def _seed_team_with_event(db, name, title):
    tid = db.create_team(name)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (team_id, name, name_norm, is_active, is_hidden, is_private) "
                     "VALUES (?, ?, ?, 1, 0, 0)", (tid, f"P_{name}", f"p_{name}".lower()))
        conn.execute("INSERT INTO events (title, start_datetime, team_id, project, is_public, is_active, kanban_status, event_type) "
                     "VALUES (?, '2026-06-01T09:00:00', ?, ?, 0, 1, 'todo', 'schedule')",
                     (title, tid, f"P_{name}"))
    return tid


def _set_cookie_header(r):
    return r.headers.get("set-cookie", "")


# ── A. 첫 로드, 쿠키 없음, 2팀 멤버(joined_at 순) → 가장 이른 팀 ──
def test_a_first_load_member_two_teams(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid_early = db.create_team("EarlyTeam")
        tid_late = db.create_team("LateTeam")
        u = _make_user(db, "다중팀유저")
        _join(db, u["id"], tid_late, joined_at="2026-02-01 00:00:00")    # 늦게 가입
        _join(db, u["id"], tid_early, joined_at="2026-01-01 00:00:00")   # 먼저 가입
        _login(db, client, u)
        client.cookies.delete(WORK_COOKIE) if WORK_COOKIE in client.cookies else None
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        sc = _set_cookie_header(r)
        assert f"{WORK_COOKIE}={tid_early}" in sc, f"대표 팀(가장 이른) Set-Cookie 안 됨: {sc!r}"


# ── B. 첫 로드, 쿠키 없음, admin → 가장 작은 id 비삭제 팀 ──
def test_b_first_load_admin(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid1 = db.create_team("T1")  # 가장 작은 id
        tid2 = db.create_team("T2")
        a = _make_user(db, "관리자", admin=True)
        _login(db, client, a)
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        sc = _set_cookie_header(r)
        assert f"{WORK_COOKIE}={tid1}" in sc, f"admin 첫 비삭제 팀 Set-Cookie 안 됨: {sc!r}"


# ── C. 유효 쿠키 → 사용, Set-Cookie 갱신 없음 ──
def test_c_valid_cookie_no_resetcookie(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t1 = db.create_team("Alpha")
        t2 = db.create_team("Beta")
        u = _make_user(db, "쿠키유저")
        _join(db, u["id"], t1, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], t2, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        client.cookies.set(WORK_COOKIE, str(t2))   # 대표 팀(t1)이 아닌 t2 를 쿠키로
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        sc = _set_cookie_header(r)
        assert WORK_COOKIE not in sc, f"유효 쿠키인데 Set-Cookie 갱신됨: {sc!r}"


# ── D. 쿠키가 soft-deleted 팀 → 새 대표 팀으로 갱신 ──
def test_d_cookie_deleted_team_recompute(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t_alive = db.create_team("AliveTeam")
        t_dead = db.create_team("DeadTeam")
        u = _make_user(db, "삭제팀쿠키유저")
        _join(db, u["id"], t_alive, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], t_dead, joined_at="2026-02-01 00:00:00")
        _soft_delete_team(db, t_dead)
        _login(db, client, u)
        client.cookies.set(WORK_COOKIE, str(t_dead))
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        sc = _set_cookie_header(r)
        assert f"{WORK_COOKIE}={t_alive}" in sc, f"삭제 예정 팀 쿠키 → 새 대표 팀 갱신 안 됨: {sc!r}"


# ── E. 쿠키 팀에 멤버 아님(추방) → 새 대표 팀으로 갱신 ──
def test_e_cookie_not_member_recompute(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t_mine = db.create_team("MyTeam")
        t_other = db.create_team("NotMyTeam")
        u = _make_user(db, "추방쿠키유저")
        _join(db, u["id"], t_mine, joined_at="2026-01-01 00:00:00")
        _login(db, client, u)
        client.cookies.set(WORK_COOKIE, str(t_other))  # 소속 아닌 팀
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        sc = _set_cookie_header(r)
        assert f"{WORK_COOKIE}={t_mine}" in sc, f"비소속 팀 쿠키 → 새 대표 팀 갱신 안 됨: {sc!r}"


# ── F. POST 정상 → 200 + Set-Cookie ──
def test_f_post_valid(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t1 = db.create_team("Aaa")
        t2 = db.create_team("Bbb")
        u = _make_user(db, "전환유저")
        _join(db, u["id"], t1, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], t2, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        r = client.post("/api/me/work-team", json={"team_id": t2})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] and j["team_id"] == t2 and j["team_name"] == "Bbb"
        assert f"{WORK_COOKIE}={t2}" in _set_cookie_header(r)


# ── G. POST 비소속 팀 (비admin) → 403 ──
def test_g_post_non_member_forbidden(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t1 = db.create_team("Mine")
        t2 = db.create_team("NotMine")
        u = _make_user(db, "비소속전환유저")
        _join(db, u["id"], t1)
        _login(db, client, u)
        r = client.post("/api/me/work-team", json={"team_id": t2})
        assert r.status_code == 403, r.text


# ── H. POST 삭제 예정 팀 → 404 ──
def test_h_post_deleted_team(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t_alive = db.create_team("Live")
        t_dead = db.create_team("Gone")
        u = _make_user(db, "삭제팀전환유저")
        _join(db, u["id"], t_alive)
        _join(db, u["id"], t_dead)
        _soft_delete_team(db, t_dead)
        _login(db, client, u)
        r = client.post("/api/me/work-team", json={"team_id": t_dead})
        assert r.status_code == 404, r.text
        # 잘못된 타입도 400
        r2 = client.post("/api/me/work-team", json={"team_id": "abc"})
        assert r2.status_code == 400, r2.text


# ── I. POST 후 후속 API 가 새 팀 컨텍스트로 ──
def test_i_apis_use_new_work_team(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = _seed_team_with_event(db, "TeamA", "EVENT_A")
        tb = _seed_team_with_event(db, "TeamB", "EVENT_B")
        u = _make_user(db, "API유저")
        _join(db, u["id"], ta, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], tb, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        # 기본(대표 팀 = ta) → EVENT_A 만
        r = client.get("/api/kanban")
        titles = {e["title"] for e in r.json()}
        assert "EVENT_A" in titles and "EVENT_B" not in titles, titles
        # 작업 팀을 tb 로 전환
        client.post("/api/me/work-team", json={"team_id": tb})
        for path in ("/api/kanban", "/api/events", "/api/project-timeline"):
            r = client.get(path)
            data = r.json()
            if path == "/api/project-timeline":
                titles = {e["title"] for t in data for p in t.get("projects", []) for e in p.get("events", [])}
            else:
                titles = {e["title"] for e in data}
            assert "EVENT_B" in titles and "EVENT_A" not in titles, (path, titles)
        # /api/checklists / /api/doc 는 빈 목록이어도 OK — 다른 팀 데이터 없으면 됨
        r = client.get("/api/checklists")
        assert r.status_code == 200
        r = client.get("/api/doc")
        assert r.status_code == 200


# ── J. #10 회귀: 명시 ?team_id=X 가 쿠키보다 우선 ──
def test_j_explicit_team_id_wins(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = _seed_team_with_event(db, "TA", "EV_A")
        tb = _seed_team_with_event(db, "TB", "EV_B")
        u = _make_user(db, "명시유저")
        _join(db, u["id"], ta, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], tb, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        client.cookies.set(WORK_COOKIE, str(ta))
        # 명시 team_id=tb 가 쿠키(ta)보다 우선
        r = client.get(f"/api/kanban?team_id={tb}")
        titles = {e["title"] for e in r.json()}
        assert "EV_B" in titles and "EV_A" not in titles, titles
        # 비소속 팀 명시는 무시 → 쿠키(ta) fallback
        t_other = db.create_team("Other")
        r = client.get(f"/api/kanban?team_id={t_other}")
        titles = {e["title"] for e in r.json()}
        assert "EV_A" in titles and "EV_B" not in titles, titles


# ── K. 미배정 SSR / 는 Set-Cookie 없음 + 비로그인 영향 없음 ──
def test_k_unassigned_no_setcookie(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        db.create_team("SomeTeam")
        u = _make_user(db, "미배정유저")  # user_teams 없음 → 미배정
        _login(db, client, u)
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        assert WORK_COOKIE not in _set_cookie_header(r), "미배정 사용자에게 work_team_id Set-Cookie 됨"
        # 비로그인도 영향 없음
        client.cookies.clear()
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200
        assert WORK_COOKIE not in _set_cookie_header(r)


# ── L. admin _work_scope 는 None — /api/kanban 전 팀 노출 ──
def test_l_admin_sees_all_teams(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = _seed_team_with_event(db, "AT", "ADMIN_EV_A")
        tb = _seed_team_with_event(db, "BT", "ADMIN_EV_B")
        a = _make_user(db, "전역관리자", admin=True)
        _login(db, client, a)
        # admin 쿠키가 ta 라도 _work_scope 는 None → 전 팀 노출
        client.cookies.set(WORK_COOKIE, str(ta))
        r = client.get("/api/kanban")
        titles = {e["title"] for e in r.json()}
        assert "ADMIN_EV_A" in titles and "ADMIN_EV_B" in titles, titles


# ── M. GET /api/me/work-team ──
def test_m_get_work_team_list(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        t1 = db.create_team("One")
        t2 = db.create_team("Two")
        t3 = db.create_team("Three")
        # 비admin → 본인 소속 팀만
        u = _make_user(db, "목록유저")
        _join(db, u["id"], t1, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], t2, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        r = client.get("/api/me/work-team")
        assert r.status_code == 200
        j = r.json()
        ids = {t["id"] for t in j["teams"]}
        assert ids == {t1, t2}, ids
        assert j["current"] == t1   # 대표 팀
        assert j["is_admin"] is False
        # admin → 전체 비삭제 팀
        client.cookies.clear()
        a = _make_user(db, "목록관리자", admin=True)
        _login(db, client, a)
        r = client.get("/api/me/work-team")
        j = r.json()
        ids = {t["id"] for t in j["teams"]}
        assert ids == {t1, t2, t3}, ids
        assert j["is_admin"] is True


# ── N. 비로그인 GET /api/me/work-team → 401 ──
def test_n_get_work_team_requires_login(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        r = client.get("/api/me/work-team")
        assert r.status_code == 401
        r = client.post("/api/me/work-team", json={"team_id": 1})
        assert r.status_code == 401
