import sqlite3
c = sqlite3.connect('D:/Github/WhatUdoin/whatudoin.db')
tables = ['users','events','checklists','notifications']
for t in tables:
    try:
        n = c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
        print(f'{t}={n}')
    except:
        print(f'{t}=N/A')
c.close()
