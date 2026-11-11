"""Шина событий в памяти процесса: подписчики SSE получают события задач вживую.

Интерфейс publish/subscribe намеренно минимальный — при переходе на несколько процессов
его реализация меняется на Redis Pub/Sub без правок у вызывающих.
"""
import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def publish(self, channel: str, event: dict) -> None:
        for q in list(self._subs.get(channel, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:                # медленный подписчик не тормозит остальных
                pass

    @asynccontextmanager
    async def subscribe(self, channel: str) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[channel].add(q)
        try:
            yield q
        finally:
            self._subs[channel].discard(q)
            if not self._subs[channel]:
                self._subs.pop(channel, None)


bus = EventBus()
