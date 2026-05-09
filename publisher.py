"""publish 추상화 레이어.

환경 분기:
- WHATUDOIN_SSE_PUBLISH_URL 설정 시: HTTP POST IPC로 SSE service에 전달
- 미설정 시: in-process wu_broker.publish() 호출 (단일 프로세스 fallback)

외부 의존성 없음 — stdlib urllib.request 사용.
M2-17이 토큰 인증(WHATUDOIN_INTERNAL_TOKEN)을 SSE service 측에 강제 적용.
M2-18이 sse_publish_failure 카운터를 expose/metric으로 연결.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque

from broker import wu_broker

logger = logging.getLogger("publisher")

# ── 실패 카운터 + 메타 (thread-safe) ─────────────────────────────────────────
sse_publish_failure: int = 0
_failure_meta: deque = deque(maxlen=50)  # (timestamp, event_name, reason)
_failure_lock = threading.Lock()

_SNAPSHOT_EMPTY = {
    "count": 0,
    "last_event": None,
    "last_reason": None,
    "last_at": None,
}


def get_failure_snapshot() -> dict:
    """{'count': int, 'last_event': str|None, 'last_reason': str|None, 'last_at': float|None}"""
    with _failure_lock:
        count = sse_publish_failure
        if _failure_meta:
            last_at, last_event, last_reason = _failure_meta[-1]
        else:
            last_at = last_event = last_reason = None
    return {
        "count": count,
        "last_event": last_event,
        "last_reason": last_reason,
        "last_at": last_at,
    }


def _record_failure(event: str, reason: str, url: str | None = None) -> None:
    """카운터 +1, deque 추가, 경고 로그. lock 호출자가 아닌 내부에서 lock."""
    global sse_publish_failure
    with _failure_lock:
        sse_publish_failure += 1
        _failure_meta.append((time.time(), event, reason))
    logger.warning(
        "sse_publish_failed",
        extra={"event": event, "reason": reason, "url": url},
    )


def publish(event: str, data: dict) -> None:
    """이벤트 publish 추상화. sync 전용 — async/sync 핸들러 모두에서 호출 가능.

    WHATUDOIN_SSE_PUBLISH_URL 설정 시 IPC 모드, 미설정 시 in-process fallback.
    실패는 silent — sse_publish_failure 카운터 증가만.
    """
    publish_url = os.environ.get("WHATUDOIN_SSE_PUBLISH_URL")
    if publish_url:
        _ipc_publish(publish_url, event, data)
    else:
        try:
            wu_broker.publish(event, data)
        except Exception as exc:
            _record_failure(event, str(exc))


def _ipc_publish(url: str, event: str, data: dict) -> None:
    """HTTP POST IPC: SSE service의 /internal/publish 에 전달."""
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    # M2-17: WHATUDOIN_INTERNAL_TOKEN 설정 시 Authorization 헤더 첨부.
    token = os.environ.get("WHATUDOIN_INTERNAL_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception as exc:
        _record_failure(event, str(exc), url=url)
