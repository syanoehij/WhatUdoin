---
name: code-review
description: WhatUdoin 정적 코드 리뷰 스킬. 백엔드·프론트엔드 구현 완료 후 소스 변경이 프로젝트 관례를 준수하는지 검증한다. whatudoin-team의 code-reviewer 에이전트가 사용한다.
---

# WhatUdoin 코드 리뷰 스킬

## 리뷰 절차

### Step 1: 변경 범위 파악

`_workspace/backend_changes.md`와 `_workspace/frontend_changes.md`를 읽고 변경된 파일 목록을 파악한다.
변경 파일을 직접 Read하여 diff를 확인한다.

### Step 2: 체크리스트 순서로 검토

에이전트 정의(`.claude/agents/code-reviewer.md`)의 체크리스트를 순서대로 적용한다.
각 항목을 실제 코드에서 확인하고 통과/실패 여부를 기록한다.

### Step 3: 결함 분류 및 처리

- **차단(Blocking)**: 즉시 해당 에이전트에게 SendMessage로 수정 요청
  - 수정 완료 메시지 수신 후 해당 파일만 재검토 (1회)
  - 재실패 시 리더에게 보고하고 QA 진행 보류 권고
- **경고(Warning)**: 보고서에 기록 후 QA 진행 허용

### Step 4: 보고서 작성 및 QA 신호

리뷰 완료 후 `_workspace/code_review_report.md`를 작성하고,
차단 결함이 없으면 qa에게 SendMessage로 진행 신호를 보낸다.

---

## 주요 패턴 검증 예시

### 권한 체크 누락 (차단)

```python
# 잘못된 예 — 권한 없이 데이터 반환
@app.post("/api/sensitive-resource")
async def create_resource(request: Request):
    body = await request.json()
    ...

# 올바른 예
@app.post("/api/sensitive-resource")
async def create_resource(request: Request):
    user = _require_editor(request)   # ← 필수
    body = await request.json()
    ...
```

### _ctx() 누락 (차단)

```python
# 잘못된 예
return templates.TemplateResponse(request, "page.html", {"data": data})

# 올바른 예
return templates.TemplateResponse(request, "page.html", _ctx(request, data=data))
```

### DB 경로 혼용 (차단)

```python
# 잘못된 예 — DB 파일에 _BASE_DIR 사용
db_path = _BASE_DIR / "whatudoin.db"

# 올바른 예
db_path = _RUN_DIR / "whatudoin.db"
```

### SQL 직접 삽입 (차단)

```python
# 잘못된 예 — SQL injection 위험
conn.execute(f"SELECT * FROM events WHERE title = '{title}'")

# 올바른 예
conn.execute("SELECT * FROM events WHERE title = ?", (title,))
```

### fetch() 오류 처리 누락 (경고)

```javascript
// 잘못된 예
const data = await fetch('/api/resource').then(r => r.json());

// 올바른 예
const res = await fetch('/api/resource');
if (!res.ok) { showToast('오류가 발생했습니다.', 'error'); return; }
const data = await res.json();
```

### innerHTML XSS (차단)

```javascript
// 잘못된 예
element.innerHTML = userInput;

// 올바른 예
element.textContent = userInput;
// 또는 서버에서 Jinja2 자동 이스케이프 처리된 값만 사용
```

---

## 보고서 형식 (`_workspace/code_review_report.md`)

```markdown
## 코드 리뷰 보고서 — {기능명}

### 리뷰 대상 파일
- `app.py` (라인 XXX-YYY)
- `templates/my_page.html`

### 차단(Blocking) ❌
- [ ] `app.py:123` — `_require_editor` 누락. `/api/new-endpoint`에 권한 체크 필요.
  → backend-dev에게 수정 요청 발송

### 경고(Warning) ⚠️
- [ ] `templates/my_page.html:45` — `response.ok` 체크 없이 `.json()` 직접 호출

### 통과 ✅
- [x] DB 스키마 변경: `_migrate` 패턴 올바르게 사용
- [x] 파일 경로: `_RUN_DIR` 올바르게 사용
- [x] SQL 파라미터화 확인

### 최종 판정
- **통과** / **차단 결함 있음 (수정 요청 중)**
```
