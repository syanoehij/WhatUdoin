"""팀 기능 그룹 B #14: `/팀이름` 로그인 사용자 (소속 무관) — 공개 포털 버튼 분기.

#13 에서 `/{team_name}` 동적 라우트 + `templates/team_portal.html` 공개 포털이 생겼고,
로그인 사용자도 200 공개 포털을 받되 redirect 안 함까지 구현됐다. #14 는 그 위에
계획서 섹션 7 "`/팀이름` 공개 포털에서의 계정/팀 신청 버튼 상태" 표를 구현한다:

  | 접근 상태                       | 버튼            |
  |---------------------------------|-----------------|
  | 비로그인                        | "계정 가입"      |
  | 로그인, 해당 팀 미소속/rejected | "팀 신청"        |
  | 로그인, 해당 팀 pending         | "가입 대기 중"(disabled) |
  | 로그인, 해당 팀 approved        | 버튼 없음        |
  | 로그인, admin                   | 버튼 없음 (슈퍼유저 — 표에 admin 행 없음, 본 구현의 결정) |

공통: 로그인이든 admin이든 `/팀이름` → 항상 200 공개 포털, redirect(30x) 금지. 홈 버튼은 `/`.

검증 (TestClient + 임시 DB — 운영 서버는 IP 자동 로그인이라 특정 사용자 상태 브라우저 재현 불가):
  1. 정적: app.py `team_public_portal` 가 `my_team_status` 컨텍스트를 넘긴다 + redirect 안 함
  2. 정적: templates/team_portal.html — my_team_status 분기 + admin 분기(user.role) + applyToTeam JS + #14 주석
  3. 미소속 로그인 사용자, 신청 이력 없음 → /ABC 200, "팀 신청"(applyToTeam) 노출, "가입 대기 중"·"계정 가입" 부재
  4. 해당 팀 pending → /ABC 200, "가입 대기 중" + disabled 노출, "팀 신청" 부재
  5. 다른 팀 pending (해당 팀은 미소속) → /ABC 200, "팀 신청" 노출 (서버가 클릭 시 차단 — UI 관심사 아님)
  6. 해당 팀 approved 멤버 → /ABC 200, "팀 신청"·"가입 대기 중"·"계정 가입" 모두 부재
  7. 해당 팀 rejected → /ABC 200, "팀 신청" 재노출 (재신청 가능)
  8. admin → /ABC 200 (30x 아님), "팀 신청"·"가입 대기 중" 부재, 포털 본문 정상
  9. 모든 케이스: status 200, redirect 없음, 홈 버튼 href="/" 존재
 10. 비로그인 → /ABC 200, "계정 가입" 그대로 (#13 회귀)
 11. import app OK

서버 재시작 불필요 — 임시 DB로 격리 실행한다.
"""
import os
import sys
import uuid
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── 1·2. 정적 소스 invariant ──────────────────────────────────
def test_route_passes_my_team_status_no_redirect():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    import re
    # 그룹 D catchup: team_public_portal 본문은 공통 헬퍼 _render_team_menu 로 위임됨.
    # 핵심 컨텍스트(my_team_status, is_admin, user_team_ids, get_my_team_statuses)는 헬퍼 안에.
    m = re.search(r'def _render_team_menu\(.*?\):(.*?)\n(?:@app\.|def )', src, re.S)
    assert m, "_render_team_menu 본문 추출 실패"
    body = m.group(1)
    assert "my_team_status" in body, "헬퍼가 my_team_status 컨텍스트를 넘기지 않음"
    # 로그인/ admin 분기 사용
    assert "is_admin" in body and "user_team_ids" in body and "get_my_team_statuses" in body
    # redirect 절대 금지 — RedirectResponse 호출 없음 (헬퍼·라우트 모두)
    assert "RedirectResponse" not in body, "_render_team_menu 가 redirect 한다 (#14 위반)"
    # team_public_portal 자체에도 RedirectResponse 없음
    mr = re.search(r'def team_public_portal\(.*?\):(.*?)\n(?:@app\.|if __name__|def )', src, re.S)
    assert mr and "RedirectResponse" not in mr.group(1), "team_public_portal 가 redirect 한다 (#14 위반)"
    import app  # noqa: F401  (11. import OK)


def test_template_button_branches():
    html = (Path(ROOT) / "templates" / "team_portal.html").read_text(encoding="utf-8")
    # my_team_status 분기 3종
    assert "my_team_status == 'approved'" in html
    assert "my_team_status == 'pending'" in html
    # admin 분기 — else 로 안 떨어지게 user.role == 'admin'
    assert "user.role == 'admin'" in html
    # "팀 신청" 버튼 + applyToTeam
    assert "팀 신청" in html and "applyToTeam(" in html
    # "가입 대기 중" disabled
    assert "가입 대기 중" in html and "disabled" in html
    # applyToTeam JS 함수 정의 + #14 주석
    assert "async function applyToTeam" in html
    assert "/api/me/team-applications" in html
    assert "#14" in html
    # #13 이 남긴 "#14 범위 ... 구현하지 않는다" 미루기 주석은 제거됐어야 함
    assert "구현하지 않는다" not in html


# ── 3~10. TestClient 동작 검증 (임시 DB) ───────────────────────
def _setup_app_with_temp_db(monkeypatch):
    db_dir = Path(ROOT) / ".claude" / "workspaces" / "current" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_portal14_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("WHATUDOIN_BASE_DIR", ROOT)
    monkeypatch.setenv("WHATUDOIN_RUN_DIR", ROOT)
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    import app as app_module
    return app_module, db


def _seed_team(db, name="ABC"):
    """공개 데이터가 약간 있는 팀 1개 → team_id."""
    tid = db.create_team(name)
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (team_id, name, name_norm, is_active, is_hidden, is_private) "
                     "VALUES (?, 'PubProj', 'pubproj', 1, 0, 0)", (tid,))
        conn.execute("INSERT INTO events (title, start_datetime, team_id, project, is_public, is_active, kanban_status, event_type) "
                     "VALUES ('PUBLIC_EVENT', '2026-06-01T09:00:00', ?, 'PubProj', 1, 1, 'todo', 'schedule')", (tid,))
    return tid


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


def _set_membership(db, user_id, team_id, status):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO user_teams (user_id, team_id, role, status) VALUES (?, ?, 'member', ?)",
                     (user_id, team_id, status))
        if status == "approved":
            conn.execute("UPDATE user_teams SET joined_at = CURRENT_TIMESTAMP WHERE user_id = ? AND team_id = ?",
                         (user_id, team_id))


def _assert_portal_ok(r):
    assert r.status_code == 200, r.status_code
    assert "공개 포털 — 공개 설정된 항목만" in r.text
    assert 'href="/" class="btn btn-sm btn-outline">홈' in r.text, "홈 버튼(href=/) 누락"


_APPLY_BTN = 'onclick="applyToTeam('
_PENDING_BTN = '<button class="btn btn-sm" disabled>가입 대기 중</button>'
_REGISTER_BTN = 'btn-primary">계정 가입'


def test_unassigned_no_application_shows_apply(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team(db, "ABC")
        u = _make_user(db, "미소속유저")
        _login(db, client, u)
        r = client.get("/ABC", follow_redirects=False)
        _assert_portal_ok(r)
        assert _APPLY_BTN in r.text, "미소속 로그인 사용자에게 '팀 신청' 버튼이 없음"
        assert _PENDING_BTN not in r.text
        assert _REGISTER_BTN not in r.text, "로그인 사용자에게 '계정 가입' 버튼이 노출됨"


def test_pending_this_team_shows_waiting(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = _seed_team(db, "ABC")
        u = _make_user(db, "대기유저")
        _set_membership(db, u["id"], tid, "pending")
        _login(db, client, u)
        r = client.get("/ABC", follow_redirects=False)
        _assert_portal_ok(r)
        assert _PENDING_BTN in r.text, "이 팀 pending 사용자에게 '가입 대기 중'(disabled) 버튼이 없음"
        assert _APPLY_BTN not in r.text, "이 팀 pending 사용자에게 '팀 신청' 버튼이 노출됨"
        assert _REGISTER_BTN not in r.text


def test_pending_other_team_shows_apply_here(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team(db, "ABC")
        other_tid = db.create_team("OtherTeam")
        u = _make_user(db, "다른팀대기유저")
        _set_membership(db, u["id"], other_tid, "pending")  # 다른 팀에만 pending
        _login(db, client, u)
        r = client.get("/ABC", follow_redirects=False)
        _assert_portal_ok(r)
        # ABC 에는 미소속 → "팀 신청" 노출 (서버가 클릭 시 pending_other 차단 — UI 관심사 아님)
        assert _APPLY_BTN in r.text
        assert _PENDING_BTN not in r.text


def test_approved_member_no_join_button(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = _seed_team(db, "ABC")
        u = _make_user(db, "승인유저")
        _set_membership(db, u["id"], tid, "approved")
        _login(db, client, u)
        r = client.get("/ABC", follow_redirects=False)
        _assert_portal_ok(r)
        assert _APPLY_BTN not in r.text, "approved 멤버에게 '팀 신청' 버튼이 노출됨"
        assert _PENDING_BTN not in r.text, "approved 멤버에게 '가입 대기 중' 버튼이 노출됨"
        assert _REGISTER_BTN not in r.text


def test_rejected_shows_reapply(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = _seed_team(db, "ABC")
        u = _make_user(db, "거절유저")
        _set_membership(db, u["id"], tid, "rejected")
        _login(db, client, u)
        r = client.get("/ABC", follow_redirects=False)
        _assert_portal_ok(r)
        assert _APPLY_BTN in r.text, "rejected 사용자에게 '팀 신청'(재신청) 버튼이 없음"
        assert _PENDING_BTN not in r.text


def test_admin_no_join_button_no_redirect(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team(db, "ABC")
        u = _make_user(db, "관리자유저", admin=True)
        _login(db, client, u)
        r = client.get("/ABC", follow_redirects=False)
        # 200 포털, redirect(30x) 금지
        assert r.status_code == 200, f"admin /ABC → {r.status_code} (redirect 금지)"
        _assert_portal_ok(r)
        assert _APPLY_BTN not in r.text, "admin 에게 '팀 신청' 버튼이 노출됨 (#14 결정: 버튼 없음)"
        assert _PENDING_BTN not in r.text
        assert _REGISTER_BTN not in r.text
        # 그룹 D catchup: 별도 .portal-tabs 영역 제거. 포털 본문은 active_menu 단일 패널.
        # _assert_portal_ok 가 "공개 포털 — 공개 설정된 항목만" 문구로 본문 정상 검증 완료.
        assert "공개 포털 — 공개 설정된 항목만" in r.text


def test_anon_still_shows_register(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team(db, "ABC")
        r = client.get("/ABC", follow_redirects=False)  # 쿠키 없음 = 익명
        _assert_portal_ok(r)
        assert _REGISTER_BTN in r.text, "비로그인 사용자에게 '계정 가입' 버튼이 없음 (#13 회귀)"
        assert _APPLY_BTN not in r.text
        assert _PENDING_BTN not in r.text


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
