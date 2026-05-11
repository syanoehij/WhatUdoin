# QA 리포트 — 팀 기능 그룹 A #8 (계정 가입과 팀 신청 분리)

방식: import-time 검증 + 합성 임시 DB(`tempfile`) + FastAPI `TestClient`. 운영 DB·실행 서버 무관(VSCode 디버깅 모드라 서버 재시작 불가 — 본 변경은 런타임 라우트/DB 함수만, 마이그레이션 phase 변경 없음 → **서버 재시작 불필요**, 재시작 후 추가 E2E는 후속 가능).

스크립트: `.claude/workspaces/current/scripts/verify_team_a_008.py`
실행: `"D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_team_a_008.py`
결과: **72 PASS / 0 FAIL**

추가: `python -c "import app"` OK + 신규 라우트 4건 등록 확인.

## Part 1 — DB 함수 직접 검증 (31 PASS)
- `create_user_account`: 반환 dict / role='member' / team_id NULL / name 보존 / name_norm 정규화 / password 컬럼 빈문자열 / password_hash 채워짐+`verify_password` 통과 / is_active=1.
- name_norm 충돌: 대소문자 변형(`HONG` vs `Hong`) → None / 동일 이름 재가입 → None.
- admin 시드 존재.
- `apply_to_team`: 신규→`("created",None)` + pending row 1건 / 같은 팀 재신청→`("blocked","pending_here")` / 다른 팀 신청(pending 존재)→`("blocked","pending_other")` + row 추가 안 됨.
- `decide_team_application`: approve→True + `status='approved'` + `joined_at` 채워짐 / approved 멤버 재신청→`("blocked","already_member")` / 이미 approved 상태에서 decide→False.
- rejected→재신청: `("updated",None)` + **같은 row(id 동일) 갱신** + row count 동일 + `status='pending'` 복귀.
- rejected→pending 갱신 시 다른 팀에 pending 있으면 `("blocked","pending_other")`.
- `list_team_applications`: pending 목록 + `user_name` 포함.
- `get_team_active`: 삭제된 팀→None / 정상 팀→dict.
- `pending_users` 테이블: register 흐름 미사용 → 0건.

## Part 2 — TestClient 라우트 검증 (41 PASS)
(rate limit은 테스트에서 비활성화 — `app.limiter.enabled = False`. 실서버에서는 `/api/register` 5/min, `/api/me/team-applications` 10/min 유지.)

- 예약 사용자명 차단 400: `admin`, `Admin`, `ADMIN`, `system`, `ROOT`, `Guest`, `anonymous` (7케이스).
- 정규식 위반 400: `hong gildong`(공백), `a_b`(밑줄), `user@1`(@), `a-b`(하이픈), `x.y`(점).
- 비밀번호 정책 위반 400: `abcdef`(숫자없음), `123456`(영문없음), `한글만`, `` (빈값).
- 정상 가입: 200 + 응답 `{ok, name, role='member', team_id=None}` + Set-Cookie 세션 발급 + DB users row 생성(role='member', team_id NULL, password='', password_hash 채워짐).
- 동일 이름(대소문자 변형) 재가입: 409.
- 팀 신청(가입 직후 세션 보유): 200 `{status:'pending'}` + user_teams pending row 생성 / 같은 팀 재신청 409 / 다른 팀 신청(pending 존재) 409 / 없는 팀 404 / 비로그인 401.
- admin 로그인 후 팀 신청 시도: 400.
- admin decide approve: 200 + `status='approved'` + `joined_at` / 이미 approved 재처리: 404 / 잘못된 decision: 400 / admin list applications: 200(list).
- 권한: 외부인 decide 403 / 외부인 list 403 / 팀 관리자(`user_teams.role='admin'`) 자기 팀 decide 200 / 팀 관리자 다른 팀 list 403.
- register 흐름 후 `pending_users` 0건.

## 미검증 / 후속
- 실서버 브라우저 E2E(register.html 폼 제출 → `/` 리다이렉트, base.html 모달 링크 표시) — 서버 재시작 후 Playwright 로 후속 가능. 본 사이클은 import-time + 합성 DB로 백엔드/경계면 검증 완료.
- rate limit 자체 동작은 기존 기능(slowapi)이라 별도 검증 안 함.
