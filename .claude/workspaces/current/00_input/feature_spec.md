# 팀 기능 그룹 A — #7 진행 사양 (메인 → 플래너 인계)

## 요청

`팀 기능 구현 todo.md` 그룹 A의 **#7. 로그인 인증 기반 정비**를 한 사이클로 진행. 마스터 plan은 `팀 기능 구현 계획.md` §6.

#8 이후는 본 사이클 범위 밖.

## 분류

백엔드 핵심: **비밀번호 hash 변환 + 일반 로그인을 이름+비밀번호로 전환 + name_norm UNIQUE 인덱스 + 비밀번호 정책 검증**.
프론트 변경 없음 (로그인 입력은 이미 이름·비밀번호 입력 폼이 있는지 backend가 grep하여 확인. 폼 변경 필요 시 #8과 함께 처리).
**팀 모드: backend → reviewer → qa.**

## 전제 (#1~#6 완료)

- PHASES 인프라 + Phase 1·2·3·4·5·6 본문 (#1~#6).
- `users.password_hash TEXT` 컬럼만 추가됨 (#2). 본 사이클이 변환 phase 본문 추가.
- `auth.py`: `is_member`, `user_can_access_team`, `resolve_work_team` 등 헬퍼 (#2).
- `database.py:165, 367` settings 테이블 정의 중복 (인지만, 본 사이클 X).

## 핵심 인계 사실 (메인이 이미 파악)

### 현재 인증 흐름
- `app.py:1383` `POST /api/login` — 비밀번호 단독, 30일 세션. `db.get_user_by_password(password)` 호출.
- `app.py:1404` `POST /api/me/change-password` — `db.get_user_by_password(current_pw)`로 본인 검증, `db.reset_user_password(user_id, new_pw)`로 평문 저장.
- `app.py:1452` `POST /api/admin/login` — 이름+비밀번호, 5분 세션. `db.get_user_by_credentials(name, password)`. 본 사이클: 함수 내부만 hash 검증으로 교체.
- `app.py:1434` `POST /api/register` — `db.check_register_duplicate` + `db.create_pending_user`. 본 사이클은 **register 플로우 변경 X (#8 책임)**, 단 정규식 헬퍼는 본 사이클에서 추가하고 #8에서 사용.

### DB 함수 (database.py)
- `get_user_by_password(password)` L3875 — 평문 매칭 SELECT. 본 사이클이 제거(또는 deprecated 표시 후 호출부 모두 교체).
- `get_user_by_credentials(name, password)` L3885 — admin 전용 평문 매칭. 본 사이클: name_norm 매칭 + hash 검증으로 교체.
- `reset_user_password(user_id, new_pw)` — 평문 저장. 본 사이클: hash 저장으로 교체.

### crypto.py 현황
- 기존 `crypto.py`는 Fernet 대칭 암호화 유틸 (credentials.json/`WHATUDOIN_CRYPTO_KEY` 기반). **비밀번호 hash와 무관.**
- `cryptography` 패키지가 이미 의존성에 있음 → 비밀번호 hash에 `cryptography.hazmat.primitives.kdf.scrypt` 등 사용 가능. 또는 표준 `hashlib.scrypt`/`hashlib.pbkdf2_hmac`. 또는 `passlib` 신규 의존성. **선택은 backend 판단** (사유 backend_changes.md에 기록).

### 권장 hash 알고리즘 결정 가이드 (backend가 채택)
- **권장: 표준 `hashlib.scrypt`** (cost-factor 가능, 의존성 0, PyInstaller spec 변경 0). 또는 `hashlib.pbkdf2_hmac('sha256', ...)`.
- 저장 형식: `f"{algo}${cost}${salt_hex}${hash_hex}"` 단일 컬럼 (`users.password_hash`). 검증 시 split 후 같은 cost·salt로 재계산하여 비교.
- bcrypt/argon2를 채택하려면 `passlib` 또는 직접 `bcrypt` 패키지 추가 필요 → PyInstaller spec 갱신 필요.

## #7 step 분해 (플래너 참고)

| step | 제목 | exit criteria 핵심 |
|------|------|--------------------|
| #7-S1 | hash 유틸 추가 + crypto/auth 모듈 정비 | `hash_password(plaintext) -> str`, `verify_password(plaintext, stored) -> bool` 신규(Crypto 또는 신규 `passwords.py` 모듈, backend 결정). 알고리즘·cost·저장 형식 결정 사유 backend_changes.md 기록. |
| #7-S2 | phase 본문(`team_phase_7_password_hash_v1`) | `users.password_hash IS NULL AND password IS NOT NULL AND password != ''` 사용자에 대해 `password_hash = hash_password(password)`, **같은 트랜잭션에서 `password = NULL` 처리**. admin 포함. 빈 password row는 변환 안 함(가드). 변환 후 sanity check: 무작위 1건 row의 `verify_password(원래 평문 — 본문 시작 시 임시 캡처, hash)` 결과 True가 아니면 ROLLBACK + RuntimeError. |
| #7-S3 | Phase 4 인덱스 + preflight | preflight `_check_users_name_norm_unique`, `_check_teams_name_norm_unique` 등록 — 충돌 시 서버 시작 거부 + warning 누적. 인덱스: `users.name_norm` 전역 UNIQUE(`is_active` 무관), `teams.name_norm` UNIQUE. |
| #7-S4 | DB 함수 + 라우트 변경 | `get_user_by_login(name_or_norm, plaintext) -> Optional[user]` 신규 (admin 제외). `get_user_by_credentials(name, plaintext)`는 admin 전용 + name_norm 매칭 + hash 검증으로 내부 변경. `reset_user_password(user_id, new_pw)` hash 저장으로 변경. POST /api/login 라우트: 이름·비밀번호 받음 + name 정규식 검증(`^[A-Za-z0-9가-힣]+$`) + `get_user_by_login` 호출 + admin이면 401. POST /api/me/change-password: 현재 비밀번호 hash 검증 + 새 비밀번호 정책(영문+숫자 동시 포함) 검증 + hash 저장. POST /api/admin/login: 내부 hash 검증으로 교체(외부 동작 동일). |
| #7-S5 | 정규식 헬퍼 + 기존 호출부 정리 | `is_valid_user_name(s) -> bool` (정규식 `^[A-Za-z0-9가-힣]+$`) 헬퍼 추가. **register 플로우 변경은 #8**이라 `is_valid_user_name`/비밀번호 정책 헬퍼는 신규 추가만 — 본 사이클 라우트(`/api/login`/`/api/me/change-password`)에서만 사용. `get_user_by_password(password)` 단독 사용은 모두 제거. 호출부가 있다면 함께 정리. |

## exit criteria (사이클 전체)

### 마이그레이션 동작
- [ ] 빈 DB → phase 본문 노옵 (변환할 row 0건).
- [ ] 합성 구 DB(평문 password 5명, admin 1명, 빈 password 1명) → 5+1=6명 변환, 빈 password 1명은 그대로. 변환 후 `password` 모두 NULL, `password_hash` 모두 채움.
- [ ] sanity check 통과 (변환된 hash로 원래 평문 검증 OK).
- [ ] 두 번째 init_db() → 마커로 phase 미실행. 마커 강제 삭제 후 재실행 → `password_hash IS NULL AND password IS NOT NULL` 가드로 노옵 (이미 변환된 row 안전).
- [ ] preflight `users.name_norm` 충돌 케이스(중복 강제) → 서버 시작 거부 + warning `preflight_users_name_norm` 누적.
- [ ] preflight `teams.name_norm` 충돌 케이스 → 동일.
- [ ] 충돌 0건 → 정상 통과 + 인덱스 생성.

### 라우트
- [ ] `POST /api/login` 이름+비밀번호 흐름:
  - 정상 사용자(member) → 200 + 세션.
  - admin 이름으로 시도 → 401 + 동일 메시지(존재 노출 금지).
  - 잘못된 비밀번호 → 401.
  - 정규식 위반 이름(밑줄·공백·특수문자) → 400.
  - 대소문자 다른 이름(예: `Kim`/`kim`) → 동일 계정으로 인식.
- [ ] `POST /api/admin/login` 그대로 동작 (admin이 hash로 변환된 후에도 정상).
- [ ] `POST /api/me/change-password`:
  - 현재 비밀번호 정확 → 새 비밀번호 정책 통과 시 hash 저장.
  - 새 비밀번호 영문만 또는 숫자만 → 400 + 정책 메시지.

### 단위 테스트
- [ ] `hash_password` + `verify_password` round-trip 정상.
- [ ] 같은 평문이라도 매번 다른 salt → 다른 stored 문자열.
- [ ] 잘못된 평문 → `verify_password` False.
- [ ] 정규식 헬퍼: `Kim`, `김민수`, `kim123`, `김123` 통과 / `_kim`, `kim ` (공백), `kim@example` 차단.

## 진행 방식

- backend 1~2회 호출(S1+S2가 한 흐름, S3+S4+S5가 한 흐름). step별 섹션으로 분리.
- reviewer는 (a) hash 알고리즘·저장 형식 검토, (b) sanity check 정합성, (c) preflight 정의, (d) 라우트 검사 누락, (e) admin 노출 차단(동일 메시지) 검토.
- qa는 (1) 합성 구 DB 변환·sanity, (2) preflight 충돌 거부, (3) 라우트 정상/실패 시나리오, (4) 비밀번호 정책 단위.
- 메인에는 한 줄 요약만 반환.

## 주의사항

- **본 사이클은 #7 범위만.** 계정 가입 분리·팀 신청은 #8. IP 자동 로그인 토글은 #9. 가시성 라우트 적용은 #10.
- **password 컬럼 drop은 Phase 5(별도 릴리스)** — 본 사이클은 NULL 처리만.
- **admin 존재 여부 노출 금지**: 일반 `/api/login`이 admin 이름·비밀번호로 시도해도 일반 사용자 401과 동일 메시지("아이디 또는 비밀번호가 올바르지 않습니다.")로 응답. 응답 시간도 차이 최소화 (가능하면 더미 hash 비교).
- **변환 phase 트랜잭션 안전**: `password` NULL 처리는 hash 검증·sanity check 통과 후. 실패 시 ROLLBACK으로 원본 평문 보존(보안 공백 회피).
- **VSCode 디버깅 모드** — qa는 import-time + 합성 DB. 실서버 재시작 필요 시 사용자에게 요청.
- 의존성 추가 시 PyInstaller spec(`WhatUdoin.spec`) 업데이트 필요 — 표준 라이브러리만 쓰면 회피 가능.

## 산출물 위치

- `backend_changes.md`, `code_review_report.md`, `qa_report.md`: `.claude/workspaces/current/` 직속
- 검증 스크립트: `.claude/workspaces/current/scripts/verify_password_hash.py`, `verify_login_routes.py`
