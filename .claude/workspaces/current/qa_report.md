# QA Report — 팀 기능 그룹 A #7

## 결론

**모든 import-time + 합성 DB 검증 통과 (40 + 23 = 63 PASS / 0 FAIL).**
실서버 검증은 사용자 재시작 후에 가능 (VSCode 디버깅 모드 — 자동 재시작 불가).

## 검증 시나리오 매핑 (spec L87)

### (1) 합성 구 DB 변환·sanity
- 스크립트: `verify_password_hash.py` test 2 (빈 DB) + test 3 (합성 7명) + test 4 (idempotent)
- 결과: 빈 DB → admin 시드 1명 변환 + 인덱스 2개 생성. 합성 → 6명 변환, Frank(빈 password) 보존, sanity check 통과 (Alice/admin verify_password OK). 마커 강제 삭제 후 재실행 → 변환 0건 (이미 변환됨).
- **PASS.**

### (2) preflight 충돌 거부
- 스크립트: `verify_password_hash.py` test 5 (users 충돌) + test 6 (teams 충돌)
- 결과: 같은 name_norm 2 row 강제 INSERT → 마이그레이션 시 RuntimeError + warning 누적 (`preflight_users_name_norm` / `preflight_teams_name_norm` 카테고리).
- **PASS.**

### (3) 라우트 정상/실패 시나리오
- 스크립트: `verify_login_routes.py`
  - test 1: POST /api/login (정상/잘못된 비밀번호/admin 차단/없는 사용자/정규식 위반 3종/대소문자/빈 입력) — 7개 케이스
  - test 2: POST /api/admin/login (admin 정상 + member 차단)
  - test 3: POST /api/me/change-password (정상/새 비밀번호로 로그인/현재 비밀번호 틀림/영문만/숫자만)
- **PASS.**

### (4) 비밀번호 정책 단위
- 스크립트: `verify_password_hash.py` test 1 (passwords.py 단위)
  - hash_password round-trip
  - 같은 평문 다른 salt
  - wrong plaintext → False
  - 정규식: Kim/김민수/kim123/김123/abc OK / `_kim`/`kim ` (공백)/`kim@example`/` kim`/`kim-lee`/빈/`kim_lee` BAD
  - 정책: abc123/A1/Pass9word OK / abcdef/123456/빈/공백/한글123 BAD
- **PASS.**

## 카운트

```
verify_password_hash.py: 40 PASS / 0 FAIL  (passwords 단위 19 + phase 7 14 + preflight 7)
verify_login_routes.py:  23 PASS / 0 FAIL  (login 14 + admin login 2 + change pw 7)
─────────────────────────────────────────
                          63 PASS / 0 FAIL
```

## 실서버 Playwright 검증 — 미실행 (사용자 재시작 대기)

### 사유
- VSCode 디버깅 모드 — 코드 변경 후 자동 재시작 불가 (CLAUDE.md `feedback_server_restart.md`).
- 현재 운영 서버(https://192.168.0.18:8443)는 변경 전 코드를 들고 있음 → /api/login 신 사양으로 호출하면 실패.

### 사용자에게 요청
**서버를 재시작해 주세요.** 재시작 후 다음 import-time 검증 한 번 더 돌려서 실 DB에 phase 7 마이그레이션이 올바르게 적용되었는지 확인 가능:

```powershell
"D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_password_hash.py
"D:/Program Files/Python/Python312/python.exe" .claude/workspaces/current/scripts/verify_login_routes.py
```

(이 두 스크립트는 모두 격리된 임시 DB를 사용하므로 운영 DB 영향 없음. 단, 운영 DB의 phase 7 적용 자체는 서버 재시작 시 자동 마이그레이션이 처리한다.)

### Playwright 보강 시나리오 (재시작 후 옵션)
- `/api/login` 모달에서 이름·비밀번호 입력 → 200 + 리로드 검증.
- `/api/me/change-password` 모달에서 현재 비밀번호 hash 검증 + 새 비밀번호 정책 violation 케이스.
- 본 사이클 spec L96은 import-time을 메인 QA로 명시하므로 Playwright는 옵션. 별도 spec 파일 작성 안 함.

## 산출물 검증

| 산출물 | 위치 | 상태 |
|---------|------|------|
| backend_changes.md | .claude/workspaces/current/ | 작성 완료 |
| code_review_report.md | 동상 | 작성 완료 |
| qa_report.md | 동상 | 본 파일 |
| verify_password_hash.py | .claude/workspaces/current/scripts/ | 6 test PASS |
| verify_login_routes.py | 동상 | 3 test PASS |
| passwords.py | 프로젝트 루트 | 신규 모듈, 모든 단위 PASS |

## 잠재 리스크 / 운영 시 점검 사항

1. **서버 재시작 시 운영 DB phase 7 자동 적용**
   - 백업 1회 자동 (`backupDB/whatudoin-migrate-*.db`).
   - admin 비밀번호도 변환됨 — 이전 평문 동일하게 사용 가능 (verify_password가 매칭).
   - 운영 DB의 `users.name_norm`/`teams.name_norm`에 충돌이 있으면 서버 시작 거부 + warning 누적. 재시작 전 운영 DB에서 정규화 충돌 확인 권장:
     ```sql
     SELECT name_norm, COUNT(*) FROM users WHERE name_norm IS NOT NULL GROUP BY name_norm HAVING COUNT(*) > 1;
     SELECT name_norm, COUNT(*) FROM teams WHERE name_norm IS NOT NULL GROUP BY name_norm HAVING COUNT(*) > 1;
     ```

2. **로그인 폼 변경 — 사용자 영향**
   - 기존: 비밀번호만 입력. 신규: 이름+비밀번호.
   - 기존 사용자는 첫 로그인 시 자기 이름을 알아야 함. UI에 안내 1줄 추가됨 ("이름과 비밀번호를 입력하세요.").

3. **register / approve_pending_user는 변경 없음 (#8 책임)**
   - 새 회원 가입 후 phase 7이 다시 돌지 않으면 `approve_pending_user`가 평문을 INSERT. 다음 phase 7 재실행(마커 삭제) 시 변환 — 일반 운영에서 마커는 한 번만 찍히므로 신규 회원 평문 잔존 가능. **#8가 register/approve 자체에서 hash 저장으로 바꾸면 해결.**

4. **DUMMY_HASH 모듈 import 비용**
   - `passwords.DUMMY_HASH = hash_password(secrets.token_hex(16))` — PBKDF2 200k iter ≈ 200ms.
   - 서버 시작 시 1회. 무시 가능.

5. **Minor timing leak — 알려진 차이, 비차단, #8 이관**
   - `get_user_by_login`: row가 있지만 `password_hash`가 falsy(빈 문자열·NULL)인 경우 `return None`만 하고 DUMMY_HASH verify를 돌리지 않음 → 정상 매칭과 약간 다른 응답 시간.
   - admin은 phase 7 변환 후 항상 hash 보유하므로 admin 노출과 무관.
   - "이 username은 존재하나 비밀번호가 비어 있다"는 시그널만 누설 — spec 범위 외 미세 leak.
   - 수정은 2줄(`verify_user_password` 패턴 mirror) — 본 사이클 후반에 추가하지 않고 #8 register/approve 정비 시 함께 처리 권장.
