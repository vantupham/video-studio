"""Thread-safe fan-out of job events to asyncio subscribers (WebSocket clients)."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, max_queue: int = 1000) -> None:
        self._lock = threading.Lock()
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._max_queue = max_queue

    def subscribe(self) -> asyncio.Queue:
        """Must be called from inside a running event loop."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = {s for s in self._subscribers if s[1] is not queue}

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        """Callable from any thread."""
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(_put_nowait, queue, event)
            except RuntimeError:
                pass  # loop shutting down


def _put_nowait(queue: asyncio.Queue, event: dict[str, Any]) -> None:
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        log.warning("Dropping job event: subscriber queue full")
