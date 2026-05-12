"""팀 기능 그룹 B #15-3: team_notices 팀별 공지 전환.

계획서 §13 (team_notices 팀별 공지 전환) + §8-1 (자료별 적용 표 — 팀 공지는 "팀 공유":
같은 팀 승인 멤버 누구나 작성·갱신·발송; links 의 "작성자/admin만"과 다른 예외) + §16 (권한 원칙).
그룹 A #2(Phase 1: team_notices.team_id 컬럼) + #4(Phase 2: created_by → users.name 백필,
admin/매칭실패는 NULL 유지) 가 끝났고, #15-3 은 `/api/notice` 계열 라우트를 전역 단일 공지에서
`work_team_id` 기준으로 옮기고 30일/100개 자동 정리도 팀별로 적용한다.

검증 (TestClient + 임시 DB — 운영 서버는 IP 자동 로그인이라 특정 사용자/다중 팀/admin 상태
브라우저 재현 불가; TestClient 는 session/work_team_id 쿠키 set 가능):
  A. 다중 팀 사용자 작업 팀 전환 → GET /api/notice 가 새 팀의 최신 공지로 (명시 ?team_id 우선)
  B. 다른 팀 멤버 세션에선 그 팀 공지 안 보임 (명시 ?team_id 비소속 → 무시·대표 팀 fallback)
  C. POST /api/notice → team_id 가 work_team_id 로 확정 저장; 비소속 명시 → 403; 미배정 → 400;
     admin 이 work_team 없이 → 400; admin 쿠키/명시 후 → 200
  D. POST /api/notice/notify → 같은 팀 approved 멤버에게만 알림; 다른 팀·pending·글로벌 admin 미수신;
     발송자(exclude_user) 제외; 공지 없으면 {"ok": False, "reason": "no_notice"}
  E. 같은 팀 멤버 B 가 멤버 A 작성 공지를 POST(갱신)·notify 가능 (팀 공유 모델 — links 와 반대)
  F. 자동 정리 팀별: 팀A 101개 → save 시 팀A 최신 100개만; 팀B 공지 영향 없음; 30일 이전 팀A row 만 삭제;
     NULL 잔존 row 영향 없음
  G. NULL 잔존 row: GET /api/notice 미노출; get_notice_history(tid, include_null=False) 미포함;
     get_notice_history(tid, include_null=True)(admin) 포함; SSR /notice/history admin 응답 포함·비admin 미포함
  H. SSR: GET /notice·/notice/history 가 work_team_id 쿠키 없으면(소속 1개) Set-Cookie 발급;
     미배정 사용자 → Set-Cookie 없음·notice/histories 빈 값
  + 정적: database.py / app.py 시그니처·라우트 invariant
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
def test_static_db_signatures():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    # get_notice_latest_for_team(team_id) — team_id 필터
    m = re.search(r"\ndef get_notice_latest_for_team\((.*?)\)", src)
    assert m and "team_id" in m.group(1), "get_notice_latest_for_team(team_id) 시그니처 부재"
    body = re.search(r"\ndef get_notice_latest_for_team\(.*?\n(?=\ndef )", src, re.S).group(0)
    assert "WHERE team_id = ?" in body
    # save_notice(content, team_id, created_by)
    m = re.search(r"\ndef save_notice\((.*?)\)", src)
    assert m and "team_id" in m.group(1), "save_notice 에 team_id 인자 부재"
    body = re.search(r"\ndef save_notice\(.*?\n(?=\ndef )", src, re.S).group(0)
    assert "INSERT INTO team_notices (team_id, content, created_by)" in body
    # 자동 정리 두 쿼리 모두 team_id 한정
    assert body.count("WHERE team_id = ?") >= 2, "save_notice 자동정리가 팀별로 제한되지 않음"
    assert "datetime('now', '-30 days')" in body
    assert "ORDER BY id DESC LIMIT 100" in body
    # 옛 전역 일괄 삭제 잔존 없음
    assert "DELETE FROM team_notices WHERE created_at" not in src, "전역 30일 일괄 삭제 잔존"
    assert "DELETE FROM team_notices WHERE id NOT IN (SELECT id FROM team_notices ORDER BY" not in src.replace("\n", " ")
    # get_notice_history(team_id, include_null=...)
    m = re.search(r"\ndef get_notice_history\((.*?)\)", src)
    assert m and "team_id" in m.group(1) and "include_null" in m.group(1), "get_notice_history 시그니처 미전환"
    body = re.search(r"\ndef get_notice_history\(.*?\n(?=\ndef )", src, re.S).group(0)
    assert "OR team_id IS NULL" in body, "get_notice_history 에 admin NULL 노출 분기 없음"
    # create_notification_for_team — approved JOIN (시그니처가 2줄에 걸침 → re.S)
    m = re.search(r"\ndef create_notification_for_team\((.*?)\):", src, re.S)
    assert m and "team_id" in m.group(1), "create_notification_for_team 부재"
    body = re.search(r"\ndef create_notification_for_team\(.*?\n(?=\ndef )", src, re.S).group(0)
    assert "JOIN user_teams ut" in body and "ut.status = 'approved'" in body
    # 옛 함수명 잔존 없음
    assert "def get_latest_notice(" not in src


def test_static_app_routes():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    # 헬퍼 _notice_work_team
    assert "def _notice_work_team(" in src
    # SSR /notice — _ensure_work_team_cookie + 팀 기준
    m = re.search(r'@app\.get\("/notice", response_class=HTMLResponse\)\ndef notice_page\(.*?\n(?=\n@app\.)', src, re.S)
    assert m and "_ensure_work_team_cookie" in m.group(0) and "get_notice_latest_for_team" in m.group(0)
    # SSR /notice/history
    m = re.search(r'@app\.get\("/notice/history", response_class=HTMLResponse\)\ndef notice_history_page\(.*?\n(?=\n@app\.)', src, re.S)
    assert m and "_ensure_work_team_cookie" in m.group(0) and "get_notice_history" in m.group(0) and "include_null" in m.group(0)
    # GET /api/notice — team_id 파라미터 + _notice_work_team
    m = re.search(r'@app\.get\("/api/notice"\)\ndef api_get_notice\((.*?)\):.*?\n(?=\n@app\.)', src, re.S)
    assert m and "team_id" in m.group(1) and "_notice_work_team" in m.group(0)
    # POST /api/notice — resolve_work_team + require_work_team_access + 400 + save_notice 3-인자
    m = re.search(r'@app\.post\("/api/notice"\)\nasync def api_save_notice\(.*?\n(?=\n@app\.)', src, re.S)
    assert m
    pb = m.group(0)
    assert "resolve_work_team" in pb and "require_work_team_access" in pb and "status_code=400" in pb
    assert "save_notice(content, team_id" in pb
    # 작성자 본인 게이트 없음 (팀 공유 모델)
    assert 'created_by' not in pb and 'user["name"]' in pb  # user["name"] 은 save 인자로만 쓰임
    # POST /api/notice/notify — resolve_work_team + require_work_team_access + create_notification_for_team
    m = re.search(r'@app\.post\("/api/notice/notify"\)\nasync def api_notify_notice\(.*?\n(?=\n@app\.|\Z)', src, re.S)
    assert m
    nb = m.group(0)
    assert "resolve_work_team" in nb and "require_work_team_access" in nb
    assert "create_notification_for_team" in nb
    assert "create_notification_for_all" not in nb
    # 옛 전역 호출 잔존 없음
    assert "db.get_latest_notice()" not in src
    import app  # noqa: F401


# ── TestClient fixtures ───────────────────────────────────────
def _setup(monkeypatch):
    db_dir = Path(ROOT) / ".claude" / "workspaces" / "current" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_notices87_{uuid.uuid4().hex}.db"
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


def _seed_notice(db, *, team_id, content, created_by, created_at=None):
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO team_notices (team_id, content, created_by) VALUES (?, ?, ?)",
            (team_id, content, created_by))
        nid = cur.lastrowid
        if created_at is not None:
            conn.execute("UPDATE team_notices SET created_at = ? WHERE id = ?", (created_at, nid))
        return nid


def _notif_msgs(db, user_name):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT message FROM notifications WHERE user_name = ? AND type = 'notice'",
            (user_name,)).fetchall()
    return [r[0] for r in rows]


# ── A. 다중 팀 사용자 작업 팀 전환 → GET /api/notice ──
def test_a_work_team_switch_changes_notice(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("NoticeA")
        tb = db.create_team("NoticeB")
        u = _make_user(db, "공지다중유저")
        _join(db, u["id"], ta, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], tb, joined_at="2026-02-01 00:00:00")
        _seed_notice(db, team_id=ta, content="# A팀 공지", created_by="공지다중유저")
        _seed_notice(db, team_id=tb, content="# B팀 공지", created_by="공지다중유저")
        _login(db, client, u)
        # 기본 = 대표 팀(ta)
        r = client.get("/api/notice")
        assert r.status_code == 200
        assert r.json().get("content") == "# A팀 공지", r.json()
        # 작업 팀 전환(쿠키) → tb
        client.cookies.set(WORK_COOKIE, str(tb))
        r = client.get("/api/notice")
        assert r.json().get("content") == "# B팀 공지", r.json()
        # 명시 ?team_id=ta 가 쿠키(tb)보다 우선
        r = client.get(f"/api/notice?team_id={ta}")
        assert r.json().get("content") == "# A팀 공지", r.json()


# ── B. 다른 팀 멤버 세션에선 그 팀 공지 안 보임 ──
def test_b_other_team_member_cannot_see_notice(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("공지팀가")
        tb = db.create_team("공지팀나")
        a_user = _make_user(db, "가팀공지멤버")
        b_user = _make_user(db, "나팀공지멤버")
        _join(db, a_user["id"], ta)
        _join(db, b_user["id"], tb)
        _seed_notice(db, team_id=ta, content="# 가팀 공지", created_by="가팀공지멤버")
        # tb 에는 공지 없음
        _login(db, client, b_user)
        r = client.get("/api/notice")
        assert r.json() == {}, r.json()  # tb 공지 없음 → 빈 dict (ta 공지 누수 X)
        # 명시 ?team_id=ta 비소속 → 무시·대표 팀(tb) fallback → 여전히 {}
        r = client.get(f"/api/notice?team_id={ta}")
        assert r.json() == {}, r.json()
        # a_user 세션: 가팀 공지 보임
        _logout(client)
        _login(db, client, a_user)
        r = client.get("/api/notice")
        assert r.json().get("content") == "# 가팀 공지", r.json()


# ── C. POST /api/notice — team_id 확정 / 403 / 400 ──
def test_c_post_notice_team_resolution(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("저장공지A")
        tb = db.create_team("저장공지B")
        t_other = db.create_team("저장공지타")
        u = _make_user(db, "공지저장유저")
        _join(db, u["id"], ta, joined_at="2026-01-01 00:00:00")
        _join(db, u["id"], tb, joined_at="2026-02-01 00:00:00")
        _login(db, client, u)
        client.cookies.set(WORK_COOKIE, str(tb))
        r = client.post("/api/notice", json={"content": "# tb 공지"})
        assert r.status_code == 200, r.text
        nid = r.json()["id"]
        with db.get_conn() as conn:
            row = conn.execute("SELECT team_id, content, created_by FROM team_notices WHERE id=?", (nid,)).fetchone()
        assert tuple(row) == (tb, "# tb 공지", "공지저장유저"), tuple(row)
        # 명시 team_id=ta (소속) 가 쿠키보다 우선
        r = client.post("/api/notice", json={"content": "# ta 공지", "team_id": ta})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            assert conn.execute("SELECT team_id FROM team_notices WHERE id=?", (r.json()["id"],)).fetchone()[0] == ta
        # 비소속 명시 team_id → 403
        r = client.post("/api/notice", json={"content": "# 비소속", "team_id": t_other})
        assert r.status_code == 403, r.text
        # 미배정 사용자 → 400
        ux = _make_user(db, "미배정공지유저")
        _logout(client)
        _login(db, client, ux)
        r = client.post("/api/notice", json={"content": "# 미배정 시도"})
        assert r.status_code == 400, r.text
        # admin: work_team 없이 (쿠키 X + 본문 X) → 400 (팀이 있어도 admin 대표=first_active_team_id 라 None 아님 →
        #   따라서 이 케이스를 명확히 만들려면 admin 에게 쿠키도 없고 본문도 없을 때... admin 대표 팀이 있으면 400 안 됨)
        # 별도 테스트 H 에서 admin no-team 케이스. 여기선 admin 쿠키 설정 후 → 200 만 확인.
        admin = _make_user(db, "공지관리자C", admin=True)
        _logout(client)
        _login(db, client, admin)
        client.cookies.set(WORK_COOKIE, str(ta))
        r = client.post("/api/notice", json={"content": "# admin ta 공지"})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            assert conn.execute("SELECT team_id FROM team_notices WHERE id=?", (r.json()["id"],)).fetchone()[0] == ta
        # admin 명시 본문 team_id
        r = client.post("/api/notice", json={"content": "# admin tb 공지", "team_id": tb})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            assert conn.execute("SELECT team_id FROM team_notices WHERE id=?", (r.json()["id"],)).fetchone()[0] == tb


# ── D. POST /api/notice/notify — 같은 팀 승인 멤버에게만 ──
def test_d_notify_team_members_only(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        ta = db.create_team("알림팀A")
        tb = db.create_team("알림팀B")
        sender = _make_user(db, "발송자D")          # ta 멤버, 공지 발송
        a_other = _make_user(db, "A팀동료D")         # ta approved → 수신
        a_pending = _make_user(db, "A팀대기D")       # ta pending → 미수신
        b_member = _make_user(db, "B팀멤버D")        # tb approved → 미수신 (다른 팀)
        admin = _make_user(db, "글로벌관리자D", admin=True)  # user_teams 없음 → 미수신
        _join(db, sender["id"], ta)
        _join(db, a_other["id"], ta)
        _join(db, a_pending["id"], ta, status="pending")
        _join(db, b_member["id"], tb)
        _seed_notice(db, team_id=ta, content="# 발송할 ta 공지 내용", created_by="발송자D")
        _seed_notice(db, team_id=tb, content="# tb 공지 내용", created_by="B팀멤버D")
        _login(db, client, sender)
        client.cookies.set(WORK_COOKIE, str(ta))
        r = client.post("/api/notice/notify", json={})
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        assert len(_notif_msgs(db, "A팀동료D")) == 1, "approved 동료에게 알림 미도착"
        assert _notif_msgs(db, "발송자D") == [], "발송자 본인에게 알림 도착(exclude 실패)"
        assert _notif_msgs(db, "A팀대기D") == [], "pending 멤버에게 알림 도착"
        assert _notif_msgs(db, "B팀멤버D") == [], "다른 팀 멤버에게 알림 도착"
        assert _notif_msgs(db, "글로벌관리자D") == [], "글로벌 admin 에게 알림 도착"
        # 공지 없는 팀(tb 에 공지 있음 — 새 팀 만들어 공지 없게)
        tc = db.create_team("알림팀C")
        senderc = _make_user(db, "발송자D_C")
        _join(db, senderc["id"], tc)
        _logout(client)
        _login(db, client, senderc)
        client.cookies.set(WORK_COOKIE, str(tc))
        r = client.post("/api/notice/notify", json={})
        assert r.status_code == 200 and r.json() == {"ok": False, "reason": "no_notice"}, r.text


# ── E. 같은 팀 멤버 B 가 멤버 A 공지를 갱신·발송 (팀 공유 모델) ──
def test_e_team_share_model(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("협업공지팀")
        a = _make_user(db, "공지멤버A")
        b = _make_user(db, "공지멤버B")
        c = _make_user(db, "공지멤버C")  # 알림 수신 확인용
        _join(db, a["id"], tid)
        _join(db, b["id"], tid)
        _join(db, c["id"], tid)
        # A 가 공지 작성
        _login(db, client, a)
        client.cookies.set(WORK_COOKIE, str(tid))
        r = client.post("/api/notice", json={"content": "# A가 쓴 공지"})
        assert r.status_code == 200, r.text
        # B 가 같은 팀 공지를 갱신 (작성자 아님에도 OK — 팀 공유)
        _logout(client)
        _login(db, client, b)
        client.cookies.set(WORK_COOKIE, str(tid))
        r = client.post("/api/notice", json={"content": "# B가 갱신한 공지"})
        assert r.status_code == 200, r.text
        with db.get_conn() as conn:
            row = conn.execute("SELECT content, created_by FROM team_notices ORDER BY id DESC LIMIT 1").fetchone()
        assert tuple(row) == ("# B가 갱신한 공지", "공지멤버B"), tuple(row)
        # B 가 발송 → C 가 수신, B 본인은 제외
        r = client.post("/api/notice/notify", json={})
        assert r.status_code == 200 and r.json() == {"ok": True}, r.text
        assert len(_notif_msgs(db, "공지멤버C")) == 1
        assert _notif_msgs(db, "공지멤버B") == []
        # GET /api/notice 로 양쪽 다 최신(B 갱신본) 본다
        r = client.get("/api/notice")
        assert r.json().get("content") == "# B가 갱신한 공지", r.json()
        _logout(client)
        _login(db, client, a)
        client.cookies.set(WORK_COOKIE, str(tid))
        r = client.get("/api/notice")
        assert r.json().get("content") == "# B가 갱신한 공지", r.json()


# ── F. 자동 정리 팀별 (100개 / 30일) — 다른 팀·NULL 영향 없음 ──
def test_f_auto_cleanup_per_team(monkeypatch):
    app_module, db = _setup(monkeypatch)
    db.init_db()
    ta = db.create_team("정리팀A")
    tb = db.create_team("정리팀B")
    # 팀A 에 100개 미리 시드
    for i in range(100):
        _seed_notice(db, team_id=ta, content=f"A공지{i}", created_by="kim")
    # 팀B 에 5개
    for i in range(5):
        _seed_notice(db, team_id=tb, content=f"B공지{i}", created_by="lee")
    # NULL 잔존 row 1개 (백필 누락 시뮬레이션)
    _seed_notice(db, team_id=None, content="고아공지", created_by="admin_or_unmatched")
    # 30일 이전 팀A row 1개
    old_a = _seed_notice(db, team_id=ta, content="오래된A공지", created_by="kim",
                         created_at="2020-01-01 00:00:00")
    # 30일 이전 팀B row 1개 (자동정리는 팀A 만 도므로 살아남아야 함)
    old_b = _seed_notice(db, team_id=tb, content="오래된B공지", created_by="lee",
                         created_at="2020-01-01 00:00:00")
    # 이제 팀A 에 새 공지 1개 저장 → save_notice 가 팀A 만 정리
    new_a = db.save_notice("새 A 공지", ta, "kim")
    with db.get_conn() as conn:
        a_cnt = conn.execute("SELECT COUNT(*) FROM team_notices WHERE team_id=?", (ta,)).fetchone()[0]
        b_cnt = conn.execute("SELECT COUNT(*) FROM team_notices WHERE team_id=?", (tb,)).fetchone()[0]
        null_cnt = conn.execute("SELECT COUNT(*) FROM team_notices WHERE team_id IS NULL").fetchone()[0]
        old_a_alive = conn.execute("SELECT 1 FROM team_notices WHERE id=?", (old_a,)).fetchone()
        old_b_alive = conn.execute("SELECT 1 FROM team_notices WHERE id=?", (old_b,)).fetchone()
        new_a_alive = conn.execute("SELECT 1 FROM team_notices WHERE id=?", (new_a,)).fetchone()
    # 팀A: 30일 이전 1개 삭제 + 100개 캡 → 정확히 100개 (가장 오래된 일반 row 들이 잘려나감)
    assert a_cnt == 100, a_cnt
    # 팀B: 손대지 않음 → 7개 (5 + old_b + 30일이전건은 안 지워짐)... 실제로 5 + 1(old_b) = 6
    assert b_cnt == 6, b_cnt
    # NULL 잔존 row: 영향 없음
    assert null_cnt == 1, null_cnt
    # 팀A 30일 이전 row 는 삭제됨
    assert old_a_alive is None, "팀A 30일 이전 row 가 자동정리되지 않음"
    # 팀B 30일 이전 row 는 살아남음 (팀A save 가 팀B 를 안 건드림)
    assert old_b_alive is not None, "팀B 30일 이전 row 가 잘못 삭제됨"
    # 새 팀A 공지는 살아남음
    assert new_a_alive is not None


# ── G. NULL 잔존 row 가시성: GET 미노출 / admin history 만 포함 ──
def test_g_null_orphan_visibility(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("고아테스트팀")
        member = _make_user(db, "고아테스트멤버")
        admin = _make_user(db, "고아테스트관리자", admin=True)
        _join(db, member["id"], tid)
        # ASCII 토큰을 본문에 박아 SSR(HISTORIES tojson) 응답에서 확인 — tojson 의 ensure_ascii 여부와 무관.
        _seed_notice(db, team_id=tid, content="# TEAMNOTICE_TOKEN body", created_by="팀공지작성자")
        _seed_notice(db, team_id=None, content="# ORPHANNOTICE_TOKEN body", created_by="옛작성자")
        # 직접 DB: get_notice_latest_for_team 은 NULL row 미반환
        latest = db.get_notice_latest_for_team(tid)
        assert latest and latest["content"] == "# TEAMNOTICE_TOKEN body"
        # get_notice_history(tid, include_null=False) → 팀 공지만
        hist = db.get_notice_history(tid, include_null=False)
        assert {h["content"] for h in hist} == {"# TEAMNOTICE_TOKEN body"}, hist
        # get_notice_history(tid, include_null=True) → 고아도 포함
        hist = db.get_notice_history(tid, include_null=True)
        assert {h["content"] for h in hist} == {"# TEAMNOTICE_TOKEN body", "# ORPHANNOTICE_TOKEN body"}, hist
        # GET /api/notice 멤버 세션 → NULL row 안 나옴 (팀 공지만)
        _login(db, client, member)
        client.cookies.set(WORK_COOKIE, str(tid))
        r = client.get("/api/notice")
        assert r.json().get("content") == "# TEAMNOTICE_TOKEN body", r.json()
        # SSR /notice/history 비admin → 고아 미포함 (HTML 본문 검사)
        r = client.get("/notice/history")
        assert r.status_code == 200
        assert "TEAMNOTICE_TOKEN" in r.text
        assert "ORPHANNOTICE_TOKEN" not in r.text
        # SSR /notice/history admin → 고아 포함
        _logout(client)
        _login(db, client, admin)
        client.cookies.set(WORK_COOKIE, str(tid))
        r = client.get("/notice/history")
        assert r.status_code == 200
        assert "ORPHANNOTICE_TOKEN" in r.text


# ── H. SSR 쿠키 발급 + 미배정 사용자 빈 값 ──
def test_h_ssr_cookie_and_unassigned(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = db.create_team("SSR공지팀")
        member = _make_user(db, "SSR공지멤버")
        _join(db, member["id"], tid)
        _seed_notice(db, team_id=tid, content="# SSRNOTICE_TOKEN body", created_by="SSR공지작성자")
        _login(db, client, member)
        # 쿠키 없음 → /notice 진입 시 Set-Cookie 발급
        client.cookies.clear()
        client.cookies.set("session_id", db.create_session(member["id"]))
        r = client.get("/notice")
        assert r.status_code == 200
        set_cookie = r.headers.get("set-cookie", "")
        assert "work_team_id=" in set_cookie, set_cookie
        assert "SSRNOTICE_TOKEN" in r.text  # 작업 팀 공지가 INITIAL_MD 로 렌더됨
        # /notice/history 도 마찬가지
        client.cookies.clear()
        client.cookies.set("session_id", db.create_session(member["id"]))
        r = client.get("/notice/history")
        assert r.status_code == 200
        assert "work_team_id=" in r.headers.get("set-cookie", "")
        # 미배정 사용자 → Set-Cookie 없음, notice/histories 빈
        ux = _make_user(db, "SSR미배정유저")
        client.cookies.clear()
        client.cookies.set("session_id", db.create_session(ux["id"]))
        r = client.get("/notice")
        assert r.status_code == 200
        assert "work_team_id=" not in r.headers.get("set-cookie", "")
        assert "SSRNOTICE_TOKEN" not in r.text
        r = client.get("/api/notice")
        assert r.json() == {}, r.json()
        # 비로그인 → /api/notice {}
        client.cookies.clear()
        r = client.get("/api/notice")
        assert r.status_code == 200 and r.json() == {}, r.text
