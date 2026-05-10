# 팀 기능 그룹 A — #5 QA report

검증 스크립트: `.claude/workspaces/current/scripts/verify_projects_unique.py`.
실행 환경: Python 3.12, SQLite via stdlib `sqlite3`. 각 시나리오는 별도 `WHATUDOIN_RUN_DIR`(임시 디렉토리)에서 독립 subprocess로 실행되어 module-level state(`DB_PATH`, `_WAL_MODE_READY`, PHASES 마커) 오염 없음.

VSCode 디버깅 모드 — 본 사이클은 import-time + 합성 DB 검증만 수행, 실서버 재시작 불필요.

## 시나리오 결과 — 9/9 PASS

| # | 시나리오 | 사양 매핑 | 결과 |
|---|---------|----------|------|
| 1 | empty_db | "빈 DB → phase 본문 노옵, 인덱스 신규 생성" | PASS |
| 2 | backfill_null_name_norm | "잔존 NULL name_norm row + 같은 팀 다른 프로젝트 → 백필 후 부분 UNIQUE 인덱스" | PASS |
| 3 | preflight_conflict | "합성 DB (team_id=1, name_norm='abc') 2건 → 시작 거부 + warning(`preflight_projects_team_name`)" | PASS |
| 4 | cross_team_same_name_allowed | "같은 이름 다른 팀에 생성 → 성공" | PASS |
| 5 | same_team_duplicate_blocked | "같은 팀 안에서 같은 이름(대소문자·NFC 차이 포함) 두 번째 생성 차단" + "rename 충돌 차단" | PASS |
| 6 | null_team_id_exempt | "team_id IS NULL 끼리 같은 이름 허용" | PASS |
| 7 | marker_strip_idempotent | "마커 강제 삭제 후 재실행 → 백필 0건, IF NOT EXISTS 노옵" | PASS |
| 8 | cross_team_rename_no_interference | (B1 회귀 방지) "다른 팀의 동일 이름 프로젝트가 rename 시 휘말리지 않음" | PASS |
| 9 | hidden_project_team_scoped | "히든 프로젝트도 (team_id, name_norm) 정책 — 같은 팀 차단, 다른 팀 허용" | PASS |

## 시나리오 본문 요약

### 1. empty_db — 빈 DB → phase 본문 노옵 + 인덱스 신규 생성
- `init_db()` 1회 호출.
- 검증: `settings`에 phase 마커 6개(`team_phase_{1,2,4_indexes,3,4_data_backfill,5}_*`), `idx_projects_team_name` 인덱스 존재, `team_migration_warnings`에 `preflight_projects_team_name` 카테고리 없음.

### 2. backfill_null_name_norm
- `init_db()` 1회 → 팀+프로젝트 2건 삽입(같은 팀 다른 이름) → 한 row의 `name_norm`을 NULL로 강제 → phase 5 마커 삭제 → `init_db()` 재호출.
- 검증: 재실행 후 `name_norm`이 `normalize_name(name)`으로 채워지고, 인덱스 그대로 유지(IF NOT EXISTS).

### 3. preflight_conflict
- `init_db()` 1회 → 인덱스 DROP + phase 5 마커 삭제 → 같은 팀에 같은 `name_norm` row 2건 삽입(`Alpha`/`ALPHA` casefold 동일) → warnings 초기화 → `init_db()` 재호출.
- 검증: `RuntimeError("migration preflight failed with 1 conflict(s); ...")` 발생. `team_migration_warnings`에 `preflight_projects_team_name` 카테고리 추가. preflight 메시지: `projects (team_id=N, name_norm='alpha') duplicates=2 ids=[X,Y]`.

### 4. cross_team_same_name_allowed
- 두 팀 생성 → `db.create_project("Shared", ..., team_id=t1)` + `db.create_project("Shared", ..., team_id=t2)`.
- 검증: 두 row의 id 분리, `team_id` 각각 t1/t2로 저장, 동시 존재.

### 5. same_team_duplicate_blocked
- `db.create_project("MyProj", ..., team_id=t1)` 후 `db.create_project("MYPROJ", ..., team_id=t1)` 시도.
- 검증: `sqlite3.IntegrityError`. (사전 검사가 casefold 동일 → 차단; 추가로 부분 UNIQUE 인덱스도 backstop.)
- 추가: `db.create_project("Other", ..., team_id=t1)` 후 `db.rename_project("Other", "myproj")` 시도 → `sqlite3.IntegrityError`.

### 6. null_team_id_exempt
- `db.create_project("Floating", ..., team_id=None)` × 2.
- 검증: 두 row 동시 존재. 부분 UNIQUE 인덱스의 `WHERE team_id IS NOT NULL` 정의 정합.

### 7. marker_strip_idempotent
- `init_db()` 1회 → phase 5 마커만 삭제(인덱스 유지) → `init_db()` 재호출.
- 검증: 마커 재기록, 인덱스 그대로(IF NOT EXISTS), 백필 0건(`WHERE name_norm IS NULL` 가드).

### 8. cross_team_rename_no_interference (B1 회귀 방지 — code review에서 발견)
- 두 팀 생성 → 양 팀에 `foo` 프로젝트 + 양 팀에 `events.project='foo'` 1건씩 → `db.rename_project("foo", "bar")`.
- 검증:
  - 두 projects row 중 정확히 하나만 `bar`, 다른 하나는 `foo` 유지.
  - rename된 팀의 events.project='bar', 다른 팀의 events.project='foo' 유지.
- 결론: `rename_project`의 일반 분기가 `WHERE id = ?`로 좁혀졌고 events/checklists 갱신이 `team_id` 한정으로 동작.

### 9. hidden_project_team_scoped — 사양 §exit criteria "히든 프로젝트도 같은 정책"
- 두 팀 + 한 사용자(team_id=t1, role=member) 생성.
- `db.create_hidden_project("Secret", "#fff", "memo", owner_id=alice, team_id=t1)` → 성공.
- `db.create_hidden_project("SECRET", "#000", None, owner_id=alice, team_id=t1)` → None (casefold 동일, 같은 팀 차단).
- `db.create_hidden_project("Secret", "#abc", None, owner_id=alice, team_id=t2)` → 다른 id로 성공.
- 검증: `is_hidden=1` row가 두 팀에 각 1개씩 존재.

## 비차단 메모

1. **admin UX 변화** — `POST /api/manage/projects`는 admin이 work_team_id 쿠키 미설정 + 명시 team_id 미지정 상태에서 400으로 거부됨. 사양 §40이 NULL 저장 회피를 명시했으므로 의도된 변경. #15(쿠키 통합) 적용 후 admin은 쿠키나 본문 명시로 컨텍스트를 결정. backend_changes.md "본 사이클이 손대지 않은 dormant 이슈" 섹션에 기록되어 있음.

2. **`db.get_project(name)` ambiguity** — PUT rename 라우트가 사용. 본 사이클 시점에는 cross-team 동일 이름 row가 아직 존재하지 않으므로 모호하지 않음. QA에서 cross-team 동일 이름은 POST 라우트 검증 시나리오에서만 만들고 PUT 검증은 별도 setup. #10(가시성 라우트 적용)에서 team_id 파라미터 명시화 예정.

3. **단독 시나리오 실행 시 백업 누적** — `verify_projects_unique.py --scenario N`(특정 시나리오 단독 실행)은 RUN_DIR 격리를 거치지 않으므로 `D:\Github\WhatUdoin\backupDB\`에 백업이 생성될 수 있음. 정상 흐름(인자 없이 전체 실행)은 모두 임시 디렉토리에서 동작하므로 영향 없음. 운영 환경에서는 단독 실행을 권장하지 않음.

## 검증 명령

```
"D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_projects_unique.py
```

마지막 실행 결과: `OK: all 9 scenarios pass`.

## 결론

사양서가 정의한 8개 exit criteria + code review에서 발견된 1개 회귀 시나리오 모두 PASS. 차단 결함 1건은 같은 사이클 안에서 backend 패치 후 재검증 통과. 본 사이클(#5)은 종료 가능.
