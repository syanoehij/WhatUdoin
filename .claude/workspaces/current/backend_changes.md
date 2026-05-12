# backend_changes — 팀 기능 그룹 B #14

## 변경 파일
- `app.py` — `team_public_portal` 라우트만 (1곳)

## 변경 내용
`@app.get("/{team_name}")` 의 deleted 아닌 경로에서, 템플릿 컨텍스트에 `my_team_status` 추가:

- `user = auth.get_current_user(request)` — `_ctx` 가 이미 user 를 넣지만 라우트에서 분기용으로 명시 조회.
- `my_team_status` 계산:
  - 비로그인 (`user is None`) 또는 `auth.is_admin(user)` → `None` (admin = 슈퍼유저, "팀 신청" 의미 없음 → 버튼 없음. 계획서 섹션 7 표에 admin 행 없어 본 구현이 명시 결정).
  - 그 외: `team["id"] in auth.user_team_ids(user)` → `"approved"`; 아니면 `db.get_my_team_statuses(user["id"]).get(team["id"])` → `"pending"` | `"rejected"` | `None`.
- `_ctx(request, team=team, deleted=False, portal=portal, my_team_status=my_team_status)` 로 전달.
- deleted 분기는 변경 없음 (안내 페이지만 — 버튼 없음).
- 라우트 상단 주석을 #14 구현 완료에 맞게 갱신 ("로그인 사용자·admin 별 버튼 분기는 my_team_status 컨텍스트로 처리").

## 재사용한 기존 자산 (새 DB 헬퍼/마이그레이션 없음)
- `auth.get_current_user`, `auth.is_admin`, `auth.user_team_ids` (approved set, `auth.py`)
- `db.get_my_team_statuses(user_id)` (#12, pending/rejected 만 반환)
- `db.get_public_portal_data` (#13) — 변경 없음

## 스키마
무변경. 마이그레이션 PHASES 추가 없음 (SELECT 만 재사용).

## redirect
없음 — 로그인/admin 모두 200 공개 포털. `RedirectResponse` 미사용.

## import 확인
`import app` OK.
