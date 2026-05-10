# 백엔드 변경 — 그룹 A 보강 사이클 (자동 dedup phase + migration_doctor)

## 개요

회사 운영 DB 첫 실행 시 #5 preflight이 충돌을 차단하지 않도록 두 가지 추가:

1. **자동 dedup phase** `team_phase_5a_projects_dedup_safe_v1` — 안전 그룹(빈 + 참조 0건)을
   같은 init_db() 호출 안에서 자동 정리.
2. **운영자 도구 `tools/migration_doctor.py`** — `WhatUdoin.exe --doctor` sub-command.
   사전 점검 / 사후 진단 / 안전 그룹 자동 정리 / unsafe 충돌 SQL 템플릿.

## S1 — 자동 dedup phase 본문 등록 (database.py)

### 추가된 헬퍼

#### `_projects_duplicate_groups(conn) -> list[dict]`
projects (team_id, name_norm) 충돌 그룹 목록. `team_id IS NOT NULL AND name_norm IS NOT NULL`
조건 안에서 GROUP BY HAVING COUNT(*) > 1. 그룹당 `{team_id, name_norm, ids: list[int]}`.
빈 테이블/컬럼 미존재면 [].

#### `_project_reference_count(conn, project_id, project_name) -> int`
주어진 projects.id의 다른 테이블 참조 총합. 카운트 대상:
- `events.project_id`, `checklists.project_id` (deleted_at 컬럼이 있으면 IS NULL만)
- `events.project = name AND project_id IS NULL` (문자열 잔존)
- `checklists.project = name AND project_id IS NULL`
- `events/checklists/meetings.trash_project_id`
- `project_members.project_id`, `project_milestones.project_id`

각 카운트는 컬럼 존재 여부를 사전 검사 (`_has_col`) — 합성 DB나 미적용 환경에서도 안전.

#### `_classify_projects_dedup_group(conn, group) -> dict`
그룹 안의 어떤 id를 살리고 어떤 id를 자동 DELETE할지 분류. 반환:
```
{
  team_id, name_norm, ids,
  ref_counts: dict[id, int],
  keep:   list[int],   # 보존
  delete: list[int],   # 자동 DELETE 대상
  safe:   bool,        # delete 1건 이상 → True
  unsafe_reason: str | None
}
```

분류 규칙 (사양 §"정리된 자동 dedup 정책"):
- **참조 있는 row가 1+개**: 참조 ≥1 모두 보존, 참조 0건 row만 자동 DELETE.
  - 참조 row가 2+면 인덱스 충돌은 여전히 unsafe → 이후 #5 preflight이 잡음.
  - delete가 0건이면 safe=False.
- **모두 참조 0건**: MIN(id) 1개 살리고 나머지 DELETE → safe=True.

### 새 phase 본문

#### `_phase_5a_projects_dedup_safe(conn)`
- 빈 DB / 컬럼 미존재 / 충돌 0건 → 사실상 노옵.
- 충돌 그룹 순회 → safe 그룹만 hard DELETE.
- 정리된 그룹마다 `_append_team_migration_warning`로 카테고리 `dedup_projects_auto`,
  메시지 `projects (team_id=X, name_norm='Y') kept_ids=[...] deleted_ids=[...]` 누적.
  dedup 가드는 헬퍼 자체에 내장(중복 메시지 자동 무시).
- 정리 row가 1건 이상이면 `[WhatUdoin][migration] phase 5a: auto-deleted N ...` 로그.

#### 등록 — PHASES 순서

```python
PHASES.append(("team_phase_5a_projects_dedup_safe_v1", _phase_5a_projects_dedup_safe))
# 그 다음 줄(파일 line 순서)
PHASES.append(("team_phase_5_projects_unique_v1", _phase_5_projects_unique))
```

같은 init_db() 호출에서 **5a (dedup) → 5 (preflight + UNIQUE 인덱스)** 순서가 보장됨.
preflight `_check_projects_team_name_unique`도 `_PREFLIGHT_CHECKS` 순서에 따라 phase
실행 직전에 수행되며, 5a가 안전 그룹을 청소한 후 남은 unsafe 충돌만 잡는다.

### Idempotency

- 두 번째 init_db() → phase 마커가 있어 미실행.
- 마커 강제 삭제 후 재실행 → row 수가 1이라 GROUP BY HAVING COUNT(*) > 1 자체가 0건 매칭.
  본문 노옵.

## S2 — 운영자 도구 `tools/migration_doctor.py`

### 패키지 구조

- `tools/__init__.py` (placeholder)
- `tools/migration_doctor.py`

### 명령어

#### `WhatUdoin.exe --doctor check` (read-only)
1. **projects 충돌 그룹 진단** — 각 그룹의 id별 참조 카운트 + 자동 정리 가능(safe) /
   운영자 결정 필요(unsafe) 분류 + ASCII 표 출력.
2. **users.name_norm 충돌 진단** — 자동 정리 X. 권장 SQL 템플릿(UPDATE/DELETE) 출력.
3. **teams.name_norm 충돌 진단** — 동일.
4. 종합:
   - 충돌 0건 → exit 0, "이상 없음".
   - unsafe 또는 users/teams 충돌 1+ → exit 1.
   - safe만 남음 → exit 0, "fix-projects --apply 권장".

DB는 `file:...?mode=ro` URI로 read-only 연결 — 락·트랜잭션 영향 0.

#### `WhatUdoin.exe --doctor fix-projects` (dry-run, 기본)
- 어떤 row가 정리되는지 표로 출력만, 변경 X.
- read-only 연결.

#### `WhatUdoin.exe --doctor fix-projects --apply`
1. `backup.run_migration_backup(db_path, run_dir)` 호출 → `whatudoin-migrate-{ts}.db`
   생성. 백업 실패 시 정리 거부 (exit 2).
2. read-write 연결로 `BEGIN IMMEDIATE` → 안전 그룹만 `DELETE FROM projects WHERE id IN (...)`
   → `COMMIT`. 실패 시 `ROLLBACK`.
3. 정리된 row 수 출력.

#### `--db-path PATH`
기본은 운영 DB(`whatudoin.db`). QA에서 합성 DB 경로 지정 가능.

### 도구가 사용하는 헬퍼

도구는 database 모듈에서 다음을 import만 한다:
- `_projects_duplicate_groups`
- `_classify_projects_dedup_group`
- `_table_exists`, `_column_set`

도구는 phase 본문을 *호출하지 않는다*. 같은 분류 로직을 read-only로 검사 + dry-run 출력
+ apply 시 단일 트랜잭션 정리. 결과는 phase 본문이 떴을 때와 정확히 동일.

## main.py — sub-command 분기

### 추가된 함수

#### `_is_doctor_invocation(argv) -> bool`
`argv[1]`이 `--doctor` 또는 `doctor`면 True. 단순 길이/값 검사.

#### `_run_doctor(argv) -> int`
도구 모드 진입점:
1. `WHATUDOIN_BASE_DIR` / `WHATUDOIN_RUN_DIR` 환경변수만 주입 (DB 경로 해석용).
2. Windows 콘솔 인코딩 처리:
   - dev 모드: stdout/stderr UTF-8 wrapping + `chcp 65001`.
   - frozen 모드: `AllocConsole` → CONOUT$ open.
3. `tools.migration_doctor.main(argv[2:])` 호출 → exit code 반환.
4. 트레이 / uvicorn / sidecar / supervisor 모두 **건드리지 않음**.

### `if __name__ == "__main__"` 분기

```python
if __name__ == "__main__":
    multiprocessing.freeze_support()
    if _is_doctor_invocation(sys.argv):
        sys.exit(_run_doctor(sys.argv))
    # ── 콘솔/스트림 초기화 ──
    ...
```

콘솔 초기화·sidecar 토글 검사·트레이 생성 모두 위 분기 *뒤*에 위치 → 도구 모드에서는
일반 동작 100% 영향 0.

## S3 — PyInstaller spec 업데이트 (WhatUdoin.spec)

### datas 추가

```python
('tools/__init__.py',         'tools'),
('tools/migration_doctor.py', 'tools'),
```

main.py가 frozen 모드에서도 `from tools.migration_doctor import main as doctor_main`을
import할 수 있도록 패키지 디렉토리째 번들에 포함.

### hiddenimports 추가

```python
'tools',
'tools.migration_doctor',
```

PyInstaller의 모듈 그래프가 동적 import를 추적 못 할 경우를 대비한 명시적 등록.

## 변경 파일 요약

| 파일 | 변경 종류 | 설명 |
|------|----------|------|
| `database.py` | 추가 | 5a phase 본문 + 헬퍼 3종 (`_projects_duplicate_groups`, `_project_reference_count`, `_classify_projects_dedup_group`) + `PHASES.append(...5a...)` |
| `tools/__init__.py` | 신설 | 빈 패키지 마커 |
| `tools/migration_doctor.py` | 신설 | argparse + check / fix-projects (dry-run · --apply) |
| `main.py` | 추가 | `_is_doctor_invocation`, `_run_doctor` + `__main__` 첫 줄 분기 |
| `WhatUdoin.spec` | 추가 | datas / hiddenimports에 tools 패키지 등록 |

## 안전 / 보존 정책

- **자동 정리는 `dedup_projects_auto` 워닝으로 영속 기록** — 운영자가 사후 추적 가능.
- **phase 5a hard DELETE는 `run_migration_backup()` 백업이 떠 있는 상태에서만 발생** —
  phase 러너가 백업 → preflight → phase 본문 순서를 강제.
- **도구 `--apply`도 자체 백업 호출 후 정리** — phase와 별도 백업 파일 생성.
- **users/teams.name_norm 자동 정리 X** — 합병 의사결정이 필요해 본 사이클에서는 진단·SQL
  템플릿만 출력. 운영자가 직접 처리.

## 한계 / 후속 사이클

- 같은 (team_id, name_norm) 그룹의 **참조 row가 2+개**이면 5a가 정리하지 않음 (참조 0건이
  아니므로). #5 preflight이 충돌 거부 → 운영자가 도구로 진단 후 직접 SQL 정리 → 재시작.
- users/teams 자동 정리는 별도 사이클 검토 (이름 정책·합병 정책 결정 후).
