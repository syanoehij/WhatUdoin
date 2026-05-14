"""팀 기능 그룹 B #13: `/팀이름` 비로그인 공개 포털.

검증 항목 (TestClient + 임시 DB — 운영 서버는 IP 자동 로그인이라 비로그인 브라우저 재현 불가,
TestClient 기본 IP `testclient` 는 whitelist 미매칭이라 익명 요청으로 직접 검증 가능):

  정적 invariant:
    1. app.py — `/{team_name}` 동적 라우트가 모든 정적 페이지 라우트보다 *뒤*에 등록
    2. database.py — get_team_by_name_exact / get_team_menu_visibility / get_public_portal_data 존재,
       마이그레이션 PHASES 추가 없음
    3. templates/team_portal.html 존재 + 삭제 예정 분기 + 비로그인 계정 가입 버튼 조건

  TestClient 동작:
    4. `GET /ABC`(대문자 팀) → 200, 팀 이름·"계정 가입" 포함
    5. `GET /abc` → 404 (대소문자 정확 일치 분리)
    6. `GET /Nonexistent` → 404
    7. `GET /admin` → admin 로그인 페이지 (eclipse 안 됨)
    8. `GET /api/health` → 200
    9. `GET /docs` /redoc /openapi.json → 200 (FastAPI 자동 문서 우선)
   10. `GET /static/<존재 파일>` → 404 아님 (mount 살아 있음)
   11. 예약어 각각 `GET /<예약어>` → 포털 아님 (404 또는 정적 라우트 응답)
   12. `GET /Bad-Name` (정규식 불일치 — 하이픈) → 404
   13. 공개 포털: is_public=0 일정·체크·문서가 응답 마크업에 안 나옴
   14. 공개 포털: 히든 프로젝트(is_hidden=1) 하위 항목은 is_public=1 이어도 안 나옴
   15. 삭제 예정 팀: 안내 페이지, "계정 가입" 버튼 없음, 공개 데이터 없음
   16. 로그인 사용자가 `GET /팀이름` → 200 포털 (redirect 안 됨)

서버 재시작 불필요 — 임시 DB로 격리 실행한다.
"""
import os
import re
import sys
import uuid
from pathlib import Path

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── 1·2·3. 정적 소스 invariant ─────────────────────────────────
def test_dynamic_route_registered_last():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    # /{team_name} 라우트가 정적 페이지 라우트들보다 뒤에 등록되어야 한다.
    idx_dyn = src.find('@app.get("/{team_name}"')
    assert idx_dyn != -1, "/{team_name} 라우트 누락"
    for static_path in ('"/"', '"/calendar"', '"/admin"', '"/kanban"', '"/gantt"', '"/doc"',
                        '"/check"', '"/notice"', '"/trash"', '"/remote"', '"/avr"'):
        idx_static = src.find('@app.get(' + static_path)
        assert idx_static != -1, f"정적 라우트 {static_path} 누락"
        assert idx_static < idx_dyn, f"{static_path} 가 /{{team_name}} 보다 뒤에 등록됨 (eclipse 위험)"
    # 그룹 D catchup: 정규식 검사 + casefold 예약어 비교는 공통 헬퍼 `_render_team_menu` 로 이동.
    # team_public_portal 은 헬퍼에 위임만 한다 — 헬퍼 본문에 검증 코드가 있어야 함.
    m = re.search(r'def _render_team_menu\(.*?\):(.*?)\n(?:@app\.|def )', src, re.S)
    assert m, "_render_team_menu 본문 추출 실패"
    body = m.group(1)
    assert "_TEAM_NAME_RE" in body and "casefold" in body and "RESERVED_TEAM_PATHS" in body
    assert "404" in body
    assert "get_team_by_name_exact" in body
    assert "deleted_at" in body or "deleted" in body


def test_db_helpers_exist_no_new_phases():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    for fn in ("def get_team_by_name_exact(", "def get_team_menu_visibility(", "def get_public_portal_data("):
        assert fn in src, f"{fn} 누락"
    # get_team_by_name_exact 는 대소문자 정확 일치 (name = ?) — 함수 본문 안에 있어야 한다.
    m = re.search(r"def get_team_by_name_exact\(.*?\n(.*?)(?=\ndef |\n# )", src, re.S)
    assert m and "WHERE name = ?" in m.group(1), "get_team_by_name_exact 가 name = ? 정확 일치를 안 함"
    # 마이그레이션 phase 추가 없음 — #13 관련 새 phase 마커 부재.
    assert "team_phase_13" not in src and "public_portal_v1" not in src, \
        "#13 관련 새 마이그레이션 phase가 추가됨 (스키마 무변경이어야 함)"


def test_team_portal_template():
    src = (Path(ROOT) / "templates" / "team_portal.html").read_text(encoding="utf-8")
    assert "{% if deleted %}" in src, "삭제 예정 분기 누락"
    assert "{% if not user %}" in src and 'href="/register"' in src, "비로그인 계정 가입 버튼 조건 누락"
    # 그룹 D catchup: portal.menu 토글은 백엔드 _render_team_menu 가 active_menu 로 사전 결정.
    # 템플릿 마커는 active_menu 분기로 갱신됨.
    assert "active_menu" in src, "active_menu 분기 사용 누락"
    # #14 자리 표시 주석
    assert "#14" in src, "로그인 사용자 UI 분기를 #14 로 미룬다는 주석 누락"


# ── 4~16. TestClient 동작 검증 (임시 DB) ──────────────────────
def _setup_app_with_temp_db(monkeypatch):
    db_dir = Path(ROOT) / ".claude" / "workspaces" / "current" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_portal_{uuid.uuid4().hex}.db"
    monkeypatch.setenv("WHATUDOIN_BASE_DIR", ROOT)
    monkeypatch.setenv("WHATUDOIN_RUN_DIR", ROOT)
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    import app as app_module
    return app_module, db


def _seed_team_with_data(db, team_name="ABC"):
    """공개/비공개/히든 데이터 섞인 팀 1개 생성 → team_id 반환."""
    tid = db.create_team(team_name)
    # 일반 프로젝트 1개 (외부 공개), 외부 비공개 프로젝트 1개, 히든 프로젝트 1개
    with db.get_conn() as conn:
        conn.execute("INSERT INTO projects (team_id, name, name_norm, is_active, is_hidden, is_private) "
                     "VALUES (?, 'PubProj', 'pubproj', 1, 0, 0)", (tid,))
        conn.execute("INSERT INTO projects (team_id, name, name_norm, is_active, is_hidden, is_private) "
                     "VALUES (?, 'HiddenProj', 'hiddenproj', 1, 1, 0)", (tid,))
        # 일정: 공개(is_public=1, PubProj), 비공개(is_public=0, PubProj), 히든프로젝트 일정(is_public=1)
        conn.execute("INSERT INTO events (title, start_datetime, team_id, project, is_public, is_active, kanban_status, event_type) "
                     "VALUES ('PUBLIC_EVENT', '2026-06-01T09:00:00', ?, 'PubProj', 1, 1, 'todo', 'schedule')", (tid,))
        conn.execute("INSERT INTO events (title, start_datetime, team_id, project, is_public, is_active, kanban_status, event_type) "
                     "VALUES ('PRIVATE_EVENT', '2026-06-02T09:00:00', ?, 'PubProj', 0, 1, 'todo', 'schedule')", (tid,))
        conn.execute("INSERT INTO events (title, start_datetime, team_id, project, is_public, is_active, kanban_status, event_type) "
                     "VALUES ('HIDDEN_PROJ_EVENT', '2026-06-03T09:00:00', ?, 'HiddenProj', 1, 1, 'todo', 'schedule')", (tid,))
        # 체크: 공개 / 비공개 / 히든프로젝트
        conn.execute("INSERT INTO checklists (project, title, created_by, team_id, is_public) "
                     "VALUES ('PubProj', 'PUBLIC_CHECK', 'someone', ?, 1)", (tid,))
        conn.execute("INSERT INTO checklists (project, title, created_by, team_id, is_public) "
                     "VALUES ('PubProj', 'PRIVATE_CHECK', 'someone', ?, 0)", (tid,))
        conn.execute("INSERT INTO checklists (project, title, created_by, team_id, is_public) "
                     "VALUES ('HiddenProj', 'HIDDEN_PROJ_CHECK', 'someone', ?, 1)", (tid,))
        # 문서(meetings): 공개 팀 문서 / 비공개 팀 문서 / 개인 문서(is_team_doc=0, is_public=1 이어도 포털 비노출)
        conn.execute("INSERT INTO meetings (title, content, created_by, team_id, is_public, is_team_doc) "
                     "VALUES ('PUBLIC_DOC', '...', 1, ?, 1, 1)", (tid,))
        conn.execute("INSERT INTO meetings (title, content, created_by, team_id, is_public, is_team_doc) "
                     "VALUES ('PRIVATE_DOC', '...', 1, ?, 0, 1)", (tid,))
        conn.execute("INSERT INTO meetings (title, content, created_by, team_id, is_public, is_team_doc) "
                     "VALUES ('PERSONAL_DOC', '...', 1, ?, 1, 0)", (tid,))
    return tid


def test_portal_case_exact_and_404s(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team_with_data(db, "ABC")
        # 4: /ABC → 200, 팀 이름 + 포털 본문의 "계정 가입" 버튼 (base.html 로그인 모달의 링크와 구분)
        r = client.get("/ABC", follow_redirects=False)
        assert r.status_code == 200, r.status_code
        assert "ABC" in r.text
        assert 'btn-primary">계정 가입' in r.text, "포털 본문 계정 가입 버튼 누락"
        assert "공개 포털 — 공개 설정된 항목만" in r.text
        # 5: /abc → 404 (대소문자 분리)
        assert client.get("/abc", follow_redirects=False).status_code == 404
        # 6: /Nonexistent → 404
        assert client.get("/Nonexistent", follow_redirects=False).status_code == 404
        # 12: /Bad-Name (하이픈 — 정규식 불일치) → 404
        assert client.get("/Bad-Name", follow_redirects=False).status_code == 404


def test_static_and_api_routes_not_eclipsed(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        # 7: /admin → admin 로그인 페이지 (포털 아님)
        r = client.get("/admin", follow_redirects=False)
        assert r.status_code == 200
        assert "계정 가입" not in r.text or "관리자" in r.text  # admin_login.html
        assert "공개 포털" not in r.text
        # 8: /api/health → 200
        assert client.get("/api/health").status_code == 200
        # 9: /docs /redoc /openapi.json → 200
        assert client.get("/docs").status_code == 200
        assert client.get("/redoc").status_code == 200
        assert client.get("/openapi.json").status_code == 200
        # 10: /static/<존재 파일> — 404 아님 (mount 살아 있음). 디렉토리 자체는 보통 404 라
        #     실제 파일을 찾아 본다.
        static_dir = Path(ROOT) / "static"
        sample = None
        for p in static_dir.rglob("*"):
            if p.is_file():
                sample = p.relative_to(static_dir).as_posix()
                break
        if sample:
            assert client.get(f"/static/{sample}").status_code != 404
        # 11: 예약어 각각 — 포털이 아니어야 한다 ("공개 포털" 마커 부재)
        for word in ("api", "admin", "docs", "redoc", "kanban", "check", "doc", "notice",
                     "register", "trash", "remote", "avr", "calendar", "gantt", "changelog",
                     "ai-import", "alarm-setup", "settings", "static", "uploads"):
            r = client.get(f"/{word}", follow_redirects=False)
            # 404 / 200(정적 페이지) / 307·308(redirect) 어느 쪽이든 공개 포털 마크업은 없어야 함
            assert "공개 포털 — 공개 설정된 항목만" not in r.text, f"/{word} 가 팀 포털로 매칭됨"


def test_portal_data_filtering(monkeypatch):
    # 그룹 D catchup: `/팀이름` 기본 진입은 active_menu 1개(kanban)만 렌더한다.
    # 다른 채널은 각자의 라우트(`/팀이름/{한글}`)에서 검증한다.
    from fastapi.testclient import TestClient
    from urllib.parse import quote
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team_with_data(db, "ABC")
        # kanban 패널 (기본 진입)
        r = client.get("/ABC", follow_redirects=False)
        assert r.status_code == 200
        html_kanban = r.text
        # 13: is_public=0 항목 비노출 (칸반 채널)
        assert "PUBLIC_EVENT" in html_kanban
        assert "PRIVATE_EVENT" not in html_kanban
        # 14: 히든 프로젝트 하위 항목은 is_public=1 이어도 비노출
        assert "HIDDEN_PROJ_EVENT" not in html_kanban
        assert "HiddenProj" not in html_kanban
        # check 채널
        r = client.get(f"/ABC/{quote('체크')}", follow_redirects=False)
        assert r.status_code == 200
        html_check = r.text
        assert "PUBLIC_CHECK" in html_check
        assert "PRIVATE_CHECK" not in html_check
        assert "HIDDEN_PROJ_CHECK" not in html_check
        # doc 채널
        r = client.get(f"/ABC/{quote('문서')}", follow_redirects=False)
        assert r.status_code == 200
        html_doc = r.text
        assert "PUBLIC_DOC" in html_doc
        assert "PRIVATE_DOC" not in html_doc
        # 개인 문서(is_team_doc=0)는 포털에 안 나옴
        assert "PERSONAL_DOC" not in html_doc


def test_deleted_team_notice_only(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        tid = _seed_team_with_data(db, "GoneTeam")
        with db.get_conn() as conn:
            conn.execute("UPDATE teams SET deleted_at = datetime('now') WHERE id = ?", (tid,))
        r = client.get("/GoneTeam", follow_redirects=False)
        # 15: 안내 페이지 (200), 포털 본문 계정 가입 버튼·공개 데이터·탭 없음
        assert r.status_code == 200, r.status_code
        html = r.text
        assert "삭제 예정" in html
        assert 'btn-primary">계정 가입' not in html, "삭제 예정 팀에 포털 계정 가입 버튼이 노출됨"
        assert "공개 포털 — 공개 설정된 항목만" not in html, "삭제 예정 팀에 공개 포털 본문이 노출됨"
        assert 'id="portal-tabs"' not in html, "삭제 예정 팀에 탭 네비게이션이 노출됨"
        assert "PUBLIC_EVENT" not in html and "PUBLIC_CHECK" not in html and "PUBLIC_DOC" not in html


def test_logged_in_user_gets_portal_no_redirect(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        _seed_team_with_data(db, "ABC")
        user = db.create_user_account("테스터", "pw1234")
        assert user
        sid = db.create_session(user["id"])
        client.cookies.set("session_id", sid)
        r = client.get("/ABC", follow_redirects=False)
        # 16: 200 포털, redirect 아님. 로그인이라 포털 본문 "계정 가입" 버튼은 안 보임.
        assert r.status_code == 200, r.status_code
        assert "공개 포털 — 공개 설정된 항목만" in r.text
        assert 'btn-primary">계정 가입' not in r.text, "로그인 사용자에게 포털 계정 가입 버튼이 노출됨"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
