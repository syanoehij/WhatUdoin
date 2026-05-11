# 코드 리뷰 — 팀 기능 그룹 A #1 마무리

## 변경 범위
- **소스 코드(`*.py`, `*.html`, `*.js`) 변경 0건.** `database.py`는 감사 목적으로 읽기만 함.
- `팀 기능 구현 todo.md`: #1 섹션 sub-task 토글 + preflight 항목에 "의도적 미적용" 주석 추가 + L683 그룹 A 완료 마킹 + 단위 사이클 기록 1줄.
- 신규 파일 `.claude/workspaces/current/scripts/verify_team_a_001_close.py` (+ `.log`): 워크스페이스 내부 검증 스크립트 (배포 대상 아님, `tests/` 컨벤션 밖 — 일회성 검증이라 의도적).

## 검토 결과

### 1. 가드 감사의 정확성
backend_changes.md 표의 14개 항목을 `database.py` 원문과 대조 — 모두 정확. 특히:
- `_phase_7_password_hash` (L2186): `WHERE password_hash IS NULL AND password IS NOT NULL AND password != ''` — 1차 가드는 `password_hash IS NULL`, 2·3차 가드(`password IS NOT NULL`, `!= ''`)가 빈/NULL password를 추가 보호. 1회 실행 후 `password_hash` 비NULL + `password=''`이라 재실행 시 SELECT 0건. ✅
- `_phase_3_admin_separation` (L1193): `SELECT id FROM teams WHERE name='관리팀' LIMIT 1` → 없으면 `return`. rename 후엔 이름이 `AdminTeam`/`관리팀_legacy_{id}`이라 재진입 시 lookup 0건 → no-op. AdminTeam 사전 존재 충돌은 fallback 이름 + warning으로 처리(IntegrityError 회피). ✅
- `_phase_6_backfill_table_project_id` (L1996): SELECT·UPDATE 모두 `WHERE project_id IS NULL`. 재실행 시 SELECT 0건 → `_phase_6_lookup_or_create_project` 호출 자체가 없음 → 자동 프로젝트 중복 생성 불가. ✅

### 2. preflight 미적용 판단의 타당성
- `user_teams`: 테이블이 `_phase_1_team_columns`에서 빈 상태로 생성, `_phase_2_team_backfill`의 INSERT가 `... WHERE NOT EXISTS (SELECT 1 FROM user_teams ut WHERE ut.user_id=u.id AND ut.team_id=u.team_id)`. Phase 1 이전엔 테이블이 없으므로 사전 중복도 불가능. → preflight는 발생 불가능한 조건을 검사하는 것 → over-engineering. 만일을 위한 안전망은 `CREATE UNIQUE INDEX IF NOT EXISTS`의 IntegrityError → phase 러너 `ROLLBACK` + `RuntimeError` (서버 시작 거부). ✅ 판단 타당.
- `team_menu_settings`: 동일 — Phase 1에서 빈 테이블, 시드는 #19. #19 전까지 비어 있으므로 중복 불가. todo.md에 "#19가 시드 채울 때 preflight 추가 책임" 주석 추가됨. ✅

### 3. preflight 일관성 (충돌 시 거부 패턴)
`_run_phase_migrations` (L2421): 4개 preflight 함수 모두 `list[(category, message)]` 반환, `category`는 `preflight_*` 네임스페이스. 충돌 1건+ → 각각 `_append_team_migration_warning` + stdout 로그 → commit 후 `RuntimeError(f"migration preflight failed with {n} conflict(s); see settings.team_migration_warnings")`. 일관됨. ✅

### 4. 검증 스크립트 품질
- `WHATUDOIN_RUN_DIR` env를 import 전에 설정해 `DB_PATH`를 임시 디렉토리로 격리 — 실서버/실 DB 오염 없음. 끝나면 `shutil.rmtree`. ✅
- 루트에 임시 파일 안 남김 (OS temp dir 사용, CLAUDE.md 정책 준수). ✅
- case 3에서 "마이그레이션-후 상태의 위험 데이터"를 명시적으로 심어서 가드를 실제로 exercise함 — case 2와 관찰 가능하게 다름. ✅
- exit code로 PASS/FAIL 신호 + `.log` 디스크 보존. ✅
- 사소: 가짜 bcrypt 형태 문자열(`$2b$12$ABC...`)을 `password_hash`에 직접 INSERT — 실제 해시 검증을 트리거하지 않으므로(가드가 그 row를 건너뛰니까) 무해. 의도된 단순화.

## 차단 결함
없음.

## 경고 / 후속
1. `user_teams` 실제 컬럼명(`role`/`status`)과 todo.md §#2 명세(`team_role`/`join_status`)가 불일치. #2 사이클에서 단순화된 것으로 보임. 본 #1 범위 밖 — backend_changes.md에 기록만. (후속: todo.md §#2 명세를 코드에 맞춰 정정하거나, 코드 식별자를 명세에 맞추거나 — 별 todo로.)
2. 검증 스크립트가 `tests/phaseN_*.spec.js` 컨벤션이 아닌 워크스페이스 내부 파이썬 — 마이그레이션 인프라 검증은 Playwright보다 직접 DB 조작이 적합하므로 의도적. (이전 #1~#9 사이클들도 `scripts/verify_*.py` 패턴 사용 — 일관됨.)

## 결론
**통과** (경고 2건, 모두 후속/기록 성격).
