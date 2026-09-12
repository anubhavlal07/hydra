from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from dataclasses import dataclass, field

from . import certs
from .config import settings

log = logging.getLogger(__name__)

TICK_S = 1.0


@dataclass
class _Watched:
    label: str
    context: ssl.SSLContext
    cert_file: str
    key_file: str
    stamp: tuple[tuple[float, int], tuple[float, int]] = field(default=((0.0, 0), (0.0, 0)))

    def current(self) -> tuple[tuple[float, int], tuple[float, int]]:
        return (certs.stamp(self.cert_file), certs.stamp(self.key_file))


class CertificateWatcher:
    def __init__(self) -> None:
        self._watched: list[_Watched] = []
        self._task: asyncio.Task | None = None
        self._nudged = False
        self.reloads = 0
        self.failures = 0
        self.last_reload_at = 0.0
        self.last_error = ""

    @property
    def listeners(self) -> list[str]:
        return [w.label for w in self._watched]

    def register(
        self,
        label: str,
        context: ssl.SSLContext,
        cert_file: str = "",
        key_file: str = "",
    ) -> None:
        entry = _Watched(
            label=label,
            context=context,
            cert_file=cert_file or settings.dot_cert_file,
            key_file=key_file or settings.dot_key_file,
        )
        entry.stamp = entry.current()
        self._watched.append(entry)

    def reload(self, force: bool = False) -> list[str]:
        reloaded: list[str] = []
        for entry in self._watched:
            if not entry.cert_file or not entry.key_file:
                continue
            stamp = entry.current()
            if not force and stamp == entry.stamp:
                continue
            try:
                entry.context.load_cert_chain(entry.cert_file, entry.key_file)
            except (OSError, ssl.SSLError) as exc:
                self.failures += 1
                self.last_error = f"{entry.label}: {exc}"
                log.error("could not load the renewed certificate for %s: %s", entry.label, exc)
                continue
            entry.stamp = stamp
            reloaded.append(entry.label)

        if reloaded:
            self.reloads += 1
            self.last_reload_at = time.time()
            self.last_error = ""
            log.info("picked up a renewed certificate on %s without a restart", ", ".join(reloaded))
        return reloaded

    def start_background(self) -> None:
        if self._watched and settings.dot_cert_poll_s > 0 and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hydra-cert-watch")

    def nudge(self) -> None:
        self._nudged = True

    async def _loop(self) -> None:
        waited = 0.0
        while True:
            await asyncio.sleep(TICK_S)
            waited += TICK_S
            if not self._nudged and waited < settings.dot_cert_poll_s:
                continue
            forced, self._nudged, waited = self._nudged, False, 0.0
            try:
                self.reload(force=forced)
            except Exception:
                log.exception("certificate reload check failed")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    def snapshot(self) -> dict:
        return {
            "watching": bool(self._watched),
            "listeners": self.listeners,
            "poll_s": settings.dot_cert_poll_s,
            "reloads": self.reloads,
            "failures": self.failures,
            "last_reload_at": self.last_reload_at or None,
            "last_error": self.last_error,
        }


watcher = CertificateWatcher()
