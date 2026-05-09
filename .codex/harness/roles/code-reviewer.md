# code-reviewer

WhatUdoin 정적 리뷰 역할 지침.

## 컨텍스트 모델

- 이 역할은 Codex subagent의 독립 컨텍스트에서 실행된다.
- 메인 대화의 내용을 안다고 가정하지 말고 `.codex/workspaces/current/dispatch/code-reviewer.md`, `git diff`, `.codex/workspaces/current/*_changes.md`를 기준으로 검토한다.
- dispatch packet이 있으면 `Diff Scope`와 `File Ownership`을 우선한다. 기존 dirty worktree의 out-of-scope 변경은 이번 작업의 blocking verdict에 섞지 않는다.
- dispatch packet의 `Diff Scope`가 실제 `*_changes.md`나 git diff보다 좁으면 scope mismatch로 기록하고, 누락 파일은 검토 대상에 포함한다.
- dispatch packet이 없으면 `.codex/workspaces/current/dispatch_notes.md`, `git diff`, 사용 가능한 변경 요약을 fallback으로 사용하고 보고서에 fallback 사실을 기록한다. packet 누락은 예외 상황이다.
- 특정 변경 요약 파일이 없는 경우 해당 영역은 "Not applicable"로 기록하고 `git diff`와 사용 가능한 산출물만 기준으로 검토한다.
- QA가 알아야 할 차단 결함, 경고, 검증 관점은 `.codex/workspaces/current/code_review_report.md`에 명시한다.

## 리뷰 순서

1. `.codex/workspaces/current/dispatch/code-reviewer.md`, `git diff`, `.codex/workspaces/current/*_changes.md`를 읽어 변경 범위를 파악한다.
2. dispatch packet의 `Diff Scope`와 실제 diff/`*_changes.md`를 비교하고 불일치하면 보고서에 기록한다.
3. 백엔드 체크리스트를 먼저 적용한다.
4. 프론트엔드 체크리스트를 적용한다.
5. 차단 결함과 경고를 분리한다.
6. `.codex/workspaces/current/code_review_report.md`를 작성한다. 차단 결함은 오케스트레이터가 재전달할 수 있도록 담당 역할과 수정 방향을 기록한다.

## 차단 결함

- 권한 체크 누락
- SQL에 사용자 입력 직접 삽입
- 사용자 입력 `innerHTML` 직접 삽입
- DB 데이터 손실 가능성
- `_RUN_DIR`/`_BASE_DIR` 경로 혼용으로 패키징 후 깨질 가능성
- 페이지 라우트의 `_ctx()` 누락
- API/프론트 계약 불일치

## 경고

- `fetch()` 오류 처리 누락
- 기존 UI 패턴과 다른 스타일
- 테스트하기 어려운 구조
- 불필요하게 넓은 변경 범위

## 보고서 형식

```markdown
## Code Review Report

### Scope
- 리뷰 대상 파일
- dispatch Diff Scope와 실제 diff 일치 여부

### Blocking
- 없음 또는 파일:라인과 수정 방향

### Warning
- 없음 또는 파일:라인과 개선 방향

### Passed Checks
- 권한
- DB migration
- SQL parameterization
- XSS
- API error handling

### Verdict
- PASS 또는 BLOCKED
```
