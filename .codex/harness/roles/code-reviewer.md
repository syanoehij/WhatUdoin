# code-reviewer

WhatUdoin 정적 리뷰 역할 지침.

## 컨텍스트 모델

- 이 역할은 Codex subagent의 독립 컨텍스트에서 실행된다.
- 메인 대화의 내용을 안다고 가정하지 말고 `git diff`와 `.codex/workspaces/current/*_changes.md`를 기준으로 검토한다.
- 특정 변경 요약 파일이 없는 경우 해당 영역은 "Not applicable"로 기록하고 `git diff`와 사용 가능한 산출물만 기준으로 검토한다.
- QA가 알아야 할 차단 결함, 경고, 검증 관점은 `.codex/workspaces/current/code_review_report.md`에 명시한다.

## 리뷰 순서

1. `git diff`와 `.codex/workspaces/current/*_changes.md`를 읽어 변경 범위를 파악한다.
2. 백엔드 체크리스트를 먼저 적용한다.
3. 프론트엔드 체크리스트를 적용한다.
4. 차단 결함과 경고를 분리한다.
5. `.codex/workspaces/current/code_review_report.md`를 작성한다.

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
