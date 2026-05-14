# 코드 리뷰 보고서 — 비로그인 진입 재설계 (그룹 D catchup)

## 리뷰 대상 파일
- `app.py` (line 668~, line 5486~5612)
- `templates/base.html` (line 386~)
- `templates/team_portal.html` (전반)
- `templates/admin.html` (line 145~)

## 차단(Blocking) — 없음

## 경고(Warning)
- `base.html` nav 분기에서 portal context의 active-state 클래스는 부여하지 않음. 의식적 수용(advisor 권고 — 본 변경 범위 밖, 후속 catchup 으로 처리 가능).

## 통과 ✅
- `_ctx(request, ...)` 호출 패턴 유지 (모든 신규/수정 라우트).
- 신규 라우트 등록 순서: 정적 라우트 뒤 → `/{team_name}/{한글키}` 4개 → `/{team_name}`. FastAPI 첫 매치 승 규칙상 잠식 없음.
- `RESERVED_TEAM_PATHS` 잠식 없음 — `_build_reserved_team_paths` 는 segment 1 만 추출하고 `{team_name}` 은 `{` 포함 skip. 칸반/간트/문서/체크 가 RESERVED 에 들어가지 않음. (QA invariant 로 검증)
- SQL 신규 변경 없음 (헬퍼 재사용).
- innerHTML XSS 없음 (신규 JS 없음, Jinja2 자동 이스케이프).
- 권한 모델: 비로그인 공개 포털이므로 권한 체크 의도적 부재 (phase82 패턴과 정합).
- DB 경로 변경 없음.
- 한글 path segment: Starlette UTF-8 디코드 후 매칭 — `_TEAM_NAME_RE` 는 segment 1 만 제약. segment 2 한글 통과 OK.
- Jinja `urlencode` 필터 일관성: home.html 의 `{{ team.name | urlencode }}` 패턴 따라 모든 곳에서 동일 적용.
- 기존 invariant 보존: phase82 line 138/234 의 hero-sub 문구·`btn-primary">계정 가입` 마커 유지.

## 최종 판정
- **통과** (차단 결함 없음)
