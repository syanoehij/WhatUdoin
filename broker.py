import asyncio
from typing import Optional


class SSEBroker:
    """스레드 안전한 in-memory SSE broker.

    - `publish()`는 async/sync(스레드풀) 어디서든 호출 가능.
      내부에서 `call_soon_threadsafe`로 이벤트 루프 스레드에 안전하게 전달한다.
    - `QueueFull`은 조용히 무시 — 느린 클라이언트 1명이 publisher를 블록하지 않도록.
    - 단일 uvicorn 워커 + PyInstaller 단일 프로세스 환경 전제(싱글톤 `wu_broker`).
      멀티워커 전환 시 Redis pubsub 등 외부 브로커로 교체 필요.
    """

    def __init__(self):
        self._subs: set = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start_on_loop(self, loop: asyncio.AbstractEventLoop):
        """lifespan에서 1회 호출해 이벤트 루프 참조 저장."""
        self._loop = loop

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subs.add(q)
        return q

    def unsubscribe(self, q):
        self._subs.discard(q)

    def publish(self, event: str, data: dict):
        """이벤트 브로드캐스트.

        async 컨텍스트, sync(스레드풀) 컨텍스트 모두에서 호출 가능.
        """
        if not self._loop or not self._subs:
            return
        msg = (event, data)

        def _put():
            for q in list(self._subs):
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    # 느린 클라이언트 보호: 해당 구독자만 유실
                    pass

        self._loop.call_soon_threadsafe(_put)


wu_broker = SSEBroker()
