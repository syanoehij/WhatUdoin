# QA 보고서 — 39차: MCP 도구 도메인별 재편

## 대상 변경

- `mcp_server.py`: `search` → `search_all` rename + 신규 도구 6개
  (`search_events`, `list_kanban`, `get_kanban_item`, `search_kanban`,
   `search_documents`, `search_checklists`)
- `database.py`: 신규 함수 5개 (`search_events_mcp`, `get_kanban_summary`,
   `search_kanban_mcp`, `search_documents_mcp`, `search_checklists_mcp`)

## 수행 작업

### 작업 1 — phase43_mcp_notion.py 도구명 갱신

`"search"` → `"search_all"` 6곳 갱신 (호출 코드 + 에러 메시지 텍스트).
Grep 후속 검증으로 잔존 0건, `search_all` 신규 등장 15곳 확인.

추가 surgical fix (회귀 가드):
- 39차에 `search_all`이 `start_after`/`end_before` 미지정 시 ±7일 기본값을
  자동 적용하도록 변경됨. phase43 fixture event 는 2027-09-15 / 2027-09-16
  로 ±7일 범위 밖이라 t04 / t05 가 깨질 위험.
- t04 (전체 검색)와 t05 (type=event)의 호출 인자에 `start_after="2000-01-01"`
  추가 — 38차 동등 동작 보존.
- t06 (type=document) / t07 (type=checklist) / t08 (빈쿼리) / t09 (NEVER) 는
  날짜 필터 영향이 없으므로 그대로 유지.

### 작업 2 — phase44_mcp_domain.py 신규 작성

phase43 패턴을 그대로 따라(`__phase44_mcp_` prefix, asyncio, httpx) 10개
케이스 작성. 사용자 요구 8개 케이스를 모두 커버하며, 추가 회귀 가드 2건 포함.

| # | 테스트 | 검증 포인트 |
|---|--------|------------|
| 01 | `search_all_compatible` | 기존 search 와 동일하게 type 필드 포함 — document/checklist 매치 (event 는 t02/t03 에서 분리 검증) |
| 02 | `search_events_default_range` | ±7일 기본값 동작 — 미래 fixture event 는 결과 제외 (음성 검증) |
| 03 | `search_events_widened_range` | `start_after="2000-01-01"` 시 fixture event 두 건 모두 포함 + default ⊂ wide 단조 증가 |
| 04 | `list_kanban_lightweight` | 칸반 항목 반환, `kanban_status="todo"` / `priority="high"` 포함, description 등 무거운 필드 부재 |
| 05 | `get_kanban_item_full_content` | 풀 필드 반환 (description 노출, get_event 와 동일) |
| 06 | `get_kanban_item_not_found` | 존재하지 않는 id → `{"error": "not_found"}` |
| 07 | `search_kanban_only` | 칸반 항목만, type 필드 없음, kanban_status/priority 포함, 무거운 필드 부재 |
| 08 | `search_documents_only` | 문서만, content 키 없음, type 필드 없음, 경량 5필드 (id/title/author_name/team_name/updated_at) |
| 09 | `search_checklists_only` | 체크리스트만, content 키 없음, item_count=3 / done_count=2 |
| 10 | `empty_query_guards` | search_events / search_kanban / search_documents / search_checklists 빈 쿼리 → [] |

### Fixture 설계 핵심

- `event_future` (2027-09-15, kanban_status=NULL): `search_events` 의
  ±7일 기본 범위에서 제외돼야 함을 검증 (t02 음성 / t03 양성).
- `event_kanban` (2027-09-20, kanban_status="todo", priority="high"):
  `list_kanban` / `search_kanban` 검증 대상.
- `document` (is_public=1, 본문 UNIQ 미포함, title 만 UNIQ): title 검색만으로
  매치되는지 / content 키가 응답에 노출되지 않는지 검증.
- `checklist` (item_count=3 / done_count=2 정확히 가산): regex 가산 정확성 검증.

## 정적 검증 결과

- [x] phase43: `"search"` 토큰 잔존 0건 (Grep 검증)
- [x] phase43: 39차 ±7일 회귀 가드 (t04/t05 에 `start_after="2000-01-01"` 추가)
- [x] phase44: AST 파싱 통과 (`python -c "import ast; ast.parse(...)"`)
- [x] phase43: AST 파싱 통과
- [x] 백엔드 시그니처 정합 — `mcp_server.py` 신규 6개 도구 + `database.py` 신규 5개 함수
- [x] 반환 필드 정합 — backend_changes.md / 코드 리뷰 보고서의 "반환 필드" 절과 phase44 의 `_EXPECTED_*` 상수 일치
- [x] `search_kanban_mcp` 가 title 전용 검색이라는 사실(QA 주의사항) 반영 — fixture event_kanban title 에 UNIQ 포함, content 검색 미가정

## 미해결 / 후속 작업

### 서버 재시작 필요 (E2E 실행 차단)

- `mcp_server.py` 에 신규 도구 6개가 추가됐고 `search` → `search_all` rename 됐다.
- 현재 실행 중인 VSCode 디버깅 서버는 38차 시점 등록 도구 (search 포함) 만
  알고 있으므로 phase44 / 갱신된 phase43 둘 다 신규 도구를 찾지 못해 실패한다.
- **사용자에게 서버 재시작 요청 필요.** 재시작 완료 후 다음 명령으로 실행:

  ```
  "D:\Program Files\Python\Python312\python.exe" tests/phase43_mcp_notion.py --base-url https://192.168.0.18:8443
  "D:\Program Files\Python\Python312\python.exe" tests/phase44_mcp_domain.py --base-url https://192.168.0.18:8443
  ```

### 동적 실행 미수행

본 보고서는 **정적 분석 + 코드 작성** 단계까지만 검증됐다. 실제 MCP
호출 / DB 조회 동작은 서버 재시작 후 실행을 통해 추가 검증해야 한다.
실행 결과는 별도 후속 보고로 갱신 예정.

## 통과 ✅

- [x] phase43 6곳 도구명 갱신
- [x] phase43 t04/t05 39차 ±7일 회귀 가드 추가
- [x] phase44 신규 작성 — 사용자 요구 8개 케이스 + 회귀 가드 2건 (총 10건)
- [x] phase43/phase44 AST 파싱 통과
- [x] `_workspace/backend_changes.md` 의 QA 주의사항 모두 반영
  (search_all rename, title 전용 search_kanban, 반환 필드 정합)

## 실패 ❌

(서버 재시작 전이므로 동적 실행 미수행)

## 회귀 확인

- phase43 의 11개 케이스는 `search_all` rename 외 변경 없음. t04/t05 의
  ±7일 가드는 38차 동등 동작 보존이므로 회귀 위험 없음.

## 참고 — 산출 파일 경로 (모두 절대 경로)

- `D:\Github\WhatUdoin\tests\phase43_mcp_notion.py` (수정)
- `D:\Github\WhatUdoin\tests\phase44_mcp_domain.py` (신규)
- `D:\Github\WhatUdoin\_workspace\qa_report.md` (본 보고서)
