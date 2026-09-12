from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from ..config import settings
from .cache import AnswerCache

log = logging.getLogger(__name__)


class CachePersistence:
    def __init__(self, cache: AnswerCache) -> None:
        self._cache = cache
        self._task: asyncio.Task | None = None
        self.restored = 0
        self.saved = 0
        self.last_save_at = 0.0
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(settings.cache_file)

    def load(self) -> int:
        if not self.configured:
            return 0
        self.restored = self._cache.load(settings.cache_file)
        return self.restored

    def start_background(self) -> None:
        if self.configured and settings.cache_save_every_s > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hydra-cache-snapshot")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(settings.cache_save_every_s)
            await self.save()

    async def save(self) -> int:
        if not self.configured:
            return 0
        try:
            written = await asyncio.to_thread(
                self._cache.save, settings.cache_file, settings.cache_save_max
            )
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("could not write the cache snapshot: %s", exc)
            return 0

        self.saved = written
        self.last_save_at = time.time()
        self.last_error = ""
        return written

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        await self.save()

    def snapshot(self) -> dict:
        return {
            "enabled": self.configured,
            "path": settings.cache_file,
            "restored": self.restored,
            "saved": self.saved,
            "last_save_at": self.last_save_at or None,
            "last_error": self.last_error,
        }
