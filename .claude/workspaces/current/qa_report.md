## QA 보고서 — 팀 기능 #12 (`/` 팀 미배정 로그인 사용자 + "내 자료")

### 검증 방식
운영 서버는 IP 자동 로그인이라 브라우저로 "미배정 사용자" 특정 화면을 재현할 수 없다 → #11과 동일하게
**임시 DB(`.claude/workspaces/current/test_dbs/`) + FastAPI TestClient** 로 세션 쿠키를 직접 주입해 검증.
신규 테스트: `tests/phase81_unassigned_user.py` — **8/8 PASS**.

### 통과 ✅
- [x] 정적 invariant: `auth.is_unassigned`(admin 먼저 체크 + `user_team_ids` 사용) / `db.get_my_team_statuses` / `db.get_my_personal_meetings`(SQL에 `team_id IS NULL`·`team_share` 필터 없음) / `app.index()` 미배정 분기 / `_ctx` `is_unassigned` 주입 / `create_doc` 미배정 `team_id=None` 강제 / `base.html` 알림 벨 `{% if not is_unassigned %}` / `home.html` `#view-unassigned` 블록 + 미배정 화면 팀 카드가 `/팀이름` 링크 아님(#13 책임) + 공유 코드 유지.
- [x] 미배정 사용자 `GET /` → 200, `id="view-unassigned"` 렌더, "팀 신청"/"내 자료" 텍스트 + "+ 새 문서"(`/doc/new?personal=1`, 가입 role=member 도 노출 — `_require_editor`=is_member 통과) 존재, `id="notif-bell-wrap"` **없음**.
- [x] 팀 신청(`POST /api/me/team-applications`) → 200; 이후 `GET /` 에 "가입 대기 중"(비활성) 노출; 다른 팀 신청 → **409**(pending_other).
- [x] 신청 승인(`db.decide_team_application(..., 'approved')`) → 사용자 배정됨 → `GET /` → `id="view-user"` (미배정 분기 해제) + 알림 벨 다시 노출.
- [x] 미배정 사용자 `POST /api/doc` body `{is_team_doc:1, is_public:0, team_share:1}` → 저장 row: `is_team_doc=0`, `team_share=0`, `team_id=NULL` (UI 우회해도 서버 강제). 해당 문서가 `get_my_personal_meetings`/`GET /` 마크업에 노출.
- [x] 추방-잔존 케이스: `team_id != NULL`, `is_team_doc=0`, `created_by=self` 인 문서도 "내 자료"에 포함.
- [x] `team_share=1` 본인 개인 문서: 작성자 "내 자료"엔 보이되, 다른 팀(`다른팀`) 멤버에겐 `/api/doc` 에서 비노출(작업 팀 일치 조건 — 그룹 A #10 가시성 규칙 유지).
- [x] soft-deleted 팀(`deleted_at IS NOT NULL`)은 미배정 화면 팀 목록에서 제외.
- [x] admin(자동 생성 계정, `user_teams` row 없음) → `auth.is_unassigned` False → `GET /` → `view-user` + 알림 벨 노출.
- [x] 미배정 사용자 `GET /api/notifications/count` → `{"count":0}`, `/api/notifications/pending` → `[]`.
- [x] 회귀: 일반 배정 사용자 `GET /` → `view-user`, 비로그인 → `view-guest`(알림 벨은 `{% if user %}` 밖이라 원래 없음).

### 실패 ❌
- 없음.

### 회귀 확인
- `tests/phase80_landing_page.py` (#11 회귀) — **5/5 PASS**.
- `tests/phase75_m6_mcp_owner_boundary.py` — **PASS**.
- `tests/test_project_rename.py` — 2건 **FAIL**: 옛 픽스처 DB에 `projects.team_id` 컬럼 없음 → `no such column: team_id`. 이는 todo.md에 기재된 **사전 결함**(master HEAD에서도 동일, #12와 무관 — 픽스처 DB 미갱신).
- `import app` OK.

### 서버 재시작
- **운영 서버 반영 시 재시작 필요** — 코드 reload(app.py / auth.py / database.py / 템플릿 3종). 스키마 변경 없음 → 마이그레이션 불필요. 본 단위 검증은 TestClient(임시 DB)로 완료했으므로 검증 자체엔 재시작 불필요.

### 최종 판정
**통과** — 차단/실패 없음. 사전 결함 1건(project_rename 픽스처) 외 회귀 없음.
