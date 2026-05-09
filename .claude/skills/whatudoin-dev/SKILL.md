---
name: whatudoin-dev
description: WhatUdoin 기능 개발 오케스트레이터. 신기능 추가, 버그 수정, UI 개선 등 모든 개발 작업 요청 시 사용. "~기능 추가해줘", "~버그 수정해줘", "~개선해줘", "~만들어줘" 등 WhatUdoin 코드 변경이 필요한 모든 요청에 반드시 이 스킬을 사용. 후속 작업(결과 수정, 보완, 다시 해줘, 이어서, 이전 결과 개선)도 반드시 이 스킬을 사용.
---

# WhatUdoin 개발 오케스트레이터

## 실행 모드

- **기능 추가**: 에이전트 팀 (backend-dev + frontend-dev + qa 협업)
- **단순 버그 수정 / 단일 파일 변경**: 서브 에이전트 (해당 에이전트만 호출)
- **DB 스키마만 변경**: 서브 에이전트 (backend-dev만)
- 오케스트레이터가 요청을 분석하여 자동 선택

## 에이전트 구성

| 팀원 | 타입 | 역할 | 스킬 |
|------|------|------|------|
| backend-dev | general-purpose | FastAPI, DB, LLM, 인증 | backend |
| frontend-dev | general-purpose | 템플릿, JS, UI | frontend |
| code-reviewer | general-purpose | 정적 코드 검토, 패턴·보안 검증 | code-review |
| qa | general-purpose | Playwright 테스트, 경계면 검증 | qa |

## 워크플로우

### Phase 0: 컨텍스트 확인

1. `_workspace/` 디렉토리 존재 여부 확인
2. 실행 모드 결정:
   - `_workspace/` 미존재 → **초기 실행** → Phase 1 진행
   - `_workspace/` 존재 + 부분 수정 요청 → **부분 재실행** → 해당 에이전트만 재호출
   - `_workspace/` 존재 + 새 기능 요청 → **새 실행** → 기존 `_workspace/`를 `_workspace_{YYYYMMDD_HHMMSS}/`로 이동 후 Phase 1

### Phase 1: 요청 분석

1. 사용자 요청을 다음 기준으로 분류:
   - **기능 추가**: 새 라우트, 새 DB 테이블/컬럼, 새 UI 페이지/컴포넌트
   - **버그 수정**: 특정 파일·함수의 오동작 수정
   - **UI 개선**: 프론트엔드만 변경 (백엔드 수정 불필요)
   - **DB 변경**: 스키마 추가/수정만

2. 실행 모드 최종 결정:
   - 기능 추가 → **에이전트 팀** (3인)
   - 버그 수정 (백엔드) → **서브 에이전트** (backend-dev만)
   - 버그 수정 (프론트) → **서브 에이전트** (frontend-dev만)
   - UI 개선 → **서브 에이전트** (frontend-dev만)
   - DB 변경 → **서브 에이전트** (backend-dev만)

3. `_workspace/` 생성, `_workspace/00_input/feature_spec.md`에 분석 결과 저장

### Phase 2: 팀 구성 (에이전트 팀 모드만)

단순 버그/단일 파일 변경은 Phase 2를 건너뛰고 Phase 3에서 서브 에이전트 직접 호출.

```
TeamCreate(
  team_name: "whatudoin-team",
  members: [
    {
      name: "backend-dev",
      agent_type: "general-purpose",
      model: "sonnet",
      prompt: "당신은 WhatUdoin 백엔드 개발자입니다. .claude/agents/backend-dev.md와 .claude/skills/backend/SKILL.md를 먼저 읽고 작업하세요. _workspace/00_input/feature_spec.md를 읽어 담당 작업을 파악하세요."
    },
    {
      name: "frontend-dev",
      agent_type: "general-purpose",
      model: "sonnet",
      prompt: "당신은 WhatUdoin 프론트엔드 개발자입니다. .claude/agents/frontend-dev.md와 .claude/skills/frontend/SKILL.md를 먼저 읽고 작업하세요. _workspace/00_input/feature_spec.md를 읽어 담당 작업을 파악하세요. backend-dev의 완료 메시지를 받은 후 구현을 시작하세요."
    },
    {
      name: "code-reviewer",
      agent_type: "general-purpose",
      model: "opus",
      prompt: "당신은 WhatUdoin 코드 리뷰어입니다. .claude/agents/code-reviewer.md와 .claude/skills/code-review/SKILL.md를 먼저 읽고 작업하세요. backend-dev와 frontend-dev의 완료 메시지를 모두 받은 후 코드 리뷰를 시작하세요."
    },
    {
      name: "qa",
      agent_type: "general-purpose",
      model: "opus",
      prompt: "당신은 WhatUdoin QA 엔지니어입니다. .claude/agents/qa.md와 .claude/skills/qa/SKILL.md를 먼저 읽고 작업하세요. code-reviewer의 리뷰 완료 메시지를 받은 후 테스트를 작성하세요. 서버 재시작이 필요하면 반드시 사용자에게 요청하세요."
    }
  ]
)
```

작업 등록:
```
TaskCreate(tasks: [
  { title: "백엔드 구현", description: "API 라우트, DB 스키마, 비즈니스 로직 구현", assignee: "backend-dev" },
  { title: "프론트엔드 구현", description: "템플릿, JS 구현 (백엔드 완료 후)", assignee: "frontend-dev", depends_on: ["백엔드 구현"] },
  { title: "코드 리뷰", description: "정적 코드 검토, 패턴·보안·권한 체크 준수 검증", assignee: "code-reviewer", depends_on: ["백엔드 구현", "프론트엔드 구현"] },
  { title: "E2E 테스트 작성", description: "Playwright 테스트, 경계면 검증", assignee: "qa", depends_on: ["코드 리뷰"] },
])
```

### Phase 3: 구현

**에이전트 팀 모드:**
- 팀원들이 작업 목록에서 자신의 작업을 수행하고 SendMessage로 조율
- 리더(오케스트레이터)는 진행 상황 모니터링
- 의존성 있는 작업(프론트, QA)은 이전 단계 완료 확인 후 시작

**서브 에이전트 모드 (3-에이전트 순차 실행):**
```
# 1단계: 구현
Agent(
  subagent_type: "general-purpose",
  model: "sonnet",
  prompt: "당신은 WhatUdoin [백엔드/프론트엔드] 개발자입니다.
           [해당 agents/.md]와 [해당 skills/.md]를 먼저 읽으세요.
           요청: [구체적인 작업 내용]
           변경이 필요한 파일: [파일 목록]
           완료 후 _workspace/[backend|frontend]_changes.md에 변경 내용을 기록하세요."
)

# 구현 완료 확인 후 2단계: 코드 리뷰
Agent(
  subagent_type: "general-purpose",
  model: "opus",
  prompt: "당신은 WhatUdoin 코드 리뷰어입니다.
           .claude/agents/code-reviewer.md와 .claude/skills/code-review/SKILL.md를 먼저 읽으세요.
           _workspace/[backend|frontend]_changes.md를 읽고 변경된 파일들을 코드 리뷰하세요.
           결과를 _workspace/code_review_report.md에 기록하세요."
)

# 리뷰 통과 확인 후 3단계: 핀포인트 QA
Agent(
  subagent_type: "general-purpose",
  model: "opus",
  prompt: "당신은 WhatUdoin QA 엔지니어입니다.
           .claude/agents/qa.md와 .claude/skills/qa/SKILL.md를 먼저 읽으세요.
           _workspace/[backend|frontend]_changes.md와 _workspace/code_review_report.md를 읽으세요.
           전체 E2E 테스트가 아닌 이번 변경사항만 검증하는 핀포인트 테스트를 작성하고 실행하세요.
           서버 재시작이 필요하면 반드시 사용자에게 요청하세요.
           결과를 _workspace/qa_report.md에 기록하세요."
)
```

### Phase 4: 결과 수집 및 검토

1. 각 에이전트의 산출물 파일 확인:
   - `_workspace/backend_changes.md`
   - `_workspace/frontend_changes.md`
   - `_workspace/code_review_report.md`
   - `_workspace/qa_report.md`
2. 팀 정리 (팀 모드인 경우)
3. 사용자에게 결과 요약 보고:
   - 변경된 파일 목록
   - 주요 구현 내용
   - QA 결과 (통과/실패)
   - 서버 재시작 필요 여부 안내

## 에러 핸들링

- **에이전트 실패**: 해당 작업만 재시도 1회, 재실패 시 나머지 없이 진행 + 사용자 보고
- **서버 재시작 필요**: qa 에이전트가 사용자에게 직접 요청 (오케스트레이터가 대신 처리하지 않음)
- **백엔드-프론트 불일치**: qa 에이전트가 발견 시 각 에이전트에게 SendMessage로 보고, 1회 수정 후 재검증

## 데이터 전달 프로토콜

| 전달 경로 | 방식 |
|---------|------|
| 백엔드 → 프론트 | SendMessage (API 스펙) + `_workspace/backend_changes.md` |
| 백엔드 → 리뷰어 | SendMessage (구현 완료) + `_workspace/backend_changes.md` |
| 프론트 → 리뷰어 | SendMessage (구현 완료) + `_workspace/frontend_changes.md` |
| 리뷰어 → QA | SendMessage (리뷰 통과 신호) + `_workspace/code_review_report.md` |
| 리뷰어 → 구현 에이전트 | SendMessage (차단 결함, 수정 요청) |
| 각 에이전트 → 리더 | SendMessage (완료 보고) + `_workspace/*.md` |

## 테스트 시나리오

### 정상 흐름 — 신기능 추가
1. 사용자: "캘린더에 반복 일정 색상 변경 기능 추가해줘"
2. Phase 1: 기능 추가 분류 → 에이전트 팀 모드
3. backend-dev: `events` 테이블에 `color` 컬럼 추가, `/api/events/{id}` PUT에 color 파라미터 추가
4. frontend-dev: 색상 선택 UI 추가, FullCalendar `eventColor` 옵션 연동
5. code-reviewer: `_migrate` 패턴 준수, `_require_editor` 적용, `response.ok` 체크 확인 → 통과
6. qa: 색상 선택 → 저장 → 캘린더 반영 E2E 테스트 작성
7. 결과 보고: 변경 파일 목록 + 코드 리뷰 통과 + QA 통과 여부

### 에러 흐름 — 서버 재시작 필요
1. qa 에이전트: Playwright 실행 전 코드 변경 감지
2. qa → 사용자: "서버를 재시작해 주세요"
3. 사용자 재시작 후 확인 메시지
4. qa: 테스트 실행 재개
