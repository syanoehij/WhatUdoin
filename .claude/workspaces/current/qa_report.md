# QA 보고서 — 팀 기능 그룹 A #3 (시스템 관리자 분리 + 관리팀 시드)

검증 대상:
- `database.py:565–576` — 시드 변경 (관리팀 자동 생성 제거 + admin team_id=NULL).
- `database.py:1091–1104` — `_ADMIN_TEAM_REF_TABLES`.
- `database.py:1107–1120` — `_team_has_external_refs()`.
- `database.py:1123–1208` — `_phase_3_admin_separation()` (1176–1208행 rename fallback 패치 포함).
- `database.py:1211` — `PHASES.append(("team_phase_3_admin_separation_v1", ...))`.
- `database.py:773–809` — `_append_team_migration_warning()` (재사용, 중복 가드).
- admin 제외 함수: `get_hidden_project_addable_members`, `list_users_with_avr`, `/api/members`, `/api/teams/members` Python 필터.

검증 방식: import-time + 합성 DB. 실서버 무영향(`DB_PATH`·`_RUN_DIR`을 임시 폴더로 monkey-patch).
검증 도구: `.claude/workspaces/current/scripts/verify_admin_separation.py` (재실행 가능, exit code = FAIL 개수).
실행 환경: Python 3.12 (`D:\Program Files\Python\Python312\python.exe`), Windows 11.

## 사이클 이력
- **#3-cycle1 (이전)**: S4 차단 결함 발견 (BLOCKING) — `teams.name UNIQUE` 제약으로 IntegrityError, 서버 시작 거부 위험. 나머지 6 시나리오는 PASS.
- **#3-cycle2 (현재)**: backend가 rename 분기에 사전 SELECT + fallback 이름(`관리팀_legacy_<id>`) + `_append_team_migration_warning("admin_separation", ...)` 추가. **재검증 — 9 시나리오 전체 PASS**.

## 요약 (cycle2 재검증 결과)

| 시나리오 | 결과 | 주요 검증 포인트 |
|---------|------|----------------|
| S1 | **PASS** | 빈 DB init_db() — teams 0건, admin team_id=NULL, user_teams admin 0건, phase 마커 기록 |
| S2 | **PASS** | 합성 구 DB (a) 참조 0건 → 관리팀 DELETE + admin 분리 후속 모두 |
| S3 | **PASS** | 합성 구 DB (b) 참조 ≥1건 → AdminTeam rename + name_norm 동시 갱신, alice 보존 |
| **S4** | **PASS** | **fallback 패치 검증** — 사전 AdminTeam 그대로, 관리팀은 `관리팀_legacy_<id>`로 rename, warning 1건 누적 |
| **S4-extra (신규)** | **PASS** | 사전 AdminTeam에도 외부 참조 ≥1건 (bob) → 두 팀 모두 보존, 관리팀만 fallback rename, AdminTeam 외부 참조 보존, warning 1건 |
| **S4-rerun (신규)** | **PASS** | 마커 강제 삭제 후 재실행 → fallback rename 결과 유지, warning 카운트 1 유지 (중복 누적 없음) |
| S5 | **PASS** | admin 분리 후속 — team_id/mcp_token NULL, whitelist→history(row 보존), user_teams admin 정리 |
| S6 | **PASS** | 재실행 가드 — 두 번째 init_db()는 마커로 미실행, 마커 강제 삭제 후 재실행도 본문 가드로 노옵 |
| S7 | **PASS** | admin 제외 — addable_members / list_users_with_avr / /api/members / /api/teams/members 모두 admin 미포함 |

**판정: 전체 PASS (9/9). cycle1의 차단 결함이 fallback 패치로 해소됨.**

```
summary: PASS=9 / FAIL=0 / WARN=0 / TOTAL=9
exit code: 0
```

---

## S4 fallback 패치 검증 상세

### 패치 동작 (database.py:1176–1208)
1. `_team_has_external_refs(conn, admin_team_id)` True 분기 진입.
2. `target_name = "AdminTeam"`로 시작.
3. 사전 SELECT: `SELECT 1 FROM teams WHERE name = ? AND id != ?` (target_name, admin_team_id).
4. 충돌 row 발견 시:
   - `target_name = f"관리팀_legacy_{admin_team_id}"`로 전환.
   - `_append_team_migration_warning(conn, "admin_separation", "AdminTeam 이름 충돌, '관리팀'을 '<fallback>'(id=<id>)로 rename")` 누적.
5. `UPDATE teams SET name = ?, name_norm = ? WHERE id = ?` — `target_name`과 `normalize_name(target_name)` 동시 갱신.

### S4 (cycle2) — 단일 fallback 케이스
- 합성: `관리팀(id=1)` + 사전 `AdminTeam(id=2, name_norm='adminteam')` + alice(team_id=1).
- 마이그레이션 후:
  - id=1 → `name='관리팀_legacy_1'`, `name_norm='관리팀_legacy_1'` (NFC + casefold 결과; 한글이라 변화 없음, 영문/대문자 없음).
  - id=2 → `name='AdminTeam'`, `name_norm='adminteam'` 그대로.
  - alice.team_id=1 보존.
  - admin.team_id=NULL.
  - `team_migration_warnings`에 `category='admin_separation'` 1건. message: `"AdminTeam 이름 충돌, '관리팀'을 '관리팀_legacy_1'(id=1)로 rename"`.

### S4-extra — 두 팀 모두 외부 참조
- 합성: `관리팀(id=1)` + 사전 `AdminTeam(id=2)` + alice(team_id=1, 관리팀 참조) + bob(team_id=2, AdminTeam 참조).
- 마이그레이션 후:
  - id=1 → `관리팀_legacy_1`로 fallback rename.
  - id=2 → `AdminTeam` 그대로 (rename 안 됨, 패치 분기는 admin_team_id에만 작동).
  - **bob.team_id=2 보존** — 패치가 사전 AdminTeam의 외부 참조에 영향을 주지 않음을 확인.
  - alice.team_id=1 보존.
  - total teams = 2 (두 팀 모두 살아남음).
  - admin_separation warning 1건.

이 시나리오는 운영자가 'AdminTeam'을 미리 만들고 거기에 사용자를 배치한 환경에서 마이그레이션이 안전하게 작동함을 직접 입증한다.

### S4-rerun — warning 중복 가드
- 1차 실행: fallback 발생, admin_separation warning 1건 누적.
- 마커 (`migration_phase:team_phase_3_admin_separation_v1`) 강제 삭제.
- 2차 실행 (`_run_phase_migrations()` 재호출):
  - phase 본문 진입 → `WHERE name='관리팀'` SELECT가 None (이미 `관리팀_legacy_1`으로 rename됨) → 즉시 `return` (관리팀 분기 노옵).
  - **본문이 rename 분기에 도달하지 않으므로** `_append_team_migration_warning` 호출도 일어나지 않음.
  - admin_separation warning 카운트 = 1 유지.
  - rename된 row 상태(`관리팀_legacy_1`) 그대로.

추가 안전망: 본문이 어떤 이유로든 rename 분기에 다시 도달했다고 하더라도, `_append_team_migration_warning(database.py:773–809)`이 같은 `(category, message)` 쌍을 두 번 추가하지 않는 race-safe 중복 가드(database.py:790–797)를 갖고 있음. 이중 idempotency.

---

## 통과 시나리오 상세

### S1: 빈 DB init_db()
- `teams_count=0`, `admin.team_id=None`, `admin.mcp_token_hash=None`, `admin.mcp_token_created_at=None`.
- `user_teams.admin_count=0`, `user_ips.admin_whitelist_count=0`.
- phase 마커 `migration_phase:team_phase_3_admin_separation_v1` 기록 확인.

### S2: 합성 구 DB (a) 참조 0건 → DELETE
- 관리팀 row가 마이그레이션 후 사라짐 (DELETE 분기).
- admin: team_id NULL, mcp_token_hash/created_at NULL.
- user_ips admin: whitelist 0건, history 1건 (row 보존, type만 강등).

### S3: 합성 구 DB (b) 참조 ≥1건 → rename (정상 케이스)
- 사전 AdminTeam 충돌 없음 → fallback 분기 진입 안 함.
- 관리팀 row → `name='AdminTeam'`, `name_norm='adminteam'`(NFC + casefold)으로 갱신.
- alice의 team_id 보존.
- admin team_id NULL.
- admin_separation warning 0건 (정상 rename 시 누적 안 함, 의도된 동작).

### S5: admin 분리 후속 처리
- `inject_user_teams_admin_row=True`로 정상 흐름엔 없는 잔존물(user_teams admin row)을 강제 주입.
- 마이그레이션 후: ut_admin_after=0 (정리됨), ips_total 보존(2 → 2), whitelist=0/history=1.
- admin team_id=NULL, mcp_token_*=NULL.

### S6: 재실행 가드
- 첫 init_db() 후 phase 마커 기록 확인.
- 두 번째 init_db()로도 admin 스냅샷·teams_count 변화 없음 → 마커 가드 동작.
- 마커 강제 삭제 후 세 번째 init_db() → 마커 재기록되고 데이터 변화 없음 → 본문 가드 동작.

### S7: admin 제외 함수 단위
- 합성 데이터: 팀A + alice/bob(member). 히든 프로젝트 owner=alice.
- `get_hidden_project_addable_members(project_id)` → admin 미포함, bob 포함.
- `list_users_with_avr()` → admin 미포함.
- `/api/members` 시뮬레이트(`get_all_users` + Python 필터) → admin 미포함, alice·bob 포함.
- `/api/teams/members` 시뮬레이트 → admin 미포함.

---

## 회귀 확인

- 기존 phase 1·2·4 마이그레이션은 매 시나리오에서 정상 OK 출력 → 회귀 없음.
- backup 인프라(`backup.py:run_migration_backup`)도 임시 폴더로 정상 작동 — 매 phase 호출 전 `backupDB/whatudoin-migrate-{timestamp}.db` 생성 확인.
- 기존 PASS 시나리오(S1·S2·S3·S5·S6·S7) 모두 cycle2에서도 PASS 유지 → 패치가 다른 분기에 부작용 없음.

## 산출물

- `.claude/workspaces/current/scripts/verify_admin_separation.py` — **위치 정합성 정리** (이전 cycle은 `<root>/scripts/`에 있었음 → 사양서가 정의한 워크스페이스 scripts/로 이동, ROOT 경로도 `parents[4]`로 업데이트). 9개 시나리오 통합 검증. 실서버 영향 없음(임시 폴더 사용). 재실행 가능. exit code=FAIL 개수.
- `.claude/workspaces/current/qa_report.md` — 본 보고서 (cycle2 재검증으로 갱신).

## 미실행 항목

- 실서버 Playwright E2E — task spec에서 명시적으로 import-time + 합성 DB만 요구. fallback 분기는 데이터 마이그레이션 동작이라 UI 테스트 대상이 아님.
- 실 운영 DB에서의 마이그레이션 — VSCode 디버깅 모드라 서버 재시작 불가. 본 사이클의 데이터 변경은 없으므로 재시작은 다음 사이클(또는 운영 배포 시점)까지 지연 가능.

## 다음 액션

1. **차단 결함 해소** — backend 재호출 불요. 9 시나리오 전체 PASS.
2. (필요 시) 사용자 결정으로 실서버 재시작 — VSCode 디버깅 모드라 자동 재시작 불가, 수동 처리 필요. 본 사이클 변경은 import-time + 합성 DB로 검증 완료이므로 재시작은 다음 묶음 배포 때 함께 진행해도 안전.
3. 후속 사이클(#4 등) 진행 시 `team_migration_warnings`의 `admin_separation` 카테고리를 운영 UI에서 노출하는 작업이 필요할 수 있음(범위 외).
