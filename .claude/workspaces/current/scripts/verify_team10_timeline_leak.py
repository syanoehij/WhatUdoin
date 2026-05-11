"""팀 기능 #10 누수 패치 검증 — /api/project-timeline 비로그인 누수 + 회귀.

서버 OFF / VSCode 디버깅 모드 → 합성 DB + FastAPI TestClient.
실행: "D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_team10_timeline_leak.py

검증:
  (1) 비로그인 GET /api/project-timeline → []  (이전엔 전 팀 public 일정 노출)
  (2) 팀 A 멤버 → A 팀 데이터만
  (3) admin → 전 팀 데이터 (무필터, 의도된 동작 유지)
  (4) 팀 미배정 로그인 → []
  (5) 다중 팀 사용자 작업 팀 전환 (team_id=tA / team_id=tB) 각각 해당 팀만
  (6) 회귀: /api/kanban, /api/events, /api/checklists, /api/projects, /api/doc 도
      비로그인·멤버·admin·미배정에서 #10 명세대로 (누수 없음, 회귀 없음)
"""
import os, sys, tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, _ROOT)

_tmpdir = tempfile.mkdtemp(prefix="wd_team10_tl_")
os.environ["WHATUDOIN_RUN_DIR"] = _tmpdir
os.environ["WHATUDOIN_BASE_DIR"] = _ROOT

import database as db
db.init_db()
import auth
import app as app_module
from fastapi.testclient import TestClient

PASS = 0
FAIL = 0
def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")

def _conn():
    return db.get_conn()

# ── 합성 데이터: 팀 A, 팀 B ─────────────────────────────────
# 사용자: X (A,B 둘 다), Y (A만), Z (B만), adminU (admin), unassignedU (팀 없음)
# events: 팀A 공개 1건(EvA_pub), 팀A 비공개 1건(EvA_priv), 팀B 공개 1건(EvB_pub), NULL팀 1건(EvNull, 작성자 X)
# projects: ProjA(팀A, 공개), ProjB(팀B, 공개), ProjPriv(팀A, is_private=1)
with _conn() as conn:
    conn.execute("INSERT INTO teams (name, name_norm) VALUES ('TeamA','teama')")
    conn.execute("INSERT INTO teams (name, name_norm) VALUES ('TeamB','teamb')")
    tA = conn.execute("SELECT id FROM teams WHERE name='TeamA'").fetchone()[0]
    tB = conn.execute("SELECT id FROM teams WHERE name='TeamB'").fetchone()[0]

    def mkuser(name, role="member", team_id=None):
        conn.execute("INSERT INTO users (name, name_norm, password, role, team_id) VALUES (?,?,?,?,?)",
                     (name, name.lower(), "x", role, team_id))
        return conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]
    uX = mkuser("X", team_id=tA)
    uY = mkuser("Y", team_id=tA)
    uZ = mkuser("Z", team_id=tB)
    uAdmin = mkuser("adminU", role="admin")
    uUn = mkuser("unassignedU", team_id=None)

    def membership(uid, tid, status="approved", role="member"):
        conn.execute("INSERT INTO user_teams (user_id, team_id, status, role) VALUES (?,?,?,?)",
                     (uid, tid, status, role))
    membership(uX, tA); membership(uX, tB)
    membership(uY, tA)
    membership(uZ, tB)

    # projects
    def mkproject(name, team_id, owner_id, is_private=0):
        conn.execute("INSERT INTO projects (name, name_norm, team_id, owner_id, is_private, is_active) VALUES (?,?,?,?,?,1)",
                     (name, name.lower(), team_id, owner_id, is_private))
        return conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()[0]
    mkproject("ProjA", tA, uX)
    mkproject("ProjB", tB, uZ)
    mkproject("ProjPriv", tA, uX, is_private=1)

    # events
    def mkevent(title, team_id, created_by, is_public=None, project=None):
        conn.execute("INSERT INTO events (title, team_id, created_by, is_public, project, start_datetime) VALUES (?,?,?,?,?,?)",
                     (title, team_id, created_by, is_public, project, "2026-06-01T10:00"))
        return conn.execute("SELECT id FROM events WHERE title=?", (title,)).fetchone()[0]
    mkevent("EvA_pub",  tA, str(uX), is_public=1, project="ProjA")
    mkevent("EvA_priv", tA, str(uX), is_public=0, project="ProjA")
    mkevent("EvB_pub",  tB, str(uZ), is_public=1, project="ProjB")
    mkevent("EvNull",   None, str(uX), is_public=0, project=None)

    # checklists
    def mkchecklist(title, team_id, created_by, project="", is_public=None):
        conn.execute("INSERT INTO checklists (project, title, content, created_by, team_id, is_public) VALUES (?,?,?,?,?,?)",
                     (project, title, "- [ ] a", created_by, team_id, is_public))
        return conn.execute("SELECT id FROM checklists WHERE title=?", (title,)).fetchone()[0]
    mkchecklist("CkA_pub", tA, "X", project="ProjA", is_public=1)
    mkchecklist("CkB_pub", tB, "Z", project="ProjB", is_public=1)

    # meetings
    def mkmeeting(title, team_id, created_by, is_team_doc=1, is_public=0, team_share=0):
        conn.execute("INSERT INTO meetings (title, content, team_id, created_by, is_team_doc, is_public, team_share) VALUES (?,?,?,?,?,?,?)",
                     (title, "body", team_id, created_by, is_team_doc, is_public, team_share))
        return conn.execute("SELECT id FROM meetings WHERE title=?", (title,)).fetchone()[0]
    mkmeeting("DocTeamA", tA, uX, is_team_doc=1)
    mkmeeting("DocPubB", tB, uZ, is_team_doc=1, is_public=1)

def sess(uid, role="member"):
    return db.create_session(uid, role)
sX = sess(uX); sY = sess(uY); sZ = sess(uZ); sAdmin = sess(uAdmin, "admin"); sUn = sess(uUn)

client = TestClient(app_module.app, base_url="http://testserver")
def get(path, session=None, **params):
    cookies = {"session_id": session} if session else {}
    return client.get(path, params=params, cookies=cookies)

def timeline_team_names(resp):
    """[{team_name, projects:[...]}] → set of team_name"""
    j = resp.json()
    return {t.get("team_name") for t in j}

def timeline_event_titles(resp):
    titles = set()
    for t in resp.json():
        for p in t.get("projects", []):
            for e in p.get("events", []):
                titles.add(e.get("title"))
    return titles

# ─────────────────────────────────────────────────────────
print("\n=== /api/project-timeline — 누수 패치 핵심 ===")
# (1) 비로그인 → [] (이전엔 EvA_pub + EvB_pub 다 나옴)
r = get("/api/project-timeline")
check("비로그인: /api/project-timeline == []  (이전엔 전 팀 public 일정 노출)", r.json() == [])

# (3) admin → 무필터, 전 팀 (의도된 동작 유지)
r = get("/api/project-timeline", sAdmin)
names = timeline_team_names(r)
ev = timeline_event_titles(r)
check("admin: 전 팀 노출 (TeamA + TeamB)", "TeamA" in names and "TeamB" in names)
check("admin: EvA_pub, EvA_priv, EvB_pub 모두 노출", {"EvA_pub", "EvA_priv", "EvB_pub"}.issubset(ev))

# (2) 팀 A 멤버(Y, 작업 팀 미지정 → 대표 팀 A) → A 팀만
r = get("/api/project-timeline", sY)
names = timeline_team_names(r)
ev = timeline_event_titles(r)
check("Y(대표팀 A): TeamA 노출, TeamB 미노출", "TeamA" in names and "TeamB" not in names)
check("Y: EvA_pub + EvA_priv 노출 (같은 팀이라 비공개도 봄), EvB_pub 미노출",
      "EvA_pub" in ev and "EvA_priv" in ev and "EvB_pub" not in ev)

# (5) 다중 팀 사용자 X — team_id=tA / team_id=tB 전환
r = get("/api/project-timeline", sX, team_id=tA)
check("X@A: TeamA 만", timeline_team_names(r) == {"TeamA"} or ("TeamA" in timeline_team_names(r) and "TeamB" not in timeline_team_names(r)))
check("X@A: EvB_pub 미노출", "EvB_pub" not in timeline_event_titles(r))
r = get("/api/project-timeline", sX, team_id=tB)
check("X@B: TeamB 만 (작업 팀 전환)", "TeamB" in timeline_team_names(r) and "TeamA" not in timeline_team_names(r))
check("X@B: EvB_pub 노출, EvA_pub 미노출",
      "EvB_pub" in timeline_event_titles(r) and "EvA_pub" not in timeline_event_titles(r))

# X@B 명시 — Z 는 B 만 소속이므로 비소속 명시 케이스 = Z@A → 대표팀 B fallback
r = get("/api/project-timeline", sZ, team_id=tA)
check("Z@A(비소속 명시): A 팀 자료 노출 안 됨 (대표팀 B fallback)",
      "TeamA" not in timeline_team_names(r) and "TeamB" in timeline_team_names(r))

# (4) 팀 미배정 로그인 → []
r = get("/api/project-timeline", sUn)
check("unassignedU: /api/project-timeline == []", r.json() == [])

# ─────────────────────────────────────────────────────────
print("\n=== 회귀: /api/kanban ===")
check("비로그인 /api/kanban == []", get("/api/kanban").json() == [])
ek = {e["title"] for e in get("/api/kanban", sY).json()}
check("Y@A 칸반: EvNull? (project NULL → backlog)  EvB_pub 미노출", "EvB_pub" not in ek)
ek = {e["title"] for e in get("/api/kanban", sAdmin).json()}
check("admin 칸반: EvNull 노출 (project NULL backlog)", "EvNull" in ek)
check("unassignedU 칸반 == []", get("/api/kanban", sUn).json() == [])

print("\n=== 회귀: /api/events (비로그인은 is_public 만, 누수 아님) ===")
titles = {e["title"] for e in get("/api/events").json()}
check("비로그인 /api/events: EvA_pub + EvB_pub 노출 (is_public=1)", {"EvA_pub", "EvB_pub"}.issubset(titles))
check("비로그인 /api/events: EvA_priv, EvNull 미노출", "EvA_priv" not in titles and "EvNull" not in titles)
titles = {e["title"] for e in get("/api/events", sY).json()}
# 주의: /api/events rule 4 — is_public=1 일정은 작업 팀 무관 전원 노출 (기존·의도된 동작).
#   따라서 EvB_pub(타팀 공개)도 Y 에게 보이는 게 정상. EvNull(작성자 X 아님) 만 안 보여야 함.
check("Y@A /api/events: EvA_pub, EvA_priv 노출 (같은 팀 — 비공개도 봄)",
      "EvA_pub" in titles and "EvA_priv" in titles)
check("Y@A /api/events: EvB_pub 노출 (is_public=1 — 작업 팀 무관, 기존 동작)", "EvB_pub" in titles)
check("Y@A /api/events: EvNull 미노출 (NULL팀, 작성자 X — Y 아님)", "EvNull" not in titles)
titles = {e["title"] for e in get("/api/events", sAdmin).json()}
check("admin /api/events: 전부 노출", {"EvA_pub", "EvA_priv", "EvB_pub", "EvNull"}.issubset(titles))

print("\n=== 회귀: /api/checklists ===")
titles = {c["title"] for c in get("/api/checklists").json()}
check("비로그인 /api/checklists: CkA_pub + CkB_pub (is_public=1, project non-private)", {"CkA_pub", "CkB_pub"}.issubset(titles))
titles = {c["title"] for c in get("/api/checklists", sY).json()}
check("Y@A /api/checklists: CkA_pub 노출 / CkB_pub 미노출", "CkA_pub" in titles and "CkB_pub" not in titles)

print("\n=== 회귀: /api/projects ===")
names = set(get("/api/projects").json())
check("비로그인 /api/projects: ProjA, ProjB 노출 / ProjPriv 미노출 (is_private)", {"ProjA", "ProjB"}.issubset(names) and "ProjPriv" not in names)
names = set(get("/api/projects", sY).json())
check("Y@A /api/projects: ProjA + ProjPriv 노출 / ProjB 미노출", "ProjA" in names and "ProjPriv" in names and "ProjB" not in names)
names = set(get("/api/projects", sAdmin).json())
check("admin /api/projects: 전부 노출", {"ProjA", "ProjB", "ProjPriv"}.issubset(names))

print("\n=== 회귀: /api/doc ===")
titles = {d["title"] for d in get("/api/doc").json()}
check("비로그인 /api/doc: DocPubB 만 (is_public=1)", "DocPubB" in titles and "DocTeamA" not in titles)
titles = {d["title"] for d in get("/api/doc", sY).json()}
check("Y@A /api/doc: DocTeamA + DocPubB", "DocTeamA" in titles and "DocPubB" in titles)
titles = {d["title"] for d in get("/api/doc", sAdmin).json()}
check("admin /api/doc: 전부", {"DocTeamA", "DocPubB"}.issubset(titles))

print("\n=== 권한 가드: 비로그인은 _require_editor 라우트에서 차단 ===")
check("비로그인 /api/project-list → 401/403", get("/api/project-list").status_code in (401, 403))
check("비로그인 /api/manage/projects → 401/403", get("/api/manage/projects").status_code in (401, 403))

print(f"\n========== 결과: {PASS} PASS / {FAIL} FAIL ==========")
sys.exit(1 if FAIL else 0)
