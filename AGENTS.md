# WhatUdoin Codex Harness

이 파일은 Codex가 WhatUdoin repo에서 작업할 때 항상 우선 적용할 프로젝트 하네스다.

## 하네스 목표

- FastAPI + SQLite + Ollama 기반 인트라넷 일정 관리 앱의 기능 개발, 버그 수정, UI 개선, 테스트 자동화를 안정적으로 수행한다.
- 변경은 작고 검증 가능하게 유지한다.
- `whatudoin-dev` 역할 분리와 산출물 흐름을 Codex 방식으로 운용한다.

## 기본 행동 원칙

- 작업 전 요구사항, 영향 범위, 성공 기준을 짧게 정리한다.
- 모호한 요구는 구현 전에 질문한다. 단, repo에서 확인 가능한 내용은 먼저 직접 확인한다.
- 요청과 직접 관련 없는 리팩터링, 포맷 변경, 죽은 코드 삭제는 하지 않는다.
- 기존 코드 스타일과 패턴을 우선한다.
- 변경한 코드가 만든 unused import, 변수, 함수는 정리한다.
- 검증 없이 완료로 보고하지 않는다. 검증이 불가능하면 이유를 명확히 남긴다.

## 일반 LLM 행동 지침

이 섹션은 WhatUdoin Codex 작업에 적용하는 공통 행동 지침이다.

# Common LLM Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Codex 실행 모델

Codex에서는 메인 오케스트레이터가 역할별 subagent를 직접 생성하고 산출물 파일로 단계를 연결한다.

- 기본: Codex 본체는 오케스트레이터 역할만 맡고, planning/backend/frontend/review/qa 단계는 실제 Codex subagent를 역할별로 나누어 수행한다.
- 작은 버그 수정, 작은 UI 수정, 하네스 문서 자체의 작은 수정은 planner 없이 오케스트레이터가 바로 구현/review/qa 흐름을 시작할 수 있다.
- 기능 추가, DB/API/UI가 함께 바뀌는 작업, 성능 개선, 권한/보안 변경, 범위가 애매한 작업은 먼저 planner subagent를 실행한다.
- planner는 구현하거나 다른 subagent를 직접 호출하지 않는다. 요구사항 분석, 실행 계획, 다음 역할에게 넘길 짧은 dispatch 메모만 산출한다.
- 실제 subagent 호출권과 중단/범위 변경 판단은 항상 Codex 본체 오케스트레이터가 유지한다.
- subagent는 메인 대화와 분리된 컨텍스트로 실행한다. 필요한 요구사항, 파일 경로, 이전 단계 산출물만 명시적으로 전달한다.
- 역할 간 공유 상태는 대화 기억이 아니라 `.codex/workspaces/current/*.md` 산출물을 기준으로 한다.
- subagent의 최종 채팅 응답은 짧게 유지하고, 상세 분석과 검증 기록은 산출물 파일에 남긴다.
- 단순 질의, 코드 설명, 로그 해석, 하네스 문서 자체의 작은 수정은 Codex 본체가 직접 처리할 수 있다.
- subagent 도구를 사용할 수 없는 환경이면 본체가 직접 진행하되, 최종 보고에 fallback 사실과 이유를 명확히 남긴다.
- 역할별 세부 지침은 `.codex/harness/roles/`를 따른다.
- 오케스트레이션 상세는 `.codex/harness/whatudoin-dev.md`를 따른다.

## 트리거

WhatUdoin 코드 변경이 필요한 모든 작업에는 `whatudoin-dev` 하네스 흐름을 사용한다.

- 기능 추가
- 버그 수정
- UI 개선
- DB 스키마 변경
- Playwright 테스트 추가 또는 수정
- 이전 변경의 보완, 재작업, 이어서 작업

단순 질문, 코드 설명, 로그 해석은 직접 답변해도 된다.

## 프로젝트 구조 핵심

- `app.py`: FastAPI 페이지 라우트와 JSON API
- `database.py`: SQLite CRUD, `init_db()`, `_migrate()`
- `auth.py`, `crypto.py`: 세션 인증과 암호화
- `llm_parser.py`: Ollama 자연어 파싱
- `templates/*.html`: Jinja2 템플릿과 임베디드 JavaScript
- `static/js/*.js`: 공통 프론트엔드 스크립트
- `tests/*.spec.js`: Playwright E2E 테스트

## 백엔드 규칙

- 새 JSON API는 `/api/...` 패턴을 사용한다.
- 페이지 라우트는 `HTMLResponse`와 `templates.TemplateResponse(request, template, _ctx(request, ...))` 패턴을 사용한다.
- 쓰기/수정/삭제 작업에는 `_require_editor(request)` 또는 `_require_admin(request)`를 적용한다.
- SQL에는 파라미터 바인딩을 사용하고, 사용자 입력을 f-string으로 직접 삽입하지 않는다.
- DB 스키마 변경은 별도 migration 파일을 만들지 않고 `database.py`의 `_migrate(conn, table, columns)` 또는 `CREATE TABLE IF NOT EXISTS` 패턴으로 처리한다.
- 기존 데이터 손실이 생기는 컬럼 삭제, 타입 변경, 테이블 재생성은 하지 않는다.
- PyInstaller 대응 경로 규칙을 지킨다. 정적 자원은 `_BASE_DIR`, DB/업로드 등 쓰기 파일은 `_RUN_DIR`를 사용한다.
- Ollama 연동 실패는 graceful degradation으로 처리한다.

## 프론트엔드 규칙

- 템플릿은 `{% extends "base.html" %}`와 `{% block content %}` 구조를 따른다.
- Vanilla JS와 `fetch()`를 사용한다.
- `fetch()` 응답은 `response.ok`를 확인한 뒤 JSON을 파싱한다.
- 실패 시 기존 `showToast(message, 'error')` 패턴으로 사용자 피드백을 준다.
- 사용자 입력을 `innerHTML`에 직접 넣지 않는다. 기본은 `textContent` 또는 Jinja2 자동 이스케이프를 사용한다.
- 기존 UI 패턴을 재사용한다. 예: `.assignee-chip`, `.badge-*`, modal `active` 클래스, 기존 toast 함수.
- FullCalendar, TUI Editor, Flatpickr, Highlight.js는 repo의 로컬 라이브러리를 우선 사용한다.

## QA 규칙

- 서버는 사용자가 VSCode 디버깅 모드로 실행 중일 수 있다. 코드 변경 뒤 서버 재시작이 필요하면 사용자에게 요청하고 직접 kill/restart하지 않는다.
- Playwright 테스트는 `tests/phaseN_*.spec.js` 컨벤션을 따른다.
- 테스트는 단순 존재 확인보다 API 응답과 UI 렌더링의 경계면 정합성을 검증한다.
- 신규 기능은 골든 패스, 빈 상태/오류 상태, 권한 경계를 우선 검증한다.

## 표준 산출물

작업이 실질적으로 진행되면 `.codex/workspaces/current/`에 다음 파일을 필요 범위만 작성한다.
Codex subagent 간 전달도 이 폴더의 산출물을 기준으로 한다.

- `.codex/workspaces/current/00_input/feature_spec.md`: 요청 분석, 범위, 성공 기준
- `.codex/workspaces/current/execution_plan.md`: planner가 작성한 단계별 실행 계획
- `.codex/workspaces/current/dispatch_notes.md`: 오케스트레이터가 다음 subagent 프롬프트에 넣을 짧은 전달 메모
- `.codex/workspaces/current/backend_changes.md`: 백엔드 변경 요약, API, DB 변경
- `.codex/workspaces/current/frontend_changes.md`: 프론트 변경 요약, UI 흐름
- `.codex/workspaces/current/code_review_report.md`: 정적 리뷰 결과
- `.codex/workspaces/current/qa_report.md`: 테스트/검증 결과

새로운 독립 작업을 시작하는데 기존 `.codex/workspaces/current/`가 있으면 기존 디렉터리를 `.codex/workspaces/YYYYMMDD_HHMMSS/`로 보관한 뒤 새 `.codex/workspaces/current/`를 만든다.

## Git 추적 범위

공유해야 하는 repo-wide 지침과 Codex 하네스 정의는 git 추적 대상으로 유지한다.

- `AGENTS.md`
- `.codex/config.toml`
- `.codex/agents/**`
- `.codex/harness/**`

하네스 실행 산출물과 개인/임시 작업 공간은 git 추적 대상이 아니다.

- `.codex/workspaces/**`
- `.claude/workspaces/**`
- `_workspace_*/`

## 검증 명령

상황에 맞게 최소 검증을 선택한다.

- Python import/문법 확인: `& 'D:\Program Files\Python\Python312\python.exe' -m py_compile app.py database.py llm_parser.py auth.py crypto.py backup.py`
- 앱 import 확인: `& 'D:\Program Files\Python\Python312\python.exe' -c "import app; print('OK')"`
- 번들 확인: `npx.cmd rollup -c`
- Playwright: `npx.cmd playwright test`

검증 명령이 환경 문제, 서버 미실행, 권한, 네트워크 제한으로 실패하면 원인과 다음 조치를 보고한다.
