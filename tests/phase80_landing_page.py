"""팀 기능 그룹 B #11 회귀: `/` 비로그인 접속 화면 = 팀 목록 + 로그인/계정 가입.

검증 항목:
  1. app.py `index()` — 비로그인 시 `/kanban` redirect 코드 제거 (grep)
  2. database.py `get_visible_teams()` 존재 + deleted_at 필터 (grep + 동작)
  3. templates/home.html — `#view-guest`가 팀 목록 랜딩, 게스트 칸반 고아 코드 제거 (grep)
  4. TestClient: 비로그인 `GET /` → 200, view-guest + 계정 가입 버튼 + 팀 카드 렌더
  5. TestClient: soft-deleted 팀(`deleted_at IS NOT NULL`)은 목록에서 제외
  6. TestClient: 로그인 사용자 `GET /` → view-user 대시보드 정상 (회귀)

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
def test_index_route_no_kanban_redirect():
    src = (Path(ROOT) / "app.py").read_text(encoding="utf-8")
    # index() 함수 본문 추출
    m = re.search(r'@app\.get\("/", response_class=HTMLResponse\)\s*\ndef index\(request: Request\):(.*?)\n@app\.', src, re.S)
    assert m, "index() 라우트를 찾지 못함"
    body = m.group(1)
    assert "RedirectResponse" not in body, "index()에 비로그인 redirect가 아직 남아 있음"
    assert "get_visible_teams" in body, "index()가 get_visible_teams를 쓰지 않음"


def test_get_visible_teams_exists_and_filters():
    src = (Path(ROOT) / "database.py").read_text(encoding="utf-8")
    assert "def get_visible_teams(" in src, "get_visible_teams 헬퍼 누락"
    m = re.search(r"def get_visible_teams\(.*?\n(.*?)\n\ndef ", src, re.S)
    assert m, "get_visible_teams 본문 추출 실패"
    body = m.group(1)
    assert "deleted_at IS NULL" in body, "get_visible_teams가 deleted_at 필터를 안 함"
    # get_all_teams는 그대로(전체) — 시그니처/동작 변경 금지
    assert re.search(r'def get_all_teams\(\):\s*\n\s*with get_conn\(\) as conn:\s*\n\s*rows = conn\.execute\("SELECT \* FROM teams ORDER BY name"\)', src), \
        "get_all_teams가 변경됨 (다른 라우트 공유 — 변경 금지)"


def test_home_template_landing_and_no_guest_kanban():
    src = (Path(ROOT) / "templates" / "home.html").read_text(encoding="utf-8")
    # 새 랜딩 마크업
    assert "landing-team-card" in src or "아직 생성된 팀이 없습니다" in src
    assert 'href="/{{ team.name | urlencode }}"' in src, "팀 카드 href가 urlencode 인코딩되어야 함"
    assert "계정 가입" in src and 'href="/register"' in src
    assert "openLoginModal()" in src
    # 게스트 칸반 고아 코드 제거
    for orphan in ("loadGuest", "guest-board", "guest-team-filter", "guest-total", "home-toolbar", "kanban-total-label"):
        assert orphan not in src, f"고아 코드 '{orphan}'가 home.html에 아직 남아 있음"
    # 공유 코드는 유지
    for keep in ("buildBoard", "cardHTML", "loadUser", "view-user"):
        assert keep in src, f"공유 코드 '{keep}'가 사라짐"


# ── 4·5·6. TestClient 동작 검증 (임시 DB) ──────────────────────
def _setup_app_with_temp_db(monkeypatch):
    db_dir = Path(ROOT) / "_workspace" / "test_dbs"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_dir / f"test_landing_{uuid.uuid4().hex}.db"

    # app import 전에 환경 세팅 (BASE/RUN dir → ROOT, DB → 임시)
    monkeypatch.setenv("WHATUDOIN_BASE_DIR", ROOT)
    monkeypatch.setenv("WHATUDOIN_RUN_DIR", ROOT)
    import database as db
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    # app 모듈 (캐시되어 있을 수 있음) — DB_PATH는 get_conn에서 동적 참조하므로 OK
    import app as app_module
    return app_module, db


def test_anon_index_renders_team_landing(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:  # lifespan → init_db()
        # 팀 3개 (1개는 soft-delete)
        db.create_team("알파팀")
        db.create_team("베타 팀")  # 공백 포함 → urlencode 검증
        del_id = db.create_team("삭제예정팀")
        with db.get_conn() as conn:
            conn.execute("UPDATE teams SET deleted_at = datetime('now') WHERE id = ?", (del_id,))

        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200, r.status_code
        html = r.text
        assert 'id="view-guest"' in html
        assert "계정 가입" in html
        # 비삭제 팀은 카드로 (urlencode 적용 — 공백은 %20)
        assert 'href="/%EC%95%8C%ED%8C%8C%ED%8C%80"' in html  # 알파팀
        assert 'href="/%EB%B2%A0%ED%83%80%20%ED%8C%80"' in html  # "베타 팀"
        # soft-deleted 팀은 제외
        assert "삭제예정팀" not in html
        # 게스트 칸반 고아 흔적 없음
        assert "loadGuest" not in html and "guest-board" not in html


def test_logged_in_index_still_dashboard(monkeypatch):
    from fastapi.testclient import TestClient
    app_module, db = _setup_app_with_temp_db(monkeypatch)
    with TestClient(app_module.app) as client:
        # 계정 가입(#8) → 세션 생성 → 쿠키 주입
        user = db.create_user_account("테스터", "pw1234")
        assert user, "create_user_account 실패"
        sid = db.create_session(user["id"])
        client.cookies.set("session_id", sid)  # auth.SESSION_COOKIE
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200, r.status_code
        html = r.text
        assert 'id="view-user"' in html
        # 게스트 랜딩이 아닌 대시보드여야 함 (회귀)
        assert "loadGuest" not in html
