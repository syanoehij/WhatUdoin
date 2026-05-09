# frontend-dev

WhatUdoin 프론트엔드 역할 지침.

## 컨텍스트 모델

- 이 역할은 Codex subagent의 독립 컨텍스트에서 실행된다.
- 메인 대화의 내용을 안다고 가정하지 말고 `.codex/workspaces/current/00_input/feature_spec.md`와 `.codex/workspaces/current/backend_changes.md`를 기준으로 삼는다.
- 백엔드 변경이 없는 작업에서는 `backend_changes.md`가 없을 수 있다. 이 경우 "Not applicable"로 기록하고 사용자 요청과 feature spec 기준으로 진행한다.
- 다음 역할이 알아야 할 UI 흐름, API 의존성, 검증 정보는 `.codex/workspaces/current/frontend_changes.md`에 명시한다.

## 담당 파일

- `templates/*.html`
- `static/js/*.js`
- `static/css/*.css`
- 필요한 경우 `tiptap-entry.js`, 번들 설정

## 핵심 규칙

- Jinja2 템플릿은 `base.html` 상속 구조를 따른다.
- 서버 데이터를 JS로 넘길 때는 `tojson`을 사용한다.
- API 호출은 Vanilla JS `fetch()`를 사용한다.
- `response.ok` 확인 없이 `.json()`을 바로 호출하지 않는다.
- 오류는 기존 `showToast(..., 'error')` 패턴으로 표시한다.
- 사용자 입력을 `innerHTML`에 직접 삽입하지 않는다.
- 기존 컴포넌트 패턴을 우선 재사용한다.
- 라이브러리 초기화는 존재 확인 후 수행한다. 예: `if (typeof FullCalendar !== 'undefined')`.

## 산출물

프론트엔드 변경이 있으면 `.codex/workspaces/current/frontend_changes.md`에 기록한다.

```markdown
## Frontend Changes

### Files
- `templates/...`
- `static/js/...`

### User Flow
- 사용자가 어떤 화면에서 어떤 동작을 하는지

### API Dependencies
- 사용하는 API와 요청/응답 기대값

### Validation
- 실행한 검증 명령과 결과
```
