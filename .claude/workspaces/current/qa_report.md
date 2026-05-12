## QA 보고서 — #15-3 team_notices 팀별 공지 전환

### 방식
운영 서버는 IP 기반 자동 로그인이라 특정 사용자/다중 팀/admin 상태를 브라우저로 재현 불가 → **TestClient(FastAPI) + 임시 SQLite DB**(`.claude/workspaces/current/test_dbs/`). #15(phase84)·#15-1(phase85)·#15-2(phase86)와 동일 패턴. 신규 파일 `tests/phase87_team_notices_multiteam.py`.

### 통과 ✅ — `tests/phase87_team_notices_multiteam.py` 10/10 PASS (6.3s)
- [x] **정적 invariant ×2**:
  - `database.py`: `get_notice_latest_for_team(team_id)`(WHERE team_id=?)·`save_notice(content, team_id, created_by)`(INSERT 3컬럼 + 자동정리 2쿼리 모두 `WHERE team_id = ?` + 30일/100개 절·전역 일괄 삭제 잔존 없음)·`get_notice_history(team_id, include_null=...)`(`OR team_id IS NULL` 분기)·`create_notification_for_team`(JOIN user_teams + status='approved')·옛 `get_latest_notice` 정의 부재.
  - `app.py`: `_notice_work_team` 헬퍼 존재·`/notice`·`/notice/history` SSR 에 `_ensure_work_team_cookie` + 팀 기준 조회·`GET /api/notice` `team_id` 파라미터+`_notice_work_team`·`POST /api/notice` `resolve_work_team`+`require_work_team_access`+400+`save_notice(content, team_id, ...)`·작성자 게이트 없음·`POST /api/notice/notify` `resolve_work_team`+`require_work_team_access`+`create_notification_for_team`(전역 `create_notification_for_all` 미사용)·옛 `db.get_latest_notice()` 호출 부재·`import app` OK.
- [x] **A** 다중 팀 사용자 작업 팀 전환(쿠키 ta→tb)→`GET /api/notice` 가 새 팀 최신 공지로·명시 `?team_id=ta` 가 쿠키(tb)보다 우선·old 팀 공지 누수 없음.
- [x] **B** 다른 팀 멤버 세션에선 그 팀 공지 안 보임(`{}` 반환)·명시 `?team_id` 비소속 → 무시·대표 팀 fallback(여전히 `{}`)·소속 멤버 세션은 보임.
- [x] **C** `POST /api/notice` → team_id 가 work_team_id(쿠키)로 확정 저장·명시 본문 `team_id`(소속) 우선·비소속 명시 → 403·미배정 사용자 → 400·admin 쿠키/명시 본문 후 → 200(각 팀 id 정확).
- [x] **D** `POST /api/notice/notify` → 같은 팀 approved 멤버에게만 알림(1건)·발송자 본인 제외·pending 멤버 미수신·다른 팀 멤버 미수신·**글로벌 admin(user_teams row 없음) 미수신**·공지 없는 팀 → `{"ok": False, "reason": "no_notice"}`.
- [x] **E (팀 공유 모델)** 멤버 A 작성 공지를 같은 팀 멤버 B 가 `POST`(갱신)→200·`created_by` 가 B 로 바뀜(작성자 무관)·B 가 `notify`→200·제3 멤버 C 수신·B 본인 제외·GET 시 A·B 둘 다 최신(B 갱신본) 봄. (links 의 "작성자/admin만"과 의도적으로 다름 — 계획서 §8-1.)
- [x] **F (자동 정리 팀별)** 팀A 100개 시드 + 30일 이전 1개 + NULL orphan 1개 + 팀B 5개 + 팀B 30일이전 1개 상태에서 팀A 에 `save_notice` 1회 → 팀A 정확히 100개(30일이전 1개 삭제 + 100개 캡)·**팀B 6개 그대로(팀A save 가 팀B 미터치)**·팀B 30일이전 row 살아남음·**NULL orphan row 영향 없음**·팀A 30일이전 row 삭제됨·새 팀A 공지 살아남음.
- [x] **G (NULL orphan 가시성)** `get_notice_latest_for_team(tid)` NULL row 미반환·`get_notice_history(tid, include_null=False)` 팀 공지만·`get_notice_history(tid, include_null=True)`(admin) 고아도 포함·`GET /api/notice` 멤버 세션 → 고아 안 나옴·SSR `/notice/history` 비admin → 고아 토큰 미포함·**admin → 고아 토큰 포함**.
- [x] **H (SSR 쿠키 + 미배정)** 쿠키 없는 멤버 `/notice`·`/notice/history` GET → `Set-Cookie: work_team_id=...` 발급(`_ensure_work_team_cookie`)·작업 팀 공지가 `INITIAL_MD` 로 렌더·미배정 사용자 → Set-Cookie 없음·공지 미렌더·`GET /api/notice` `{}`·비로그인 `GET /api/notice` `{}`.

### 회귀 확인 ✅
- `tests/phase80~86` (landing/unassigned/portal×2/work_team_cookie/hidden_project_multiteam/links_multiteam) — **73/73 PASS** (28.0s). 그룹 B 화면 정비 + work_team_id 흐름 + 히든프로젝트·links 다중 팀 전환에 회귀 없음.
- `import app` OK. Jinja `notice.html`/`notice_history.html`/`base.html` `get_template` OK.

### 미실행 (서버 재시작 필요 — 본 사이클 라이브 검증 안 함)
- `tests/check_notice.spec.js` (Playwright, live server `localhost:8000`, admin/admin 로그인): 에디터 렌더링·저장 toast·알림 발송 검증. **코드 reload 필요** — 재시작 후 별도 확인 권장. 위험 낮음(프론트 무변경; admin 은 `first_active_team_id` 로 작업 팀 결정되므로 활성 팀이 있으면 저장·발송 정상).
- `tests/test_project_rename.py` 2 FAIL 은 사전 결함(옛 픽스처 DB 에 `projects.team_id` 없음 — master HEAD 동일, #15-3 무관).

### 서버 재시작 필요 여부
**필요** — `app.py`·`database.py` 코드 reload. **스키마 무변경 → 마이그레이션 phase 추가 없음.** (본 단위 검증은 TestClient/임시 DB 로 완료.)

### 최종 판정
- **통과** — 신규 10/10 + 회귀 73/73. 차단 결함 없음.
