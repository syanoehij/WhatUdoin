# Backend Changes — 39차: MCP 도구 도메인별 재편

## 변경 파일

- `mcp_server.py`
- `database.py`

---

## mcp_server.py

### 1. search → search_all 이름 변경

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| 함수명 | `async def search(...)` | `async def search_all(...)` |
| 동작 | 동일 (코드 무변경) | 동일 |
| DB 호출 | `db.search_all(...)` | `db.search_all(...)` (변경 없음) |

### 2. 신규 도구 6개 추가

#### `search_events(ctx, query, start_after=None, end_before=None)`
- 일정 전용 키워드 검색
- 날짜 미지정 시 오늘 ±7일 기본값 적용 (search_all과 동일 로직)
- DB: `db.search_events_mcp()`
- 반환 필드: id, title, project, start_datetime, end_datetime, assignee, kanban_status, event_type

#### `list_kanban(ctx, project=None)`
- 칸반 보드 기준 항목 경량 목록
- DB: `db.get_kanban_summary(project=project, viewer=user)`
- 반환 필드: id, title, project, kanban_status, priority, assignee, start_datetime, end_datetime

#### `get_kanban_item(ctx, event_id)`
- 칸반 맥락 전용 상세 조회 (내부적으로 `db.get_event_for_mcp()` 호출 — get_event와 동일)
- 존재하지 않는 id → `{"error": "not_found", ...}` 반환

#### `search_kanban(ctx, query, project=None)`
- 칸반 항목 title 키워드 검색 (날짜 필터 없음)
- DB: `db.search_kanban_mcp(query, project, viewer=user)`
- 반환 필드: id, title, project, kanban_status, priority, assignee

#### `search_documents(ctx, query)`
- 문서(회의록) title+content 키워드 검색
- DB: `db.search_documents_mcp(query, viewer=user)`
- 반환 필드: id, title, author_name, team_name, updated_at

#### `search_checklists(ctx, query, project=None)`
- 체크리스트 title+content 키워드 검색
- DB: `db.search_checklists_mcp(query, project, viewer=user)`
- 반환 필드: id, title, project, updated_at, item_count, done_count

### 최종 도구 목록 (총 14개)

| 도구명 | 비고 |
|--------|------|
| `list_projects` | 기존 유지 |
| `list_events` | 기존 유지 |
| `get_event` | 기존 유지 |
| `list_documents` | 기존 유지 |
| `get_document` | 기존 유지 |
| `list_checklists` | 기존 유지 |
| `get_checklist` | 기존 유지 |
| `search_all` | 기존 `search` rename |
| `search_events` | 신규 |
| `list_kanban` | 신규 |
| `get_kanban_item` | 신규 |
| `search_kanban` | 신규 |
| `search_documents` | 신규 |
| `search_checklists` | 신규 |

---

## database.py

### 신규 함수 5개 (`search_all` 함수 바로 아래에 추가)

#### `search_events_mcp(query, start_after=None, end_before=None)`
- events 테이블 title LIKE %query% 검색
- 날짜 겹침 조건: search_all events 부분과 동일 로직
- 빈 query → `[]` 조기 반환
- 이벤트 프라이버시 필터 없음 (search_all 기존 동작과 동일 — 의도된 동작)

#### `get_kanban_summary(project=None, viewer=None)`
- get_kanban_events()의 필터 조건 재사용, 경량 필드만 SELECT
- project 파라미터: `None`=전체, `""`=프로젝트 미지정 항목만, 문자열=해당 프로젝트만
- **spec 대비 변경**: `viewer` 파라미터 추가 — get_checklists_summary 패턴 준수. 없으면 인증된 MCP 사용자에게도 공개 필터가 적용되어 비공개 프로젝트 항목이 누락됨.

#### `search_kanban_mcp(query, project=None, viewer=None)`
- 칸반 조건 + title LIKE %query% (title 전용 검색, events에 content 컬럼 없음)
- project 파라미터: `None`/`""`/문자열 3단계 분기 (get_checklists_summary 패턴 준수)
- 빈 query → `[]` 조기 반환

#### `search_documents_mcp(query, viewer=None)`
- search_all의 meetings 부분 독립 추출
- title LIKE % OR content LIKE % 검색
- 가시성 로직: viewer None/admin/일반 사용자 3단계 분기 유지
- 빈 query → `[]` 조기 반환

#### `search_checklists_mcp(query, project=None, viewer=None)`
- search_all의 checklists 부분 독립 추출 + project 필터 추가
- title LIKE % OR content LIKE % 검색
- item_count/done_count: get_checklists_summary와 동일 정규식 패턴 사용
- project 파라미터: `None`/`""`/문자열 3단계 분기
- 빈 query → `[]` 조기 반환

---

## DB 스키마 변경

없음 (신규 함수만 추가, 스키마 변경 없음)

---

## QA 주의사항

- `tests/phase43_mcp_notion.py` 내 `"search"` 도구명 참조(417, 446, 468, 486, 503, 513번째 줄)를 `"search_all"`로 업데이트 필요
- `search_kanban_mcp`는 title 전용 검색 (description/content 매칭 없음)
- `list_kanban`/`search_kanban`의 `project=""` 파라미터는 프로젝트 미지정 항목만 반환 (`project=None`과 다름)
