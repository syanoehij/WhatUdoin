# Code Review — 팀 기능 그룹 A #7

## 결론

**차단 결함 0건. 통과 (경고 2건).**

## (a) hash 알고리즘·저장 형식

- PBKDF2-HMAC-SHA256, 200,000 iter, salt 16바이트, hash 32바이트.
- 저장 형식: `f"{algo}${cost}${salt_hex}${hash_hex}"` 단일 문자열, `users.password_hash` 단일 컬럼.
- 검증: `hmac.compare_digest`로 timing-safe.
- stored 형식 검증: 4-part split + algo 확인 + bytes.fromhex 가드 + 길이 가드. 잘못된 입력 시 raise 대신 False (라우트 일관성 유지).
- 의존성: 표준 라이브러리만. PyInstaller spec 변경 없음. **OK.**
- 권고(비차단): OWASP 2023 권장은 SHA-256 PBKDF2 600k 또는 argon2id. 현 200k는 인트라넷 환경에서 충분하나, 향후 cost factor 상향 시 stored 문자열에 cost가 들어 있어 호환됨 — 변경 비용 낮음.

## (b) sanity check 정합성

- 본문 시작 시점에 첫 변환 대상 row의 `(id, plaintext)`를 로컬 변수로 캡처.
- 변환 후 같은 id의 `password_hash` SELECT → 캡처한 평문으로 `verify_password` True 검증.
- 실패 시 RuntimeError → 러너가 ROLLBACK (database.py:2118 try/except + conn.execute("ROLLBACK")).
- 평문 변수는 sanity 직후 `None` 재할당으로 참조 해제. log/print 안 함. **OK.**
- 권고(비차단): `sanity_plain` 변수는 함수 종료와 함께 GC 대상 — 명시적 None 재할당은 의미적 안전성. Python 메모리 모델상 동일 평문이 메모리에 잠시 남을 수 있으나 트랜잭션 단위라 실용적 영향 없음.

## (c) preflight 정의

- `_check_users_name_norm_unique`: name_norm IS NOT NULL 안에서 GROUP BY HAVING COUNT > 1.
- `_check_teams_name_norm_unique`: 동일 패턴.
- 컬럼 존재 가드: phase 1 이전엔 컬럼 없어 검사 skip(노옵).
- 충돌 메시지: `users (name_norm='kim') duplicates=2 ids=[10,11]` 형태로 진단 충분.
- `_PREFLIGHT_CHECKS.append` 두 번 — 등록 위치 일관 (`_check_projects_team_name_unique` 직후). **OK.**
- 권고(비차단): teams.name_norm UNIQUE는 `deleted_at IS NULL` 안에서만 충돌이 의미 있다. 현재 deleted_at 무관 전수 검사 — 휴면 팀이 같은 이름이면 거부. 후속 사이클에서 부분 UNIQUE 인덱스 + 보완 가능 (본 사이클 spec L62는 전체 충돌 거부 명시).

## (d) 라우트 검사

### POST /api/login
- `name`/`password` 빈 → 400 ("이름과 비밀번호를 입력하세요.")
- 정규식 위반 → 400 ("이름은 영문·숫자·한글만 사용할 수 있습니다.")
- admin 시도 / 없는 사용자 / 잘못된 비밀번호 → 401 (동일 메시지)
- 정상 member → 200 + 30일 세션 쿠키
- 검증 스크립트(verify_login_routes.py) 7개 케이스 모두 PASS.

### POST /api/me/change-password
- 미인증 → 401 ("로그인이 필요합니다.")
- 빈 입력 → 400
- 현재 비밀번호 틀림 → 401 (`db.verify_user_password`로 hash 검증)
- 새 비밀번호 정책(영문+숫자) 위반 → 400
- 정상 → 200 + hash 저장 (`reset_user_password`)
- 검증 스크립트 6개 케이스 모두 PASS.

### POST /api/admin/login
- 외부 변경 없음. 내부 `get_user_by_credentials`가 hash 검증으로 교체.
- admin 정상 / member 차단 모두 PASS.

### POST /api/admin/users/{user_id}/reset-password
- 라우트 그대로. 내부 `reset_user_password`가 hash 저장으로 자동 적용.
- 정책 미적용 (admin 운영 — backend_changes.md에 사유 명시).
- **경고 1**: admin 리셋도 정책 검증을 강제하면 사용자 보호가 강화되지만 spec 외. 후속 검토 권장.

### 누락 확인
- 기존 호출부 grep 결과: `get_user_by_password()` 호출 0건 (함수도 삭제 완료).
- `reset_user_password()` 호출 2건 (`/api/me/change-password`, `/api/admin/users/{id}/reset-password`) — 둘 다 hash 저장으로 자동 반영.
- `get_user_by_credentials()` 호출 1건 (`/api/admin/login`) — hash 검증으로 자동 반영. **OK.**

## (e) admin 노출 차단

### 메시지 동일성
- `/api/login` 401 케이스 모두 "아이디 또는 비밀번호가 올바르지 않습니다."로 통일.
- 검증 스크립트로 admin/없는 사용자/잘못된 비밀번호 3 케이스 모두 동일 detail 확인.

### Timing 균등화
- `get_user_by_login`: name 빈 / lookup miss / hash mismatch 3가지 경로 모두 `verify_password(submitted, DUMMY_HASH)` 또는 정상 `verify_password(submitted, stored)` 1회 수행.
- `verify_user_password`: stored 없음 시 DUMMY_HASH로 균등화.
- `get_user_by_credentials`: lookup miss 시 DUMMY_HASH 균등화.
- DUMMY_HASH는 모듈 import 시점 1회 생성 (passwords.DUMMY_HASH).
- **경고 2**: 정규식 위반(`is_valid_user_name` False)은 즉시 400 → DB 쿼리·verify 안 거침. 응답 시간 짧음. 그러나 정규식 위반 자체는 형식 오류라 admin 존재 노출과 무관. 수용.

## 추가 점검

### 신규 INSERT 경로 (`approve_pending_user`)
- 평문 password로 users INSERT — register 플로우 #8 책임이라 수정 안 함.
- 다음 phase 7 재실행 시 hash로 변환됨 (phase는 1회만 돌지만 마커 강제 삭제 시).
- **경고 3 → 비차단**: register 승인 직후엔 평문이 잠시 들어옴. spec L26 "본 사이클은 register 플로우 변경 X" 명시 — 후속.

### 기존 `check_register_duplicate` 평문 SELECT
- `WHERE password = ?` 2건 — 비밀번호로 중복 체크. phase 7 후 `users.password = ''` 다수가 되어도 register 라우트가 빈 비밀번호 거부하므로 무해.
- spec L26 #8 책임. **OK.**

### `users.password` 컬럼 NOT NULL 충돌 — 자가 발견 후 패치 완료
- spec "NULL 처리"가 컬럼 제약과 충돌. `password = ''`로 deviation. 의미 동등(가드 `password != ''`).
- backend_changes.md "spec deviation" 항목에 기록. **OK.**

### frontend 변경 (templates/base.html)
- 로그인 모달에 `<input id="login-name">` 추가 + JS `doLogin()`에서 `name` 전송.
- 라우트 호환성을 위해 본 사이클 포함 (spec L12 권고와 다름 — backend_changes.md에 사유 명시).
- 시각/스타일 변경은 QA가 점검.

### 모듈-스코프 DUMMY_HASH 생성 비용
- `hash_password(secrets.token_hex(16))` — 약 200ms 정도 (PBKDF2 200k iter).
- 모듈 import 시점 1회 → 서버 시작 시 200ms 추가. 인트라넷 환경에서 무시 가능.

## 변경 이력 점검 — 의도하지 않은 손상

- `database.py`: import에 `passwords` 추가 / phase 7 본문 + preflight 2건 / DB 함수 4건 변경(get_user_by_password 삭제). **모두 spec 범위 내.**
- `app.py`: import 추가 + 라우트 2건 변경. **spec 범위 내.**
- `templates/base.html`: 로그인 모달 input + JS. **최소 변경.**
- `passwords.py`: 신규.
- 다른 파일 손대지 않음 (기존 호출부 grep 결과 변경 불필요).

## 단위 테스트 커버리지

- `passwords.py` 단위: round-trip / salt 차이 / wrong plaintext / 정규식 양/음 7건 / 정책 양/음 8건. **충분.**
- phase 7: 빈 DB / 합성 구 DB(7명) / idempotent / preflight users / preflight teams. **충분.**
- 라우트: /api/login 7 case / /api/admin/login 2 case / /api/me/change-password 6 case. **충분.**

## 결론

차단 결함 없음. 경고 2건은 모두 spec 범위 외 보강(admin 리셋 정책, register 평문 잠시 잔존). qa 단계 진행 권고.
