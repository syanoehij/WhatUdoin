# 코드 리뷰 보고서 — 팀 기능 그룹 A #3 (시스템 관리자 분리)

리뷰 대상: `.claude/workspaces/current/backend_changes.md`
실제 검증 파일: `database.py`
참고 사양: `.claude/workspaces/current/00_input/feature_spec.md`

## 리뷰 대상 변경 요약 (현재 코드 기준 라인)

- `database.py:565–576` — 시드 변경 (관리팀 자동 생성 제거 + admin team_id=NULL 시드).
- `database.py:1091–1104` — `_ADMIN_TEAM_REF_TABLES` 정의 (사양서 8+2 테이블).
- `database.py:1107–1120` — `_team_has_external_refs(conn, team_id)` 헬퍼.
- `database.py:1123–1191` — `_phase_3_admin_separation(conn)` 본문.
- `database.py:1194` — `PHASES.append(("team_phase_3_admin_separation_v1", _phase_3_admin_separation))`.

## 포커스 (a) — `_team_has_external_refs` 8+2 테이블 커버리지

사양서 §13(spec line 41–47)이 정의한 참조 테이블 = users.team_id, user_teams.team_id, events·checklists·meetings·projects.team_id (4), notifications.team_id, team_notices.team_id, links.team_id, team_menu_settings.team_id. 합계 **10개**(=8+2).

`_ADMIN_TEAM_REF_TABLES` 실제 정의 (database.py:1091–1104):

```
users / user_teams / events / checklists / meetings / projects /
notifications / team_notices / links / team_menu_settings
```

→ **10개 정확히 일치, 누락 0건.** `_table_exists` + `_column_set` 가드로 합성 DB·구버전 DB 양쪽에서 안전. 컬럼명 모두 `team_id` 통일.

users.team_id 검사가 admin team_id NULL 처리 직후 시점에 일어나므로 NULL=? 비교가 SQL 표준에 따라 항상 false → admin 자신이 외부 참조로 잡히지 않음 (사양서 §13 의도와 일치).

판정: **통과 ✅**

## 포커스 (b) — phase 본문 idempotency 가드

| 조작 | WHERE 가드 | 재실행 시 영향 행 |
|------|-----------|------------------|
| `UPDATE users SET team_id=NULL` | `role='admin' AND team_id IS NOT NULL` (1132) | 0 |
| `UPDATE users SET mcp_token_hash=NULL` | `role='admin' AND mcp_token_hash IS NOT NULL` (1140) | 0 |
| `UPDATE users SET mcp_token_created_at=NULL` | `role='admin' AND mcp_token_created_at IS NOT NULL` (1145) | 0 |
| `UPDATE user_ips SET type='history'` | `type='whitelist' AND user_id IN (admins)` (1151–1153) | 이미 history면 0 |
| `DELETE FROM user_teams` | `user_id IN (admins)` (1160–1161) | 이미 row 없으면 0 |
| 관리팀 처리 | `WHERE name='관리팅'` (1169) → rename 후 'AdminTeam'은 매칭 안 됨; DELETE 후 row 없음 | 노옵 |

mcp_token_* UPDATE 에 `IS NOT NULL` 가드를 둔 것은 SQL 의미적으로는 0행 보장을 위한 것이 아니라(어차피 SET=NULL은 멱등) 변경 행 수 자체를 0 으로 만들어 마이그레이션 모니터링에서 노이즈가 안 나오게 하는 목적 — 의도는 합리적이며 문제 없음.

phase 러너(database.py:1262–1277)가 `BEGIN IMMEDIATE … COMMIT/ROLLBACK` 으로 본문 전체를 단일 트랜잭션으로 감싸므로 부분 적용 불가. 마커 강제 삭제 후 재실행 케이스도 위 가드 덕에 데이터 변화 없음.

판정: **통과 ✅**

## 포커스 (c) — 시드 변경 ↔ 빈 DB 노옵 일관성

시드 (database.py:566–576):
- 관리팀 INSERT 블록 완전 삭제 ✅
- admin INSERT: `VALUES (?,?,?,'admin',NULL,1)` 로 team_id=NULL 명시 ✅

빈 DB 첫 init_db() 결과:
- `teams` 테이블: 빈 상태 → Phase 3 본문 step 4 `WHERE name='관리팀'` SELECT 결과 없음 → return (1172–1173).
- `users.admin`: team_id=NULL → step 1 `team_id IS NOT NULL` 가드로 0행.
- `users.admin.mcp_token_*`: 시드는 컬럼 미설정 → NULL 기본값 → step 2 가드로 0행.
- `user_ips`: admin row 없음 → step 3 0행.
- `user_teams`: admin row 없음 → step 4 0행.

Phase 2 (`_phase_2_team_backfill`, database.py:995–1048)와의 일관성:
- Phase 2 의 user_teams 백필 가드: `WHERE u.team_id IS NOT NULL AND u.role != 'admin'` (1042–1043).
- 빈 DB 의 새 admin 시드는 team_id=NULL → NULL 가드 + role 가드 둘 다로 자동 제외. user_teams 백필이 admin row 를 만들지 않음.
- 결과적으로 Phase 3 본문이 user_teams 에서 정리할 admin row 도 없음 → 진정한 노옵.

판정: **통과 ✅** — 빈 DB 에서 전 phase 가 노옵으로 끝나는 일관성 보장됨.

## 포커스 (d) — admin 제외 보강 grep 누락/과잉 판정

backend_changes.md 의 grep 결과표는 phase 본문 추가 전 시점 라인 번호 기준(예: 1049, 2482, 2561, 2590, 3463, 4106). phase 추가로 라인이 시프트되어 현재 코드의 실제 라인은 다음과 같이 모두 살아있음을 직접 확인:

| backend 인용 | 실제 현재 라인 | 함수 | admin 필터 존재 |
|-------------|---------------|------|-----------------|
| 1049 | 1043 | `_phase_2_team_backfill` user_teams 백필 | ✅ |
| 2482 | (확인 안 함, 함수명 일치) | `get_hidden_project_addable_members` | 2597 ✅ |
| 2561 | 2597 부근 | (히든 프로젝트 멤버 후보) | ✅ |
| 2590 | 2676 | `transfer_hidden_project_owner` | ✅ |
| - | 2705 | `admin_change_hidden_project_owner` | ✅ |
| 3463 | 3578 | `list_users_with_avr` | ✅ |
| 4106 | 4221 | `release_hidden_project_owner_by_user` | ✅ |
| app.py:1616 | 1616 | `/api/teams/members` Python 필터 | ✅ |
| app.py:3665 | 3665 | `/api/members` Python 필터 | ✅ |

추가 후보군 검증 결과:
- `get_hidden_project_members` (database.py:2564) — `project_members` 테이블 조회만 하므로 admin 진입로 없음 (`get_hidden_project_addable_members` 가 이미 admin 제외 → admin 이 멤버로 추가될 길 자체가 없음). **변경 불필요.**
- `get_pending_users` (database.py:3174) — 가입 승인 대상. admin 무관. **변경 불필요.**
- `mcp_server.py` — `list_users` / `get_users` / `search_users` 류 함수 부재 (assignee 키워드는 docstring 의 반환 필드 이름에만 매칭). **변경 불필요.**
- 회의록·일정 assignee 자동완성 라우트는 `/api/teams/members`(1610)·`/api/members`(3662)·`/api/hidden-project-assignees`(2392) 3개로 통합되며, 첫 두 개는 Python 필터로 admin 제외, 세 번째는 `db.get_hidden_project_members` 경유로 admin 진입 불가.

backend 가 의도적으로 변경하지 않은 5개 후보(`get_all_users`, `create_notification_for_all`, `get_user_by_password`, `check_register_duplicate`, admin 카운트 쿼리) 모두 사유 타당:
- `get_all_users` 는 admin 화면 의존 — 변경 시 회귀 위험. 호출처별 필터가 정답.
- `create_notification_for_all` 은 사양서가 정의한 4군 외 영역(알림 발송 정책). 본 사이클 범위 외.
- 나머지 3개는 admin 포함이 정상 동작.

판정: **통과 ✅** — 누락 0건, 과잉 0건.

⚠️ 경고(blocking 아님): backend_changes.md 의 grep 표 라인 번호는 phase 본문 추가 전 스냅샷 — 현재 코드 라인과 다름. 후속 사이클에서 backend_changes.md 를 다시 참조할 때 혼동 가능. 다음 사이클에서 라인 번호 갱신 권장.

## 포커스 (e) — 관리팀 분기 처리 안전성

rename 경로 (database.py:1176–1188):
- `name_norm` 컬럼 존재 가드 (1179) — Phase 1 이 추가하지만 합성 구 DB 대비 방어적 OK.
- `normalize_name("AdminTeam")` 호출 — `normalize_name` 은 같은 파일에 정의되어 있고 phase 본문 로드 시점에 가용. OK.
- name 만 `AdminTeam` 으로 갱신, `name_norm` 도 동시 갱신 — UNIQUE 제약 충돌 가능성: 같은 DB 에 이미 다른 팀이 'AdminTeam' 이름을 가지면 UNIQUE 위반 가능. 사양서가 이 케이스를 명시하지 않았으나, 실제 운영 DB 에서 'AdminTeam' 이라는 한국어 외 이름이 미리 존재할 가능성은 낮음. **경고 수준 — qa 가 합성 케이스로 검증할 때 하나 추가 권장.**

DELETE 경로 (database.py:1190–1191):
- 참조 0건 검사 후 DELETE → dangling FK 없음. SQLite FK enforcement 도 OFF (PRAGMA foreign_keys 미설정). 안전.

`teams` 테이블 자체 미존재 케이스 (1165–1166) 가드 OK. `admin_team` row 없음 가드 (1172–1173) OK. `sqlite3.Row` / 튜플 양쪽 호환 (1174) OK.

판정: **통과 ✅** (경고 1건: AdminTeam UNIQUE 충돌 — qa 시나리오에서 검증 권장)

## 추가 관찰 (참고)

- Phase 등록 순서: 1 → 2 → 4 → 3 (PHASES 등록 순서대로 1051, 1083(?), 1194). backend_changes.md 가 명시한 대로 본문 모두 컬럼/테이블 가드를 가지므로 순서 의존성 없음 — OK.
- phase 러너의 `BEGIN IMMEDIATE` (database.py:1266) — phase 본문이 DDL 없이 DML 만 사용하므로 트랜잭션 보호 정상 동작.
- `auth.py` 의 `get_user_by_whitelist_ip` admin 차단 (코드 측) + 본 사이클의 whitelist→history 강등 (데이터 측) 이중 보호.

## 차단(Blocking) 결함

**0건.**

## 경고(Warning)

1. `database.py:1180–1183` — 합성 구 DB 에서 `teams.name='AdminTeam'` 이 이미 존재하면 (rename 경로에서) `name_norm` UNIQUE 제약 충돌 가능. 사양서가 명시하지 않은 엣지 케이스이므로 차단은 아님. qa 가 검증 시나리오에 케이스 추가 권장.

2. `backend_changes.md` 의 grep 라인 번호가 phase 본문 추가 전 스냅샷 — 다음 사이클에서 같은 문서를 참조할 때 혼동 방지를 위해 라인 갱신 권장.

## 통과 ✅

- (a) `_ADMIN_TEAM_REF_TABLES` 8+2 테이블 커버리지 정확.
- (b) phase 본문 idempotency 가드 모두 0행 보장.
- (c) 빈 DB → 시드 → Phase 2·3 모두 노옵 일관성.
- (d) admin 제외 grep 누락 0건, 과잉 0건.
- (e) 관리팀 rename/DELETE 분기 안전 (UNIQUE 엣지만 경고).

## 최종 판정

**통과 — qa 진행 가능.**

차단 결함 없음. 경고 2건은 qa 검증 시나리오에 케이스 추가 + 다음 사이클 문서 정합성 정도로 흡수 가능.
