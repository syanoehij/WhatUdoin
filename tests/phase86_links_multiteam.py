"""팀 기능 그룹 B #15-2: links 기능 다중 팀 전환.

계획서 §13 (links 테이블 정리) + §16 (권한 원칙) + §8-1 (자료별 적용 표 — 링크는
"작성자/admin만 편집·삭제"라는 일정·체크와 다른 예외). 그룹 A #4 에서 `links.team_id`
백필(scope='team' + NULL row 한정)이 끝났고, #15-2 는 `/api/links` 4개 라우트를
`users.team_id` 단일 비교 → `work_team_id`(또는 명시 team_id) 기반으로 옮긴다.

검증 (TestClient + 임시 DB — 운영 서버는 IP 자동 로그인이라 특정 사용자/다중 팀/admin
상태 브라우저 재현 불가; TestClient 는 session/work_team_id 쿠키 set 가능):
  A. 다중 팀 사용자 작업 팀 전환 → GET /api/links 가 새 팀의 scope='team' 링크로
  B. 다른 팀 멤버 세션에선 그 팀 scope='team' 링크 안 보임
  C. personal 링크는 작성자 본인에게만 노출 (작업 팀 무관)
  D. POST /api/links scope='team' → team_id 가 work_team_id 로 확정 저장 (personal 은 NULL)
  E. admin: work_team_id 명시(쿠키 또는 ?team_id) 후 scope='team' 링크 CRUD; admin GET 은 전 팀 노출
  F. 같은 팀 멤버 B 가 멤버 A 의 scope='team' 링크 PUT·DELETE → 403
  G. admin 이 타인 scope='team' 링크 PUT·DELETE 가능
  H. admin 이 work_team 없이 (쿠키 없음 + ?team_id 없음) scope='team' POST → 400
  I. 회귀: personal 링크 CRUD 본인; 비로그인 GET /api/links → []
  + 정적: database.py / app.py 마크업·시그니처 invariant
  + import app OK

서버 재시작 필요 — 코드 reload (스키마 무변경, 마이그레이션 phase 추가 없음).
"""
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORK_COOKIE = "work_team_id"


# ── 정적 invariant ────────────────────────────────────────────
def test_static_db_get_links_signature():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    m = re.search(r"\ndef get_links\((.*?)\):", src)
    assert m, "get_links 정의 추출 실패"
    assert "work_team_ids" in m.group(1), "get_links 가 work_team_ids 시그니처로 전환되지 않음"
    body = re.search(r"\ndef get_links\(.*?\n(?=\ndef )", src, re.S).group(0)
    assert "user_teams" not in body  # links 는 user_teams 직접 조회 안 함 (라우트의 _work_scope 가 담당)
    # users.team_id 단일 비교 잔존 없음
    assert "scope = 'team' AND team_id = ?)" in body or "team_id IN" in body


def test_static_db_update_link_role_branch():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    m = re.search(r"\ndef update_link\((.*?)\)(?:\s*->\s*\w+)?:", src)
    assert m and "role" in m.group(1), "update_link 가 role 인자를 받지 않음"
    body = re.search(r"\ndef update_link\(.*?\n(?=\ndef )", src, re.S).group(0)
    assert "role == 'admin'" in body, "update_link 에 admin 분기 없음"
    # admin 경로는 created_by 없이 UPDATE
    assert "WHERE id=?" in body and "WHERE id=? AND created_by=?" in body


def test_static_app_routes():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    # GET: _work_scope 사용
    m_get = re.search(r'@app\.get\("/api/links"\)\ndef api_get_links\(.*?\n(?=\n@app\.)', src, re.S)
    assert m_get and "_work_scope" in m_get.group(0) and "user.get(\"team_id\")" not in m_get.group(0)
    # POST: require_admin_work_team(explicit_id=data.get("team_id")) + 400 거부 (#16 헬퍼 통합)
    m_post = re.search(r'@app\.post\("/api/links"\)\nasync def api_create_link\(.*?\n(?=\n@app\.)', src, re.S)
    assert m_post
    pb = m_post.group(0)
    assert "require_admin_work_team" in pb
    assert "explicit_id=data.get(\"team_id\")" in pb
    assert "status_code=400" in pb
    assert "user.get(\"team_id\")" not in pb
    # PUT: update_link 에 role 전달
    m_put = re.search(r'@app\.put\("/api/links/\{link_id\}"\)\nasync def api_update_link\(.*?\n(?=\n@app\.)', src, re.S)
    assert m_put and "user.get(\"role\"" in m_put.group(0)
    # DELETE: delete_link 에 role 전달 (이미 있던 동작)
    m_del = re.search(r'@app\.delete\("/api/links/\{link_id\}"\)\ndef api_delete_link\(.*?\n(?=\n@app\.)', src, re.S)
    assert m_del and "user.get(\"role\"" in m_del.group(0)
    import app  # noqa: F401


# ── TestClient fixtures ───────────────────────────────────────
def _setup(monkeypatch):
    db_dir = Path(ROOT) / ".claude" / "workspaces" / "current" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_links86_{uuid.uuid4().hex}.db"
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


def _logout(client):
    client.cookies.clear()


def _join(db, user_id, team_id, status="approved", joined_at=None):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO user_teams (user_id, team_id, role, status) VALUES (?, ?, 'member', ?)",
                     (user_id, team_id, status))
        if joined_at is not None:
            conn.execute("UPDATE user_teams SET joined_at = ? WHERE user_id = ? AND team_id = ?",
                         (joined_at, user_id, team_id))


def _seed_link(db, *, title, url, scope, team_id, created_by):
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO links (title, url, description, scope, team_id, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (title, url, "", scope, team_id, created_by))
        return cur.lastrowid


def _titles(resp):
    return {l["title"] for l in resp.json()}


# ── A. 다중 팀 사용자 작업 팀 전환 → GET /api/links 가 새 팀 컨텍스트로 ──
def test_a_work_team_switch_changes_team_links(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("TeamA")
        tb = db.create_team("TeamB")
        u = _make_user(db, "다중팀유저")
        _join(db, u["id"], ta, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], tb, joined_at="2026-02-01 00:00:00")
        _seed_link(db, title="A팀링크", url="https://a.example", scope="team", team_id=ta, created_by="다중팀유저")
        _seed_link(db, title="B팀링크", url="https://b.example", scope="team", team_id=tb, created_by="다중팀유저")
        _seed_link(db, title="내개인링크", url="https://me.example", scope="personal", team_id=None, created_by="다중팀유저")
        _login(db, client, u)
        # 기본 = 대표 팀(ta)
        r = client.get("/api/links")
        assert r.status_code == 200
        assert _titles(r) == {"A팀링크", "내개인링크"}, _titles(r)
        # 작업 팀을 tb 로 전환 (쿠키)
        client.cookies.set(WORK_COOKIE, str(tb))
        r = client.get("/api/links")
        assert _titles(r) == {"B팀링크", "내개인링크"}, _titles(r)
        # 명시 ?team_id=ta 가 쿠키(tb)보다 우선
        r = client.get(f"/api/links?team_id={ta}")
        assert _titles(r) == {"A팀링크", "내개인링크"}, _titles(r)


# ── B. 다른 팀 멤버 세션에선 그 팀 scope='team' 링크 안 보임 ──
def test_b_other_team_member_cannot_see_team_links(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("팀가")
        tb = db.create_team("팀나")
        a_user = _make_user(db, "가팀멤버")
        b_user = _make_user(db, "나팀멤버")
        _join(db, a_user["id"], ta)
        _join(db, b_user["id"], tb)
        _seed_link(db, title="가팀링크", url="https://x.example", scope="team", team_id=ta, created_by="가팀멤버")
        # b_user 세션: 가팀링크 안 보임 (소속 아님)
        _login(db, client, b_user)
        r = client.get("/api/links")
        assert "가팀링크" not in _titles(r), _titles(r)
        # 명시 ?team_id=ta 로 시도해도 비소속 → _work_scope 가 무시·대표팀(tb) fallback → 안 보임
        r = client.get(f"/api/links?team_id={ta}")
        assert "가팀링크" not in _titles(r), _titles(r)
        # a_user 세션: 가팀링크 보임
        _logout(client)
        _login(db, client, a_user)
        r = client.get("/api/links")
        assert "가팀링크" in _titles(r), _titles(r)


# ── C. personal 링크는 작성자 본인에게만 (작업 팀 무관) ──
def test_c_personal_links_owner_only(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("공용팀")
        u1 = _make_user(db, "유저1")
        u2 = _make_user(db, "유저2")
        _join(db, u1["id"], tid)
        _join(db, u2["id"], tid)
        _seed_link(db, title="유저1개인", url="https://u1.example", scope="personal", team_id=None, created_by="유저1")
        _seed_link(db, title="공유팀링크", url="https://t.example", scope="team", team_id=tid, created_by="유저1")
        # u2 는 유저1개인 안 보임, 공유팀링크 보임
        _login(db, client, u2)
        r = client.get("/api/links")
        assert _titles(r) == {"공유팀링크"}, _titles(r)
        # u1 은 둘 다 보임
        _logout(client)
        _login(db, client, u1)
        r = client.get("/api/links")
        assert _titles(r) == {"유저1개인", "공유팀링크"}, _titles(r)


# ── D. POST scope='team' → team_id 가 work_team_id 로 확정 저장; personal → NULL ──
def test_d_post_team_scope_fixes_team_id(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("저장팀A")
        tb = db.create_team("저장팀B")
        u = _make_user(db, "저장유저")
        _join(db, u["id"], ta, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], tb, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        client.cookies.set(WORK_COOKIE, str(tb))   # 작업 팀 = tb
        r = client.post("/api/links", json={"title": "팀스코프링크", "url": "https://s.example", "scope": "team"})
        assert r.status_code == 200, r.text
        lid = r.json()["id"]
        with db.get_conn() as conn:
            row = conn.execute("SELECT scope, team_id, created_by FROM links WHERE id=?", (lid,)).fetchone()
        assert tuple(row) == ("team", tb, "저장유저"), tuple(row)
        # personal → team_id NULL (작업 팀과 무관)
        r = client.post("/api/links", json={"title": "개인스코프링크", "url": "https://p.example", "scope": "personal"})
        assert r.status_code == 200, r.text
        lid2 = r.json()["id"]
        with db.get_conn() as conn:
            row2 = conn.execute("SELECT scope, team_id FROM links WHERE id=?", (lid2,)).fetchone()
        assert tuple(row2) == ("personal", None), tuple(row2)
        # 명시 team_id=ta 가 쿠키보다 우선 (소속 팀)
        r = client.post("/api/links", json={"title": "명시팀링크", "url": "https://e.example", "scope": "team", "team_id": ta})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            row3 = conn.execute("SELECT team_id FROM links WHERE id=?", (r.json()["id"],)).fetchone()
        assert row3[0] == ta, row3[0]
        # 비소속 명시 team_id 는 무시 → 작업 팀(tb)으로 저장 (resolve_work_team explicit 무신뢰 X — 여기선 explicit 우선이지만
        # require_work_team_access 가 비admin 비소속이면 403)
        t_other = db.create_team("타팀")
        r = client.post("/api/links", json={"title": "비소속시도", "url": "https://o.example", "scope": "team", "team_id": t_other})
        assert r.status_code == 403, r.text


# ── E. admin: work_team_id 명시 후 scope='team' CRUD; admin GET 은 전 팀 노출 ──
def test_e_admin_crud_with_explicit_team(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("관리팀A")
        tb = db.create_team("관리팀B")
        admin = _make_user(db, "관리자E", admin=True)
        # 두 팀에 미리 팀링크 (다른 사용자 작성)
        _seed_link(db, title="A팀기존링크", url="https://a0.example", scope="team", team_id=ta, created_by="누군가")
        _seed_link(db, title="B팀기존링크", url="https://b0.example", scope="team", team_id=tb, created_by="누군가")
        _login(db, client, admin)
        # admin GET: 전 팀의 scope='team' 링크 모두 노출 (_work_scope → None → 무필터)
        r = client.get("/api/links")
        assert {"A팀기존링크", "B팀기존링크"}.issubset(_titles(r)), _titles(r)
        # admin POST scope='team' with 쿠키 work_team_id=ta
        client.cookies.set(WORK_COOKIE, str(ta))
        r = client.post("/api/links", json={"title": "admin작성A", "url": "https://aa.example", "scope": "team"})
        assert r.status_code == 200, r.text
        lid = r.json()["id"]
        with db.get_conn() as conn:
            assert conn.execute("SELECT team_id FROM links WHERE id=?", (lid,)).fetchone()[0] == ta
        # admin POST scope='team' with 명시 ?team_id 본문
        r = client.post("/api/links", json={"title": "admin작성B", "url": "https://ab.example", "scope": "team", "team_id": tb})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            assert conn.execute("SELECT team_id FROM links WHERE id=?", (r.json()["id"],)).fetchone()[0] == tb
        # admin PUT / DELETE 본인 작성
        r = client.put(f"/api/links/{lid}", json={"title": "admin작성A수정", "url": "https://aa2.example"})
        assert r.status_code == 200, r.text
        r = client.delete(f"/api/links/{lid}")
        assert r.status_code == 200, r.text


# ── F. 같은 팀 멤버 B 가 멤버 A 의 scope='team' 링크 PUT·DELETE → 403 ──
def test_f_team_member_cannot_edit_others_link(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("협업팀")
        a = _make_user(db, "멤버A")
        b = _make_user(db, "멤버B")
        _join(db, a["id"], tid)
        _join(db, b["id"], tid)
        lid = _seed_link(db, title="A의팀링크", url="https://af.example", scope="team", team_id=tid, created_by="멤버A")
        # B 가 본다 (같은 팀 → 조회는 됨)
        _login(db, client, b)
        r = client.get("/api/links")
        assert "A의팀링크" in _titles(r), _titles(r)
        # B 가 수정 시도 → 403
        r = client.put(f"/api/links/{lid}", json={"title": "B가수정", "url": "https://b1.example"})
        assert r.status_code == 403, r.text
        # B 가 삭제 시도 → 403
        r = client.delete(f"/api/links/{lid}")
        assert r.status_code == 403, r.text
        # 여전히 원본 유지
        with db.get_conn() as conn:
            row = conn.execute("SELECT title FROM links WHERE id=?", (lid,)).fetchone()
        assert row and row[0] == "A의팀링크"
        # A 본인은 수정·삭제 가능
        _logout(client)
        _login(db, client, a)
        r = client.put(f"/api/links/{lid}", json={"title": "A가수정", "url": "https://a1.example"})
        assert r.status_code == 200, r.text


# ── G. admin 이 타인 scope='team' 링크 PUT·DELETE 가능 ──
def test_g_admin_can_edit_others_team_link(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("관리대상팀")
        member = _make_user(db, "일반멤버G")
        admin = _make_user(db, "관리자G", admin=True)
        _join(db, member["id"], tid)
        lid1 = _seed_link(db, title="멤버팀링크1", url="https://g1.example", scope="team", team_id=tid, created_by="일반멤버G")
        lid2 = _seed_link(db, title="멤버팀링크2", url="https://g2.example", scope="team", team_id=tid, created_by="일반멤버G")
        # admin 이 타인 작성 팀링크 수정
        _login(db, client, admin)
        r = client.put(f"/api/links/{lid1}", json={"title": "admin이수정", "url": "https://g1b.example"})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            assert conn.execute("SELECT title FROM links WHERE id=?", (lid1,)).fetchone()[0] == "admin이수정"
        # admin 이 타인 작성 팀링크 삭제
        r = client.delete(f"/api/links/{lid2}")
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            assert conn.execute("SELECT 1 FROM links WHERE id=?", (lid2,)).fetchone() is None
        # admin 이 타인 작성 personal 링크도 수정·삭제 가능 (admin 슈퍼유저)
        lid3 = _seed_link(db, title="멤버개인링크", url="https://g3.example", scope="personal", team_id=None, created_by="일반멤버G")
        r = client.put(f"/api/links/{lid3}", json={"title": "admin이개인수정", "url": "https://g3b.example"})
        assert r.status_code == 200, r.text
        r = client.delete(f"/api/links/{lid3}")
        assert r.status_code == 200, r.text


# ── H. admin 이 work_team 없이 scope='team' POST → 400 ──
def test_h_admin_post_team_without_work_team(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        # 팀이 아예 없을 수도 / 있어도 쿠키 미설정 + 본문 team_id 미지정 → resolve_work_team → None
        admin = _make_user(db, "관리자H", admin=True)
        _login(db, client, admin)
        # 쿠키 없음 + team_id 없음 + admin (user_teams 없음 + first_active_team_id None 일 수도 있으나
        # 팀이 있으면 admin 대표 팀이 first_active_team_id 라 None 이 아님 — 그러므로 팀을 만들지 않는다)
        r = client.post("/api/links", json={"title": "팀없는팀링크", "url": "https://h.example", "scope": "team"})
        assert r.status_code == 400, r.text
        # personal 은 work_team 불필요 → 200
        r = client.post("/api/links", json={"title": "admin개인", "url": "https://hp.example", "scope": "personal"})
        assert r.status_code == 200, r.text


# ── I. 회귀: personal CRUD 본인; 비로그인 GET → [] ──
def test_i_regression_personal_crud_and_anon(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("회귀팀")
        u = _make_user(db, "회귀유저")
        _join(db, u["id"], tid)
        # 비로그인 → []
        r = client.get("/api/links")
        assert r.status_code == 200 and r.json() == [], r.text
        # 로그인 → personal 생성·수정·삭제
        _login(db, client, u)
        r = client.post("/api/links", json={"title": "내링크", "url": "https://my.example", "scope": "personal"})
        assert r.status_code == 200, r.text
        lid = r.json()["id"]
        r = client.put(f"/api/links/{lid}", json={"title": "내링크수정", "url": "https://my2.example"})
        assert r.status_code == 200, r.text
        r = client.get("/api/links")
        assert _titles(r) == {"내링크수정"}, _titles(r)
        r = client.delete(f"/api/links/{lid}")
        assert r.status_code == 200, r.text
        r = client.get("/api/links")
        assert r.json() == [], r.text
        # title/url 누락 → 400 (기존 검증 유지)
        r = client.post("/api/links", json={"title": "", "url": "https://x.example", "scope": "personal"})
        assert r.status_code == 400, r.text
        # 잘못된 scheme → 400 (기존 검증 유지)
        r = client.post("/api/links", json={"title": "js링크", "url": "javascript:alert(1)", "scope": "personal"})
        assert r.status_code == 400, r.text


# ── 직접 DB: get_links work_team_ids 컨벤션 ──
def test_db_get_links_conventions(monkeypatch):
    app_module, db = _setup(monkeypatch)
    db.init_db()
    ta = db.create_team("DBTeamA")
    tb = db.create_team("DBTeamB")
    _seed_link(db, title="A팀", url="https://a.example", scope="team", team_id=ta, created_by="kim")
    _seed_link(db, title="B팀", url="https://b.example", scope="team", team_id=tb, created_by="kim")
    _seed_link(db, title="kim개인", url="https://k.example", scope="personal", team_id=None, created_by="kim")
    _seed_link(db, title="lee개인", url="https://l.example", scope="personal", team_id=None, created_by="lee")
    # None (admin) → 전 팀 팀링크 + 본인 개인링크
    titles = {l["title"] for l in db.get_links("kim", None)}
    assert titles == {"A팀", "B팀", "kim개인"}, titles
    # set() (미배정) → 본인 개인링크만
    titles = {l["title"] for l in db.get_links("kim", set())}
    assert titles == {"kim개인"}, titles
    # {ta} → A팀 + 본인 개인링크
    titles = {l["title"] for l in db.get_links("kim", {ta})}
    assert titles == {"A팀", "kim개인"}, titles
    # {ta, tb} → A·B팀 + 본인 개인링크 (admin 명시 다중 — 일반화 경로)
    titles = {l["title"] for l in db.get_links("kim", {ta, tb})}
    assert titles == {"A팀", "B팀", "kim개인"}, titles
    # lee 관점: {ta} → A팀 + lee개인
    titles = {l["title"] for l in db.get_links("lee", {ta})}
    assert titles == {"A팀", "lee개인"}, titles
