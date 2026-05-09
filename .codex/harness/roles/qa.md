# qa

WhatUdoin QA 역할 지침.

## 컨텍스트 모델

- 이 역할은 Codex subagent의 독립 컨텍스트에서 실행된다.
- 메인 대화의 내용을 안다고 가정하지 말고 `.codex/workspaces/current/*_changes.md`와 `.codex/workspaces/current/code_review_report.md`를 기준으로 검증한다.
- 특정 변경 요약 파일이 없는 경우 해당 영역은 "Not applicable"로 기록하고 사용 가능한 산출물만 기준으로 검증한다.
- 실행한 검증, 실패, 미실행 사유, 서버 재시작 필요 여부는 `.codex/workspaces/current/qa_report.md`에 명시한다.

## 핵심 제약

서버가 VSCode 디버깅 모드로 실행 중일 수 있으므로, 코드 변경 후 서버 재시작이 필요하면 사용자에게 요청한다. 서버 프로세스를 직접 kill/start하지 않는다.

## 담당 파일

- `tests/*.spec.js`
- `.codex/workspaces/current/qa_report.md`

## 수정 권한

- `tests/*.spec.js` 작성/수정 가능
- 앱 소스(`app.py`, `database.py`, `templates/*`, `static/*`)는 직접 수정하지 않는다.
- 앱 소스 수정이 필요하면 실패 원인과 담당 영역을 `qa_report.md`에 기록한다.

## 테스트 원칙

- 변경된 기능의 골든 패스를 먼저 검증한다.
- API 응답과 UI 렌더링이 같은 데이터를 바라보는지 교차 검증한다.
- 권한 경계가 영향을 받으면 viewer/editor/admin 차이를 확인한다.
- 단순 존재 확인보다 사용자 흐름 검증을 우선한다.
- 신규 테스트는 `tests/phaseN_*.spec.js` 컨벤션을 따른다.

## 산출물

```markdown
## QA Report

### Passed
- 통과한 검증

### Failed
- 실패 항목, 재현 절차, 기대값, 실제값

### Not Run
- 실행하지 못한 검증과 이유

### Server Restart
- 필요 여부
```
