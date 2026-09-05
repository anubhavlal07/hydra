from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class SingleFlight:
    def __init__(self) -> None:
        self._waiters: dict[Any, asyncio.Future] = {}
        self.shared = 0
        self.led = 0

    @property
    def in_flight(self) -> int:
        return len(self._waiters)

    async def run(self, key: Any, factory: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]:
        existing = self._waiters.get(key)
        if existing is not None:
            self.shared += 1
            return await asyncio.shield(existing), True

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._waiters[key] = future
        self.led += 1

        try:
            result = await factory()
        except BaseException as exc:
            self._waiters.pop(key, None)
            if not future.done():
                future.set_exception(exc)
                future.exception()
            raise

        self._waiters.pop(key, None)
        if not future.done():
            future.set_result(result)
        return result, False

    def snapshot(self) -> dict[str, int]:
        return {"shared": self.shared, "led": self.led, "in_flight": len(self._waiters)}
