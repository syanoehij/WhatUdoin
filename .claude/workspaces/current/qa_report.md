# QA — 팀 기능 그룹 A #1 마무리

## 환경 제약
- 실서버 꺼짐 + VSCode 디버깅 모드 → 실서버 E2E(Playwright) 불가.
- 본 사이클은 **소스 코드 변경 0건** (todo.md 토글 + 검증 스크립트 추가만) → 회귀 위험 없음. Playwright 스위트 실행 생략 정당.
- 검증은 합성 임시 DB(`tempfile`, `WHATUDOIN_RUN_DIR` 오버라이드)로 마이그레이션 인프라를 직접 exercise.

## 실행한 검증
`.claude/workspaces/current/scripts/verify_team_a_001_close.py` — `D:\Program Files\Python\Python312\python.exe`로 실행. 로그: 같은 폴더 `.log`.

### case 1 — 빈 DB 첫 init_db()
- `init_db()` 예외 없이 완료 (preflight 충돌 없음) ✅
- 등록된 phase 10개(`team_phase_1_columns_v1` … `team_phase_7_password_hash_v1`) 마커가 `settings`에 모두 기록 ✅
- 마이그레이션 로그에 phase 1~7 + 4b + 5a 전부 "OK", "phase 7: converted 1 plaintext password(s) to hash" (admin 시드) 확인

### case 2 — 재호출(같은 DB로 init_db 재호출) → 모든 phase skip
- `init_db()` 재호출 예외 없이 완료 ✅
- 모든 phase `is_phase_done() == True` ✅
- 재호출이 users/teams row 수를 바꾸지 않음 (users 1→1, teams 0→0) ✅
- `_pending_phases() == []` → 백업·preflight 모두 skip 경로 진입 확인 ✅ (마이그레이션 로그에 "pre-migration backup" 두 번째 없음)

### case 3 — Phase 마커 강제 삭제 + 위험 합성 데이터 → 재실행 시 데이터 무결성
**심은 위험 데이터** (마이그레이션-후 상태 흉내):
- `guard_user_pw`: `password='PLAINTEXT_LEFTOVER'` + `password_hash=<가짜 bcrypt 형태>` (이미 변환된 row)
- `guard event`: `team_id=<GuardTeam id>` + `project_id=<guard project id>` (이미 백필된 row)
- `AdminTeam` 이름 팀 (이미 rename 완료된 상태)

**마커 전부 삭제 → `init_db()` 재호출** 후:
- 마커 전부 삭제됨 / 재호출 예외 없이 완료(preflight 충돌 없음) / 마커 10개 재기록 ✅
- phase 7 가드: `guard_user_pw`의 `password_hash` 불변 — 마이그레이션 로그 "phase 7: converted 0 plaintext password(s) to hash" → `WHERE password_hash IS NULL` 가드가 이 row를 다시 hash()에 안 넘김 ✅
- phase 7 가드: 평문 `password='PLAINTEXT_LEFTOVER'` 잔존 row는 가드(`WHERE password_hash IS NULL`)에 안 걸려 손대지 않음 — 의도된 동작 ✅
- phase 4-data 가드: `guard event`의 `team_id` 1→1 (덮어쓰기 없음) ✅
- phase 6 가드: `guard event`의 `project_id` 1→1 (덮어쓰기 없음) ✅
- phase 3 가드: `AdminTeam` 팀 이름 불변 (관리팀 lookup no-op, `관리팀_legacy_*` 안 생김) ✅
- phase 3 가드: `AdminTeam`/`관리팀` 류 팀 수 1→1 (중복 rename 없음) ✅

## 결과
**15 PASS / 0 FAIL** — exit code 0.

## 백엔드-프론트엔드 경계면
이번 변경에 프론트엔드/API 표면 변경 없음 → 경계면 정합성 점검 불필요.

## 서버 재시작
**불필요** — 소스 코드 무변경.

## 결론
**통과.** 마이그레이션 인프라의 idempotency 가드·preflight·마커 동작이 합성 DB에서 전부 확인됨.
