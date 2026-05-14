"""팀 기능 그룹 B #15-1: 히든 프로젝트 다중 팀 전환.

계획서 §12 (히든 프로젝트) + §8-1 (히든 프로젝트 추가 예외). 그룹 A #5 에서
`create_hidden_project(team_id=...)` 시그니처 + `(team_id, name_norm)` 팀 제한 중복 검사가
들어갔고, #15-1 은 owner 의 `users.team_id` 단일 비교를 마저 제거하고 멤버 후보·가시성·이양
헬퍼를 `user_teams.status='approved'` + `projects.team_id` 기준으로 옮긴다.

검증 (TestClient/직접 DB + 임시 DB — 운영 서버는 IP 자동 로그인이라 다중 팀 owner 시나리오
브라우저 재현 불가):
  - 정적: database.py 에 히든 프로젝트 함수 한정 `u.team_id = p.team_id` 잔존 0건,
    `create_hidden_project` users.team_id fallback 제거, 6개 함수에 user_teams approved EXISTS,
    `add_hidden_project_member` 2-인자, app.py 라우트 호출부 갱신.
  - A. owner 추방 → 같은 팀 활성 멤버에게 added_at 오름차순 자동 이양.
  - B. 후보 없으면 owner_id = NULL → admin 이 같은 팀 승인 멤버 추가 → 그 멤버를 owner 지정 가능.
  - C. admin 은 멤버 후보(addable_members)·assignee 후보에서 자동 제외.
  - D. 다중 팀 owner: projects.team_id 팀 기준으로만 멤버 후보 조회 (다른 팀 멤버 안 보임).
  - E. 멀티팀 가시성: project_members row + user_teams approved → 보임; team 에서 빠지면 안 보임;
       다시 approved 면 보임 (project_members row 는 그대로).
  - F. owner_id = NULL 이어도 addable_members 가 projects.team_id 기준 후보 반환 (빈 리스트 X).
  - G. create_hidden_project(team_id=None) → ValueError; 라우트는 team_id 기준 저장.
  - 회귀: import app OK.

서버 재시작 불필요 — 임시 DB로 격리 실행.
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


# ── 정적 invariant ────────────────────────────────────────────
_HIDDEN_FUNCS = (
    "create_hidden_project", "_hidden_project_visible_row", "get_hidden_project_members",
    "get_hidden_project_addable_members", "add_hidden_project_member",
    "transfer_hidden_project_owner", "admin_change_hidden_project_owner",
    "transfer_hidden_projects_on_removal",
)


def _func_body(src, name):
    """database.py 소스에서 `def name(...)` 본문(다음 def 직전까지)을 잘라낸다."""
    m = re.search(rf"\ndef {re.escape(name)}\(.*?\n(?=\ndef )", src, re.S)
    if m is None:
        # 마지막 함수일 수 있으니 끝까지
        m = re.search(rf"\ndef {re.escape(name)}\(.*", src, re.S)
    assert m, f"database.py 에서 def {name} 를 찾지 못함"
    return m.group(0)


def test_static_no_legacy_team_id_join():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    # 히든 프로젝트 관련 함수 한정으로 `u.team_id = p.team_id` 잔존이 없어야 한다.
    for fn in _HIDDEN_FUNCS:
        body = _func_body(src, fn)
        assert "u.team_id = p.team_id" not in body, f"{fn} 에 legacy u.team_id = p.team_id 잔존"
        assert "owner_row" not in body or fn == "create_hidden_project", \
            f"{fn} 에 owner 의 users.team_id 참조(owner_row) 잔존"


def test_static_create_hidden_project_no_users_teamid_fallback():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    body = _func_body(src, "create_hidden_project")
    assert "SELECT team_id FROM users WHERE id = ?" not in body, \
        "create_hidden_project 가 아직 users.team_id fallback 을 함"
    assert "ValueError" in body, "create_hidden_project 가 team_id None 시 ValueError 안 함"


def test_static_user_teams_approved_exists():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    for fn in ("_hidden_project_visible_row", "get_hidden_project_addable_members",
               "transfer_hidden_project_owner", "admin_change_hidden_project_owner",
               "transfer_hidden_projects_on_removal"):
        body = _func_body(src, fn)
        assert "user_teams" in body and "status = 'approved'" in body, \
            f"{fn} 가 user_teams approved 기준으로 전환되지 않음"
    # add_hidden_project_member 는 EXISTS 대신 직접 SELECT 도 허용 — user_teams + approved 만 확인
    amb = _func_body(src, "add_hidden_project_member")
    assert "user_teams" in amb and "status = 'approved'" in amb
    assert "owner_id" not in amb.split("\n")[0], "add_hidden_project_member 가 아직 owner_id 인자를 받음"


def test_static_app_route_call_updated():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    assert "db.add_hidden_project_member(proj[\"id\"], target_user_id)" in src, \
        "app.py 라우트가 add_hidden_project_member 를 2-인자로 호출하지 않음"
    assert "db.add_hidden_project_member(proj[\"id\"], target_user_id, proj[\"owner_id\"])" not in src
    import app  # noqa: F401


# ── DB fixtures ───────────────────────────────────────────────
# P4-1 catchup: 임시 DB 를 tempfile 시스템 폴더로 격리 + atexit 으로 항상 cleanup.
# 기존에는 ROOT 에 _phase85_{uuid}.db 가 누적되어 14개 잔재 → 루트 가시성 손상.
def _setup(monkeypatch):
    import atexit
    import tempfile
    tmp = tempfile.NamedTemporaryFile(prefix="phase85_", suffix=".db", delete=False)
    tmp.close()
    db_path = Path(tmp.name)

    def _cleanup():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass

    # atexit: pytest 프로세스 종료 시 항상 호출 → 함수형 fixture 라도 누적 방지.
    atexit.register(_cleanup)

    monkeypatch.setenv("WHATUDOIN_BASE_DIR", ROOT)
    monkeypatch.setenv("WHATUDOIN_RUN_DIR", ROOT)
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.init_db()
    return db, db_path


def _make_user(db, name, *, admin=False):
    u = db.create_user_account(name, "pw1234")
    assert u, f"create_user_account({name}) 실패"
    if admin:
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (u["id"],))
        u = dict(u); u["role"] = "admin"
    return u


def _join(db, user_id, team_id, status="approved"):
    with db.get_conn() as conn:
        conn.execute("INSERT INTO user_teams (user_id, team_id, role, status) VALUES (?, ?, 'member', ?)",
                     (user_id, team_id, status))


def _set_member_added_at(db, project_id, user_id, added_at):
    with db.get_conn() as conn:
        conn.execute("UPDATE project_members SET added_at = ? WHERE project_id = ? AND user_id = ?",
                     (added_at, project_id, user_id))


def _user_dict(db, user_id):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row)


# ── A. owner 추방 → 같은 팀 활성 멤버 자동 이양 ────────────────
def test_a_transfer_on_removal_picks_oldest_member(monkeypatch):
    db, _ = _setup(monkeypatch)
    tid = db.create_team("HwDev")
    owner = _make_user(db, "오너")
    m_old = _make_user(db, "선임멤버")
    m_new = _make_user(db, "후임멤버")
    for uid in (owner["id"], m_old["id"], m_new["id"]):
        _join(db, uid, tid)
    proj = db.create_hidden_project("비밀과제", "#fff", "메모", owner["id"], team_id=tid)
    assert proj and proj["owner_id"] == owner["id"]
    pid = proj["id"]
    # 멤버 추가 (후임 → 선임 순으로 추가하되 added_at 을 선임이 더 오래되게 조정)
    assert db.add_hidden_project_member(pid, m_new["id"]) is True
    assert db.add_hidden_project_member(pid, m_old["id"]) is True
    _set_member_added_at(db, pid, m_old["id"], "2026-01-01 00:00:00")
    _set_member_added_at(db, pid, m_new["id"], "2026-03-01 00:00:00")
    # owner 추방 시뮬레이션
    owned = db.get_user_owned_hidden_projects(owner["id"])
    assert [p["id"] for p in owned] == [pid]
    db.transfer_hidden_projects_on_removal(owner["id"], owned)
    with db.get_conn() as conn:
        new_owner = conn.execute("SELECT owner_id FROM projects WHERE id = ?", (pid,)).fetchone()[0]
        still = conn.execute("SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                             (pid, owner["id"])).fetchone()
    assert new_owner == m_old["id"], "added_at 오름차순 최선두(선임)에게 이양되어야 함"
    assert still is None, "추방된 owner 는 project_members 에서 제거되어야 함"


# ── B. 후보 없음 → owner_id NULL → admin 이 신규 멤버 추가 후 owner 지정 ──
def test_b_owner_null_then_admin_recovery(monkeypatch):
    db, _ = _setup(monkeypatch)
    tid = db.create_team("SwDev")
    owner = _make_user(db, "단독오너")
    _join(db, owner["id"], tid)
    _make_user(db, "글로벌관리자", admin=True)  # admin 은 user_teams row 없음 (자연)
    new_member = _make_user(db, "복구멤버")
    _join(db, new_member["id"], tid)
    proj = db.create_hidden_project("고립과제", "#abc", None, owner["id"], team_id=tid)
    pid = proj["id"]
    # owner 추방 → 후보 없음 → owner_id NULL
    owned = db.get_user_owned_hidden_projects(owner["id"])
    db.transfer_hidden_projects_on_removal(owner["id"], owned)
    with db.get_conn() as conn:
        oid = conn.execute("SELECT owner_id FROM projects WHERE id = ?", (pid,)).fetchone()[0]
    assert oid is None, "후보 없으면 owner_id 가 NULL 이어야 함"
    # admin(owner 부재 케이스)이 같은 팀 승인 멤버 추가 — owner 참조 없이 projects.team_id 기준
    assert db.add_hidden_project_member(pid, new_member["id"]) is True
    # 그 멤버를 owner 로 지정
    assert db.admin_change_hidden_project_owner(pid, new_member["id"]) is True
    with db.get_conn() as conn:
        oid2 = conn.execute("SELECT owner_id FROM projects WHERE id = ?", (pid,)).fetchone()[0]
    assert oid2 == new_member["id"]


# ── C. admin 은 멤버 후보·assignee 후보에서 자동 제외 ───────────
def test_c_admin_excluded_from_candidates(monkeypatch):
    db, _ = _setup(monkeypatch)
    tid = db.create_team("DesignT")
    owner = _make_user(db, "디자인오너")
    member = _make_user(db, "디자인멤버")
    admin = _make_user(db, "관리자C", admin=True)
    for uid in (owner["id"], member["id"]):
        _join(db, uid, tid)
    # admin 도 (비정상이지만) user_teams approved row 를 가졌다고 가정 → 그래도 후보에서 제외돼야
    _join(db, admin["id"], tid)
    proj = db.create_hidden_project("디자인히든", "#0f0", None, owner["id"], team_id=tid)
    pid = proj["id"]
    addable_ids = {u["id"] for u in db.get_hidden_project_addable_members(pid)}
    assert admin["id"] not in addable_ids, "admin 은 멤버 후보에서 제외돼야 함"
    assert member["id"] in addable_ids
    # assignee 후보 = get_hidden_project_members 결과 → admin 은 멤버가 아니므로 자연 제외
    member_names = {m["name"] for m in db.get_hidden_project_members(pid)}
    assert admin["name"] not in member_names
    # add_hidden_project_member 로 admin 추가 시도 → role == 'admin' → False
    assert db.add_hidden_project_member(pid, admin["id"]) is False


# ── D. 다중 팀 owner — projects.team_id 기준으로만 멤버 후보 ────
def test_d_multiteam_owner_candidate_scope(monkeypatch):
    db, _ = _setup(monkeypatch)
    ta = db.create_team("TeamA")
    tb = db.create_team("TeamB")
    owner = _make_user(db, "양다리오너")
    _join(db, owner["id"], ta)
    _join(db, owner["id"], tb)              # owner 는 팀 A·B 둘 다 approved
    a_member = _make_user(db, "팀A멤버")
    _join(db, a_member["id"], ta)
    b_member = _make_user(db, "팀B멤버")
    _join(db, b_member["id"], tb)
    proj = db.create_hidden_project("팀A히든", "#00f", None, owner["id"], team_id=ta)
    pid = proj["id"]
    addable_ids = {u["id"] for u in db.get_hidden_project_addable_members(pid)}
    assert a_member["id"] in addable_ids, "팀 A 멤버는 후보에 포함"
    assert b_member["id"] not in addable_ids, "팀 B 멤버는 (owner 가 팀B 소속이어도) 후보에서 제외"
    # 팀 B 멤버 추가 시도 → projects.team_id(=A) 승인 멤버 아님 → False
    assert db.add_hidden_project_member(pid, b_member["id"]) is False
    assert db.add_hidden_project_member(pid, a_member["id"]) is True


# ── E. 멀티팀 가시성: project_members + user_teams approved ────
def test_e_visibility_follows_user_teams(monkeypatch):
    db, _ = _setup(monkeypatch)
    ta = db.create_team("VisT")
    owner = _make_user(db, "가시성오너")
    viewer = _make_user(db, "가시성멤버")
    _join(db, owner["id"], ta)
    _join(db, viewer["id"], ta)
    proj = db.create_hidden_project("가시성히든", "#f0f", None, owner["id"], team_id=ta)
    pid = proj["id"]
    assert db.add_hidden_project_member(pid, viewer["id"]) is True
    assert db.is_hidden_project_visible(pid, _user_dict(db, viewer["id"])) is True
    # viewer 를 팀에서 제거 (user_teams row 삭제) — project_members row 는 그대로
    with db.get_conn() as conn:
        conn.execute("DELETE FROM user_teams WHERE user_id = ? AND team_id = ?", (viewer["id"], ta))
        assert conn.execute("SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?",
                            (pid, viewer["id"])).fetchone() is not None
    assert db.is_hidden_project_visible(pid, _user_dict(db, viewer["id"])) is False, \
        "팀에서 빠지면 project_members row 가 남아도 히든 프로젝트가 안 보여야 함"
    # 재가입(approved) → 다시 보임
    _join(db, viewer["id"], ta)
    assert db.is_hidden_project_visible(pid, _user_dict(db, viewer["id"])) is True
    # pending 상태는 안 보임
    with db.get_conn() as conn:
        conn.execute("UPDATE user_teams SET status = 'pending' WHERE user_id = ? AND team_id = ?",
                     (viewer["id"], ta))
    assert db.is_hidden_project_visible(pid, _user_dict(db, viewer["id"])) is False
    # admin 은 항상 보임
    admin = _make_user(db, "관리자E", admin=True)
    assert db.is_hidden_project_visible(pid, admin) is True


# ── F. owner_id NULL 이어도 addable_members 가 projects.team_id 기준 ──
def test_f_addable_members_when_owner_null(monkeypatch):
    db, _ = _setup(monkeypatch)
    ta = db.create_team("RecoT")
    owner = _make_user(db, "F오너")
    _join(db, owner["id"], ta)
    cand1 = _make_user(db, "F후보1")
    cand2 = _make_user(db, "F후보2")
    _join(db, cand1["id"], ta)
    _join(db, cand2["id"], ta)
    proj = db.create_hidden_project("F히든", "#999", None, owner["id"], team_id=ta)
    pid = proj["id"]
    # owner 추방 → owner_id NULL (멤버가 owner 1명뿐). 단 이 테스트는 user_teams 는 건드리지
    # 않으므로(=팀에서 제거된 게 아니라 project_members 에서만 제거) 전 owner 도 후보로 남는다.
    db.transfer_hidden_projects_on_removal(owner["id"], db.get_user_owned_hidden_projects(owner["id"]))
    with db.get_conn() as conn:
        assert conn.execute("SELECT owner_id FROM projects WHERE id = ?", (pid,)).fetchone()[0] is None
    addable_ids = {u["id"] for u in db.get_hidden_project_addable_members(pid)}
    # 핵심: owner_id 가 NULL 이어도 빈 리스트가 아니라 projects.team_id 기준 후보가 나와야 함.
    assert addable_ids == {owner["id"], cand1["id"], cand2["id"]}, \
        "owner_id NULL 이어도 projects.team_id 기준 후보가 나와야 함 (빈 리스트 X)"
    # 실제 추방(user_teams 에서도 제거) 시나리오: 전 owner 는 후보에서도 빠진다.
    with db.get_conn() as conn:
        conn.execute("DELETE FROM user_teams WHERE user_id = ? AND team_id = ?", (owner["id"], ta))
    addable_ids2 = {u["id"] for u in db.get_hidden_project_addable_members(pid)}
    assert addable_ids2 == {cand1["id"], cand2["id"]}


# ── G. create_hidden_project team_id 필수 ──────────────────────
def test_g_create_requires_team_id(monkeypatch):
    db, _ = _setup(monkeypatch)
    owner = _make_user(db, "G오너")
    with pytest.raises(ValueError):
        db.create_hidden_project("팀없는히든", "#fff", None, owner["id"], team_id=None)
    # 정상: team_id 기준 저장
    tid = db.create_team("GTeam")
    _join(db, owner["id"], tid)
    proj = db.create_hidden_project("G정상히든", "#fff", None, owner["id"], team_id=tid)
    assert proj and proj["team_id"] == tid
    with db.get_conn() as conn:
        stored = conn.execute("SELECT team_id, is_hidden, owner_id FROM projects WHERE id = ?",
                              (proj["id"],)).fetchone()
    assert tuple(stored) == (tid, 1, owner["id"])
    # 같은 팀 동일 이름 중복 → None
    assert db.create_hidden_project("G정상히든", "#000", None, owner["id"], team_id=tid) is None
    # 다른 팀에는 같은 이름 허용
    tid2 = db.create_team("GTeam2")
    assert db.create_hidden_project("G정상히든", "#000", None, owner["id"], team_id=tid2) is not None
