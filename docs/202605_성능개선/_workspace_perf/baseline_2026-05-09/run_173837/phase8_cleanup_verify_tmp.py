import sqlite3
c = sqlite3.connect('D:/Github/WhatUdoin/whatudoin.db')
u  = c.execute("SELECT COUNT(*) FROM users    WHERE name  LIKE 'test_perf_%'").fetchone()[0]
s  = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id IN (SELECT id FROM users WHERE name LIKE 'test_perf_%')").fetchone()[0]
ev = c.execute("SELECT COUNT(*) FROM events   WHERE title LIKE 'test_perf_evt_%'").fetchone()[0]
c.close()
print(f'{u},{s},{ev}')
