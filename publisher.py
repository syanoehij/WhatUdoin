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
import os
import urllib.error
import urllib.request

from broker import wu_broker

# publish 실패 횟수 카운터 (IPC 모드에서 unreachable 시 증가).
# M2-18에서 expose/metric 연결. 본 step에서는 module-level int로만 관리.
sse_publish_failure: int = 0


def publish(event: str, data: dict) -> None:
    """이벤트 publish 추상화. sync 전용 — async/sync 핸들러 모두에서 호출 가능.

    WHATUDOIN_SSE_PUBLISH_URL 설정 시 IPC 모드, 미설정 시 in-process fallback.
    실패는 silent — sse_publish_failure 카운터 증가만.
    """
    publish_url = os.environ.get("WHATUDOIN_SSE_PUBLISH_URL")
    if publish_url:
        _ipc_publish(publish_url, event, data)
    else:
        wu_broker.publish(event, data)


def _ipc_publish(url: str, event: str, data: dict) -> None:
    """HTTP POST IPC: SSE service의 /internal/publish 에 전달."""
    global sse_publish_failure
    payload = json.dumps({"event": event, "data": data}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    # M2-17 자리: WHATUDOIN_INTERNAL_TOKEN 설정 시 Authorization 헤더 첨부.
    # 본 step에서는 토큰 미강제 — SSE service측도 M2-17에서 검증 추가 예정.
    token = os.environ.get("WHATUDOIN_INTERNAL_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=1.0):
            pass
    except Exception:
        sse_publish_failure += 1
