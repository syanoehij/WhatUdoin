# 요청
마이그레이션 dedup phase ordering 버그 수정. `database.py:_run_phase_migrations()`에서 모든 preflight가 모든 phase 본문보다 먼저 통째로 실행되기 때문에, "재시작만으로 안전 dedup → 그 다음 #5 preflight 통과 → 인덱스 생성"으로 의도한 `team_phase_5a_projects_dedup_safe_v1`가 실행 기회를 못 얻고 죽은 코드가 됨. dedup-성격 phase가 자기 대응 preflight보다 먼저 실행되도록 러너 실행 순서를 고친다. 작업 중 발생하는 에러도 같은 흐름에서 수정.

# 분류
백엔드 수정 (러너 실행 순서 / preflight gating) / 백엔드 모드 (backend → reviewer → qa)

# 채택 방향 (advisor 확인 완료 — Candidate 1)
`_PRE_PREFLIGHT_PHASES` 허용 리스트 방식. 새 phase 본문을 추가하지 않고 러너(`_run_phase_migrations`)의 실행 순서만 고친다. 기존 `_phase_5a_projects_dedup_safe` 본문은 그대로 둠.

후보 2(preflight가 dedup pending이면 skip)·후보 3(preflight 직전 별도 훅으로 추출)은 채택하지 않음 — 2는 unsafe 케이스를 사람이 읽을 수 있는 preflight 메시지 대신 raw IntegrityError가 잡게 되어 UX 후퇴 + `team_migration_warnings`의 `team_id`/`name_norm` 컨텍스트 손실; 3은 phase 마커 + per-phase 트랜잭션 래퍼를 제거해 수술 범위가 크고 향후 dedup-성격 phase 추가가 어려움.

# backend-dev 담당 작업 (database.py 보고 최선 판단 — 아래는 가이드)

## 1. `_PRE_PREFLIGHT_PHASES` 정의
`PHASES`/`_PREFLIGHT_CHECKS` 정의부 근처(L729~736 부근)에 추가:
```python
# preflight 검사보다 *먼저* 실행되어야 하는 phase 이름 집합.
# 여기 등록된 phase는 같은 init_db() 호출에서 preflight 앞에서 본문 실행 + 마커 커밋되고,
# 그 뒤 preflight → 나머지 phase 순으로 진행된다.
# 계약: (a) idempotent (재실행 안전, clean state면 노옵), (b) preflight가 강제하는
#       UNIQUE invariant에 의존하지 않을 것 (그 invariant를 만들어주는 정리 작업이 목적).
# 의도: team_phase_5a가 안전 dedup → 그 결과를 #5 preflight가 검증 → #5 인덱스 생성.
_PRE_PREFLIGHT_PHASES: frozenset = frozenset({"team_phase_5a_projects_dedup_safe_v1"})
```

## 2. `_run_phase_migrations` 실행 순서 분할
현재 흐름: ① `pending = _pending_phases()`; 비면 즉시 반환 → ② 백업 1회 → ③ preflight 일괄 → ④ pending 전체 phase 본문 순차 실행.

수정 후 흐름:
- ① `pending` 계산 + 비면 즉시 반환 — **불변** (백업·preflight 모두 skip).
- ② 백업 1회 — **불변** (위치·로직 그대로).
- ②.5 `pending`을 `pre_preflight = [(n,b) for (n,b) in pending if n in _PRE_PREFLIGHT_PHASES]` 와 `rest = [(n,b) for (n,b) in pending if n not in _PRE_PREFLIGHT_PHASES]`로 분할. **PHASES 등록 순서 유지** (재정렬 금지 — 필터링만).
- ②.6 `pre_preflight`를 기존 ④의 per-phase 트랜잭션 래퍼(L2471~2486)와 **완전히 동일한 패턴**으로 먼저 실행: `with get_conn() as conn: conn.isolation_level=None; BEGIN IMMEDIATE; body(conn); _mark_phase_done; COMMIT` / 실패 시 ROLLBACK + RuntimeError. **각 pre-preflight phase는 preflight가 돌기 전에 마커까지 커밋되어야 함** (preflight 실패가 5a 마커를 롤백시키면 안 됨 — 별개 트랜잭션·별개 `with get_conn()` 블록이라 자연히 분리됨).
- ③ preflight 일괄 실행 — **불변** (이제 5a dedup이 끝난 상태에서 돌므로 남은 충돌(unsafe)만 잡음).
- ④ `rest`를 기존 패턴으로 순차 실행.

가능하면 per-phase 트랜잭션 래퍼를 작은 헬퍼(`_run_phase_body(name, body)`)로 빼서 ②.6과 ④에서 공유 — 단 surgical 원칙상 중복이 짧으면 그냥 두 번 써도 됨. backend 판단.

## 3. 주석 갱신
- L1592~1594 (`# 등록 순서:` 블록): 현재 "PHASES.append 순서 = 실행 순서 … dedup → preflight → 인덱스 생성 순서가 보장된다"는 이제 거짓. `_PRE_PREFLIGHT_PHASES`를 가리키도록 교체. 예: "5a는 `_PRE_PREFLIGHT_PHASES`에 등록되어 같은 init_db()에서 preflight 앞에서 실행된다 — 이게 dedup → preflight → 인덱스 생성 순서를 보장한다. (단순 PHASES 순서로는 보장 안 됨: 러너가 preflight를 모든 phase 본문보다 먼저 일괄 실행하기 때문.)"
- L1864~1867, L1577~1578 부근 ("Phase 5a가 앞서 안전 그룹을 자동 정리하므로 …" / "→ 이후 #5 preflight가 거부") — 이미 의도를 맞게 서술하므로 사실관계만 맞으면 그대로. 어긋난 부분 있으면 surgical 수정.
- `_run_phase_migrations` docstring(L2422~2431)의 "동작 순서" 리스트에 pre-preflight 단계 한 줄 추가.
- (advisor 메모) case 2처럼 unsafe 충돌로 preflight가 거부해도 5a 마커는 set 상태로 남음 — 운영자가 unsafe row를 수동/doctor로 정리한 뒤 재시작하면 preflight가 통과하고 #5가 진행됨. 5a는 "고려됐고 안전하게 할 수 있는 게 더 없음" 상태이므로 재실행 불필요. 이 점을 5a 주석에 1줄 명시 (미래 독자가 버그로 오해 안 하게).

## 4. migration_doctor.py 영향 확인 (수정 아님)
`migration_doctor.py`는 `_classify_projects_dedup_group` / `_projects_duplicate_groups` / `_phase_5a_…` 헬퍼를 직접 호출하지 별개 진입점이라 `_run_phase_migrations`를 안 거침. grep 1회로 "doctor가 `_run_phase_migrations` 또는 분할된 함수를 호출하지 않음"만 확인. 수정 불필요.

## 5. 기존 검증 스크립트 회귀 확인
`scripts/verify_dedup_phase.py`, `scripts/verify_phase_infra.py`, `scripts/verify_team_a_001_close.py` (있으면) 가 이 변경으로 깨지지 않는지 — backend가 빠르게 실행해보거나, 적어도 어떤 가정에 의존하는지 확인. 깨지면 그 스크립트가 검증하던 invariant가 실제로 깨진 건지(=내 버그) 아니면 스크립트가 옛 순서를 하드코딩한 건지 판단.

## 6. backend_changes.md
`.claude/workspaces/current/backend_changes.md`에: 수정한 함수·라인, diff 요약, 분할 후 실행 순서, migration_doctor 무영향 근거(grep 결과), 기존 verify 스크립트 회귀 여부, 서버 재시작 필요 여부(러너 코드 변경 — 코드 reload용 재시작 필요, 스키마 변경 없음 명시).

# 주의사항 / 불변식
- 백업·트랜잭션 래퍼·`_mark_phase_done`·`team_migration_warnings` 누적 등 기존 인프라 동작 **불변**. 백업은 `if not pending: return` 직후·모든 phase 본문 전 1회 — 위치 그대로.
- unsafe 그룹(참조 ≥2 등 살아남는 row 1건 이상)은 여전히 보존 + preflight(`_check_projects_team_name_unique`)가 잡아 RuntimeError로 서버 시작 거부 — 현 동작 유지.
- pending=0이면 백업·preflight·phase 본문 전부 skip — 불변 (운영 DB는 이미 doctor로 정리됨, 마커도 다 찍혀 있을 가능성 — 이 수정이 운영 DB 기동을 막을 일 없음).
- `_pending_phases()`는 PHASES 순서대로 반환 — 그 순서를 분할 후에도 유지.
- 새 phase 본문 추가 금지. `_phase_5a_projects_dedup_safe` 본문 변경 금지 (주석은 가능).
- CLAUDE.md surgical changes 원칙. 인접 코드 "개선" 금지.
- 임시 .db 파일은 OS temp dir 또는 `.claude/workspaces/current/` 하위 — 루트에 안 남김.

# 분류 메모
백엔드 모드: backend-dev → code-reviewer → qa (frontend-dev 생략 — UI 변경 없음).
