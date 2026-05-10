"""
M1a-7 진단: seed가 만든 session_cookies.json cookie가 왜 _require_editor에서 403인가?
- seed → uvicorn 시작 → /project-manage + /api/notifications/count + /api/events 호출 → 응답 비교
- 끝나면 서버 종료 + cleanup
"""
import os, sys, json, time, subprocess, signal
import urllib3
import httpx
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = r"D:\Github\WhatUdoin"
os.chdir(REPO)
os.environ["WHATUDOIN_PERF_FIXTURE"] = "allow"
PYTHON = r"D:\Program Files\Python\Python312\python.exe"
SEED   = r"_workspace\perf\fixtures\seed_users.py"
CLEAN  = r"_workspace\perf\fixtures\cleanup.py"
COOKIES = r"_workspace\perf\fixtures\session_cookies.json"

print("=" * 60)
print("[1/5] seed_users.py")
r = subprocess.run([PYTHON, SEED], capture_output=True, text=True, encoding="utf-8", errors="replace")
print("seed exit:", r.returncode)

print("=" * 60)
print("[2/5] uvicorn start")
proc = subprocess.Popen(
    [PYTHON, "-m", "uvicorn", "app:app",
     "--host", "0.0.0.0", "--port", "8443",
     "--ssl-certfile", "whatudoin-cert.pem",
     "--ssl-keyfile",  "whatudoin-key.pem",
     "--log-level", "warning"],
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
)
ready = False
for i in range(30):
    try:
        r = httpx.get("https://localhost:8443/api/notifications/count",
                      verify=False, timeout=2)
        if r.status_code == 200:
            ready = True
            print(f"  ready after {i+1}s, /api/notifications/count -> {r.status_code} {r.text[:80]}")
            break
    except Exception:
        pass
    time.sleep(1)
if not ready:
    proc.terminate(); proc.wait()
    print("[ABORT] server not ready in 30s"); sys.exit(2)

try:
    print("=" * 60)
    print("[3/5] cookie warmup spot-check")
    with open(COOKIES, encoding="utf-8") as f:
        cookies = json.load(f)
    username, info = next(iter(cookies.items()))
    sid = info["session_id"]
    print(f"  user={username}  session_id={sid[:24]}...")

    # cookie sent ONLY (no IP-whitelist auto-login interference test):
    # IP-whitelist may also kick in for localhost -- check both with and without cookie.

    print("\n  [a] WITH cookie (Cookie header explicit) -- domain=localhost path=/")
    client = httpx.Client(
        verify=False,
        base_url="https://localhost:8443",
        cookies={"session_id": sid},
    )
    for ep in ["/project-manage", "/api/events", "/api/kanban", "/", "/check",
               "/api/notifications/count", "/api/notifications/pending"]:
        try:
            r = client.get(ep, follow_redirects=False, timeout=5)
            loc = r.headers.get("location", "")
            print(f"    {ep:35s} -> {r.status_code}  loc={loc[:80]!r}  len={len(r.content)}")
        except Exception as e:
            print(f"    {ep:35s} -> ERR: {e!r}")

    print("\n  [b] WITHOUT cookie (no Cookie header)")
    nc = httpx.Client(verify=False, base_url="https://localhost:8443")
    for ep in ["/project-manage", "/api/events", "/api/notifications/count"]:
        try:
            r = nc.get(ep, follow_redirects=False, timeout=5)
            loc = r.headers.get("location", "")
            print(f"    {ep:35s} -> {r.status_code}  loc={loc[:80]!r}  len={len(r.content)}")
        except Exception as e:
            print(f"    {ep:35s} -> ERR: {e!r}")

    print("\n  [c] DB sanity -- sessions row for that session_id")
    import sqlite3
    db = sqlite3.connect(os.path.join(REPO, "whatudoin.db"))
    row = db.execute(
        "SELECT id, user_id, created_at, expires_at FROM sessions WHERE id = ?",
        (sid,),
    ).fetchone()
    print(f"    row: {row}")
    if row:
        urow = db.execute(
            "SELECT id, name, role, team_id FROM users WHERE id = ?", (row[1],)
        ).fetchone()
        print(f"    user: {urow}")
    db.close()

finally:
    print("=" * 60)
    print("[4/5] uvicorn shutdown")
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(timeout=3)

    print("[5/5] cleanup.py")
    r = subprocess.run([PYTHON, CLEAN], capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("cleanup exit:", r.returncode)
print("=" * 60)
print("DONE")
