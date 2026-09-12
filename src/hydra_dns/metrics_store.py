from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import httpx

from .config import settings
from .resolver.metrics import Metrics
from .resolver.savings import PREFIX as SAVED_PREFIX

log = logging.getLogger(__name__)

FN_FLUSH = "hydra_metrics_flush"
FN_LOAD = "hydra_metrics_load"
FN_PAGE = "hydra_queries_page"
FN_PURGE = "hydra_queries_purge"

MAX_QUERIES_PER_FLUSH = 500


class MetricsStore:
    def __init__(self, client: httpx.AsyncClient, metrics: Metrics) -> None:
        self._client = client
        self._metrics = metrics
        self._task: asyncio.Task | None = None
        self.last_flush_at = 0.0
        self.last_error = ""
        self.flushes = 0
        self._loaded = False

    async def history(
        self,
        limit: int = 100,
        before: int | None = None,
        device: str | None = None,
        search: str | None = None,
        result: str | None = None,
    ) -> list[dict]:
        if not self.configured:
            return []

        rows = await self._rpc(
            FN_PAGE,
            {
                "p_limit": limit,
                "p_before": before,
                "p_device": device,
                "p_search": search,
                "p_result": result,
            },
        )
        return rows if isinstance(rows, list) else []

    async def purge(self, device: str | None = None, before: float | None = None) -> int:
        if not self.configured:
            return 0
        removed = await self._rpc(FN_PURGE, {"p_device": device, "p_before": before})
        return int(removed) if isinstance(removed, (int, float)) else 0

    @property
    def configured(self) -> bool:
        return bool(
            settings.store_url
            and settings.store_key
            and settings.store_secret
            and settings.metrics_persist
        )

    async def _rpc(self, function: str, payload: dict) -> object:
        response = await self._client.post(
            f"{settings.store_url}/rest/v1/rpc/{function}",
            json={"p_secret": settings.store_secret, **payload},
            headers={
                "apikey": settings.store_key,
                "authorization": f"Bearer {settings.store_key}",
                "content-type": "application/json",
            },
            timeout=25.0,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
        if not response.content:
            return None
        return response.json()

    async def load(self) -> None:
        if not self.configured:
            return
        try:
            state = await self._rpc(FN_LOAD, {"p_recent": 600})
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("could not load persisted metrics, starting from zero: %s", exc)
            return

        self._loaded = True
        if isinstance(state, dict):
            restored = self._metrics.restore(state)
            log.info(
                "restored metrics: %s queries, %s device(s), %s timeline point(s)",
                restored.get("queries", 0),
                restored.get("devices", 0),
                restored.get("timeline", 0),
            )

    def start_background(self) -> None:
        if self.configured:
            self._task = asyncio.create_task(self._loop(), name="hydra-metrics")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(settings.metrics_flush_s)
            try:
                await self.flush()
            except Exception:
                log.exception("metrics flush failed")

    async def flush(self) -> bool:
        if not self.configured:
            return False

        await self._recover()
        state = self._metrics.export_state(max_queries=MAX_QUERIES_PER_FLUSH)
        if not self._loaded:
            state = {**state, "totals": {}, "devices": [], "timeline": [], "domains": []}
        totals = state["totals"]
        if not self._metrics.savings.restored:
            totals = {k: v for k, v in totals.items() if not k.startswith(SAVED_PREFIX)}
        try:
            await self._rpc(
                FN_FLUSH,
                {
                    "p_totals": totals,
                    "p_devices": state["devices"],
                    "p_timeline": state["timeline"],
                    "p_domains": state["domains"],
                    "p_queries": state["queries"],
                    "p_keep_days": settings.query_retention_days,
                },
            )
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("metrics flush failed, will retry with the same totals: %s", exc)
            return False

        self._metrics.mark_flushed(len(state["queries"]))
        self.last_error = ""
        self.last_flush_at = time.time()
        self.flushes += 1
        return True

    async def _recover(self) -> None:
        if self._loaded and self._metrics.savings.restored:
            return
        try:
            state = await self._rpc(FN_LOAD, {"p_recent": 0})
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("persisted metrics still unreadable, keeping them unwritten: %s", exc)
            return
        if not isinstance(state, dict):
            state = {}
        if self._loaded:
            self._metrics.savings.merge(state.get("totals") or {})
            return
        self._metrics.absorb(state)
        self._loaded = True
        log.info("persisted metrics readable again; added them to this run's counts")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self.configured:
            with contextlib.suppress(Exception):
                await self.flush()

    def snapshot(self) -> dict:
        return {
            "enabled": self.configured,
            "flushes": self.flushes,
            "last_flush_at": self.last_flush_at or None,
            "last_error": self.last_error,
            "pending_queries": self._metrics.pending_count,
        }
