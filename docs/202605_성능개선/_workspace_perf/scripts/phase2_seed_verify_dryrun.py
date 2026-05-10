import sqlite3, json
from pathlib import Path
c = sqlite3.connect('D:/Github/WhatUdoin/whatudoin.db')
u = c.execute("SELECT COUNT(*) FROM users    WHERE name  LIKE 'test_perf_%'").fetchone()[0]
s = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'test_perf_%')").fetchone()[0]
c.close()
cookies_path = Path('D:/Github/WhatUdoin/_workspace/perf/fixtures/session_cookies.json')
ck = len(json.load(open(cookies_path))) if cookies_path.exists() else -1
print(f'{u},{s},{ck}')
