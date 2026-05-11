## QA 보고서 — 팀 기능 #10 가시성 누수 패치 (`/api/project-timeline`)

### 실행 환경
- 실서버 OFF + VSCode 디버깅 모드 → 합성 임시 DB + FastAPI `TestClient` (Playwright E2E 불가).
- 테스트 스크립트: `.claude/workspaces/current/scripts/verify_team10_timeline_leak.py`
- 실행: `PYTHONIOENCODING=utf-8 PYTHONUTF8=1 "D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_team10_timeline_leak.py`
- 합성 데이터: 팀 A/B, 사용자 X(A·B 둘 다)·Y(A만)·Z(B만)·adminU(admin)·unassignedU(팀 미배정), 각 팀 프로젝트·일정·체크·문서 + 공개/비공개 변형, NULL팀 row.

### 결과: **31 PASS / 0 FAIL**

### 통과 ✅ — `/api/project-timeline` 누수 패치 핵심
- [x] 비로그인 `GET /api/project-timeline` → `[]` (**이전엔 전 팀 public 일정·프로젝트 노출 — 누수 해소 확인**)
- [x] 팀 미배정 로그인(unassignedU) → `[]`
- [x] 팀 A 멤버(Y, 작업 팀 미지정 → 대표 팀 A) → TeamA 만, EvA_pub + EvA_priv 노출(같은 팀이라 비공개도), EvB_pub·TeamB 미노출
- [x] 다중 팀 사용자 X: `team_id=tA` → TeamA 만 / `team_id=tB` → TeamB 만 (작업 팀 전환 정상)
- [x] 비소속 팀 명시(Z@A) → A 팀 자료 노출 안 됨, 대표 팀 B 로 fallback
- [x] admin → 무필터, 전 팀(TeamA+TeamB) + EvA_pub·EvA_priv·EvB_pub 모두 노출 (**의도된 동작 유지**)

### 통과 ✅ — 회귀 (#10 명세 준수, 누수 없음)
- [x] `/api/kanban`: 비로그인 `[]`, 미배정 `[]`, Y@A 는 EvB_pub 미노출, admin 은 NULL팀 backlog 노출
- [x] `/api/events`: 비로그인 → is_public=1 만(EvA_pub+EvB_pub), EvA_priv·EvNull 미노출. Y@A → 같은 팀 비공개(EvA_priv) 봄 + is_public 일정은 작업 팀 무관 전원 노출(EvB_pub) [기존 rule 4, 누수 아님] + EvNull(작성자 아님) 미노출. admin → 전부.
- [x] `/api/checklists`: 비로그인 → is_public/non-private project 만. Y@A → CkA_pub 노출 / CkB_pub 미노출.
- [x] `/api/projects`: 비로그인 → ProjPriv(is_private) 제외. Y@A → ProjA+ProjPriv / ProjB 미노출. admin → 전부.
- [x] `/api/doc`: 비로그인 → DocPubB(is_public) 만. Y@A → DocTeamA+DocPubB. admin → 전부.
- [x] 권한 가드: 비로그인 `/api/project-list`·`/api/manage/projects` → 403.

### 기존 테스트 영향
- `app` 모듈 import OK, `/api/project-timeline` 라우트 정상 등록 확인.
- 변경 범위가 `/api/project-timeline` 라우트 본문 한정 — 라우트의 로그인/admin 동작은 기존과 동등(이전 `if viewer and not auth.is_admin(viewer)` 분기와 새 `if not auth.is_admin(viewer)` 분기는 비로그인을 제외하면 동일). `database.py` 무변경.
- `tests/phase46_gantt_project_date_boundary.spec.js` 등 간트 관련 JS E2E 는 실서버 필요 → 서버 재시작 후 사용자 환경에서 별도 확인 권장(본 변경은 응답 스키마·로그인 사용자 동작 불변이므로 영향 없을 것으로 판단).

### 서버 재시작
- 코드 변경(`app.py`)이므로 **서버 재시작 필요**. 스키마 변경 없음 → migration 불필요.
