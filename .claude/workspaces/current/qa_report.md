## QA 보고서 — 팀 기능 그룹 A #10 (문서·체크 팀 경계 + 편집·삭제 권한 모델)

### 환경 제약
- 실서버 OFF + VSCode 디버깅 모드 → 자동 재시작 불가 → **실서버 Playwright E2E 불가**.
- 대안: import-time 검증 + 합성 DB + FastAPI TestClient + 기존 standalone 테스트.
- **서버 재시작 필요**: 본 사이클은 라우트/쿼리/권한 헬퍼만 변경, **스키마 변경(마이그레이션 phase 추가) 없음**. 따라서 재시작 시 코드 reload 만 필요(DB 마이그레이션 불필요). 운영 반영 전 사용자가 VSCode 디버거 수동 재시작 필요.

### 통과 ✅
- [x] import-time: `import app, mcp_server, permissions, auth, database` — OK (라우트 데코레이터/시그니처/순환 import 깨짐 없음).
- [x] 합성 DB + TestClient (`.claude/workspaces/current/scripts/verify_team10.py`): **71 PASS / 0 FAIL**. 커버리지:
  - 가시성 (현재 작업 팀 기준): `/api/events`, `/api/checklists`, `/api/projects`, `/api/manage/projects`, `/api/doc`, `/api/kanban` — 다중 팀 사용자(X∈A,B)의 작업 팀 전환에 따라 자료 변동, 같은 팀 멤버(Y∈A)는 작업 팀 자료만, 비소속(Z∈B)은 타팀·NULL row 미노출, admin 은 전체, 비로그인은 is_public 만.
  - 비소속 team_id 명시(Y@B): 무시하고 대표 팀 A 로 fallback → B 자료 노출 안 됨.
  - 문서 혼합 모델: 팀문서(team_id ∈ scope) / 개인문서(작성자 본인 항상, team_share=1 은 같은 작업 팀 멤버 읽기만) / NULL팀 개인문서(작성자 본인만).
  - 편집·삭제 권한: 팀 공유 모델(같은 팀 멤버 B 가 멤버 A 작성 일정 편집 가능 / 타팀 불가), 문서 혼합(팀문서=같은팀 누구나, 개인문서=작성자만, team_share=1 은 읽기만 → 편집 시 거부), admin 전역.
  - **추방 시나리오**: 추방된 멤버는 자기 작성 팀자료(EvA·DocTeamA_byY)에도 접근/편집 불가(§8-1 "팀 소속이라서 보이는 것"), 단 자기 개인문서(DocPersA_byY)·자기 작성 개인 row(EvNullLegacy)는 계속 보유. 재가입 시 자동 복구.
  - NULL team row 회귀 방지: events/checklists/projects 의 team_id IS NULL row 가 다른 팀 멤버(Z) 세션의 모든 목록에 노출 안 됨, 작성자/owner 본인 세션에서만 노출.
  - `teams.deleted_at` 자동 제외: 삭제 예정 팀 멤버십은 `user_team_ids` 에서 빠짐.
- [x] 기존 회귀: `tests/phase75_m6_mcp_owner_boundary.py` 21 PASS (MCP write-owner 경계 — `import auth` 추가에도 영향 없음). `tests/test_html_table_to_gfm.py` 등 standalone 12 PASS.

### 실패 / 미커버 ❌
- 없음 (신규 결함). advisor 리뷰에서 발견된 `_can_read_doc` 작성자 단축 결함(추방된 팀문서 작성자에게 노출)은 같은 흐름에서 패치 + 재검증 완료(위 추방 시나리오 항목에 포함).

### 알려진 한계 (차단 아님 — 후속)
- `tests/test_project_rename.py` 2건 실패 — **#10 무관 사전 결함**. `master` HEAD(`f433c20`)에서도 동일 실패 (`database.py:4080 no such column: team_id` — 테스트 픽스처가 #4/#5 era 스키마와 불일치). 본 사이클이 도입한 회귀 아님.
- assignee 기반 "내 스케줄" 미니 위젯(`/api/my-meetings`, `/api/my-milestones`, `/api/project-milestones/calendar`): 팀 경계 미적용(담당자+히든 필터만) — backend_changes.md·code_review_report.md 에 기록. 후속 권고.
- MCP 도구 end-to-end 미검증(실서버 OFF) — 단 동일 db 헬퍼·`_can_read_*` 함수를 공유하고 합성 DB 검증으로 간접 커버됨.

### 회귀 확인
- import-time OK / MCP owner boundary 21 PASS / standalone unit 12 PASS / 합성 DB 71 PASS.
- `tests/test_project_rename.py` 2건 실패는 사전 결함(본 사이클 무관).

### 최종 판정
- **통과** — 실서버 E2E 불가 환경에서 가능한 최대 검증(import + 합성 DB + TestClient + 기존 테스트) 모두 통과. 운영 반영 시 서버 수동 재시작 필요(스키마 무변경, 코드 reload 만).
