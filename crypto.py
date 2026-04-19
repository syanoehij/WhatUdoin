"""
Fernet 대칭 암호화 유틸.

키 생성:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

credentials.json 설정 (프로젝트 루트):
  {"crypto_key": "<위 출력값>"}

또는 환경변수 fallback:
  WHATUDOIN_CRYPTO_KEY=<위 출력값>
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet, InvalidToken

# PyInstaller 번들에서는 __file__이 _MEIPASS 임시 폴더를 가리키므로 exe 옆 디렉토리를 사용
_CREDS_PATH = (
    Path(sys.executable).parent / "credentials.json"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent / "credentials.json"
)
_fernet: Optional[Fernet] = None


def _load_key() -> bytes:
    raw = ""
    if _CREDS_PATH.exists():
        try:
            with open(_CREDS_PATH, encoding="utf-8") as f:
                raw = json.load(f).get("crypto_key", "")
        except Exception as e:
            print(f"[WhatUdoin] credentials.json 읽기 실패: {e}", file=sys.stderr)
    if not raw:
        raw = os.environ.get("WHATUDOIN_CRYPTO_KEY", "")
    if not raw:
        raise RuntimeError(
            "crypto_key가 설정되지 않았습니다.\n"
            "credentials.json에 crypto_key를 추가하거나 WHATUDOIN_CRYPTO_KEY 환경변수를 설정하세요.\n"
            "키 생성: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    try:
        key = raw.strip().encode()
        Fernet(key)  # 형식 검증
        return key
    except Exception:
        raise RuntimeError(
            "crypto_key 형식이 올바르지 않습니다. "
            "Fernet.generate_key()로 생성한 base64url 32바이트 키여야 합니다."
        )


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """평문 → Fernet 토큰 (base64url str)"""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Fernet 토큰 → 평문. 유효하지 않으면 ValueError 발생."""
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError("복호화 실패: 유효하지 않은 토큰")
