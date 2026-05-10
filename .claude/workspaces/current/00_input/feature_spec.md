# 팀 기능 그룹 A — #3 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#3. 시스템 관리자(admin) 분리 + 관리팀 시드 처리**를 한 사이클로 진행. 마스터 plan은 `팀 기능 구현 계획.md` §2, §13 (시드 데이터 정리).

#4 이후는 본 사이클 범위 밖.

## 분류

백엔드: phase 본문 등록(admin 데이터 분리 + 관리팀 처리) + `init_db()` 시드 갱신 + 후보 조회 admin 제외 점검·보강.
프론트 변경 없음. **팀 모드: backend → reviewer → qa.**

## 전제 (#1·#2에서 완료된 것)

- `database.py:631-811` PHASES 인프라 + `normalize_name()` (#1).
- Phase 1·2·4 본문(`team_phase_1_columns_v1`, `team_phase_2_backfill_v1`, `team_phase_4_indexes_v1`) 등록 완료 (#2).
- `auth.py` 신규 권한 헬퍼 7개 + 기존 위임 (#2).
- `users.role`은 `editor` → `member`로 일괄 변환 완료. `admin`은 그대로.
- `user_teams` 백필은 `role != 'admin'` 가드로 들어감 → admin은 row 없음.

## 핵심 인계 사실 (메인이 이미 파악)

### 현재 시드 코드 (database.py:566-582)
- `teams`가 비어 있으면 `("관리팀", normalize_name("관리팀"))` 1행 INSERT (#2에서 name_norm 추가됨).
- `users`에 admin이 없으면 그 첫 팀(=관리팀)을 `team_id`로 admin INSERT.
- **#3에서 두 시드 모두 변경**: 관리팀 자동 생성 제거 + admin은 `team_id=NULL` 시드.

### 이미 admin 제외가 들어 있는 쿼리 (보강만)
- `database.py:2482, 2561, 2590, 3463, 4106` — assignee 후보·멤버 후보·일반 사용자 조회는 이미 `WHERE u.role != 'admin'` 패턴 다수 적용. 본 사이클은 **누락 지점**을 grep으로 확인 후 보강.

### admin 데이터 위치
- `users.role='admin'` (database.py:572 등)
- `users.team_id` — admin이 시드 시점에 관리팀 id로 설정되어 있을 수 있음 → NULL로 변경
- `users.mcp_token_hash`, `users.mcp_token_created_at` — admin이 발급받은 적 있다면 NULL로
- `user_ips` `type='whitelist'` 중 admin user_id row → `type='history'`로 강등 (row 삭제 X, 이력 보존)

### 관리팀 처리 결정 기준 (계획서 §13)
"기존 '관리팀' 처리: 참조 데이터 없으면 삭제, 있으면 `AdminTeam`으로 rename (`name`·`name_norm` 동시 갱신)"

본 사양서가 정의하는 **참조 데이터 = 다음 중 하나라도 해당 team_id를 가리키는 row가 1건 이상 존재**:
- `users.team_id` (admin 자신은 NULL로 만든 후 검사 → admin 외에 그 팀 소속 사용자가 있는가)
- `user_teams.team_id`
- `events.team_id`, `checklists.team_id`, `meetings.team_id`, `projects.team_id`
- `notifications.team_id`, `team_notices.team_id`
- `links.team_id`, `team_menu_settings.team_id`

검사 시점은 admin team_id NULL 처리 직후. 검사 결과:
- 참조 0건 → `DELETE FROM teams WHERE id = ?`
- 참조 ≥1건 → `UPDATE teams SET name='AdminTeam', name_norm=normalize_name('AdminTeam') WHERE id=?`

대상 팀 식별: `WHERE name='관리팀'`.

### IP 자동 로그인 흐름 (auth.py:28-33)
- `get_user_by_whitelist_ip(ip)` → `user_ips.type='whitelist'` 매칭. role='admin'이면 None 반환 (이미 코드 차단).
- 본 사이클은 그 차단을 데이터 측에서도 보장 (whitelist row 자체를 history로 강등).

## #3 step 분해 (플래너 참고)

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #3-S1 | phase 본문 등록 (admin 데이터 분리 + 관리팀 처리) | 새 phase `team_phase_3_admin_separation_v1` 등록. 4개 마이그레이션을 한 phase 본문 안에 idempotent 가드와 함께 묶음. 두 번째 init_db()에서 본문 미실행. 마커 강제 삭제 후 재실행도 데이터 손상 없음. |
| #3-S2 | 시드 코드 갱신 + 후보 조회 admin 제외 점검·보강 | `init_db()` 시드: 관리팀 자동 생성 제거 + admin 시드 `team_id=NULL`. 일반 사용자 자동완성·assignee 후보·멤버 목록·MCP 일반 사용자 조회·히든 프로젝트 멤버 후보 함수들을 grep으로 정렬한 뒤 admin 제외 누락 지점 보강. |

> 두 step을 1 backend 호출로 묶어도 무방하나, S1(phase 본문)은 마이그레이션 검증 스크립트가 필요하고 S2(코드 변경)는 호출부 단위 검증이 필요하므로 reviewer/qa 단계에서 두 영역을 따로 다룬다.

## exit criteria (사이클 전체)

### 마이그레이션 동작
- [ ] 빈 DB → 새 시드(관리팀 미생성, admin team_id NULL) 적용. phase 본문은 빈 데이터에 노옵으로 끝남.
- [ ] 합성 구 DB(관리팀 + admin team_id=관리팀id + admin mcp_token + admin whitelist + 다른 데이터 일부) → phase 본문 1회 실행:
  - admin team_id → NULL
  - admin mcp_token_hash·created_at → NULL
  - admin user_ips whitelist → history 강등 (row count 보존)
  - 관리팀: 참조 있으면 AdminTeam rename, 없으면 삭제
- [ ] 두 번째 init_db() → 마커 덕에 phase 본문 재실행 안 됨.
- [ ] 마커 강제 삭제 후 재실행 → 가드 덕에 노옵 (이미 admin team_id NULL이라 변화 없음).
- [ ] **참조 데이터 분기 검증**: (a) 참조 0건 케이스 → DELETE / (b) 참조 ≥1건 케이스(예: 다른 user의 team_id가 관리팀) → AdminTeam rename. 두 케이스 모두 별도 가짜 DB에서 검증.

### 시드 코드
- [ ] 빈 DB 첫 init_db() → `teams` 비어 있음. `users`에 admin 1명 있고 `team_id=NULL`.
- [ ] admin이 새로 시드된 직후 user_teams에 admin row 없음.

### admin 제외 보강
- [ ] 다음 호출에서 admin 미포함 검증:
  - 일반 사용자 자동완성 (회의록 assignee, 일정 assignee 등 — 정확한 함수명은 backend가 grep으로 확인해 backend_changes.md에 열거)
  - 멤버 목록 (예: `/admin` 사용자 목록 외, 일반 화면용 멤버 조회)
  - MCP 일반 사용자 조회 (`mcp/list_users` 류)
  - 히든 프로젝트 멤버 후보 (`get_hidden_project_addable_members` 등)
- [ ] 누락된 지점이 발견되면 보강. 이미 admin 제외가 들어 있는 쿼리는 그대로 둔다 (변경 최소화).

## 진행 방식

- backend가 S1+S2를 1회 호출에서 묶거나 분리. step 종료마다 `backend_changes.md`에 4종 추가.
- reviewer는 (a) phase 본문 idempotency·참조 분기 안전성, (b) 시드 변경, (c) admin 제외 보강 누락/과잉 확인.
- qa는 (a) 합성 구 DB로 마이그레이션 시나리오 검증(2종 분기), (b) 빈 DB 시드 검증, (c) admin 제외 함수 단위 검증.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **본 사이클은 #3 범위만.** 비밀번호 hash 변환은 #7, 가입 흐름은 #8, 라우트 호출부의 admin work_team 명시는 #16.
- **admin이 user_teams에 row가 있을 수 없다는 가정**은 #2에서 백필이 보장. 본 사이클은 추가 가정 없음. 단, 안전을 위해 phase 본문에 `DELETE FROM user_teams WHERE user_id IN (SELECT id FROM users WHERE role='admin')`도 포함 (idempotent, 정상 흐름에서는 노옵).
- 관리팀 rename 시 `AdminTeam`은 영문 그대로 (UI 노출용). `name_norm`도 `normalize_name('AdminTeam')`으로 동시 갱신.
- `delete teams WHERE id=?` 시 외래키 cascade는 없음 (SQLite 기본). 본 사양은 참조 0건 검사 후 DELETE이므로 dangling 없음.
- 후보 조회 admin 제외 보강 범위가 너무 넓어지지 않도록 backend는 grep 결과를 backend_changes.md에 명시하고 reviewer가 누락/과잉을 판단.
- VSCode 디버깅 모드 — qa는 import-time 검증 + 합성 DB 검증 위주. 실서버 재시작 필요 시 사용자에게 요청.

## 산출물 위치

- `backend_changes.md`, `code_review_report.md`, `qa_report.md`: `.claude/workspaces/current/` 직속
- 검증 스크립트: `.claude/workspaces/current/scripts/`
