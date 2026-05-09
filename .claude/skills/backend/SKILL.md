---
name: backend
description: WhatUdoin 백엔드 개발 스킬. FastAPI 라우트 추가/수정, SQLite 스키마 변경(_migrate 패턴), Ollama LLM 연동, 인증 로직 작업 시 사용. app.py, database.py, llm_parser.py, auth.py, crypto.py, backup.py를 다룬다.
---

# WhatUdoin 백엔드 개발 스킬

## 프로젝트 구조 핵심

```
app.py          — FastAPI 라우트 전체 (페이지 + JSON API)
database.py     — SQLite CRUD + init_db() + _migrate()
llm_parser.py   — Ollama 자연어 파싱
auth.py         — 세션 기반 인증
crypto.py       — Fernet 암호화
backup.py       — DB 자동 백업
```

## DB 스키마 변경 (_migrate 패턴)

별도 migration 파일 없음. 모든 스키마 변경은 `database.py`에 인라인으로 처리한다.

**새 컬럼 추가:**
```python
# database.py → init_db() 내 _migrate 호출 목록에 추가
_migrate(conn, "events", [
    ("기존컬럼", "TEXT"),
    ("신규컬럼", "TEXT DEFAULT NULL"),  # 여기 추가
])
```

**새 테이블 추가:**
```python
# init_db() 내에 CREATE TABLE IF NOT EXISTS 블록 추가
conn.execute("""
    CREATE TABLE IF NOT EXISTS new_table (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
""")
```

**기존 테이블 컬럼 추가 (이미 생성된 테이블):**
```python
# try/except로 이미 존재하는 컬럼 에러 무시
for _col, _def in [("new_col", "TEXT DEFAULT NULL")]:
    try:
        conn.execute(f"ALTER TABLE my_table ADD COLUMN {_col} {_def}")
    except Exception:
        pass
```

## FastAPI 라우트 패턴

**페이지 라우트:**
```python
@app.get("/my-page", response_class=HTMLResponse)
def my_page(request: Request):
    user = _require_editor(request)  # 로그인 필요 시
    data = db.get_something()
    return templates.TemplateResponse(request, "my_page.html", _ctx(request, data=data))
```

**JSON API:**
```python
@app.post("/api/my-resource")
async def create_my_resource(request: Request):
    user = _require_editor(request)
    body = await request.json()
    result = db.create_something(body)
    return {"id": result, "ok": True}
```

**권한 레벨:**
- 누구나: 권한 체크 없음
- 로그인 필요: `_require_editor(request)`
- 관리자 전용: `_require_admin(request)`

## 파일 경로 이중화 (PyInstaller 대응)

```python
_BASE_DIR = Path(os.environ.get("WHATUDOIN_BASE_DIR", Path(__file__).parent))
_RUN_DIR  = Path(os.environ.get("WHATUDOIN_RUN_DIR",  Path(__file__).parent))

# 정적 자원(읽기 전용): _BASE_DIR / "templates" / ...
# DB, 업로드(쓰기 필요): _RUN_DIR / "whatudoin.db" ...
```

## LLM 연동 (Ollama)

```python
# llm_parser.py
OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:e4b"

# 모델 목록 조회
models = llm_parser.get_available_models()

# 자연어 파싱 (기존 함수 활용)
result = llm_parser.parse_event_text(text, model=model_name)
```

- Ollama는 외부 프로세스이므로 실패 시 graceful degradation 필요
- `_session.trust_env = False` — 회사 프록시 우회용, 변경 금지

## DB CRUD 패턴

```python
# database.py에 함수 추가 시
def get_my_items(team_id=None):
    with get_conn() as conn:
        query = "SELECT * FROM my_table WHERE is_active = 1"
        params = []
        if team_id:
            query += " AND team_id = ?"
            params.append(team_id)
        return [dict(row) for row in conn.execute(query, params).fetchall()]
```

## 주요 DB 테이블 요약

| 테이블 | 핵심 컬럼 |
|-------|---------|
| `events` | `id`, `title`, `team_id`, `project`, `assignee`, `start_datetime`, `end_datetime`, `kanban_status`, `event_type`, `recurrence_*`, `parent_event_id` |
| `meetings` | `id`, `title`, `content`, `team_id`, `created_by`, `is_team_doc`, `is_public`, `team_share` |
| `projects` | `id`, `name`, `color`, `start_date`, `end_date`, `is_active`, `is_private` |
| `users` | `id`, `name`, `role`(`admin`/`editor`/`viewer`), `team_id`, `is_active` |
| `teams` | `id`, `name` |
