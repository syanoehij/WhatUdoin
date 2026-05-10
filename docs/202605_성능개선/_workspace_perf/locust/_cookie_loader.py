"""
M1a-5 보조 모듈: session_cookies.json 로드 + VU->user 할당

역할:
  - M1a-2 seed_users.py가 생성한 session_cookies.json을 로드
  - 각 VU에 round-robin으로 user 할당 (thread-safe)
  - SingleTabUser / MultiTabUser 양쪽에서 공유 — 전체 50슬롯 단일 카운터

VU 할당 정책:
  - 50 VU 한계(session_cookies.json 내 계정 수) 초과 시: abort
    같은 session_id를 두 VU가 공유하면 서버에서 동일 사용자로 인식해
    이벤트 CRUD가 interleave되어 측정 결과 오염 위험이 있으므로
    wrap-around 대신 abort를 선택한다.
  - 사용자 수가 50명 미만이면 그 수까지만 VU 할당, 초과 시 abort.

사용법 (locustfile.py on_start에서):
  session_id, username = assign_vu_cookie()
  client.cookies.set("session_id", session_id, domain="localhost", path="/")
"""

import json
import threading
from pathlib import Path

# session_cookies.json 위치: _workspace/perf/fixtures/session_cookies.json
# parents[0]=_workspace/perf/locust, parents[1]=_workspace/perf
_COOKIES_PATH = Path(__file__).parents[1] / "fixtures" / "session_cookies.json"

_lock = threading.Lock()
_cookies: dict | None = None       # {username: {"session_id": ..., "expires_at": ...}}
_usernames: list[str] = []
_vu_counter = 0                     # 현재까지 할당된 VU 수 (단일 카운터, 두 user class 공유)


def _load_cookies() -> None:
    """처음 한 번만 JSON 로드. 재진입 안전."""
    global _cookies, _usernames
    if _cookies is not None:
        return
    if not _COOKIES_PATH.exists():
        raise FileNotFoundError(
            f"session_cookies.json 없음: {_COOKIES_PATH}\n"
            "  M1a-2 seed_users.py를 먼저 실행하세요."
        )
    with open(_COOKIES_PATH, encoding="utf-8") as f:
        _cookies = json.load(f)
    _usernames = sorted(_cookies.keys())


def assign_vu_cookie() -> tuple[str, str]:
    """
    thread-safe round-robin VU 할당.

    Returns:
        (session_id, username) 튜플

    Raises:
        RuntimeError: 전체 50슬롯 초과 시 (abort 정책)
    """
    global _vu_counter
    with _lock:
        _load_cookies()
        max_vu = len(_usernames)
        if _vu_counter >= max_vu:
            # 50명 한계 초과 — wrap-around 대신 abort
            # wrap-around를 원하면 아래 주석 해제 후 abort 블록 제거:
            #   idx = _vu_counter % max_vu
            raise RuntimeError(
                f"VU 수({_vu_counter + 1})가 session_cookies.json 계정 수({max_vu})를 초과합니다.\n"
                "  현재 정책: abort (같은 session_id 중복 방지).\n"
                "  wrap-around를 허용하려면 _cookie_loader.py의 주석 지시를 따르세요."
            )
        idx = _vu_counter
        _vu_counter += 1

    username = _usernames[idx]
    session_id = _cookies[username]["session_id"]
    return session_id, username


def get_total_accounts() -> int:
    """로드된 계정 수 반환 (locustfile 자가 점검용)."""
    with _lock:
        _load_cookies()
        return len(_usernames)
