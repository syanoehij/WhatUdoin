# WhatUdoin — 개발 가이드

## 하네스: WhatUdoin 개발

**목표:** FastAPI + SQLite + Ollama 기반 인트라넷 일정 관리 앱의 기능 개발, 버그 수정, 테스트 자동화

**트리거:** WhatUdoin 코드 변경이 필요한 모든 작업(기능 추가, 버그 수정, UI 개선)에 `whatudoin-dev` 스킬을 사용하라. 단순 질문·코드 설명은 직접 응답 가능.

**임시 산출물 저장 위치:**
- 프로젝트 **루트에 PNG·JSON·log 같은 임시 파일을 직접 생성하지 않는다.** 누적되어 디렉토리 가시성을 해친다.
- Playwright `browser_take_screenshot` 호출 시:
  - `filename` 미지정 → 도구가 자동으로 `.playwright-mcp/`에 저장 (기본 권장)
  - `filename` 지정 시 → 반드시 명시적 폴더 경로 prefix를 붙인다. 예: `.claude/workspaces/current/screenshots/calendar_view.png`. 단순 파일명만 주면 루트에 저장되어 누적된다.
- 그 외 임시 산출물(diff snapshot, debug log, 분석용 JSON)도 `.claude/workspaces/current/` 하위에 저장한다.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-04-24 | 초기 구성 | 전체 | - |
| 2026-05-05 | QA 접속 URL 수정 (`localhost:8000` → `192.168.0.18:8443`) | agents/qa.md | 실제 환경 URL 불일치로 테스트 실패 방지 |
| 2026-05-09 | 워크스페이스 위치 이전 (`_workspace/` → `.claude/workspaces/current/`, archive 분리) | 7개 하네스 정의 + .gitignore | 루트 가시성 회복, 백업 정책 일관성 |
| 2026-05-09 | 임시 산출물 저장 위치 정책 추가 | CLAUDE.md | 루트에 디버깅 PNG 46개 + .playwright-mcp/ 8.1MB 누적 사고 방지 |
| 2026-05-09 | 플래너 에이전트 추가, whatudoin-dev 단순화 | agents/planner.md, skills/whatudoin-dev/SKILL.md | 메인 컨텍스트 보호: 플래너가 코드 탐색·분류·팀 지휘 전담, 메인에는 한 줄 요약만 반환 |
| 2026-05-09 | planner 모델 sonnet → opus | agents/planner.md | 분류·지휘 판단 품질 향상 |

# CLAUDE.md

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

