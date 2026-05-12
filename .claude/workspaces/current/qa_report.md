# qa_report — 팀 기능 그룹 B #15-2 (links 다중 팀 전환)

## 테스트 전략
운영 서버는 IP 자동 로그인(로그아웃 불가)이라 다중 팀 사용자/admin 상태/팀 전환 시나리오를 브라우저로 재현 불가 → FastAPI `TestClient` + 임시 DB(`.claude/workspaces/current/test_dbs/`)로 검증. (phase84/85와 동일 패턴.)

## 신규 테스트: `tests/phase86_links_multiteam.py` — 13 PASS / 0 FAIL

| # | 시나리오 | 결과 |
|---|----------|------|
| static 1 | `db.get_links` 시그니처 `work_team_ids` 로 전환 | PASS |
| static 2 | `db.update_link` 에 `role` 인자 + `role=='admin'` 분기 | PASS |
| static 3 | `/api/links` 라우트가 `_work_scope`/`resolve_work_team`/`require_work_team_access` 사용, `user.get("team_id")` 미참조 + `import app` | PASS |
| A | 다중 팀 사용자가 작업 팀 전환(쿠키) → GET /api/links 가 새 팀 scope='team' 링크로; 명시 ?team_id 가 쿠키보다 우선 | PASS |
| B | 다른 팀 멤버 세션에선 그 팀 scope='team' 링크 안 보임 (명시 ?team_id 로도 비소속 → 무시·대표팀 fallback) | PASS |
| C | personal 링크는 작성자 본인만 노출 (작업 팀 무관) | PASS |
| D | POST scope='team' → team_id 가 work_team_id 로 확정 저장; personal → NULL; 명시 team_id 우선; 비소속 명시 → 403 | PASS |
| E | admin: 쿠키/명시 work_team_id 후 scope='team' POST/PUT/DELETE; admin GET 은 전 팀 scope='team' 링크 노출 | PASS |
| F | 같은 팀 멤버 B 가 멤버 A 의 scope='team' 링크 PUT·DELETE → 403; 원본 유지; A 본인은 가능 | PASS |
| G | admin 이 타인 scope='team' 링크 PUT·DELETE 가능; 타인 personal 링크도 가능 | PASS |
| H | admin 이 work_team 없이(쿠키 X + ?team_id X) scope='team' POST → 400; personal POST → 200 | PASS |
| I | 회귀: personal CRUD 본인; 비로그인 GET /api/links → []; title/url 누락·잘못된 scheme → 400 | PASS |
| db-conv | get_links(None)=전 팀+본인개인 / get_links(set())=본인개인만 / get_links({ta})=A팀+본인개인 / get_links({ta,tb})=일반화 / 타 사용자 관점 | PASS |

## 회귀
- `tests/phase80~85` (landing / unassigned / team portal x2 / work_team cookie / hidden project multiteam) — **60 PASS / 0 FAIL**.
- 기존 링크 전용 테스트 없음 (phase86이 첫 커버리지).

## 서버 재시작
**필요** — `app.py`/`database.py` 코드 reload. 스키마 무변경 → 마이그레이션 phase 없음. VSCode 디버깅 모드 자동 reload 불가이므로 사용자 수동 재시작 필요.

## 알려진 한계 / 범위 밖
- `users.team_id` 컬럼 자체 제거는 #23 책임 (이번엔 links 라우트가 안 읽도록만 전환).
- base.html 헤더 드롭다운: admin이 타인 링크 편집·삭제하는 UI 경로는 노출 안 됨(`isMine` 분기) — 백엔드는 허용. 운영상 의도(작성자 큐레이션)이며 이번 범위 밖.
