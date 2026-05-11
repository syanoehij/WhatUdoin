"""팀 기능 그룹 A #9 검증 — IP 자동 로그인 관리.

합성 DB(임시 파일) + FastAPI TestClient. 운영 DB·서버 무관.

실행:
  "D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_team_a_009.py
"""
import os
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, ROOT)

_tmpdir = tempfile.mkdtemp(prefix="wu_a009_")
_tmpdb = os.path.join(_tmpdir, "test.db")
os.environ["WHATUDOIN_RUN_DIR"] = _tmpdir

import database as db  # noqa: E402
db.DB_PATH = _tmpdb
db.init_db()

PASS = 0
FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {extra}")


print("=== Part 1: DB helper 직접 검증 ===")

# 부분 UNIQUE 인덱스 존재 확인
with db.get_conn() as c:
    idx = c.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_user_ips_whitelist_unique'"
    ).fetchone()
check("부분 UNIQUE 인덱스 idx_user_ips_whitelist_unique 생성됨", idx is not None)
with db.get_conn() as c:
    marker = c.execute(
        "SELECT 1 FROM settings WHERE key='migration_phase:team_phase_4b_user_ips_whitelist_unique_v1'"
    ).fetchone()
check("phase 4b 마커 기록됨", marker is not None)

# 사용자 2명 생성
u1 = db.create_user_account("Alice", "ab12cd")
u2 = db.create_user_account("Bob", "ef34gh")
check("사용자 2명 생성", isinstance(u1, dict) and isinstance(u2, dict))

IP_A = "10.0.0.5"

# set_user_whitelist_ip — 신규
db.set_user_whitelist_ip(u1["id"], IP_A)
with db.get_conn() as c:
    r = c.execute("SELECT * FROM user_ips WHERE user_id=? AND ip_address=?", (u1["id"], IP_A)).fetchone()
check("u1 whitelist row 생성 type='whitelist'", r is not None and r["type"] == "whitelist", dict(r) if r else None)

# 멱등 — 같은 호출 노옵
db.set_user_whitelist_ip(u1["id"], IP_A)
with db.get_conn() as c:
    cnt = c.execute("SELECT COUNT(*) FROM user_ips WHERE user_id=? AND ip_address=?", (u1["id"], IP_A)).fetchone()[0]
check("멱등 — 중복 row 생기지 않음", cnt == 1, cnt)

# 다른 사용자가 같은 IP 등록 → IPWhitelistConflict
try:
    db.set_user_whitelist_ip(u2["id"], IP_A)
    check("u2가 같은 IP whitelist → IPWhitelistConflict 발생", False, "예외 안 남")
except db.IPWhitelistConflict:
    check("u2가 같은 IP whitelist → IPWhitelistConflict 발생", True)

# find_whitelist_owner
check("find_whitelist_owner 정확", db.find_whitelist_owner(IP_A) == u1["id"], db.find_whitelist_owner(IP_A))
check("find_whitelist_owner 없는 IP → None", db.find_whitelist_owner("9.9.9.9") is None)

# get_whitelist_status_for_ip
st_owner = db.get_whitelist_status_for_ip(u1["id"], IP_A)
check("status owner: enabled=True conflict=False", st_owner["enabled"] is True and st_owner["conflict"] is False, st_owner)
st_other = db.get_whitelist_status_for_ip(u2["id"], IP_A)
check("status 타인: enabled=False conflict=True conflict_user='Alice'",
      st_other["enabled"] is False and st_other["conflict"] is True and st_other["conflict_user"] == "Alice", st_other)
st_free = db.get_whitelist_status_for_ip(u2["id"], "9.9.9.9")
check("status 미등록 IP: enabled=False conflict=False", st_free["enabled"] is False and st_free["conflict"] is False, st_free)

# remove_user_whitelist_ip — 강등(row 삭제 X)
db.remove_user_whitelist_ip(u1["id"], IP_A)
with db.get_conn() as c:
    r = c.execute("SELECT * FROM user_ips WHERE user_id=? AND ip_address=?", (u1["id"], IP_A)).fetchone()
check("remove → type='history' 강등 (row 보존)", r is not None and r["type"] == "history", dict(r) if r else None)

# 강등 후 u2가 등록 가능 + history row 승격
db.set_user_whitelist_ip(u2["id"], IP_A)  # u2는 history row 없으니 INSERT
with db.get_conn() as c:
    r2 = c.execute("SELECT * FROM user_ips WHERE user_id=? AND ip_address=? AND type='whitelist'", (u2["id"], IP_A)).fetchone()
check("강등 후 u2 whitelist 등록 성공", r2 is not None, dict(r2) if r2 else None)
# u1의 history row가 다시 whitelist로 승격되는지 (u2 whitelist 해제 후)
db.remove_user_whitelist_ip(u2["id"], IP_A)
db.set_user_whitelist_ip(u1["id"], IP_A)  # u1은 history row 있음 → 승격
with db.get_conn() as c:
    rows = c.execute("SELECT type FROM user_ips WHERE user_id=? AND ip_address=?", (u1["id"], IP_A)).fetchall()
check("u1 history row가 whitelist로 승격 (row 1개 유지)", len(rows) == 1 and rows[0]["type"] == "whitelist", [dict(x) for x in rows])

# admin_set_whitelist_ip — 임의 IP (접속 이력 없음)
db.admin_set_whitelist_ip(u2["id"], "172.16.0.99")
with db.get_conn() as c:
    r = c.execute("SELECT * FROM user_ips WHERE user_id=? AND ip_address='172.16.0.99'", (u2["id"],)).fetchone()
check("admin_set_whitelist_ip 임의 IP 등록", r is not None and r["type"] == "whitelist", dict(r) if r else None)
try:
    db.admin_set_whitelist_ip(u1["id"], "172.16.0.99")  # u2가 보유 중
    check("admin이 충돌 IP 다른 사용자에게 → IPWhitelistConflict", False, "예외 안 남")
except db.IPWhitelistConflict:
    check("admin이 충돌 IP 다른 사용자에게 → IPWhitelistConflict", True)

# delete_ip_row
with db.get_conn() as c:
    del_id = c.execute("SELECT id FROM user_ips WHERE ip_address='172.16.0.99'").fetchone()[0]
db.delete_ip_row(del_id)
with db.get_conn() as c:
    gone = c.execute("SELECT 1 FROM user_ips WHERE id=?", (del_id,)).fetchone()
check("delete_ip_row → row 삭제됨", gone is None)

# toggle_ip_whitelist — enable=True 충돌 시 예외
# u3 만들어 history row + IP_A(u1 보유 중) 충돌 유도
u3 = db.create_user_account("Carol", "ij56kl")
with db.get_conn() as c:
    c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?,?,'history')", (u3["id"], IP_A))
    u3_ip_id = c.execute("SELECT id FROM user_ips WHERE user_id=? AND ip_address=? AND type='history'", (u3["id"], IP_A)).fetchone()[0]
try:
    db.toggle_ip_whitelist(u3_ip_id, True)  # IP_A는 u1 whitelist
    check("toggle enable 충돌 → IPWhitelistConflict", False, "예외 안 남")
except db.IPWhitelistConflict:
    check("toggle enable 충돌 → IPWhitelistConflict", True)
# enable=False는 항상 OK
db.toggle_ip_whitelist(u3_ip_id, False)
check("toggle disable 항상 허용", True)

# 부분 인덱스: history 중복 OK, whitelist 중복 IntegrityError
with db.get_conn() as c:
    c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '8.8.8.8', 'history')", (u1["id"],))
    c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '8.8.8.8', 'history')", (u2["id"],))
check("부분 인덱스: 같은 IP history 2건 허용", True)
import sqlite3 as _sq
try:
    with db.get_conn() as c:
        c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '7.7.7.7', 'whitelist')", (u1["id"],))
        c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '7.7.7.7', 'whitelist')", (u2["id"],))
    check("부분 인덱스: 같은 IP whitelist 2건 → IntegrityError", False, "예외 안 남")
except _sq.IntegrityError:
    check("부분 인덱스: 같은 IP whitelist 2건 → IntegrityError", True)

# auth.get_user_by_whitelist_ip 가 admin은 무시 (회귀)
import auth  # noqa: E402
with db.get_conn() as c:
    admin_id = c.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    # admin에게 직접 whitelist row 박아넣고 (마이그레이션 우회) get_current_user 동작 확인
    c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '6.6.6.6', 'whitelist')", (admin_id,))
au = db.get_user_by_whitelist_ip("6.6.6.6")
check("get_user_by_whitelist_ip는 admin row도 반환(필터는 auth.get_current_user에서)", au is not None and au["role"] == "admin")
# 정리
with db.get_conn() as c:
    c.execute("DELETE FROM user_ips WHERE ip_address='6.6.6.6'")


print()
print("=== Part 2: TestClient 라우트 검증 ===")
try:
    from fastapi.testclient import TestClient
    import app as appmod
    appmod.limiter.enabled = False
    H = {"Origin": "http://testserver", "Host": "testserver"}

    # 일반 사용자 로그인 (Alice — 위에서 ab12cd로 생성)
    with TestClient(appmod.app) as client:
        r = client.post("/api/login", json={"name": "Alice", "password": "ab12cd"}, headers=H)
        check("Alice 로그인 → 200", r.status_code == 200, r.text)

        # GET 상태 — testserver IP(testclient)는 보통 'testserver' 호스트라 client.host가 None일 수 있음.
        # auth.get_client_ip는 None이면 '127.0.0.1' fallback.
        r = client.get("/api/me/ip-whitelist", headers=H)
        check("GET /api/me/ip-whitelist → 200", r.status_code == 200, r.text)
        body = r.json()
        check("응답에 enabled/conflict/ip/admin 키", all(k in body for k in ("enabled", "conflict", "ip", "admin")), body)
        check("admin=False", body.get("admin") is False, body)
        cur_ip = body["ip"]

        # POST — 본인 등록
        r = client.post("/api/me/ip-whitelist", headers=H)
        check("POST /api/me/ip-whitelist → 200", r.status_code == 200, r.text)
        check("응답 ok/ip", r.json().get("ok") is True and r.json().get("ip") == cur_ip, r.text)
        owner = db.find_whitelist_owner(cur_ip)
        with db.get_conn() as c:
            alice_id = c.execute("SELECT id FROM users WHERE name='Alice'").fetchone()[0]
        check("DB: cur_ip의 whitelist 소유 = Alice", owner == alice_id, owner)

        # GET 다시 → enabled=True
        r = client.get("/api/me/ip-whitelist", headers=H)
        check("GET 후 enabled=True", r.json().get("enabled") is True, r.text)

        # 다른 사용자(Bob)가 같은 IP 등록 시도 → 409
        with TestClient(appmod.app) as bobc:
            rb = bobc.post("/api/login", json={"name": "Bob", "password": "ef34gh"}, headers=H)
            check("Bob 로그인 → 200", rb.status_code == 200, rb.text)
            rb = bobc.post("/api/me/ip-whitelist", headers=H)
            check("Bob 같은 IP 등록 → 409", rb.status_code == 409, rb.text)
            # Bob GET → conflict=True conflict_user='Alice'
            rb = bobc.get("/api/me/ip-whitelist", headers=H)
            jb = rb.json()
            check("Bob GET → conflict=True conflict_user='Alice'", jb.get("conflict") is True and jb.get("conflict_user") == "Alice", jb)

        # Alice DELETE → 해제
        r = client.delete("/api/me/ip-whitelist", headers=H)
        check("DELETE /api/me/ip-whitelist → 200", r.status_code == 200, r.text)
        check("DB: cur_ip whitelist 해제됨", db.find_whitelist_owner(cur_ip) is None, db.find_whitelist_owner(cur_ip))
        with db.get_conn() as c:
            still = c.execute("SELECT type FROM user_ips WHERE user_id=? AND ip_address=?", (alice_id, cur_ip)).fetchone()
        check("DB: row는 history로 남음", still is not None and still["type"] == "history", dict(still) if still else None)

        # 해제 후 Bob이 등록 → 200
        with TestClient(appmod.app) as bobc2:
            bobc2.post("/api/login", json={"name": "Bob", "password": "ef34gh"}, headers=H)
            rb = bobc2.post("/api/me/ip-whitelist", headers=H)
            check("해제 후 Bob 등록 → 200", rb.status_code == 200, rb.text)
            with db.get_conn() as c:
                bob_id = c.execute("SELECT id FROM users WHERE name='Bob'").fetchone()[0]
            check("DB: cur_ip 소유 = Bob", db.find_whitelist_owner(cur_ip) == bob_id, db.find_whitelist_owner(cur_ip))
            rb = bobc2.delete("/api/me/ip-whitelist", headers=H)  # 정리

    # admin 세션 — POST /api/me/ip-whitelist → 403
    with db.get_conn() as c:
        admin_id2 = c.execute("SELECT id FROM users WHERE role='admin'").fetchone()[0]
    db.reset_user_password(admin_id2, "admin12")
    with TestClient(appmod.app) as adminc:
        r = adminc.post("/api/admin/login", json={"name": "admin", "password": "admin12"}, headers=H)
        check("admin 로그인 → 200", r.status_code == 200, r.text)
        r = adminc.post("/api/me/ip-whitelist", headers=H)
        check("admin POST /api/me/ip-whitelist → 403", r.status_code == 403, r.text)
        # admin GET → admin=True
        r = adminc.get("/api/me/ip-whitelist", headers=H)
        check("admin GET → admin=True enabled=False", r.json().get("admin") is True and r.json().get("enabled") is False, r.text)

        # admin이 임의 사용자에게 임의 IP 등록 → 200
        with db.get_conn() as c:
            bob_id = c.execute("SELECT id FROM users WHERE name='Bob'").fetchone()[0]
        r = adminc.post(f"/api/admin/users/{bob_id}/ips", json={"ip_address": "203.0.113.7"}, headers=H)
        check("admin 임의 IP 등록 → 200", r.status_code == 200, r.text)
        check("DB: 203.0.113.7 소유 = Bob", db.find_whitelist_owner("203.0.113.7") == bob_id, db.find_whitelist_owner("203.0.113.7"))

        # 빈 IP → 400
        r = adminc.post(f"/api/admin/users/{bob_id}/ips", json={"ip_address": "   "}, headers=H)
        check("admin 빈 IP → 400", r.status_code == 400, r.text)

        # 충돌 IP를 다른 사용자에게 → 409
        with db.get_conn() as c:
            alice_id3 = c.execute("SELECT id FROM users WHERE name='Alice'").fetchone()[0]
        r = adminc.post(f"/api/admin/users/{alice_id3}/ips", json={"ip_address": "203.0.113.7"}, headers=H)
        check("admin 충돌 IP 등록 → 409", r.status_code == 409, r.text)

        # GET /api/admin/users/{id}/ips → 목록에 포함
        r = adminc.get(f"/api/admin/users/{bob_id}/ips", headers=H)
        check("admin GET user ips → 200 + 203.0.113.7 포함", r.status_code == 200 and any(x["ip_address"] == "203.0.113.7" for x in r.json()), r.text)
        ip_row_id = next(x["id"] for x in r.json() if x["ip_address"] == "203.0.113.7")

        # PUT toggle enable 충돌 — Alice에게 history row 만들고 같은 IP whitelist 토글 시도
        with db.get_conn() as c:
            c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '203.0.113.7', 'history')", (alice_id3,))
            alice_hist_id = c.execute("SELECT id FROM user_ips WHERE user_id=? AND ip_address='203.0.113.7' AND type='history'", (alice_id3,)).fetchone()[0]
        r = adminc.put(f"/api/admin/ips/{alice_hist_id}/whitelist", json={"enable": True}, headers=H)
        check("admin PUT toggle enable 충돌 → 409", r.status_code == 409, r.text)

        # DELETE /api/admin/ips/{id} → 200 + row 사라짐
        r = adminc.delete(f"/api/admin/ips/{ip_row_id}", headers=H)
        check("admin DELETE ip row → 200", r.status_code == 200, r.text)
        with db.get_conn() as c:
            gone = c.execute("SELECT 1 FROM user_ips WHERE id=?", (ip_row_id,)).fetchone()
        check("DB: 삭제된 row 없음", gone is None)

        # 권한: 일반 사용자가 admin IP API 호출 → 403
    with TestClient(appmod.app) as plainc:
        plainc.post("/api/login", json={"name": "Alice", "password": "ab12cd"}, headers=H)
        with db.get_conn() as c:
            bob_id = c.execute("SELECT id FROM users WHERE name='Bob'").fetchone()[0]
        r = plainc.post(f"/api/admin/users/{bob_id}/ips", json={"ip_address": "1.2.3.4"}, headers=H)
        check("일반 사용자 admin IP 등록 → 403", r.status_code == 403, r.text)
        r = plainc.delete("/api/admin/ips/1", headers=H)
        check("일반 사용자 admin IP 삭제 → 403", r.status_code == 403, r.text)

    # 비로그인 → /api/me/ip-whitelist 401
    with TestClient(appmod.app) as anon:
        r = anon.get("/api/me/ip-whitelist", headers=H)
        check("비로그인 GET /api/me/ip-whitelist → 401", r.status_code == 401, r.text)
        r = anon.post("/api/me/ip-whitelist", headers=H)
        check("비로그인 POST /api/me/ip-whitelist → 403/401", r.status_code in (401, 403), r.text)

except Exception:
    FAIL += 1
    print("  FAIL  TestClient 섹션 예외:")
    traceback.print_exc()


print()
print("=== Part 3: 마이그레이션 preflight 충돌 abort 검증 ===")
try:
    # 별도 임시 DB로 처음부터: init_db 후 4b 마커·인덱스 제거 → 충돌 row 삽입 → _run_phase_migrations 재호출 → RuntimeError 기대
    import importlib
    _d2 = tempfile.mkdtemp(prefix="wu_a009_pf_")
    _db2 = os.path.join(_d2, "test.db")
    db.DB_PATH = _db2
    db.init_db()
    # 4b 마커 + 인덱스 제거 (마이그레이션 미적용 상태로 되돌림)
    with db.get_conn() as c:
        c.execute("DELETE FROM settings WHERE key='migration_phase:team_phase_4b_user_ips_whitelist_unique_v1'")
        c.execute("DROP INDEX IF EXISTS idx_user_ips_whitelist_unique")
        # 기존 경고 정리
        c.execute("DELETE FROM settings WHERE key=?", (db._TEAM_MIGRATION_WARNINGS_KEY,))
    # 충돌 whitelist 2건 삽입 (서로 다른 사용자)
    ua = db.create_user_account("PfUserA", "ab12cd")
    ub = db.create_user_account("PfUserB", "ef34gh")
    with db.get_conn() as c:
        c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '198.51.100.1', 'whitelist')", (ua["id"],))
        c.execute("INSERT INTO user_ips (user_id, ip_address, type) VALUES (?, '198.51.100.1', 'whitelist')", (ub["id"],))
    # 마이그레이션 재실행 → preflight 충돌 → RuntimeError
    raised = False
    try:
        db._run_phase_migrations()
    except RuntimeError as exc:
        raised = True
        msg = repr(exc)
    check("충돌 상태에서 마이그레이션 → RuntimeError(abort)", raised, "예외 안 남")
    # 경고가 settings에 기록됐는지
    with db.get_conn() as c:
        warn_row = c.execute("SELECT value FROM settings WHERE key=?", (db._TEAM_MIGRATION_WARNINGS_KEY,)).fetchone()
    check("team_migration_warnings에 기록됨", warn_row is not None and "preflight_user_ips_whitelist" in (warn_row["value"] or ""), warn_row["value"] if warn_row else None)
    # 4b 인덱스는 여전히 미생성 (충돌 미해소이므로)
    with db.get_conn() as c:
        idx2 = c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_user_ips_whitelist_unique'").fetchone()
    check("충돌 미해소 → 인덱스 미생성", idx2 is None)
    # 충돌 해소 후 재실행 → 정상 통과
    with db.get_conn() as c:
        c.execute("UPDATE user_ips SET type='history' WHERE user_id=? AND ip_address='198.51.100.1'", (ub["id"],))
    db._run_phase_migrations()
    with db.get_conn() as c:
        idx3 = c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_user_ips_whitelist_unique'").fetchone()
    check("충돌 해소 후 재실행 → 인덱스 생성됨", idx3 is not None)
except Exception:
    FAIL += 1
    print("  FAIL  preflight 섹션 예외:")
    traceback.print_exc()


print()
print("=== Part 4: import-time smoke ===")
try:
    _d3 = tempfile.mkdtemp(prefix="wu_a009_smoke_")
    _db3 = os.path.join(_d3, "fresh.db")
    db.DB_PATH = _db3
    db.init_db()  # 빈 DB 첫 init — 모든 phase 적용
    with db.get_conn() as c:
        idx = c.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_user_ips_whitelist_unique'").fetchone()
    check("빈 DB 첫 init_db → 4b 인덱스 생성", idx is not None)
    db.init_db()  # 두 번째 호출 — 멱등
    check("init_db 재호출 멱등 (예외 없음)", True)
except Exception:
    FAIL += 1
    print("  FAIL  smoke 섹션 예외:")
    traceback.print_exc()


print()
print(f"=== 결과: {PASS} PASS / {FAIL} FAIL ===")
sys.exit(0 if FAIL == 0 else 1)
