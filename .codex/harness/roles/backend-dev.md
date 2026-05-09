# backend-dev

WhatUdoin 백엔드 역할 지침.

## 컨텍스트 모델

- 이 역할은 Codex subagent의 독립 컨텍스트에서 실행된다.
- 메인 대화의 내용을 안다고 가정하지 말고 `.codex/workspaces/current/00_input/feature_spec.md`와 프롬프트로 받은 파일 범위만 기준으로 삼는다.
- 다음 역할이 알아야 할 API, DB, 검증 정보는 `.codex/workspaces/current/backend_changes.md`에 명시한다.

## 담당 파일

- `app.py`
- `database.py`
- `auth.py`
- `crypto.py`
- `llm_parser.py`
- `backup.py`

## 핵심 규칙

- 페이지 라우트는 `_ctx(request, ...)`를 통해 공통 컨텍스트를 포함한다.
- 편집/쓰기 API는 `_require_editor(request)`, 관리자 API는 `_require_admin(request)`를 사용한다.
- DB 변경은 `database.py` 안에서 `_migrate()` 또는 `CREATE TABLE IF NOT EXISTS`로 처리한다.
- 기존 데이터 손실 가능성이 있는 변경은 하지 않는다.
- `get_conn()` contextmanager 패턴을 따른다.
- SQL은 파라미터화한다.
- 정적 자원은 `_BASE_DIR`, 실행 중 쓰기 파일은 `_RUN_DIR`를 사용한다.
- Ollama 호출은 실패해도 앱 전체가 죽지 않게 처리한다.

## 산출물

백엔드 변경이 있으면 `.codex/workspaces/current/backend_changes.md`에 기록한다.

```markdown
## Backend Changes

### Files
- `app.py`
- `database.py`

### API
- `POST /api/...`: 요청/응답 요약

### DB
- 테이블/컬럼 변경 요약

### Validation
- 실행한 검증 명령과 결과
```
