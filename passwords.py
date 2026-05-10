"""비밀번호 hash 유틸 (팀 기능 그룹 A #7).

알고리즘 결정 (backend 판단):
  - `hashlib.pbkdf2_hmac('sha256', ...)` 채택.
  - 표준 라이브러리만 사용 → 의존성 0, PyInstaller spec 변경 0.
  - `hashlib.scrypt`는 OpenSSL 1.1.0+ 필요 (Python 빌드의 OpenSSL 링크에 의존).
    pbkdf2는 OpenSSL 의존성 없이 모든 CPython 빌드에서 사용 가능.
  - bcrypt/argon2/passlib는 신규 의존성 + spec 갱신 비용 발생.

저장 형식:
  ``f"{algo}${cost}${salt_hex}${hash_hex}"`` — 단일 컬럼(`users.password_hash`).
  검증 시 split 후 같은 cost·salt로 재계산하여 hmac.compare_digest로 비교.

상수 변경 시 호환성:
  - 기존 hash는 stored 문자열에 algo·cost·salt가 모두 들어있어 그대로 검증된다.
  - 새 hash만 새 상수가 적용된다 (검증·생성 분리).

타이밍 공격 회피:
  ``DUMMY_HASH`` — 모듈 import 시점에 1회 생성되는 더미. user lookup miss 시
  `verify_password(submitted, DUMMY_HASH)`로 호출 시간을 균등화한다.
"""

import hashlib
import hmac
import os
import secrets

_ALGO = "pbkdf2_sha256"
_COST = 200_000
_SALT_BYTES = 16
_HASH_BYTES = 32  # SHA-256 = 32바이트


def hash_password(plaintext: str) -> str:
    """평문 비밀번호 → stored 문자열.

    호출마다 새 salt를 생성하므로 같은 평문도 매번 다른 결과를 반환한다.
    """
    if plaintext is None:
        plaintext = ""
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, _COST, _HASH_BYTES)
    return f"{_ALGO}${_COST}${salt.hex()}${derived.hex()}"


def verify_password(plaintext: str, stored: str) -> bool:
    """평문이 stored hash와 일치하는지. 항상 같은 시간에 가깝게 동작.

    stored 형식이 잘못된 경우 False (raise 금지 — 라우트가 401을 일관되게 반환할 수 있도록).
    """
    if not stored or not isinstance(stored, str):
        return False
    if plaintext is None:
        plaintext = ""
    parts = stored.split("$")
    if len(parts) != 4:
        return False
    algo, cost_str, salt_hex, hash_hex = parts
    if algo != _ALGO:
        return False
    try:
        cost = int(cost_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, TypeError):
        return False
    if cost <= 0 or len(salt) == 0 or len(expected) == 0:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt, cost, len(expected))
    return hmac.compare_digest(derived, expected)


def is_valid_user_name(s: str) -> bool:
    """이름 정규식 검증: ``^[A-Za-z0-9가-힣]+$``.

    공백·특수문자(밑줄·하이픈·@ 등)는 모두 차단.
    한글 음절(가-힣)·영문·숫자만 허용.
    """
    if not isinstance(s, str) or not s:
        return False
    import re
    return bool(re.match(r"^[A-Za-z0-9가-힣]+$", s))


def is_valid_password_policy(s: str) -> bool:
    """비밀번호 정책: 영문(대소문자 무관) + 숫자 동시 포함.

    길이 최소값은 별도 규정이 없어 1자 이상만 강제 (여기서는 영문/숫자 동시 포함 자체가
    최소 2자를 의미). 추가 정책(특수문자, 길이)은 후속 사이클에서.
    """
    if not isinstance(s, str) or not s:
        return False
    has_alpha = any(c.isascii() and c.isalpha() for c in s)
    has_digit = any(c.isascii() and c.isdigit() for c in s)
    return has_alpha and has_digit


# 모듈 import 시점에 더미 hash 1회 생성.
# `/api/login`에서 user lookup miss 또는 admin 매칭 시 이 더미와 verify_password를
# 수행하여 정상 경로와 응답 시간을 균등화한다 (timing oracle 차단).
DUMMY_HASH = hash_password(secrets.token_hex(16))
