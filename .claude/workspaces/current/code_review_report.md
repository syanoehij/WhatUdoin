## 코드 리뷰 보고서 — 마이그레이션 dedup phase ordering 버그 수정

### 리뷰 대상 파일
- `database.py` — `_PRE_PREFLIGHT_PHASES` 신규(L738~749), `_run_phase_body` 신규(L2441~2467), `_run_phase_migrations` 분할(L2470~2525), 5a 등록 블록 주석 갱신(L1605 부근)
- (확인만) `tools/migration_doctor.py` — 미변경, 영향 없음

### 체크리스트 적용 결과

| 항목 | 결과 |
|------|------|
| DB 경로 (`_RUN_DIR`/`_BASE_DIR`) 혼용 | N/A — DB 경로 변경 없음 |
| SQL 파라미터화 | ✅ — 새 SQL 추가 없음. 5a 본문의 `DELETE ... WHERE id IN ({placeholders})`는 미변경, `?` 바인딩 유지 |
| 권한 체크 / `_ctx()` / 라우트 | N/A — 라우트·템플릿 변경 없음 |
| 트랜잭션 시맨틱 보존 | ✅ — `_run_phase_body`는 기존 `_run_phase_migrations` 내부 per-phase 루프 본문을 그대로 추출 (`isolation_level=None` + `BEGIN IMMEDIATE` + 수동 COMMIT/ROLLBACK, 동일 로그 메시지) |
| 마커 커밋 순서 (discriminator) | ✅ — pre-preflight phase는 각자 독립 `with get_conn()` 블록으로 실행 → COMMIT이 다음 문 전에 끝남. 따라서 `_run_preflight_checks` 실행 시점에 5a 마커는 이미 영속. 직후 preflight가 RuntimeError를 던져도 5a 마커는 롤백 안 됨 |
| 백업 타이밍 | ✅ — `if not pending: return` 직후·모든 phase 본문 전 1회. 위치·로직 불변 |
| PHASES 등록 순서 보존 | ✅ — `pre_preflight`/`rest` 분할은 list comprehension 필터링만 (재정렬 없음). `_pending_phases()`는 PHASES 순서대로 반환 |
| pending=0 경로 | ✅ — 즉시 반환, 백업·preflight·phase 전부 skip. 불변 |
| 5a 본문 / dedup 헬퍼 미변경 | ✅ — `_phase_5a_projects_dedup_safe`, `_classify_projects_dedup_group`, `_projects_duplicate_groups` 미수정 (주석만) |
| 5a를 pre-preflight로 올린 게 안전한가 | ✅ — 5a는 `not groups`면 early return(노옵), UNIQUE invariant에 의존 안 함 → `_PRE_PREFLIGHT_PHASES` 계약 (a)(b) 충족 |
| migration_doctor 영향 | ✅ — grep 확인: `tools/migration_doctor.py`는 `_projects_duplicate_groups`/`_classify_projects_dedup_group`만 직접 호출. 러너 함수 미참조 |
| 호출자 | ✅ — `_run_phase_migrations()` 유일 호출처는 `database.py:583` (`init_db`). 변경 영향 없음 |
| 모듈 import / parse | ✅ — `ast.parse` OK, `import database` OK, 신규 심볼 노출 확인 |
| surgical changes | ✅ — `_run_phase_body` 추출은 pre-preflight·rest 두 루프 간 DRY 목적으로 정당. 인접 코드 무변경. docstring/주석은 거짓 서술 교정 + 신규 동작 설명에 한정 |

### 차단(Blocking) ❌
없음.

### 경고(Warning) ⚠️
- 사소: `_run_phase_body(name: str, body)` 의 `body` 인자에 타입 힌트 없음 — 기존 코드베이스 관례상 callable 인자에 힌트를 안 다는 경우가 흔하고(`PHASES`도 `list`로만 어노테이트), 의미 전달에 지장 없음. 수정 불필요.

### 최종 판정
**통과.** QA 진행 가능. 검증 시나리오는 `backend_changes.md` 하단 4건(safe-only / unsafe(discriminator: 5a 마커 set·#5 마커 미set·RuntimeError) / 충돌 0건 회귀 / 재호출 skip) 참조.
