from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import httpx

from . import webpush
from .config import settings

log = logging.getLogger(__name__)

FN_LIST = "hydra_push_list"
FN_ADD = "hydra_push_add"
FN_REMOVE = "hydra_push_remove"
FN_MARK = "hydra_push_mark"

GONE = (404, 410)
MAX_FAILURES = 5
MAX_SUBSCRIPTIONS = 20


class PushError(RuntimeError):
    pass


class PushSender:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._subscriptions: list[dict] = []
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_sync_at = 0.0
        self.last_error = ""
        self.sent = 0
        self.failures = 0
        self.pruned = 0

    @property
    def stored(self) -> bool:
        return bool(settings.store_url and settings.store_key and settings.store_secret)

    @property
    def configured(self) -> bool:
        return bool(settings.vapid_public_key and settings.vapid_private_key and self.stored)

    @property
    def subscriptions(self) -> list[dict]:
        return list(self._subscriptions)

    async def _rpc(self, function: str, payload: dict) -> object:
        try:
            response = await self._client.post(
                f"{settings.store_url}/rest/v1/rpc/{function}",
                json={"p_secret": settings.store_secret, **payload},
                headers={
                    "apikey": settings.store_key,
                    "authorization": f"Bearer {settings.store_key}",
                    "content-type": "application/json",
                },
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise PushError(f"could not reach push storage: {exc}") from exc

        if response.status_code >= 400:
            raise PushError(f"push storage returned HTTP {response.status_code}")
        if not response.content:
            return None
        return response.json()

    async def sync(self) -> list[dict]:
        if not self.stored:
            return []
        try:
            rows = await self._rpc(FN_LIST, {})
        except PushError as exc:
            self.last_error = str(exc)
            log.warning("push subscription sync failed, keeping what is in hand: %s", exc)
            return self.subscriptions

        if isinstance(rows, list):
            self._subscriptions = [r for r in rows if isinstance(r, dict) and r.get("endpoint")]
            self.last_error = ""
            self.last_sync_at = time.time()
        return self.subscriptions

    async def add(self, endpoint: str, p256dh: str, auth: str, label: str = "") -> None:
        if not endpoint.startswith("https://"):
            raise PushError("a push endpoint must be https")
        if len(self._subscriptions) >= MAX_SUBSCRIPTIONS and not any(
            s["endpoint"] == endpoint for s in self._subscriptions
        ):
            raise PushError(f"there are already {MAX_SUBSCRIPTIONS} devices subscribed")

        async with self._lock:
            await self._rpc(
                FN_ADD,
                {
                    "p_endpoint": endpoint,
                    "p_p256dh": p256dh,
                    "p_auth": auth,
                    "p_label": label[:60],
                },
            )
            await self.sync()

    async def remove(self, endpoint: str) -> bool:
        async with self._lock:
            removed = await self._rpc(FN_REMOVE, {"p_endpoint": endpoint})
            await self.sync()
        return bool(removed)

    async def _mark(self, endpoint: str, ok: bool) -> None:
        with contextlib.suppress(Exception):
            await self._rpc(FN_MARK, {"p_endpoint": endpoint, "p_ok": ok})

    async def _deliver(self, subscription: dict, payload: dict, urgency: str) -> bool:
        target = webpush.Subscription(
            endpoint=subscription["endpoint"],
            p256dh=subscription["p256dh"],
            auth=subscription["auth"],
        )
        try:
            url, body, headers = webpush.request_for(
                target,
                payload,
                settings.vapid_subject,
                settings.vapid_public_key,
                settings.vapid_private_key,
                urgency=urgency,
            )
            response = await self._client.post(
                url, content=body, headers=headers, timeout=settings.alert_timeout_s
            )
        except Exception as exc:
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("push to %s failed: %s", subscription["endpoint"][:60], exc)
            return False

        if response.status_code in GONE:
            log.info("push endpoint is gone, dropping it: %s", subscription["endpoint"][:60])
            self.pruned += 1
            await self.remove(subscription["endpoint"])
            return False

        if response.status_code >= 400:
            self.failures += 1
            self.last_error = f"HTTP {response.status_code}: {response.text[:120]}"
            log.warning("push service returned %s", response.status_code)
            await self._mark(subscription["endpoint"], False)
            return False

        self.sent += 1
        self.last_error = ""
        await self._mark(subscription["endpoint"], True)
        return True

    async def send(self, payload: dict, urgency: str = "normal") -> dict:
        if not self.configured:
            return {"sent": 0, "failed": 0, "reason": "push is not configured"}

        targets = [s for s in self._subscriptions if int(s.get("failures") or 0) < MAX_FAILURES]
        results = [await self._deliver(s, payload, urgency) for s in targets]
        if results:
            await self.sync()
        return {"sent": sum(results), "failed": len(results) - sum(results)}

    def start_background(self) -> None:
        if self.stored:
            self._task = asyncio.create_task(self._loop(), name="hydra-push")

    async def _loop(self) -> None:
        await self.sync()
        while True:
            await asyncio.sleep(max(60, settings.store_refresh_s))
            try:
                await self.sync()
            except Exception:
                log.exception("push subscription refresh failed")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    def snapshot(self) -> dict:
        return {
            "configured": self.configured,
            "stored": self.stored,
            "devices": len(self._subscriptions),
            "sent": self.sent,
            "failures": self.failures,
            "pruned": self.pruned,
            "last_sync_at": self.last_sync_at or None,
            "last_error": self.last_error,
            "max_devices": MAX_SUBSCRIPTIONS,
        }
