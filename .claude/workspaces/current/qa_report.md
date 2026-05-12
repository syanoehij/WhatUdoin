# #15 QA — work_team_id 쿠키 + 프로필 "팀 변경" UI

## 방식

운영 서버는 IP 자동 로그인이라 특정 사용자/다중 팀/admin 상태를 브라우저로 재현할 수 없다.
TestClient(`fastapi.testclient`) + 임시 DB(`monkeypatch.setattr(db, "DB_PATH", ...)`)로 검증한다 —
session/work_team_id 쿠키 set 가능, Set-Cookie 응답 헤더 inspect 가능, 다중 팀 사용자·admin 임시 구성 가능.
신규 spec: `tests/phase84_work_team_cookie.py` (네이밍 컨벤션 — phase80~83 다음).

## 결과: 19/19 PASS

| # | 시나리오 | 결과 |
|---|---------|------|
| static×5 | app.py 라우트/헬퍼/`_ctx` · auth.py `resolve_work_team` 쿠키 검증 · db 헬퍼 3종+joined_at · 템플릿(팀 변경 UI·드롭다운 제거·work_team_id) · CSS | PASS |
| A | 첫 로드(쿠키 없음), 2팀 멤버(joined_at 순) → `GET /` → `work_team_id` Set-Cookie = 가장 이른 팀 | PASS |
| B | 첫 로드(쿠키 없음), admin → Set-Cookie = 가장 작은 id 비삭제 팀 | PASS |
| C | 유효 쿠키(대표 팀 아닌 다른 소속 팀) → 그 값 사용, Set-Cookie 갱신 없음 | PASS |
| D | 쿠키 팀 soft-deleted → 새 대표 팀으로 Set-Cookie 갱신 | PASS |
| E | 쿠키 팀 비소속(추방) → 새 대표 팀으로 Set-Cookie 갱신 | PASS |
| F | `POST /api/me/work-team {소속 팀}` → 200 + `{ok, team_id, team_name}` + Set-Cookie | PASS |
| G | `POST` {비소속 팀} (비admin) → 403 | PASS |
| H | `POST` {삭제 예정 팀} → 404 / `POST` {team_id: "abc"} → 400 | PASS |
| I | `POST` 후 `/api/kanban`·`/api/events`·`/api/project-timeline` 가 새 팀 컨텍스트로 (다른 팀 데이터 미노출) + `/api/checklists`·`/api/doc` 200 | PASS |
| J | #10 회귀: `?team_id=X`(소속) 가 쿠키보다 우선 / 비소속 X 명시는 무시→쿠키 fallback | PASS |
| K | 미배정 사용자 `GET /` → Set-Cookie 없음 / 비로그인도 영향 없음 | PASS |
| L | admin `_work_scope`=None 유지 — admin 쿠키가 ta 라도 `/api/kanban` 전 팀 노출 | PASS |
| M | `GET /api/me/work-team`: 비admin → 본인 소속 팀 + 대표 팀 / admin → 전체 비삭제 팀 | PASS |
| N | 비로그인 `GET`/`POST /api/me/work-team` → 401 | PASS |

## 회귀

- `tests/phase80_landing_page.py`(#11) 5 / `phase81_unassigned_user.py`(#12) 8 / `phase82_team_portal.py`(#13) 8 / `phase83_team_portal_loggedin.py`(#14) 9 — **30/30 PASS** (10.2s).
- `tests/test_project_rename.py` 2 FAIL은 사전 결함(`git stash` 후 동일 — 옛 픽스처 DB에 `projects.team_id` 없음, master HEAD `04006ba` 동일, #15 무관).
- `python -c "import app"` OK. Jinja `get_template` base/kanban/project/calendar/doc_list OK.

## 자가 발견 결함

없음 (1차 통과).

## 서버 재시작

스키마 무변경 → 운영 DB 마이그레이션 불필요. 코드 reload(auth.py / database.py / app.py / 템플릿 5종 / style.css) 위해 **운영 서버 재시작 필요**. 단 본 단위 검증은 TestClient(임시 DB)로 완료 — 재시작 없이 검증됨.

## 브라우저 검증 미수행 사유

운영 서버 IP 자동 로그인 + 단일 IP 환경이라 다중 팀 사용자 전환·admin 슈퍼유저 시나리오를 Playwright 로 재현할 수 없다. 프로필 "팀 변경" 드롭다운 UI 자체는 정적 검증(템플릿 마크업·JS 함수 존재·`/api/me/work-team` 호출)으로 확인. 서버 재시작 후 사용자가 실제 다중 팀 계정으로 드롭다운 동작을 한 번 확인하면 좋다.
