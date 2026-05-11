"""팀 기능 그룹 A #10 검증 — 합성 DB + FastAPI TestClient.

서버 재시작 불가/실서버 OFF 환경 → import-time + 합성 DB + TestClient 위주.
실행: "D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_team10.py
"""
import os, sys, tempfile, traceback

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
sys.path.insert(0, _ROOT)

# 격리된 임시 DB 사용
_tmpdir = tempfile.mkdtemp(prefix="wd_team10_")
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

# ─────────────────────────────────────────────────────────
# 합성 데이터: 팀 A, 팀 B (+ 삭제 예정 팀 C)
# 사용자: X (A,B 둘 다), Y (A만), Z (B만), adminU (admin), unassignedU (팀 없음)
# ─────────────────────────────────────────────────────────
with _conn() as conn:
    conn.execute("INSERT INTO teams (name, name_norm) VALUES ('TeamA','teama')")
    conn.execute("INSERT INTO teams (name, name_norm) VALUES ('TeamB','teamb')")
    conn.execute("INSERT INTO teams (name, name_norm, deleted_at) VALUES ('TeamC','teamc','2026-01-01T00:00:00')")
    tA = conn.execute("SELECT id FROM teams WHERE name='TeamA'").fetchone()[0]
    tB = conn.execute("SELECT id FROM teams WHERE name='TeamB'").fetchone()[0]
    tC = conn.execute("SELECT id FROM teams WHERE name='TeamC'").fetchone()[0]

    def mkuser(name, role="member", team_id=None):
        conn.execute("INSERT INTO users (name, name_norm, password, role, team_id) VALUES (?,?,?,?,?)",
                     (name, name.lower(), "x", role, team_id))
        return conn.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]

    uX = mkuser("X", team_id=tA)
    uY = mkuser("Y", team_id=tA)
    uZ = mkuser("Z", team_id=tB)
    uAdmin = mkuser("adminU", role="admin")
    uUn = mkuser("unassignedU", team_id=None)

    # user_teams
    def membership(uid, tid, status="approved", role="member"):
        conn.execute("INSERT INTO user_teams (user_id, team_id, status, role) VALUES (?,?,?,?)",
                     (uid, tid, status, role))
    membership(uX, tA); membership(uX, tB)
    membership(uY, tA)
    membership(uZ, tB)
    membership(uX, tC)  # 삭제 예정 팀 멤버십 — user_team_ids 에서 제외돼야 함

    # events: A팀 1건(작성자 X, id기반 created_by), B팀 1건(작성자 Z), NULL팀 1건(작성자 X), is_public 1건(A팀이지만 공개)
    def mkevent(title, team_id, created_by, is_public=None, project=None):
        conn.execute(
            "INSERT INTO events (title, team_id, created_by, is_public, project, start_datetime) VALUES (?,?,?,?,?,?)",
            (title, team_id, created_by, is_public, project, "2026-06-01T10:00"))
        return conn.execute("SELECT id FROM events WHERE title=?", (title,)).fetchone()[0]
    eA = mkevent("EvA", tA, str(uX))
    eB = mkevent("EvB", tB, str(uZ))
    eNull = mkevent("EvNull", None, str(uX))             # 신규 쓰기 형식: created_by = str(id)
    eNullLegacy = mkevent("EvNullLegacy", None, "Y")     # legacy 형식: created_by = 이름
    eA.__class__  # noop

    # checklists
    def mkchecklist(title, team_id, created_by, project=None):
        conn.execute("INSERT INTO checklists (project, title, content, created_by, team_id) VALUES (?,?,?,?,?)",
                     (project or "", title, "- [ ] a", created_by, team_id))
        return conn.execute("SELECT id FROM checklists WHERE title=?", (title,)).fetchone()[0]
    cA = mkchecklist("CkA", tA, "X")
    cB = mkchecklist("CkB", tB, "Z")
    cNull = mkchecklist("CkNull", None, "X")

    # projects: A팀 1건, B팀 1건, NULL팀 1건(owner X)
    def mkproject(name, team_id, owner_id):
        conn.execute("INSERT INTO projects (name, name_norm, team_id, owner_id) VALUES (?,?,?,?)",
                     (name, name.lower(), team_id, owner_id))
        return conn.execute("SELECT id FROM projects WHERE name=?", (name,)).fetchone()[0]
    pA = mkproject("ProjA", tA, uX)
    pB = mkproject("ProjB", tB, uZ)
    pNull = mkproject("ProjNull", None, uX)

    # meetings: 팀문서(A팀, 작성자 X), 개인문서(A팀, 작성자 X, team_share=1), 개인문서(A팀, 작성자 X, team_share=0),
    #           NULL팀 개인문서(작성자 unassignedU), 공개문서(B팀)
    def mkmeeting(title, team_id, created_by, is_team_doc=1, is_public=0, team_share=0):
        conn.execute("INSERT INTO meetings (title, content, team_id, created_by, is_team_doc, is_public, team_share) VALUES (?,?,?,?,?,?,?)",
                     (title, "body", team_id, created_by, is_team_doc, is_public, team_share))
        return conn.execute("SELECT id FROM meetings WHERE title=?", (title,)).fetchone()[0]
    mTeamA = mkmeeting("DocTeamA", tA, uX, is_team_doc=1)
    mPersShareA = mkmeeting("DocPersShareA", tA, uX, is_team_doc=0, team_share=1)
    mPersA = mkmeeting("DocPersA", tA, uX, is_team_doc=0, team_share=0)
    mPersNull = mkmeeting("DocPersNull", None, uUn, is_team_doc=0, team_share=0)
    mPubB = mkmeeting("DocPubB", tB, uZ, is_team_doc=1, is_public=1)
    mTeamA_byY = mkmeeting("DocTeamA_byY", tA, uY, is_team_doc=1)        # Y 가 만든 A팀 팀문서 — 추방 시나리오용
    mPersA_byY = mkmeeting("DocPersA_byY", tA, uY, is_team_doc=0, team_share=0)  # Y 가 만든 A팀 개인문서

# 세션 생성
def sess(uid, role="member"):
    return db.create_session(uid, role)

sX = sess(uX); sY = sess(uY); sZ = sess(uZ); sAdmin = sess(uAdmin, "admin"); sUn = sess(uUn)

client = TestClient(app_module.app, base_url="http://testserver")

def get(path, session=None, **params):
    cookies = {}
    if session:
        cookies["session_id"] = session
    return client.get(path, params=params, cookies=cookies)

print("\n=== auth.user_team_ids: 삭제 예정 팀 제외 ===")
uX_dict = {"id": uX, "name": "X", "role": "member", "team_id": tA}
check("X 의 user_team_ids == {tA, tB} (tC 제외)", auth.user_team_ids(uX_dict) == {tA, tB})

print("\n=== /api/events 가시성 (현재 작업 팀) ===")
# X, team_id=tA → EvA(작업팀A) + EvNull(작성자X) + EvNullLegacy? created_by='Y' 아니므로 X 못봄.  EvB 못봄.
r = get("/api/events", sX, team_id=tA)
titles = {e["title"] for e in r.json()}
check("X@A: EvA 보임", "EvA" in titles)
check("X@A: EvNull 보임 (작성자 본인)", "EvNull" in titles)
check("X@A: EvB(타팀) 안 보임", "EvB" not in titles)
check("X@A: EvNullLegacy(작성자 Y) 안 보임", "EvNullLegacy" not in titles)
# X, team_id=tB → EvB + EvNull. EvA 안 보임.
r = get("/api/events", sX, team_id=tB)
titles = {e["title"] for e in r.json()}
check("X@B: EvB 보임", "EvB" in titles)
check("X@B: EvNull 보임", "EvNull" in titles)
check("X@B: EvA(타팀) 안 보임", "EvA" not in titles)
# Y, 작업팀 미지정 → 대표팀 A fallback. EvNullLegacy(작성자 Y) 보임.
r = get("/api/events", sY)
titles = {e["title"] for e in r.json()}
check("Y(fallback A): EvA 보임", "EvA" in titles)
check("Y: EvNullLegacy 보임 (작성자 본인)", "EvNullLegacy" in titles)
check("Y: EvNull(작성자 X) 안 보임", "EvNull" not in titles)
check("Y: EvB(B팀) 안 보임", "EvB" not in titles)
# admin → 전부
r = get("/api/events", sAdmin)
titles = {e["title"] for e in r.json()}
check("admin: EvA,EvB,EvNull 모두 보임", {"EvA","EvB","EvNull"}.issubset(titles))
# 비로그인 → is_public=1 만. (현재 데이터엔 없음 → 빈)
r = get("/api/events")
check("비로그인: 빈 목록 (is_public 없음)", r.json() == [])
# X 가 team_id=tB(소속) 명시 OK. team_id 에 비소속? X 는 A,B 둘 다 소속이라 비소속 케이스 = Y@B
r = get("/api/events", sY, team_id=tB)
titles = {e["title"] for e in r.json()}
check("Y@B(비소속 명시): B팀 자료 노출 안 됨 (대표팀 A로 fallback)", "EvB" not in titles and "EvA" in titles)

print("\n=== /api/checklists ===")
r = get("/api/checklists", sX, team_id=tA)
titles = {c["title"] for c in r.json()}
check("X@A: CkA 보임", "CkA" in titles)
check("X@A: CkB 안 보임", "CkB" not in titles)
check("X@A: CkNull 보임 (작성자 X)", "CkNull" in titles)
r = get("/api/checklists", sY)  # 대표팀 A
titles = {c["title"] for c in r.json()}
check("Y: CkA 보임, CkB·CkNull 안 보임", "CkA" in titles and "CkB" not in titles and "CkNull" not in titles)
r = get("/api/checklists", sZ)  # 대표팀 B
titles = {c["title"] for c in r.json()}
check("Z@B: CkB 보임, CkA·CkNull 안 보임", "CkB" in titles and "CkA" not in titles and "CkNull" not in titles)

print("\n=== /api/projects, /api/manage/projects ===")
r = get("/api/projects", sX, team_id=tA)
names = set(r.json())
check("X@A: ProjA 보임, ProjB 안 보임", "ProjA" in names and "ProjB" not in names)
check("X@A: ProjNull 보임 (owner X)", "ProjNull" in names)
r = get("/api/projects", sZ)  # 대표팀 B
names = set(r.json())
check("Z@B: ProjB 보임, ProjA·ProjNull 안 보임", "ProjB" in names and "ProjA" not in names and "ProjNull" not in names)
r = get("/api/manage/projects", sY)  # 대표팀 A
names = {p["name"] for p in r.json()}
check("Y: manage/projects 에 ProjA 보임, ProjB·ProjNull 안 보임", "ProjA" in names and "ProjB" not in names and "ProjNull" not in names)
r = get("/api/manage/projects", sAdmin)
names = {p["name"] for p in r.json()}
check("admin: manage/projects 에 ProjA,ProjB,ProjNull 모두 보임", {"ProjA","ProjB","ProjNull"}.issubset(names))

print("\n=== /api/doc (문서 가시성) ===")
r = get("/api/doc", sX, team_id=tA)
titles = {d["title"] for d in r.json()}
check("X@A: DocTeamA 보임 (팀문서)", "DocTeamA" in titles)
check("X@A: DocPersShareA 보임 (개인문서 작성자 본인)", "DocPersShareA" in titles)
check("X@A: DocPersA 보임 (개인문서 작성자 본인)", "DocPersA" in titles)
check("X@A: DocPubB 보임 (공개문서)", "DocPubB" in titles)
check("X@A: DocPersNull 안 보임 (NULL팀 개인문서, 작성자 unassignedU)", "DocPersNull" not in titles)
r = get("/api/doc", sY, team_id=tA)  # Y 는 A팀 멤버, 작성자 아님
titles = {d["title"] for d in r.json()}
check("Y@A: DocTeamA 보임 (같은 팀 팀문서)", "DocTeamA" in titles)
check("Y@A: DocPersShareA 보임 (team_share=1, 같은 작업팀)", "DocPersShareA" in titles)
check("Y@A: DocPersA 안 보임 (개인문서 team_share=0, 작성자 아님)", "DocPersA" not in titles)
check("Y@A: DocPubB 보임 (공개)", "DocPubB" in titles)
r = get("/api/doc", sX, team_id=tB)  # X@B: 작업팀 B. DocTeamA 는 A팀 팀문서 → 작업팀이 B 이므로 안 보임 (§8-1: 작성자 예외는 팀문서엔 적용 안 됨).
titles = {d["title"] for d in r.json()}
check("X@B: DocTeamA 안 보임 (A팀 팀문서, 작업팀 B — 작성자라도 팀문서엔 작성자 예외 없음)", "DocTeamA" not in titles)
check("X@B: DocPersA 보임 (개인문서 작성자 본인은 작업팀 무관 항상)", "DocPersA" in titles)
check("X@B: DocPersShareA 보임 (개인문서 작성자 본인)", "DocPersShareA" in titles)
check("X@B: DocPubB 보임", "DocPubB" in titles)
r = get("/api/doc", sUn)  # 팀 미배정
titles = {d["title"] for d in r.json()}
check("unassignedU: DocPersNull 보임 (작성자 본인)", "DocPersNull" in titles)
check("unassignedU: DocPubB 보임 (공개)", "DocPubB" in titles)
check("unassignedU: DocTeamA 안 보임", "DocTeamA" not in titles)
r = get("/api/doc", sAdmin)
titles = {d["title"] for d in r.json()}
check("admin: 모든 문서 보임", {"DocTeamA","DocPersShareA","DocPersA","DocPersNull","DocPubB"}.issubset(titles))

print("\n=== /api/kanban ===")
# 칸반은 kanban_status 설정 OR 프로젝트 미지정 일정. 우리 이벤트는 project NULL 이라 backlog.
r = get("/api/kanban", sX, team_id=tA)
ek = {e["title"] for e in r.json()}
check("X@A 칸반: EvA 보임, EvB 안 보임", "EvA" in ek and "EvB" not in ek)
r = get("/api/kanban", sY)  # 대표팀 A
ek = {e["title"] for e in r.json()}
check("Y 칸반: EvA 보임, EvB 안 보임", "EvA" in ek and "EvB" not in ek)
r = get("/api/kanban", sUn)  # 팀 미배정 → []
check("unassignedU 칸반: 빈 목록", r.json() == [])

print("\n=== 편집·삭제 권한 (can_edit_*) ===")
# events: 팀 공유 — Y(A팀) 가 X 가 만든 EvA 편집 가능
Y_dict = {"id": uY, "name": "Y", "role": "member", "team_id": tA}
Z_dict = {"id": uZ, "name": "Z", "role": "member", "team_id": tB}
evA_row = db.get_event(eA)
check("Y 가 EvA(X작성, A팀) 편집 가능 (팀 공유)", auth.can_edit_event(Y_dict, evA_row))
evB_row = db.get_event(eB)
check("Y 가 EvB(B팀) 편집 불가", not auth.can_edit_event(Y_dict, evB_row))
# meetings 혼합 모델
docTeamA = db.get_meeting(mTeamA)
check("Y 가 DocTeamA(팀문서, A팀) 편집 가능", app_module._can_write_doc(Y_dict, docTeamA))
docPersShareA = db.get_meeting(mPersShareA)
check("Y 가 DocPersShareA(개인문서 team_share=1) 편집 불가 (읽기만)", not app_module._can_write_doc(Y_dict, docPersShareA))
docPersA = db.get_meeting(mPersA)
check("X 가 DocPersA(개인문서, 작성자 X) 편집 가능", app_module._can_write_doc(uX_dict, docPersA))
check("Y 가 DocPersA(개인문서, 작성자 X) 편집 불가", not app_module._can_write_doc(Y_dict, docPersA))
# admin 전역
check("admin 이 DocPersA 편집 가능 (전역 슈퍼유저)", app_module._can_write_doc({"id": uAdmin, "name":"adminU","role":"admin"}, docPersA))

print("\n=== 추방 시나리오 — 권한 자동 복구 + 추방 후 자기 작성 팀자료도 차단 ===")
Y_dict2 = {"id": uY, "name": "Y", "role": "member", "team_id": tA}  # legacy team_id 는 그대로
docTeamA_byY = db.get_meeting(mTeamA_byY)
docPersA_byY = db.get_meeting(mPersA_byY)
# 추방 전: Y 는 자기 팀문서·개인문서 모두 편집 가능
check("추방 전 Y 가 DocTeamA_byY 편집 가능", app_module._can_write_doc(Y_dict2, docTeamA_byY))
# Y 를 A팀에서 추방 (status='left')
with _conn() as conn:
    conn.execute("UPDATE user_teams SET status='left' WHERE user_id=? AND team_id=?", (uY, tA))
# can_edit_event → user_can_access_team(Y, tA) → user_team_ids(Y) 쿼리 성공(0건) → set() → False
evA_row2 = db.get_event(eA)
check("추방된 Y 가 EvA(A팀) 편집 불가", not auth.can_edit_event(Y_dict2, evA_row2))
check("추방된 Y 가 DocTeamA_byY(자기 작성 A팀 팀문서) 편집 불가 (§8-1: 팀 소속이라서 보이는 것)", not app_module._can_write_doc(Y_dict2, docTeamA_byY))
check("추방된 Y 가 DocPersA_byY(자기 작성 A팀 개인문서) 편집 가능 (개인문서는 추방 무관 보유)", app_module._can_write_doc(Y_dict2, docPersA_byY))
# 가시성: 추방된 Y 의 /api/doc — 자기 작성 팀문서(DocTeamA_byY) 안 보임, 자기 작성 개인문서(DocPersA_byY) 보임
r = get("/api/doc", sY)
titles = {d["title"] for d in r.json()}
check("추방된 Y: /api/doc 에 DocTeamA_byY(자기 작성 팀문서) 안 보임", "DocTeamA_byY" not in titles)
check("추방된 Y: /api/doc 에 DocPersA_byY(자기 작성 개인문서) 보임", "DocPersA_byY" in titles)
check("추방된 Y: /api/doc 에 DocTeamA(X 작성 A팀 팀문서) 안 보임", "DocTeamA" not in titles)
# 직접 _can_read_doc 검사
import permissions
check("_can_read_doc(추방된 Y, DocTeamA_byY) == False", not permissions._can_read_doc(Y_dict2, docTeamA_byY))
check("_can_read_doc(추방된 Y, DocPersA_byY) == True (개인문서 작성자 본인)", permissions._can_read_doc(Y_dict2, docPersA_byY))
r = get("/api/events", sY)  # 추방 후: 작업팀 결정 불가 → 빈 scope. EvNullLegacy(작성자 Y) 만 보임.
titles = {e["title"] for e in r.json()}
check("추방된 Y: EvA 안 보임", "EvA" not in titles)
check("추방된 Y: EvNullLegacy(자기 작성 개인 row) 여전히 보임", "EvNullLegacy" in titles)
# 재가입
with _conn() as conn:
    conn.execute("UPDATE user_teams SET status='approved' WHERE user_id=? AND team_id=?", (uY, tA))
evA_row3 = db.get_event(eA)
check("재가입 후 Y 가 EvA 편집 가능 (자동 복구)", auth.can_edit_event(Y_dict2, evA_row3))
check("재가입 후 Y 가 DocTeamA_byY 편집 가능 (자동 복구)", app_module._can_write_doc(Y_dict2, db.get_meeting(mTeamA_byY)))
r = get("/api/events", sY)
titles = {e["title"] for e in r.json()}
check("재가입 후 Y: EvA 다시 보임", "EvA" in titles)
r = get("/api/doc", sY)
titles = {d["title"] for d in r.json()}
check("재가입 후 Y: /api/doc 에 DocTeamA_byY 다시 보임", "DocTeamA_byY" in titles)

print("\n=== NULL team row 회귀 방지 — 다른 팀 멤버에게 노출 안 됨 ===")
r = get("/api/events", sZ)  # Z@B: EvNull(작성자 X) 노출 안 됨
titles = {e["title"] for e in r.json()}
check("Z: EvNull 안 보임", "EvNull" not in titles)
check("Z: EvNullLegacy 안 보임", "EvNullLegacy" not in titles)
r = get("/api/checklists", sZ)
titles = {c["title"] for c in r.json()}
check("Z: CkNull 안 보임", "CkNull" not in titles)
r = get("/api/projects", sZ)
check("Z: ProjNull 안 보임", "ProjNull" not in set(r.json()))

print(f"\n========== 결과: {PASS} PASS / {FAIL} FAIL ==========")
sys.exit(1 if FAIL else 0)
