# 코드 리뷰 보고서 — 39차: MCP 도구 도메인별 재편

## 리뷰 대상 파일

- `D:\Github\WhatUdoin\mcp_server.py` (search→search_all rename + 도구 6개 신규)
- `D:\Github\WhatUdoin\database.py` (search_all 시그니처 확장 + 신규 함수 5개)

리뷰 시점: 39차 backend-dev 구현 완료 직후 (이전 보고서 없음 — 신규 작성).

---

## 리뷰 포인트별 결과

### 1. 기존 도구(list_events, get_event 등) 무변경 확인 — 통과

`git diff HEAD -- mcp_server.py`로 제거 라인을 전수 확인했고, 기존 도구 영역(`list_projects`, `list_events`, `get_event`, `list_documents`, `get_document`, `list_checklists`, `get_checklist`)에서 제거된 라인은 **단 두 줄**:

- `-async def search(` (rename)
- `-    return db.search_all(query=query, type=type, viewer=user)` (search_all 본문 내부 한 줄)

→ 7개 기존 도구의 시그니처·본문·docstring 모두 무변경. 회귀 위험 없음.

### 2. search → search_all rename 내부 호출 반영 — 통과

- `mcp_server.py` 라인 213: `async def search_all(...)` 정의
- `mcp_server.py` 라인 252: `db.search_all(...)` DB 호출 (DB 함수명은 원래 search_all이라 정합)
- `mcp_server.py` 다른 위치에서 `search()` 호출 없음 (Grep 검증)
- 코드베이스 전체에서 `db.search`/`database.search` 호출 0건 (Grep 검증)

→ 내부 호출 정합성 통과. **단, QA 주의사항 그대로**: `tests/phase43_mcp_notion.py`(417/446/468/486/503/513)에 `"search"` 도구명 잔존 — 이는 backend_changes.md에 이미 명시됐고 QA 단계에서 처리할 사안.

### 3. SQL injection 안전성 (파라미터 바인딩) — 통과

신규 함수 5개의 모든 SQL 실행 부위 검사:

| 함수 | 사용자 입력 바인딩 | f-string 부분 |
|------|----------------------|----------------|
| `search_events_mcp` | `like` (라인 2882), `start_after`/`end_before` (params에 append, 2892/2895) → 모두 `?` 파라미터 | 없음 |
| `get_kanban_summary` | `project` (라인 2948) → `?` 파라미터 | `private_clause`/`base_filter` 정적 SQL 조각만 합성, 사용자 입력 없음 |
| `search_kanban_mcp` | `like`, `project` 모두 `?` 파라미터 (2994/2999/3004) | 동일 — 정적 조각만 |
| `search_documents_mcp` | `like` 2회, `uid`, `tid` 모두 `?` 파라미터 (3029/3034/3048) | f-string 사용 안 함 |
| `search_checklists_mcp` | `like` 2회, `project` 모두 `?` 파라미터 (3087/3097/3107) | `inactive_filter`/`public_filter` 정적 조각만 |

→ 사용자 입력이 SQL에 직접 보간된 곳 0건. f-string은 viewer 분기에 따른 정적 절(predicate) 합성에만 쓰이며 사용자 데이터를 끼워넣지 않음. **injection-safe**.

### 4. get_kanban_summary viewer 권한 필터가 get_kanban_events 패턴과 일치 — 통과

`get_kanban_summary`(2902-2950) vs `get_kanban_events`(850-893) 비교:

- `private_clause` 블록: 라인 2909-2918과 857-866 **완전 동일** (들여쓰기, 조건절, viewer is None 분기까지 일치)
- `base_filter` 블록: 라인 2919-2934와 867-882 **완전 동일** (kanban_status/inactive project/is_active/kanban_hidden/done_at/event_type/recurrence_parent_id/deleted_at/private_clause 순서 및 조건 일치)
- 차이점: `team_id` 분기 대신 `project` 3-way 분기(None/""/문자열) 사용 — 이는 spec(MCP는 project 기반 필터)에 부합하며 `get_checklists_summary`(2715-2758) 패턴과 일관됨

→ 권한 필터(private_clause) 동등성 보장. 비공개 프로젝트 항목이 viewer=None일 때만 숨겨지고 인증 사용자에게는 노출되는 동작 일치.

같은 검증을 `search_kanban_mcp`(2953-3006)에도 적용 — `private_clause`/`base_filter`는 `get_kanban_summary`와 글자 그대로 동일. 일관성 통과.

### 5. get_kanban_item이 get_event_for_mcp() 재사용 — 통과

`mcp_server.py` 라인 335:
```python
result = db.get_event_for_mcp(event_id)
if result is None:
    return {"error": "not_found", ...}
return result
```
`get_event`(라인 122-125)와 호출 본문이 동일 (error 메시지의 한국어 표현 "이벤트"→"칸반 항목"만 다름). 칸반 전용 권한 분기 같은 중복 구현 없음. spec("내부적으로 get_event와 동일한 DB 함수 사용") 준수.

→ 중복 구현 없음. DRY 원칙 준수.

### 6. 인증 체크(_user_from_ctx) 누락 여부 — 통과

신규/변경 도구 7개 전수 확인:

| 도구 | 인증 체크 라인 | 패턴 |
|------|-----------------|------|
| `search_all` | 246-247 | `user = _user_from_ctx(ctx); if user is None: raise PermissionError(...)` |
| `search_events` | 282-284 | 동일 |
| `list_kanban` | 315-317 | 동일 |
| `get_kanban_item` | 333-334 | `if _user_from_ctx(ctx) is None: raise PermissionError(...)` (user 변수 미사용 도구는 기존 `get_event`와 동일 단축형) |
| `search_kanban` | 365-367 | user 변수 패턴 |
| `search_documents` | 389-391 | user 변수 패턴 |
| `search_checklists` | 419-421 | user 변수 패턴 |

→ 7개 도구 전부 첫 줄에서 인증 검증. `_user_from_ctx`가 None이면 즉시 PermissionError. 누락 0건.

추가 확인: viewer 파라미터를 받는 DB 함수(`get_kanban_summary`, `search_kanban_mcp`, `search_documents_mcp`, `search_checklists_mcp`) 호출 시 모두 `viewer=user`로 전달 — 비공개 프로젝트/문서 가시성 필터가 정상 동작.

---

## 추가 정적 검증

### get_conn() contextmanager 사용 — 통과
신규 5함수 모두 `with get_conn() as conn:` 패턴 사용. 트랜잭션·연결 누수 우려 없음.

### DB 스키마 변경 — 해당 없음
backend_changes.md의 명시대로 스키마 변경 없음. `_migrate` 검토 항목 무관.

### 하위호환 — 통과
- `search` → `search_all` rename은 외부 호출자 영향 사안이지만, 코드베이스 내부에서 `search` 호출 없음 (테스트만 영향 → backend_changes.md QA 주의사항으로 인계)
- `database.search_all`은 시그니처에 키워드-only 파라미터 2개(`start_after`, `end_before`) 추가만 있어 기존 호출(`db.search_all(query=..., type=..., viewer=...)`)에 영향 없음
- 기존 컬럼 삭제·타입 변경 없음

### `search_documents_mcp` 가시성 로직 — 통과
viewer None / admin / 일반 사용자 3단계 분기가 `search_all`의 meetings 부분(2810-2833) 및 `get_all_meetings_summary`(2690-2711)와 일치. team_share·is_team_doc 조건까지 동일.

### `search_checklists_mcp` item_count/done_count 정규식 — 통과
라인 3113-3114의 정규식 패턴 `r'(?m)^\s*-\s+\[[ xX]\]'` / `r'(?m)^\s*-\s+\[[xX]\]'`이 `get_checklists_summary`(2753-2754)와 글자 그대로 동일.

---

## 차단(Blocking)

없음.

## 경고(Warning)

없음.

## 통과

- [x] 기존 도구 7개 무변경 (회귀 위험 없음)
- [x] search→search_all rename 내부 정합 (`db.search_all` 호출 1곳, 다른 곳 잔존 0건)
- [x] 신규 함수 5개 SQL injection-safe (모든 사용자 입력 `?` 바인딩)
- [x] `get_kanban_summary` viewer 필터가 `get_kanban_events`와 동등 (private_clause·base_filter 완전 일치)
- [x] `search_kanban_mcp` 필터도 동일 패턴 일관성 유지
- [x] `get_kanban_item` → `get_event_for_mcp` 재사용 (중복 구현 없음)
- [x] 신규/변경 도구 7개 전부 `_user_from_ctx` 인증 체크 적용
- [x] viewer 인자가 모든 viewer-aware DB 함수에 정상 전달
- [x] `get_conn()` contextmanager 패턴 준수
- [x] 하위호환 유지 (기존 시그니처는 키워드-only 추가, 컬럼 변경 없음)
- [x] `search_documents_mcp`·`search_checklists_mcp`의 가시성/정규식 패턴이 기존 패턴과 일치

---

## 최종 판정

**통과** — 차단 결함 0건, 경고 0건. QA(E2E) 진행 가능.

QA 단계에서 처리할 사항 (코드 리뷰 범위 외, backend_changes.md에 이미 명시):
- `tests/phase43_mcp_notion.py`의 6개 위치 `"search"` → `"search_all"` 도구명 갱신
- `search_kanban_mcp`는 title 전용 검색이라는 사실(content 매칭 없음) 반영한 케이스 작성
- `project=""` vs `project=None` 동작 차이 검증 케이스
