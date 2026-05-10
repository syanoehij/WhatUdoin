# backend_changes — 팀 기능 그룹 A #3 (시스템 관리자 분리 + 관리팀 시드 처리)

## 사이클 범위
- 사양서: `.claude/workspaces/current/00_input/feature_spec.md`
- step S1+S2를 한 backend 호출에 묶어 진행. 프론트 변경 없음.
- 후속(#4·#5·#7·#8·#16) 범위 외 — 본 사이클은 건드리지 않음.

## 변경 파일·라인 요약

| 파일 | 위치 | 변경 종류 | 내용 |
|------|------|----------|------|
| `database.py` | 시드 블록 (구 566–582 → 신 566–576) | **삭제+수정** | 관리팀 자동 INSERT 블록 제거, admin 시드의 `team_id`를 NULL로 변경 |
| `database.py` | phase 4 등록 직후 (신 1080–1199 부근) | **추가** | `_ADMIN_TEAM_REF_TABLES` 리스트, `_team_has_external_refs()` 헬퍼, `_phase_3_admin_separation()` 본문, `PHASES.append(("team_phase_3_admin_separation_v1", ...))` |

신규 import 없음. `normalize_name`, `_table_exists`, `_column_set` 등은 이미 같은 파일에 정의됨.

## S1 — 새 phase 본문 (`team_phase_3_admin_separation_v1`)

### 본문 SQL 흐름 (실제 코드 인용)

```python
def _phase_3_admin_separation(conn):
    # 1) admin team_id → NULL
    conn.execute(
        "UPDATE users SET team_id = NULL "
        "WHERE role = 'admin' AND team_id IS NOT NULL"
    )

    # admin mcp_token_hash·created_at → NULL (컬럼 존재 가드)
    users_cols = _column_set(conn, "users")
    if "mcp_token_hash" in users_cols:
        conn.execute(
            "UPDATE users SET mcp_token_hash = NULL "
            "WHERE role = 'admin' AND mcp_token_hash IS NOT NULL"
        )
    if "mcp_token_created_at" in users_cols:
        conn.execute(
            "UPDATE users SET mcp_token_created_at = NULL "
            "WHERE role = 'admin' AND mcp_token_created_at IS NOT NULL"
        )

    # 2) admin user_ips whitelist → history 강등
    if _table_exists(conn, "user_ips"):
        conn.execute(
            "UPDATE user_ips SET type = 'history' "
            "WHERE type = 'whitelist' "
            "  AND user_id IN (SELECT id FROM users WHERE role = 'admin')"
        )

    # 3) 안전 보강: admin user_teams row 정리
    if _table_exists(conn, "user_teams"):
        conn.execute(
            "DELETE FROM user_teams "
            "WHERE user_id IN (SELECT id FROM users WHERE role = 'admin')"
        )

    # 4) 관리팀 분기 처리
    if not _table_exists(conn, "teams"):
        return
    teams_cols = _column_set(conn, "teams")
    admin_team = conn.execute(
        "SELECT id FROM teams WHERE name = ? LIMIT 1",
        ("관리팀",),
    ).fetchone()
    if not admin_team:
        return
    admin_team_id = admin_team["id"] if isinstance(admin_team, sqlite3.Row) else admin_team[0]

    if _team_has_external_refs(conn, admin_team_id):
        if "name_norm" in teams_cols:
            conn.execute(
                "UPDATE teams SET name = ?, name_norm = ? WHERE id = ?",
                ("AdminTeam", normalize_name("AdminTeam"), admin_team_id),
            )
        else:
            conn.execute(
                "UPDATE teams SET name = ? WHERE id = ?",
                ("AdminTeam", admin_team_id),
            )
    else:
        conn.execute("DELETE FROM teams WHERE id = ?", (admin_team_id,))


PHASES.append(("team_phase_3_admin_separation_v1", _phase_3_admin_separation))
```

### 참조 검사 헬퍼

```python
_ADMIN_TEAM_REF_TABLES = [
    ("users",              "team_id"),
    ("user_teams",         "team_id"),
    ("events",             "team_id"),
    ("checklists",         "team_id"),
    ("meetings",           "team_id"),
    ("projects",           "team_id"),
    ("notifications",      "team_id"),
    ("team_notices",       "team_id"),
    ("links",              "team_id"),
    ("team_menu_settings", "team_id"),
]


def _team_has_external_refs(conn, team_id: int) -> bool:
    for table, column in _ADMIN_TEAM_REF_TABLES:
        if not _table_exists(conn, table):
            continue
        if column not in _column_set(conn, table):
            continue
        row = conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
            (team_id,),
        ).fetchone()
        if row:
            return True
    return False
```

사양서가 정의한 8+2개 테이블 모두 검사. `users.team_id` 검사는 admin team_id NULL 처리 직후 시점에 일어나므로 admin은 자연스럽게 제외된다(NULL과 등호 비교는 항상 false). 사양서 §13 의도와 일치.

테이블/컬럼 존재 여부는 `_table_exists` + `_column_set`(PRAGMA table_info)으로 가드 → 빈 DB나 일부 테이블이 누락된 합성 DB에서도 안전.

### Idempotency 가드 설명

본 phase는 `_run_phase_migrations` 마커 외에도 **본문 자체가 idempotent**:

| 조작 | 가드 | 재실행 시 |
|------|------|-----------|
| admin team_id → NULL | `WHERE team_id IS NOT NULL` | 0행 |
| admin mcp_token_hash → NULL | `WHERE mcp_token_hash IS NOT NULL` | 0행 |
| admin mcp_token_created_at → NULL | `WHERE mcp_token_created_at IS NOT NULL` | 0행 |
| admin user_ips whitelist → history | `WHERE type='whitelist' AND user_id IN (...admin)` | 이미 history면 0행 |
| admin user_teams 정리 | DELETE…WHERE user_id IN (...admin) | 이미 row 없으면 0행 |
| 관리팀 처리 | `SELECT … WHERE name='관리팀'` | rename 후엔 'AdminTeam'이라 매칭 안 됨 → 노옵; DELETE 후엔 row 없음 → 노옵 |

마커 강제 삭제 후 재실행 시: `_run_phase_migrations`가 다시 본문을 실행하지만 위 가드 덕분에 데이터 상태 변화 없음. 마커만 다시 기록됨.

본문은 **단일 트랜잭션** (BEGIN IMMEDIATE … COMMIT, `_run_phase_migrations` 의 phase 러너가 감싼다). 부분 적용 불가.

`_run_phase_migrations` 진입점은 변경하지 않음 — phase 1·2·4와 동일한 흐름으로 자동 실행됨. PHASES 등록 순서: 1 → 2 → 4 → 3 (등록 순서). 본 사이클의 순서 의존성 없음:
- phase 3은 이미 적용된 phase 1(컬럼 추가)·phase 2(name_norm/role 백필)·phase 4(인덱스)를 가정하지만, 실제로는 모두 컬럼·테이블 존재 가드를 가지므로 등록 순서 어디에 있어도 안전.

## S2 — 시드 변경

### 변경 전 (database.py:566–582)

```python
if not conn.execute("SELECT 1 FROM teams LIMIT 1").fetchone():
    conn.execute(
        "INSERT INTO teams (name, name_norm) VALUES (?, ?)",
        ("관리팀", normalize_name("관리팀")),
    )
if not conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
    team_id = conn.execute("SELECT id FROM teams LIMIT 1").fetchone()[0]
    init_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    conn.execute(
        "INSERT INTO users (name, name_norm, password, role, team_id, is_active) "
        "VALUES (?,?,?,'admin',?,1)",
        ("admin", normalize_name("admin"), init_pw, team_id)
    )
```

### 변경 후 (database.py:566–576)

```python
# 팀 기능 그룹 A #3:
#   - 관리팀 자동 생성 제거 (시스템 관리자는 어떤 팀에도 소속되지 않는다).
#   - admin은 team_id=NULL 로 시드 → Phase 3 본문이 빈 DB에서 진정으로 노옵.
if not conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone():
    init_pw = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
    conn.execute(
        "INSERT INTO users (name, name_norm, password, role, team_id, is_active) "
        "VALUES (?,?,?,'admin',NULL,1)",
        ("admin", normalize_name("admin"), init_pw)
    )
    print(f"[WhatUdoin] 초기 관리자 비밀번호: {init_pw}  (최초 1회만 표시, 즉시 변경 권장)")
```

차이:
- 관리팀 자동 INSERT 블록 완전히 제거.
- admin INSERT 의 `team_id` placeholder 제거, `VALUES (?,?,?,'admin',NULL,1)` 로 NULL 명시.

빈 DB 첫 init_db() 결과:
- `teams` row 0건.
- `users` 에 admin 1명, team_id=NULL.
- `user_teams` 에 admin row 없음 (#2 백필 가드는 `team_id IS NOT NULL AND role != 'admin'` 이중 가드).
- Phase 3 본문 → admin team_id 이미 NULL, 관리팀 없음 → 모든 경로에서 노옵.

## admin 제외 보강 — grep 결과

사양서 §"admin 제외 보강" 의 후보 4군 + 사양서가 인용한 line 6개를 grep 으로 정렬한 결과:

### 이미 `WHERE u.role != 'admin'` 들어있는 쿼리 (변경 금지, 보존)

| 위치 | 함수/맥락 | 비고 |
|------|---------|------|
| `database.py:1049` | `_phase_2_team_backfill` user_teams 백필 | #2에서 들어감 |
| `database.py:2482` | `get_hidden_project_addable_members` | 히든 프로젝트 추가 가능 멤버 |
| `database.py:2561` | `transfer_hidden_project_owner` | owner 이양 검증 |
| `database.py:2590` | `admin_change_hidden_project_owner` | admin 강제 owner 변경 검증 |
| `database.py:3463` | `list_users_with_avr` | AVR 설정 화면용 |
| `database.py:4106` | `release_hidden_project_owner_by_user` | owner 이양 fallback |
| `app.py:1616` | `/api/teams/members` | Python `if u.get("role") != "admin"` 필터 |
| `app.py:3665` | `/api/members` | Python `if u.get("role") != "admin"` 필터 |

### 보강한 누락 지점

**없음.** 사양서가 지시한 후보 4군(일반 사용자 자동완성, 멤버 목록, MCP 일반 사용자 조회, 히든 프로젝트 멤버 후보)에 대해 grep 결과 누락 지점이 발견되지 않았다.

검증 grep 항목:
- `def\s+(get_|list_|search_).*user` — database.py 의 후보 함수 17개 모두 확인.
- `assignee|autocomplete|/api/members|/api/users|members_list` — app.py·mcp_server.py 라우트 확인.
- mcp_server.py 에는 `mcp/list_users` 류 함수 없음 (반환 필드 docstring 의 `assignee` 만 매칭).
- 회의록·일정의 assignee 자동완성은 `/api/teams/members` (app.py:1610) + `/api/members` (app.py:3662) + `/api/hidden-project-assignees` (app.py:2392) 3개로 통합되어 있고 모두 admin 필터링 됨.

### 의도적으로 변경하지 않은 후보

| 위치 | 함수 | 이유 |
|------|------|------|
| `database.py:2888` `get_all_users()` | LEFT JOIN teams, admin 포함 반환 | admin 화면(`/admin`, `/api/admin/users`) 용. 호출처 4곳 모두 admin 필터링 또는 admin 전용 페이지. 함수 자체를 바꾸면 admin 화면이 깨짐. |
| `database.py:1943` `create_notification_for_all()` | `SELECT name FROM users WHERE is_active=1` | 사양서가 명시한 4군에 속하지 않음 (알림 발송). 별도 사이클에서 정책 결정 필요. 본 사이클 범위 외. |
| `database.py:2904` `get_user_by_password()` | role 무관 비밀번호 매칭 | 에디터 로그인 경로. admin은 별도 `get_user_by_credentials`(role='admin' 필터). 변경 불필요. |
| `database.py:3036·3042` `check_register_duplicate()` | 가입 중복 검사 | admin도 중복 검사 대상이어야 함. 변경 불필요. |
| `database.py:3394` admin 카운트 | `WHERE role='admin'` | admin 1명 이상 보장 검증용. 정상. |

## 수정하지 않은 영역(사양서 명시)

- `_run_phase_migrations` 진입점 — phase 1·2·4와 동일 흐름 유지.
- auth.py — `get_user_by_whitelist_ip`의 admin 차단은 코드 측에서 이미 처리됨. 본 사이클은 데이터 측에서도 보장 (whitelist row 강등).
- 후속 사이클 범위(#4 데이터 백필, #5 project_id, #7 비밀번호 hash, #8 가입, #16 라우트 호출부) — 미터치.

## 후속 검증 가이드 (reviewer/qa 참고)

reviewer:
- phase 본문 idempotency: 위 표 참고.
- 참조 분기 안전성: `_team_has_external_refs` 가 사양서 8+2 테이블 모두 검사하는지, `_table_exists` 가드가 합성 DB 에서 안전한지.
- 시드 변경: `teams` 자동 생성 제거 후 admin team_id=NULL 만 남는지.
- admin 제외 보강 누락/과잉: 위 grep 결과표 검증.

qa (사양서 exit criteria):
- 빈 DB → 새 시드 적용. teams 0건, admin team_id NULL, user_teams admin 0건.
- 합성 구 DB(관리팀 + admin team_id=관리팀id + admin mcp_token + admin whitelist + 다른 데이터) → phase 본문 1회로 admin 데이터 분리 + 관리팀 분기 처리 검증.
- 두 번째 init_db() → phase 본문 재실행 안 됨 (마커).
- 마커 강제 삭제 후 재실행 → 가드 덕에 노옵.
- 참조 분기 (a) 0건 → DELETE / (b) ≥1건 → AdminTeam rename 두 케이스 별도 가짜 DB 검증.
- 검증 스크립트는 `.claude/workspaces/current/scripts/` 아래.
- VSCode 디버깅 모드 — qa 는 import-time + 합성 DB 위주. 실서버 재시작 필요 시 사용자에게 요청.

---

## 후속 패치 — S4 차단 결함 fix (AdminTeam 이름 충돌 fallback)

### 배경
qa 보고서(`qa_report.md` §S4)가 차단 결함을 발견:

> 합성 구 DB에 `teams.name='AdminTeam'`이 미리 1건 존재한 상태에서 phase 본문이 관리팀 rename 분기로 진입 → `IntegrityError('UNIQUE constraint failed: teams.name')` (database.py:58 `name TEXT NOT NULL UNIQUE`) → phase 러너 ROLLBACK → `_run_phase_migrations()`가 RuntimeError raise → `init_db()` 실패 → **서버 시작 거부**.

이 시나리오는 합성이 아니라 **운영자가 'AdminTeam'이라는 영문 팀을 미리 만들어둔 환경**에서 다음 배포 시 실재로 발생할 수 있다(자연스러운 팀명).

### 패치 방향
rename 시도 전에 `teams.name='AdminTeam'`이 다른 row(`admin_team_id`가 아닌 id)에 이미 있는지 사전 SELECT.
- 충돌 없음 → 기존 그대로 'AdminTeam'으로 rename (정상 케이스, warning 누적 안 함).
- 충돌 있음 → fallback 이름 `f"관리팀_legacy_{admin_team_id}"` 사용 + `_append_team_migration_warning(category="admin_separation", ...)` 누적.

대안 검토(채택하지 않음):
- (B) 충돌 시 DELETE 강제 — 참조 ≥1건이라 분기에 들어왔으므로 데이터 손실. 채택 금지.
- (C) preflight 검사 후 서버 시작 거부 — 자동 처리 정신에 어긋남, 운영 부담 큼.

→ (A) fallback 이름이 사양 정신("가능한 한 자동 처리, 운영자에게는 경고로 알림")에 가장 부합.

### 변경 위치
`database.py` `_phase_3_admin_separation` 함수 내부, `_team_has_external_refs(conn, admin_team_id)` True 분기 (이전 ~1180–1188행).

### 변경 전
```python
if _team_has_external_refs(conn, admin_team_id):
    # 참조 ≥1건 → 'AdminTeam'으로 rename (name·name_norm 동시 갱신).
    # name_norm 컬럼 존재 가드 (Phase 1이 추가하지만 방어적).
    if "name_norm" in teams_cols:
        conn.execute(
            "UPDATE teams SET name = ?, name_norm = ? WHERE id = ?",
            ("AdminTeam", normalize_name("AdminTeam"), admin_team_id),
        )
    else:
        conn.execute(
            "UPDATE teams SET name = ? WHERE id = ?",
            ("AdminTeam", admin_team_id),
        )
else:
    # 참조 0건 → DELETE
    conn.execute("DELETE FROM teams WHERE id = ?", (admin_team_id,))
```

### 변경 후
```python
if _team_has_external_refs(conn, admin_team_id):
    # 참조 ≥1건 → 'AdminTeam'으로 rename (name·name_norm 동시 갱신).
    # 단, 운영자가 미리 'AdminTeam' 팀을 만들어둔 환경(name UNIQUE 제약 충돌)에서는
    # IntegrityError로 phase가 ROLLBACK되어 서버가 시작되지 않으므로,
    # 사전 SELECT로 충돌을 감지하면 fallback 이름('관리팀_legacy_<id>')으로 rename하고
    # 운영자가 후속 정리할 수 있도록 team_migration_warnings에 기록한다.
    target_name = "AdminTeam"
    conflict_row = conn.execute(
        "SELECT 1 FROM teams WHERE name = ? AND id != ? LIMIT 1",
        (target_name, admin_team_id),
    ).fetchone()
    if conflict_row:
        target_name = f"관리팀_legacy_{admin_team_id}"
        _append_team_migration_warning(
            conn,
            "admin_separation",
            f"AdminTeam 이름 충돌, '관리팀'을 '{target_name}'(id={admin_team_id})로 rename",
        )

    # name_norm 컬럼 존재 가드 (Phase 1이 추가하지만 방어적).
    if "name_norm" in teams_cols:
        conn.execute(
            "UPDATE teams SET name = ?, name_norm = ? WHERE id = ?",
            (target_name, normalize_name(target_name), admin_team_id),
        )
    else:
        conn.execute(
            "UPDATE teams SET name = ? WHERE id = ?",
            (target_name, admin_team_id),
        )
else:
    # 참조 0건 → DELETE
    conn.execute("DELETE FROM teams WHERE id = ?", (admin_team_id,))
```

### fallback 이름 정책
- 정상 (AdminTeam 충돌 없음): `target_name = "AdminTeam"`, warning 누적 안 함.
- 충돌: `target_name = f"관리팀_legacy_{admin_team_id}"`.
  - 한국어 prefix '관리팀_legacy_' — 운영자가 한글 UI에서 즉시 알아볼 수 있게 의도적 한글 보존.
  - id suffix — 동시에 여러 충돌이 발생하더라도 rename 결과가 자연스럽게 unique (admin_team_id는 PK라 unique).
  - `name_norm`도 `normalize_name(target_name)`으로 동시 갱신 → fallback 이름의 NFC+casefold 정규화 형도 정확하게 기록.

### Idempotency 검증
- **재실행 마커 정상 동작 시 (1회만 실행)**: 어떤 분기든 1회만 일어남. 영향 없음.
- **마커 강제 삭제 후 재실행**:
  - 정상 rename 후: `WHERE name = '관리팀'` SELECT가 None → 본문 즉시 `return` (이전 SELECT가 'AdminTeam'으로 바꿔놨으므로 매칭 안 됨).
  - fallback rename 후: `WHERE name = '관리팀'` SELECT가 None → 'fallback rename'된 row의 name이 '관리팀_legacy_N'이라 매칭 안 됨 → 즉시 `return`.
  - DELETE 후: row 자체가 없음 → 즉시 `return`.
- 모든 케이스에서 데이터 변화 없음 → idempotent 보장.

### `_append_team_migration_warning` 중복 가드
이 헬퍼(database.py:773–)는 같은 (category, message) 쌍을 두 번 누적하지 않도록 race-safe append를 한다(database.py:790–795 중복 방지 루프). 마커 강제 삭제 후 재실행해도 (`충돌 row가 그대로 있다면`) `category='admin_separation'` + 동일 메시지가 이미 누적되어 있으므로 두 번째 메시지는 삽입되지 않는다.

### Warning 누적 의도
운영자는 다음 두 곳에서 fallback이 사용되었음을 인지할 수 있다:
1. `settings.team_migration_warnings` JSON 배열 (`category='admin_separation'`) — UI 운영 화면이나 API에서 조회.
2. backupDB 디렉터리에 `whatudoin-migrate-{timestamp}.db`로 phase 직전 DB 스냅샷이 저장되어 있음 — fallback이 실수라고 판단되면 운영자가 'AdminTeam'을 다른 이름으로 변경한 후 백업에서 관리팀 데이터 복원 가능.

### 변경 범위 (재확인)
- `_phase_3_admin_separation` rename 분기 내부만 수정.
- DELETE 분기, `_ADMIN_TEAM_REF_TABLES`, `_team_has_external_refs`, 시드, admin 제외 보강, PHASES 등록, 다른 phase 인프라 — **모두 미터치**.
- 신규 import 없음 (`_append_team_migration_warning`은 같은 파일 함수).

### qa 재검증 가이드
1. 기존 `scripts/verify_admin_separation.py` 7 시나리오 모두 PASS여야 함 (S4가 fallback 동작으로 PASS로 전환).
2. 추가 시나리오 권장: 합성 DB에 `teams.name='AdminTeam'`이 미리 있고 그 'AdminTeam'도 다른 user의 team_id로 참조되는 케이스 → `_phase_3_admin_separation` 실행 후 (a) 두 팀 row 모두 살아남고, (b) 관리팀이 `관리팀_legacy_<id>`로 rename, (c) 'AdminTeam' 팀의 외부 참조는 그대로 유지(다른 user의 team_id 보존), (d) `team_migration_warnings`에 충돌 경고 1건 누적.
3. 마커 강제 삭제 후 재실행해도 fallback 케이스의 warning 카운트가 1로 유지되는지(중복 누적 안 되는지) 확인.
