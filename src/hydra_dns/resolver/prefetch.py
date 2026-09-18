from __future__ import annotations

import asyncio
import contextlib
import logging

import dns.message
import dns.rdataclass
import dns.rdatatype

from .. import rebind
from ..config import settings
from .cache import AnswerCache
from .metrics import Metrics
from .service import _looks_blocked
from .upstream import UpstreamPool

log = logging.getLogger(__name__)


class Prefetcher:
    def __init__(
        self,
        cache: AnswerCache,
        pool: UpstreamPool,
        metrics: Metrics,
        blocklists: object | None = None,
        guard: rebind.RebindGuard | None = None,
        apps: object | None = None,
    ) -> None:
        self._cache = cache
        self._pool = pool
        self._metrics = metrics
        self._blocklists = blocklists
        self._apps = apps
        self._guard = guard if guard is not None else rebind.build(settings)
        self._task: asyncio.Task | None = None
        self.renewed = 0
        self.failed = 0
        self.warmed = 0
        self.skipped_listed = 0
        self.skipped_rebind = 0
        self.last_run_at: float = 0.0

    def start(self) -> None:
        if settings.prefetch and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hydra-prefetch")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(settings.prefetch_every_s)
            try:
                await self.run()
            except Exception:
                log.exception("prefetch pass failed")

    async def run(self) -> int:
        if not self._pool.state().get("ready"):
            return 0

        due = self._cache.due(settings.prefetch_window_s, max(1, settings.prefetch_min_hits))
        renewed = 0

        for key, _ in due:
            if renewed >= settings.prefetch_max_per_run:
                break
            name = key[0].rstrip(".").lower()
            if self._listed_everywhere(name) or self._refused(name, key):
                continue
            if await self._renew(key):
                renewed += 1

        self.last_run_at = asyncio.get_running_loop().time()
        if renewed:
            log.info("prefetch renewed %s of %s expiring answer(s)", renewed, len(due))
        return renewed

    async def warm(self) -> int:
        wanted = settings.cache_warm_count
        if wanted <= 0 or len(self._cache) >= wanted:
            return 0
        if not self._pool.state().get("ready"):
            return 0

        warmed = 0
        for row in self._metrics.top_domains(limit=wanted * 2):
            if warmed >= wanted:
                break
            name = str(row["domain"]).rstrip(".").lower()
            if not name or name == "." or self._listed_everywhere(name):
                continue
            key = (
                f"{name}.",
                int(dns.rdatatype.A),
                int(dns.rdataclass.IN),
                False,
            )
            if self._cache.has(key):
                continue
            if await self._renew(key):
                warmed += 1

        self.warmed = warmed
        if warmed:
            log.info("warmed %s answer(s) from the persisted domain counts", warmed)
        return warmed

    def _listed_everywhere(self, name: str) -> bool:
        covered = False
        for source in (self._apps, self._blocklists):
            if source is None:
                continue
            try:
                covered = bool(source.everywhere(name))
            except Exception as exc:
                log.debug("blocklist check failed while prefetching %s: %s", name, exc)
                continue
            if covered:
                break
        if covered:
            self.skipped_listed += 1
        return covered

    def _refused(self, name: str, key) -> bool:
        try:
            wire = self._cache.wire_of(key)
            if not wire:
                return False
            response = dns.message.from_wire(wire)
        except Exception as exc:
            log.debug("could not read the cached answer for %s: %s", name, exc)
            return False
        if _looks_blocked(response) or not self._guard.refuses(name, response):
            return False
        self.skipped_rebind += 1
        return True

    async def _renew(self, key) -> bool:
        name, rdtype, rdclass, want_dnssec = key
        try:
            query = dns.message.make_query(
                name,
                dns.rdatatype.RdataType(rdtype),
                dns.rdataclass.RdataClass(rdclass),
                want_dnssec=want_dnssec,
            )
            query.id = 0
            answer_wire, upstream, _ = await self._pool.forward(query.to_wire())
            response = dns.message.from_wire(answer_wire)
        except Exception as exc:
            self.failed += 1
            log.debug("prefetch could not renew %s: %s", name, exc)
            return False

        if upstream.is_fallback:
            return False
        self._metrics.savings.note_prefetch()

        blocked = _looks_blocked(response)
        if self._cache.put(key, response, answer_wire, blocked) is None:
            return False

        self.renewed += 1
        return True

    def snapshot(self) -> dict:
        return {
            "enabled": settings.prefetch,
            "renewed": self.renewed,
            "warmed": self.warmed,
            "failed": self.failed,
            "skipped_listed": self.skipped_listed,
            "skipped_rebind": self.skipped_rebind,
            "min_hits": settings.prefetch_min_hits,
            "window_s": settings.prefetch_window_s,
        }
