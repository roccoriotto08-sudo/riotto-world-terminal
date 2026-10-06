"""Tiny in-process pub/sub feeding the SSE stream. Thread-safe publish: the ingest
pipeline runs in a worker thread, so delivery is marshalled onto the event loop."""
import asyncio
import json
from typing import Optional, Set


class Bus:
    def __init__(self) -> None:
        self.queues: Set[asyncio.Queue] = set()
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self.queues.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self.queues.discard(q)

    def _deliver(self, msg: str) -> None:
        for q in list(self.queues):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client: drop; it re-syncs from REST on reconnect

    def publish(self, kind: str, data) -> None:
        if self.loop is None:
            return
        msg = f"event: {kind}\ndata: {json.dumps(data, default=str)}\n\n"
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._deliver(msg)
        else:
            self.loop.call_soon_threadsafe(self._deliver, msg)


BUS = Bus()
