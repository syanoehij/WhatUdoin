# whatudoin-dev Harness

WhatUdoin 코드 변경 작업을 위한 Codex 오케스트레이터 지침이다.

## 실행 모드

- 중간 이상 기능 추가: planner -> 오케스트레이터 확인/dispatch -> backend-dev -> frontend-dev -> code-reviewer -> qa 순서로 별도 Codex subagent를 실행한다.
- 성능 개선, 권한/보안 변경, DB/API/UI가 함께 바뀌는 작업, 요구사항이 애매한 작업: planner를 먼저 실행한다.
- 작은 단일 영역 변경: planner 없이 backend-dev 또는 frontend-dev -> code-reviewer -> 필요한 범위의 qa subagent로 진행할 수 있다.
- 단순 백엔드 버그: backend-dev subagent -> code-reviewer subagent -> 필요한 범위의 qa subagent.
- 단순 프론트 버그/UI 개선: frontend-dev subagent -> code-reviewer subagent -> 필요한 범위의 qa subagent.
- DB 스키마 변경: planner가 필요성을 정리한 뒤 backend-dev subagent -> code-reviewer subagent -> import/DB init 검증.
- 테스트만 변경: qa subagent를 실행하고 필요한 경우 code-reviewer subagent 관점으로 테스트 품질을 점검한다.
- 하네스 문서 자체의 작은 수정은 Codex 본체가 직접 처리할 수 있다.

## 컨텍스트 분리 원칙

- Codex 본체는 오케스트레이터다. 무거운 요구사항 분석은 planner에, 구현/리뷰/QA는 가능한 한 실제 Codex subagent에 위임한다.
- subagent는 메인 대화와 별도 컨텍스트에서 실행한다. `fork_context=false`를 기본으로 하고, 필요한 요구사항과 파일 경로만 프롬프트에 포함한다.
- 메인 대화의 암묵적 합의는 subagent가 알 수 없다고 가정한다. 필요한 내용은 `.codex/workspaces/current/00_input/feature_spec.md`와 각 단계 산출물에 명시한다.
- 단계 간 전달은 채팅 기억이 아니라 `.codex/workspaces/current/execution_plan.md`, `dispatch_notes.md`, `backend_changes.md`, `frontend_changes.md`, `code_review_report.md`, `qa_report.md`를 통해 수행한다.
- 단계별 산출물이 없는 작업도 허용한다. 예: 프론트 전용 작업에는 `backend_changes.md`가 없을 수 있으며, 이 경우 subagent는 "Not applicable"로 기록하고 사용 가능한 입력만 기준으로 진행한다.
- 병렬 실행은 파일 책임 범위가 분리되고 다음 단계가 즉시 의존하지 않을 때만 사용한다. 의존성이 있으면 순차 실행하되 각 단계는 별도 subagent로 분리한다.
- subagent 도구가 없거나 실패하면 한 번 재시도한다. 그래도 불가능하면 Codex 본체가 fallback으로 진행할 수 있지만, 최종 보고에 fallback 이유를 남긴다.
- 모든 subagent의 최종 채팅 응답은 10줄 이내로 제한하고, 상세 내용은 산출물 파일에 기록한다.

## Phase 0: 작업 공간 준비

1. 기존 `.codex/workspaces/current/`가 현재 요청과 같은 맥락인지 판단한다.
2. 새 독립 작업이면 기존 `.codex/workspaces/current/`를 `.codex/workspaces/YYYYMMDD_HHMMSS/`로 보관한다.
3. `.codex/workspaces/current/00_input/feature_spec.md`에 다음을 기록한다.
   - 사용자 요청
   - 작업 분류
   - 영향 파일 예상
   - 성공 기준
   - 검증 계획
4. planner를 실행하는 작업에서는 planner가 `feature_spec.md`를 확장하고 `execution_plan.md`, `dispatch_notes.md`를 작성한다.

## Phase 1: 요청 분석

다음 기준으로 범위를 분류한다.

- 기능 추가: 새 라우트, 새 DB 컬럼/테이블, 새 UI 화면/컴포넌트, 새 사용자 흐름.
- 버그 수정: 특정 오동작 재현과 수정.
- UI 개선: 템플릿/JS/CSS 중심 변경.
- DB 변경: 스키마, CRUD, 마이그레이션 패턴 변경.
- QA: Playwright 테스트 작성 또는 검증 자동화.

다음 조건 중 하나라도 만족하면 planner를 먼저 실행한다.

- 변경 파일이 backend와 frontend 양쪽에 걸칠 가능성이 높다.
- DB 스키마, 권한, 보안, 성능, 캐시, 배포 동작이 관련된다.
- 요구사항이 여러 단계로 나뉘거나 성공 기준이 불명확하다.
- 병렬 작업 분해가 가능하거나 필요한지 판단해야 한다.

## Phase 2: 구현

각 역할의 세부 지침은 `roles/` 문서를 따른다.

- 계획은 `roles/planner.md`.
- 백엔드 변경은 `roles/backend-dev.md`.
- 프론트엔드 변경은 `roles/frontend-dev.md`.
- 리뷰는 `roles/code-reviewer.md`.
- QA는 `roles/qa.md`.

subagent를 생성할 때는 담당 파일, 읽어야 할 산출물, 작성해야 할 산출물을 명시한다. 여러 subagent가 병렬로 작업할 때는 파일 책임 범위를 분리하고, 서로의 변경을 되돌리지 않는다.

## Codex subagent 실행 계약

역할명은 하네스상의 책임 이름이며, 실제 `spawn_agent` 호출은 다음 기준을 따른다.

- 전용 `agent_type`이 노출된 경우: `planner`, `backend-dev`, `frontend-dev`, `code-reviewer`, `qa`를 우선 사용한다.
- 전용 `agent_type`이 없는 환경: `worker`를 사용하고 프롬프트에 역할 지침을 명시한다.
- `fork_context`: `false`
- `reasoning_effort`: 작업 위험도에 맞춰 지정하거나 기본값을 사용한다.
- planner subagent는 앱 소스와 테스트를 수정하지 않고 planning 산출물만 작성한다.
- 구현 subagent는 담당 파일/모듈을 명확히 소유한다.
- code-reviewer subagent는 소스 수정 없이 보고서 작성을 기본으로 한다.
- qa subagent는 앱 소스 수정 없이 `tests/*.spec.js` 작성/수정과 `qa_report.md` 작성을 수행할 수 있다. 앱 소스 수정이 필요하면 오케스트레이터가 별도 구현 subagent를 다시 실행한다.
- 모든 subagent 프롬프트에는 "다른 작업자가 있을 수 있으므로 타인의 변경을 되돌리지 말라"는 문구를 포함한다.
- 모든 subagent 프롬프트에는 "최종 채팅 응답은 짧게, 상세는 지정 산출물에 기록하라"는 문구를 포함한다.

## Orchestrator Relay 규칙

- planner 완료 후 오케스트레이터가 `.codex/workspaces/current/dispatch_notes.md`를 읽고 실행 여부, 순서, 담당 파일 범위를 결정한다.
- planner의 계획은 권고안이며, 사용자 최신 지시나 repo 상태와 충돌하면 오케스트레이터가 조정한다.
- backend-dev 완료 후 오케스트레이터가 `.codex/workspaces/current/backend_changes.md`를 읽고 frontend-dev/code-reviewer/qa 프롬프트에 필요한 요약을 포함한다.
- frontend-dev 완료 후 오케스트레이터가 `.codex/workspaces/current/frontend_changes.md`를 읽고 code-reviewer/qa 프롬프트에 필요한 요약을 포함한다.
- code-reviewer가 `BLOCKED`를 내면 오케스트레이터가 차단 항목만 담당 구현 subagent에 재전달한다. 수정은 최대 1회 재시도한다.
- qa가 실패를 기록하면 오케스트레이터가 실패 항목을 담당 구현 subagent에 재전달한다. 수정 후 code-reviewer 또는 qa를 필요한 범위만 재실행한다.
- subagent 간 직접 메시지 전달은 가정하지 않는다. 모든 전달은 오케스트레이터 프롬프트와 `.codex/workspaces/current/*.md` 산출물로 한다.

## Subagent 프롬프트 템플릿

### planner

```text
당신은 WhatUdoin planner 역할의 Codex subagent입니다.
메인 대화 컨텍스트는 공유되지 않는다고 가정하세요.
먼저 `.codex/harness/roles/planner.md`를 읽으세요.

사용자 요청:
- [요청 원문 또는 요약]

작업:
- 요구사항, 영향 범위, 성공 기준, 검증 계획을 정리하세요.
- 필요한 경우 repo 파일을 읽어 근거를 확인하세요.
- 앱 소스와 테스트는 수정하지 마세요.
- 다른 subagent를 직접 호출하지 마세요.
- `.codex/workspaces/current/00_input/feature_spec.md`, `.codex/workspaces/current/execution_plan.md`, `.codex/workspaces/current/dispatch_notes.md`를 작성하세요.

규칙:
- 타인의 변경을 되돌리지 마세요.
- 최종 채팅 응답은 10줄 이내로 요약하고, 상세는 산출물에 기록하세요.
```

### backend-dev

```text
당신은 WhatUdoin backend-dev 역할의 Codex subagent입니다.
메인 대화 컨텍스트는 공유되지 않는다고 가정하세요.
먼저 `.codex/harness/roles/backend-dev.md`, `.codex/workspaces/current/00_input/feature_spec.md`, `.codex/workspaces/current/dispatch_notes.md`를 읽으세요.
`dispatch_notes.md`가 없으면 프롬프트와 사용 가능한 산출물만 기준으로 진행하세요.

담당 범위:
- [담당 파일/모듈]

작업:
- [구체 작업]

규칙:
- 타인의 변경을 되돌리지 마세요.
- 필요한 소스만 수정하세요.
- 완료 후 `.codex/workspaces/current/backend_changes.md`에 API, DB, 검증 정보를 기록하세요.
- 최종 채팅 응답은 10줄 이내로 요약하세요.
```

### frontend-dev

```text
당신은 WhatUdoin frontend-dev 역할의 Codex subagent입니다.
메인 대화 컨텍스트는 공유되지 않는다고 가정하세요.
먼저 `.codex/harness/roles/frontend-dev.md`, `.codex/workspaces/current/00_input/feature_spec.md`, `.codex/workspaces/current/dispatch_notes.md`, `.codex/workspaces/current/backend_changes.md`를 읽으세요.
`backend_changes.md`가 없으면 백엔드 변경 없음으로 보고 "Not applicable"을 기록한 뒤 진행하세요.
`dispatch_notes.md`가 없으면 프롬프트와 사용 가능한 산출물만 기준으로 진행하세요.

담당 범위:
- [담당 템플릿/JS/CSS]

작업:
- [구체 작업]

규칙:
- 타인의 변경을 되돌리지 마세요.
- 필요한 소스만 수정하세요.
- 완료 후 `.codex/workspaces/current/frontend_changes.md`에 UI 흐름, API 의존성, 검증 정보를 기록하세요.
- 최종 채팅 응답은 10줄 이내로 요약하세요.
```

### code-reviewer

```text
당신은 WhatUdoin code-reviewer 역할의 Codex subagent입니다.
메인 대화 컨텍스트는 공유되지 않는다고 가정하세요.
먼저 `.codex/harness/roles/code-reviewer.md`, `git diff`, `.codex/workspaces/current/dispatch_notes.md`, `.codex/workspaces/current/backend_changes.md`, `.codex/workspaces/current/frontend_changes.md`를 읽으세요.
변경 요약 파일이 없으면 해당 영역은 "Not applicable"로 기록하고 `git diff`와 사용 가능한 산출물만 기준으로 검토하세요.

작업:
- 변경 범위의 권한, 보안, 패턴, API 계약을 검토하세요.
- 소스 수정은 하지 마세요.
- 결과를 `.codex/workspaces/current/code_review_report.md`에 `PASS` 또는 `BLOCKED`로 기록하세요.
- 최종 채팅 응답은 10줄 이내로 요약하세요.
```

### qa

```text
당신은 WhatUdoin qa 역할의 Codex subagent입니다.
메인 대화 컨텍스트는 공유되지 않는다고 가정하세요.
먼저 `.codex/harness/roles/qa.md`, `.codex/workspaces/current/dispatch_notes.md`, `.codex/workspaces/current/backend_changes.md`, `.codex/workspaces/current/frontend_changes.md`, `.codex/workspaces/current/code_review_report.md`를 읽으세요.
변경 요약 파일이 없으면 해당 영역은 "Not applicable"로 기록하고 사용 가능한 산출물만 기준으로 검증하세요.

작업:
- 변경 범위에 맞는 최소 검증을 수행하세요.
- 앱 소스는 수정하지 마세요. 필요한 경우 `tests/*.spec.js`만 작성/수정하세요.
- 서버 재시작이 필요하면 직접 종료/시작하지 말고 필요 사항을 보고서에 기록하세요.
- 결과를 `.codex/workspaces/current/qa_report.md`에 Passed/Failed/Not Run/Server Restart 형식으로 기록하세요.
- 최종 채팅 응답은 10줄 이내로 요약하세요.
```

## Phase 3: 리뷰

변경 후 정적 리뷰를 수행한다.

- 권한 체크 누락
- SQL injection 위험
- `_ctx()` 누락
- `_migrate` 패턴 위반
- PyInstaller 경로 혼용
- `fetch()` 오류 처리 누락
- XSS 위험
- 기존 UI/JS 패턴 이탈

결과는 `.codex/workspaces/current/code_review_report.md`에 남긴다.

## Phase 4: QA

변경 범위에 맞는 최소 검증을 수행한다.

- 문법/import/번들 검증
- Playwright 핀포인트 테스트
- API 응답과 UI 렌더링 교차 검증
- 권한 경계 확인

서버 재시작이 필요한 경우 사용자에게 요청하고 기다린다. 서버 프로세스를 직접 종료하거나 재시작하지 않는다.

## 최종 보고

사용자에게 다음만 간결히 보고한다.

- 변경 파일
- 핵심 구현 내용
- 검증 결과
- 남은 위험 또는 사용자가 해야 할 조치
