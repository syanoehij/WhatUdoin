# Feature Spec: MCP 도구 도메인별 재편 (39차)

## 분류
- **타입**: 기능 추가 (backend-only)
- **실행 모드**: 서브 에이전트 (backend-dev → code-reviewer → qa)

## 목표
MCP 도구를 4개 도메인 × search/list/get 3단계 구조로 재편하고, search_all을 유지한다.

## 최종 도구 구조 (총 15개)

| 도메인 | search | list | get |
|--------|--------|------|-----|
| (공통) | `search_all` (기존 search 이름 변경) | `list_projects` (유지) | — |
| 일정 | `search_events` ✨NEW | `list_events` (유지) | `get_event` (유지) |
| 칸반 | `search_kanban` ✨NEW | `list_kanban` ✨NEW | `get_kanban_item` ✨NEW |
| 문서 | `search_documents` ✨NEW | `list_documents` (유지) | `get_document` (유지) |
| 체크 | `search_checklists` ✨NEW | `list_checklists` (유지) | `get_checklist` (유지) |

## 도메인 정의

### 일정 (events) vs 칸반 (kanban) 구분
- **일정**: 캘린더에 표시되는 모든 이벤트 (kanban_status 유무 무관)
- **칸반**: kanban 보드에 표시되는 항목
  - 조건: `(kanban_status IS NOT NULL OR project IS NULL OR project = '')`
  - AND `is_active IS NULL OR is_active = 1`
  - AND `kanban_hidden IS NULL OR kanban_hidden = 0`
  - AND `event_type IS NULL OR event_type = 'schedule'`
  - AND `recurrence_parent_id IS NULL`
  - AND `deleted_at IS NULL`

---

## 변경 1: mcp_server.py

### 1-1. search → search_all 이름 변경
기존 `search` 함수를 `search_all`로 rename.
도구 이름이 변경되므로 docstring도 맞게 수정.

### 1-2. search_events 신규 추가
```python
@mcp.tool()
async def search_events(
    ctx: Context,
    query: str,
    start_after: str | None = None,
    end_before: str | None = None,
) -> list[dict]:
```
- 날짜 미지정 시 오늘 ±7일 기본값 적용 (search_all과 동일 로직)
- 반환 필드: id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type

### 1-3. list_kanban 신규 추가
```python
@mcp.tool()
async def list_kanban(
    ctx: Context,
    project: str | None = None,
) -> list[dict]:
```
- 칸반 보드 기준 항목 반환 (경량)
- 반환 필드: id, title, project, kanban_status, priority, assignee, start_datetime, end_datetime

### 1-4. get_kanban_item 신규 추가
```python
@mcp.tool()
async def get_kanban_item(ctx: Context, event_id: int) -> dict:
```
- 내부적으로 get_event_for_mcp()와 동일한 DB 함수 사용
- 칸반 맥락 전용 get 도구임을 docstring에 명시

### 1-5. search_kanban 신규 추가
```python
@mcp.tool()
async def search_kanban(
    ctx: Context,
    query: str,
    project: str | None = None,
) -> list[dict]:
```
- 칸반 항목을 title로 키워드 검색
- 날짜 필터 없음 (칸반은 태스크 중심, 날짜 무관)
- 반환 필드: id, title, project, kanban_status, priority, assignee

### 1-6. search_documents 신규 추가
```python
@mcp.tool()
async def search_documents(ctx: Context, query: str) -> list[dict]:
```
- 문서 title + content 키워드 검색
- 반환 필드: id, title, author_name, team_name, updated_at

### 1-7. search_checklists 신규 추가
```python
@mcp.tool()
async def search_checklists(
    ctx: Context,
    query: str,
    project: str | None = None,
) -> list[dict]:
```
- 체크리스트 title + content 키워드 검색
- 반환 필드: id, title, project, updated_at, item_count, done_count

---

## 변경 2: database.py

### 2-1. search_events_mcp(query, start_after, end_before) 신규
- events 테이블에서 title LIKE %query% 검색
- 날짜 겹침 조건 적용 (search_all의 event 부분과 동일)
- 반환: 경량 필드 목록

### 2-2. get_kanban_summary(project) 신규
- get_kanban_events()의 조건을 그대로 사용하되 경량 필드만 SELECT
- 반환 필드: id, title, project, kanban_status, priority, assignee, start_datetime, end_datetime
- project 파라미터 지원

### 2-3. search_kanban_mcp(query, project, viewer) 신규
- 칸반 조건 + title LIKE %query%
- 반환: 경량 필드 목록

### 2-4. search_documents_mcp(query, viewer) 신규
- search_all의 meetings 부분을 독립 함수로 추출
- 반환: id, title, author_name, team_name, updated_at

### 2-5. search_checklists_mcp(query, project, viewer) 신규
- search_all의 checklists 부분을 독립 함수로 추출
- project 파라미터 지원
- 반환: id, title, project, updated_at + item_count, done_count (get_checklists_summary 참고)

---

## 변경 대상 파일
- `mcp_server.py` — search→search_all rename, 신규 도구 6개 추가
- `database.py` — 신규 DB 함수 5개 추가

## 변경하지 않는 것
- 기존 list_*/get_* 도구 — 시그니처·동작 유지 (이름 변경 없음)
- app.py, permissions.py, 프론트엔드 — 변경 없음
- DB 스키마 — 변경 없음

## 검증 게이트
1. search_all("키워드") → 이전 search()와 동일한 결과
2. search_events("키워드") → event type 결과만, ±7일 기본값 적용
3. list_kanban() → 칸반 보드 기준 항목 (경량)
4. get_kanban_item(id) → 기존 get_event(id)와 동일한 풀 필드
5. search_kanban("키워드") → 칸반 항목만, project 필터 동작
6. search_documents("키워드") → document type 결과만
7. search_checklists("키워드") → checklist type 결과 + item_count/done_count 포함
8. 기존 list_*/get_* 도구 회귀 없음
