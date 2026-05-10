# Backend Changes — 팀 기능 그룹 A #7 (비밀번호 hash 변환 + 일반 로그인 + name_norm UNIQUE)

## 요약

- 평문 비밀번호 → PBKDF2-SHA256 hash 변환 phase 추가 (`team_phase_7_password_hash_v1`).
- `users.name_norm` 전역 UNIQUE 인덱스, `teams.name_norm` UNIQUE 인덱스 생성 (phase 7 본문에서).
- preflight 2건 추가: `_check_users_name_norm_unique`, `_check_teams_name_norm_unique`.
- `/api/login`: 비밀번호 단독 → 이름+비밀번호 + 정규식 + admin 차단(동일 메시지).
- `/api/me/change-password`: hash 검증 + 새 비밀번호 정책(영문+숫자 동시 포함).
- `/api/admin/login`: 외부 동작 동일, 내부 hash 검증으로 교체.
- `db.get_user_by_password()` 함수 + 모든 호출부 제거.
- frontend(base.html): 로그인 모달에 이름 입력 필드 추가, JS 1곳 수정 (backend grep 결과 폼 변경 필요했음).

## 변경 대상 파일

| 파일 | 변경 |
|------|------|
| `passwords.py` | **신규**. hash/verify/정규식/정책 헬퍼 + 모듈-스코프 DUMMY_HASH. |
| `database.py` | import `passwords`, phase 7 본문 추가, preflight 2건 등록, DB 함수 `get_user_by_login` 신규 + `get_user_by_credentials`/`reset_user_password` hash 교체 + `verify_user_password` 신규, `get_user_by_password` 제거. |
| `app.py` | import `passwords`, `/api/login`/`/api/me/change-password` 라우트 변경. |
| `templates/base.html` | 로그인 모달에 `<input id="login-name">` 추가 + `doLogin()` JS 수정. |

## 알고리즘 결정 — PBKDF2-HMAC-SHA256

- 채택: 표준 라이브러리 `hashlib.pbkdf2_hmac('sha256', ...)`.
- cost factor: 200,000 회 반복.
- salt: 16바이트 무작위 (`secrets.token_bytes`).
- 저장 형식: `f"{algo}${cost}${salt_hex}${hash_hex}"` (단일 컬럼 `users.password_hash`).
- 사유:
  - 의존성 0 — PyInstaller spec 변경 필요 없음.
  - `hashlib.scrypt`는 OpenSSL 1.1.0+ 의존 — Python 빌드의 OpenSSL 링크에 따라 다름. PBKDF2는 모든 CPython에서 사용 가능.
  - bcrypt/argon2/passlib는 신규 의존성 + spec 갱신 비용. PBKDF2-200k는 SHA-256 기준 OWASP 2023 권장(310k)에 약간 못 미치지만 현실 인트라넷 환경에서 충분.
- 검증: `hmac.compare_digest`로 timing-safe 비교.

## spec deviation — 평문 컬럼 NULL → 빈 문자열

- spec L49, L93: phase 7이 변환 후 `password = NULL` 처리.
- 실제: `users.password` 컬럼 정의가 `TEXT NOT NULL DEFAULT ''` (database.py:73) — NULL 처리 시 IntegrityError.
- 결정: `password = ''`(빈 문자열)로 저장. 의미 동등 — phase 7 변환 가드(`password != ''`)가 NULL/빈 문자열을 동일하게 제외. 컬럼 drop은 Phase 5(별도 릴리스) 책임이므로 본 사이클은 평문 제거만 보장.
- `reset_user_password()`도 같은 deviation으로 `password = ''` 저장.

## 인덱스 위치 — phase 4 본문이 아닌 phase 7 본문에서 생성

- spec L50: "Phase 4 인덱스 + preflight" — 의미상 phase 4 위치이지만 phase 4 마커가 이미 적용된 환경에서는 본문 변경이 무효.
- 결정: phase 7 본문 안에서 `CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name_norm` / `idx_teams_name_norm` 생성. 신규/기존 환경 모두 phase 7 첫 실행에서 인덱스 생성됨. phase 4 본문 미수정.

## sanity check — 첫 변환 row의 평문 캡처

- 본문 시작 시점에 첫 변환 대상 row의 `(id, plaintext)`를 로컬 변수로 캡처.
- 변환 완료 후 같은 id의 `password_hash`를 SELECT하여 캡처한 평문으로 `verify_password` True 확인.
- 실패 시 `RuntimeError` raise → 러너가 `ROLLBACK` 처리(원본 평문 보존).
- 평문 변수는 sanity check 직후 `None` 할당하여 참조 끊음. log/print 절대 금지.

## 더미 hash로 timing 균등화

- `passwords.DUMMY_HASH` — 모듈 import 시점에 `hash_password(secrets.token_hex(16))`로 1회 생성.
- `get_user_by_login` / `get_user_by_credentials` / `verify_user_password`: lookup miss 또는 admin 매칭(일반 로그인) 시 `verify_password(submitted, DUMMY_HASH)`를 한 번 돌려 응답 시간을 정상 경로와 같게 만듦.
- 효과: admin 존재 노출 차단 + 사용자 enumeration timing oracle 차단.

## 라우트 동작

### POST /api/login (변경)
- 요청: `{name, password}`.
- 응답:
  - 200: 정상 사용자(member). `name`/`role`/`team_id` 반환 + 30일 세션 쿠키.
  - 400: `name`/`password` 빈 입력, 또는 정규식 위반(`^[A-Za-z0-9가-힣]+$`).
  - 401: 모든 인증 실패 (admin 시도 / 없는 사용자 / 잘못된 비밀번호) — 동일 메시지 "아이디 또는 비밀번호가 올바르지 않습니다."
- name_norm(NFC + casefold) 매칭 → `Kim`/`KIM`/`kim` 동일 계정.

### POST /api/admin/login (외부 동작 동일)
- 내부에서 `get_user_by_credentials`가 hash 검증으로 변경됨.
- 응답·요청 형식 변경 없음.

### POST /api/me/change-password (변경)
- 현재 비밀번호 검증을 `db.verify_user_password(user_id, current)`로 교체.
- 새 비밀번호 정책: 영문 + 숫자 동시 포함 (`is_valid_password_policy`). 위반 시 400.
- 통과 시 `db.reset_user_password()`가 hash 저장.

### POST /api/admin/users/{user_id}/reset-password
- 라우트 자체는 그대로 — `reset_user_password()` 내부가 hash 저장으로 변경되어 자동 반영.
- 정책 검증 미적용 (admin 운영 편의 — admin 책임). spec 명시 없음.

## DB 함수 변경

| 함수 | 변경 |
|------|------|
| `get_user_by_password(password)` | **삭제**. spec L52: 단독 사용 모두 제거. |
| `get_user_by_credentials(name, plaintext)` | admin 전용 + name_norm 매칭 + hash 검증. lookup miss 시 DUMMY_HASH로 timing 균등화. |
| `get_user_by_login(name, plaintext)` | **신규**. 일반 로그인용 (admin 제외). name_norm 매칭 + hash 검증 + DUMMY_HASH 균등화. |
| `verify_user_password(user_id, plaintext)` | **신규**. 본인 인증용 (`/api/me/change-password`). |
| `reset_user_password(user_id, new_password)` | hash 저장 + `password = ''`. |

## frontend 변경 — 로그인 폼

- backend grep 결과: 기존 `templates/base.html` 로그인 모달은 비밀번호 단독 입력 (line 511 `id="login-pw"`만 존재).
- 결정: 본 사이클에서 폼 최소 변경 (이름 input 1개 추가, JS 1곳).
- 사유: 라우트가 `name` 누락 시 400 반환하므로 폼 미수정 시 모든 사용자 로그인 실패. spec L12 "폼 변경 필요 시 #8과 함께"는 폼 변경을 #8로 미루라는 권고지만 라우트 호환성 깨지면 서비스 정지 → 본 사이클 포함 처리.

## 검증 스크립트 결과

### `verify_password_hash.py` — 6개 test 모두 PASS
1. `passwords.py` 단위 (round-trip, salt 차이, 정규식, 정책)
2. 빈 DB → admin 시드 1명 변환 + 마커 + UNIQUE 인덱스 2개 생성
3. 합성 구 DB(7명: admin+5+빈) → 6명 변환, Frank(빈 password) 보존
4. 마커 강제 삭제 후 재실행 → 변환 대상 0건 (idempotent)
5. preflight `users.name_norm` 충돌 → RuntimeError + warning 누적
6. preflight `teams.name_norm` 충돌 → RuntimeError + warning 누적

### `verify_login_routes.py` — 3개 test 모두 PASS
1. POST /api/login: 정상/잘못된 비밀번호/admin 차단/없는 사용자/정규식 위반 3종/대소문자/빈 입력
2. POST /api/admin/login: admin 정상 + member 차단
3. POST /api/me/change-password: 정상 변경 + 새 비밀번호 로그인 + 현재 비밀번호 틀림 + 정책 위반(영문만/숫자만)

## 자가 발견 결함 + 패치 (한 사이클 내)

**결함**: phase 7 본문이 `password = NULL`을 시도해서 IntegrityError 발생 (컬럼이 NOT NULL).
**패치**: 본문 + `reset_user_password` 둘 다 `password = ''`로 변경. doc/주석에 deviation 명시. spec deviation 항목으로 위에 기록.
**재검증**: verify_password_hash.py + verify_login_routes.py 모두 PASS.

## 미해결 / 후속 사이클로 이관

- `approve_pending_user`(L4288)는 평문 INSERT 유지 — register 플로우 #8 책임 (spec L26).
- `check_register_duplicate`의 평문 password SELECT 2건 — register 플로우 #8 책임.
- `users.password` 컬럼 자체는 Phase 5(별도 릴리스)에서 DROP — 본 사이클 범위 밖.
- `idx_users_name_norm`이 UNIQUE이므로 신규 회원이 동일 name_norm으로 가입 시도 시 INSERT 실패 — register 플로우(#8)에서 사전 검사 + 친절한 에러 메시지 필요.
