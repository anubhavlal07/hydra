from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import time
from collections.abc import Callable

from . import rebind, schedule, uncloak
from .config import settings

log = logging.getLogger(__name__)

TICK_S = 900
NAMES_PER_DEVICE = 10
PUSH_MAX_BYTES = 3000
REPORT_URL = "/#tools/digest"
ADGUARD = "adguard"
REBIND_GUARD = rebind.LABEL


def next_at(day: int, hour: int, after: float) -> float:
    zone = schedule.zone()
    moment = dt.datetime.fromtimestamp(after, zone)
    target = moment.replace(hour=hour, minute=0, second=0, microsecond=0)
    target += dt.timedelta(days=(day - target.weekday()) % 7)
    if target.timestamp() <= after:
        target += dt.timedelta(days=7)
    return target.timestamp()


def _rate(part: float, whole: float) -> int:
    return round(part / whole * 100) if whole else 0


def _compact(value: float) -> str:
    if value < 1000:
        return str(int(value))
    if value < 1_000_000:
        trimmed = round(value / 1000, 1)
        return f"{int(trimmed) if trimmed.is_integer() else trimmed}K"
    trimmed = round(value / 1_000_000, 1)
    return f"{int(trimmed) if trimmed.is_integer() else trimmed}M"


def _size(payload: dict) -> int:
    return len(json.dumps(payload).encode())


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def fit(payload: dict, limit: int = PUSH_MAX_BYTES) -> dict:
    fitted = {**payload, "reasons": [_clip(line, 200) for line in payload["reasons"]]}
    fitted["title"] = _clip(fitted["title"], 120)
    fitted["body"] = _clip(fitted["body"], 400)
    while fitted["reasons"] and _size(fitted) > limit:
        fitted["reasons"] = fitted["reasons"][:-1]
    return fitted


class Digest:
    def __init__(
        self,
        metrics: object,
        push: object | None = None,
        quota: Callable[[], dict] | None = None,
        resolver: object | None = None,
        devices: object | None = None,
    ) -> None:
        self._metrics = metrics
        self._push = push
        self._quota = quota
        self._resolver = resolver
        self._devices = devices
        self._task: asyncio.Task | None = None
        self._since = time.time()
        self._mark = self._counters()
        self._device_mark = self._device_counters()
        self._due_at = next_at(settings.digest_day, settings.digest_hour, time.time())
        self.sent = 0
        self.failures = 0
        self.last_sent_at = 0.0
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return settings.digest and bool(getattr(self._push, "configured", False))

    def _counters(self) -> dict[str, int]:
        summary = self._metrics.summary()
        watcher = getattr(self._metrics, "first_seen", None)
        return {
            "queries": int(summary.get("queries") or 0),
            "cached": int(summary.get("cached") or 0),
            "blocked": int(summary.get("blocked") or 0),
            "first_seen": int(getattr(watcher, "counted", 0) or 0),
            "suspicious": int(getattr(watcher, "suspicious", 0) or 0),
        }

    def _device_counters(self) -> dict[str, tuple[int, int]]:
        totals = getattr(self._metrics, "device_totals", None)
        if totals is None:
            return {}
        return {
            device: (int(row.get("queries") or 0), int(row.get("blocked") or 0))
            for device, row in totals().items()
        }

    def _display(self, device: str) -> str:
        lookup = getattr(self._devices, "get", None)
        known = lookup(device) if lookup is not None else None
        return str(getattr(known, "name", "") or device)

    def _layer(
        self, name: str, device: str, rebound: frozenset[str], cloaked: dict[str, str]
    ) -> dict:
        decide = getattr(self._resolver, "decide", None)
        try:
            decision = decide(name, "A", device) if decide is not None else None
        except Exception as exc:
            log.debug("could not decide %s for %s in the digest: %s", name, device, exc)
            decision = None
        if name in rebound and (decision is None or not decision.blocked):
            return {"layer": REBIND_GUARD, "label": rebind.LABEL, "blocked_now": True}
        if name in cloaked and (decision is None or not decision.blocked):
            return {"layer": uncloak.LAYER, "label": cloaked[name], "blocked_now": True}
        if decision is None:
            return {"layer": ADGUARD, "label": "AdGuard", "blocked_now": True}
        return {
            "layer": decision.layer,
            "label": decision.label,
            "blocked_now": bool(decision.blocked),
        }

    def _first_seen(self, device: str) -> tuple[int, list[dict]]:
        since = getattr(self._metrics, "first_seen_since", None)
        if since is None:
            return 0, []
        count, rows = since(device, self._since, NAMES_PER_DEVICE * 2)
        shown = [
            {"name": row["name"], "at": round(row["at"], 3)}
            for row in rows
            if not settings.hidden_from_logs(row["name"])
        ]
        return count, shown[:NAMES_PER_DEVICE]

    def _blocked_names(self, device: str) -> list[dict]:
        top = getattr(self._metrics, "device_blocked", None)
        if top is None:
            return []
        rows = [
            (name, count)
            for name, count in top(device, NAMES_PER_DEVICE * 2)
            if not settings.hidden_from_logs(name)
        ]
        guarded = getattr(self._metrics, "device_rebound", None)
        rebound = guarded(device) if guarded is not None else frozenset()
        uncloaked = getattr(self._metrics, "device_cloaked", None)
        cloaked = uncloaked(device) if uncloaked is not None else {}
        return [
            {"name": name, "count": count, **self._layer(name, device, rebound, cloaked)}
            for name, count in rows[:NAMES_PER_DEVICE]
        ]

    def report(self, now: float | None = None) -> dict:
        moment = now if now is not None else time.time()
        ceiling = settings.digest_devices
        active = []
        for device, (queries, blocked) in self._device_counters().items():
            before_queries, before_blocked = self._device_mark.get(device, (0, 0))
            asked = queries - before_queries if queries >= before_queries else queries
            refused = blocked - before_blocked if blocked >= before_blocked else blocked
            if asked > 0:
                active.append((asked, refused, device))
        active.sort(key=lambda row: (-row[0], row[2]))

        rows = []
        for asked, refused, device in active[:ceiling]:
            count, names = self._first_seen(device)
            rows.append(
                {
                    "device": device,
                    "name": self._display(device),
                    "queries": asked,
                    "blocked": refused,
                    "first_seen": {"count": count, "names": names},
                    "blocked_names": self._blocked_names(device),
                }
            )

        return {
            "since": self._since,
            "until": moment,
            "span": schedule.describe_span(max(0.0, moment - self._since)),
            "limit": ceiling,
            "active": len(active),
            "watching": getattr(self._metrics, "first_seen", None) is not None,
            "devices": rows,
        }

    def _busiest(self) -> str:
        rows = self._metrics.devices() if hasattr(self._metrics, "devices") else []
        ranked = sorted(rows, key=lambda r: int(r.get("queries") or 0), reverse=True)
        return str(ranked[0].get("device") or "") if ranked else ""

    def compose(self, now: float | None = None) -> dict:
        moment = now if now is not None else time.time()
        counters = self._counters()
        queries = counters["queries"] - self._mark["queries"]
        cached = counters["cached"] - self._mark["cached"]
        blocked = counters["blocked"] - self._mark["blocked"]
        span = max(0.0, moment - self._since)

        lines = [
            f"{_compact(queries)} queries over {schedule.describe_span(span)}",
            f"{_rate(blocked, queries)}% blocked, {_rate(cached, queries)}% from cache",
        ]

        busiest = self._busiest()
        if busiest:
            lines.append(f"busiest device: {busiest}")

        watcher = getattr(self._metrics, "first_seen", None)
        if watcher is not None:
            fresh = watcher.counted - self._mark.get("first_seen", 0)
            if fresh > 0:
                lines.append(f"{_compact(fresh)} domains seen for the first time")
            odd = watcher.suspicious - self._mark.get("suspicious", 0)
            if odd > 0:
                lines.append(f"{_compact(odd)} of them look machine-generated")

        rollup = self._quota() if self._quota else None
        if rollup:
            left = rollup.get("requests_remaining")
            days = rollup.get("headroom_days")
            if left is not None:
                lines.append(f"{_compact(left)} of pooled allowance left")
            if days is not None:
                lines.append(f"{round(days)} days of headroom at this rate")

        newest = sorted(
            (row for row in self.report(moment)["devices"] if row["first_seen"]["count"]),
            key=lambda row: -row["first_seen"]["count"],
        )[:3]
        if newest:
            named = ", ".join(f"{row['name']} {row['first_seen']['count']}" for row in newest)
            lines.append(f"new names by device: {named}")

        return fit(
            {
                "title": f"{settings.service_name}, the week so far",
                "body": lines[0] + " · " + lines[1],
                "tag": "hydra-digest",
                "url": REPORT_URL,
                "reasons": lines,
            }
        )

    def due(self, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) >= self._due_at

    async def send(self, now: float | None = None) -> dict:
        moment = now if now is not None else time.time()
        payload = self.compose(moment)

        result = {"sent": 0}
        if getattr(self._push, "configured", False):
            result = await self._push.send(payload, urgency="normal")

        delivered = int(result.get("sent") or 0)
        if delivered:
            self.sent += 1
            self.last_sent_at = moment
            self.last_error = ""
        else:
            self.failures += 1
            self.last_error = str(result.get("reason") or "nothing was subscribed")

        self._since = moment
        self._mark = self._counters()
        self._device_mark = self._device_counters()
        forget = getattr(self._metrics, "forget_device_blocked", None)
        if forget is not None:
            forget()
        self._due_at = next_at(settings.digest_day, settings.digest_hour, moment)
        return {"delivered": delivered, "payload": payload}

    def start_background(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hydra-digest")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                if self.configured and self.due():
                    await self.send()
            except Exception:
                log.exception("weekly digest failed")

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
            "enabled": settings.digest,
            "day": schedule.DAYS[settings.digest_day],
            "hour": settings.digest_hour,
            "due_at": self._due_at,
            "covering_since": self._since,
            "sent": self.sent,
            "failures": self.failures,
            "last_sent_at": self.last_sent_at or None,
            "last_error": self.last_error,
        }
