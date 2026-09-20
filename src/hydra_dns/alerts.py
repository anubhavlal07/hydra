from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

import httpx

from .config import settings

log = logging.getLogger(__name__)

MAX_TRACKED = 50


class AlertDispatcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        reasons: Callable[[], list[str]],
        push: object | None = None,
        once: Callable[[str], bool] | None = None,
    ) -> None:
        self._client = client
        self._reasons = reasons
        self._push = push
        self._once = once or (lambda _reason: False)
        self._task: asyncio.Task | None = None
        self._active: dict[str, float] = {}
        self._sent_at: dict[str, float] = {}
        self.sent = 0
        self.failures = 0
        self.last_sent_at = 0.0
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(settings.alert_webhook) or self._push_ready

    @property
    def _push_ready(self) -> bool:
        return bool(getattr(self._push, "configured", False))

    async def _notify(self, kind: str, firing: list[str]) -> None:
        if not self._push_ready:
            return
        headline = firing[0] if firing else "something wants attention"
        state = "recovered" if kind == "resolved" else "needs attention"
        await self._push.send(
            {
                "title": f"{settings.service_name} {state}",
                "body": headline if len(firing) <= 1 else f"{headline} (+{len(firing) - 1} more)",
                "tag": f"hydra-{kind}",
                "reasons": firing,
            },
            urgency="high" if kind == "attention" else "normal",
        )

    def _due(self, reason: str, now: float) -> bool:
        last = self._sent_at.get(reason, self._active.get(reason))
        if last is None:
            return True
        return now - last >= max(60, settings.alert_repeat_s)

    def evaluate(self, reasons: list[str], now: float | None = None) -> dict[str, list[str]]:
        moment = now if now is not None else time.time()
        current = set(reasons[:MAX_TRACKED])
        previous = set(self._active)

        opened = sorted(current - previous)
        resolved = sorted(previous - current)
        repeated = sorted(
            r for r in current & previous if not self._once(r) and self._due(r, moment)
        )

        for reason in opened:
            self._active[reason] = moment
        for reason in resolved:
            self._active.pop(reason, None)
            self._sent_at.pop(reason, None)

        return {"opened": opened, "resolved": resolved, "repeated": repeated}

    def _payload(self, kind: str, reasons: list[str], firing: list[str]) -> dict:
        return {
            "service": settings.service_name,
            "instance": settings.instance_id,
            "event": kind,
            "at": time.time(),
            "reasons": firing,
            "active": sorted(self._active),
            "attention": reasons,
        }

    async def _post(self, payload: dict) -> bool:
        try:
            response = await self._client.post(
                settings.alert_webhook,
                json=payload,
                timeout=settings.alert_timeout_s,
                headers={"content-type": "application/json"},
            )
        except httpx.HTTPError as exc:
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            log.warning("alert webhook unreachable: %s", exc)
            return False

        self.sent += 1
        if response.status_code >= 400:
            self.failures += 1
            self.last_error = f"HTTP {response.status_code}: {response.text[:120]}"
            log.warning("alert webhook returned %s", response.status_code)
            return False

        self.last_error = ""
        self.last_sent_at = time.time()
        return True

    async def dispatch(self) -> dict:
        reasons = self._reasons()
        changes = self.evaluate(reasons)
        if not self.configured:
            return changes

        now = time.time()
        firing = changes["opened"] + changes["repeated"]
        if firing:
            delivered = True
            if settings.alert_webhook:
                delivered = await self._post(self._payload("attention", reasons, firing))
            await self._notify("attention", firing)
            if delivered:
                for reason in firing:
                    self._sent_at[reason] = now
        if changes["resolved"]:
            if settings.alert_webhook:
                await self._post(self._payload("resolved", reasons, changes["resolved"]))
            await self._notify("resolved", changes["resolved"])

        return changes

    async def test(self) -> dict:
        if not settings.alert_webhook:
            return {"sent": False, "reason": "no webhook configured"}
        payload = self._payload("test", self._reasons(), ["this is a test alert from Hydra DNS"])
        delivered = await self._post(payload)
        return {"sent": delivered, "error": self.last_error or None}

    def start_background(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hydra-alerts")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(max(60, settings.alert_every_s))
            try:
                await self.dispatch()
            except Exception:
                log.exception("alert dispatch failed")

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
            "webhook": bool(settings.alert_webhook),
            "push": self._push_ready,
            "active": len(self._active),
            "reasons": sorted(self._active),
            "sent": self.sent,
            "failures": self.failures,
            "last_sent_at": self.last_sent_at or None,
            "last_error": self.last_error,
            "every_s": max(60, settings.alert_every_s),
            "repeat_s": max(60, settings.alert_repeat_s),
        }
