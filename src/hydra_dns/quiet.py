from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

HOUR_S = 3600
LOOKBACK_HOURS = 24
MAX_DEVICES = 500
SNAPSHOT_VERSION = 1
SAVE_EVERY_S = 300


@dataclass
class Presence:
    last_at: float = 0.0
    hour: int = -1
    hours: set[int] = field(default_factory=set)
    seen_at: float = 0.0

    def note(self, at: float) -> None:
        if at <= self.last_at:
            return
        self.last_at = at
        self.seen_at = at
        hour = int(at // HOUR_S)
        if hour == self.hour:
            return
        self.hour = hour
        self.hours.add(hour)
        floor = hour - LOOKBACK_HOURS
        if min(self.hours) <= floor:
            self.hours = {h for h in self.hours if h > floor}


class QuietWatch:
    def __init__(
        self,
        quiet_s: float,
        established_hours: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.quiet_s = max(1.0, float(quiet_s))
        self.established_hours = max(1, min(LOOKBACK_HOURS, int(established_hours)))
        self.started_at = clock()
        self.settles_at = self.started_at + self.quiet_s
        self.restored = 0
        self.restored_gap_s: float | None = None
        self._seen: dict[str, Presence] = {}

    def note(self, device: str, at: float) -> None:
        presence = self._seen.get(device)
        if presence is None:
            if len(self._seen) >= MAX_DEVICES:
                oldest = min(self._seen, key=lambda name: self._seen[name].last_at)
                del self._seen[oldest]
            presence = self._seen[device] = Presence()
        presence.note(at)

    def established(self, device: str) -> bool:
        presence = self._seen.get(device)
        return presence is not None and len(presence.hours) >= self.established_hours

    def settled(self, now: float) -> bool:
        return now >= self.settles_at

    def quiet(self, devices: Iterable[str], now: float) -> dict[str, float]:
        if not self.settled(now):
            return {}
        found: dict[str, float] = {}
        for device in devices:
            presence = self._seen.get(device)
            if presence is None or len(presence.hours) < self.established_hours:
                continue
            if now - presence.last_at >= self.quiet_s:
                found[device] = presence.seen_at or presence.last_at
        return found

    def forget(self, device: str) -> bool:
        return self._seen.pop(device, None) is not None

    def snapshot(self, now: float) -> dict:
        return {
            "quiet_s": self.quiet_s,
            "established_hours": self.established_hours,
            "tracked": len(self._seen),
            "established": sum(
                1 for p in self._seen.values() if len(p.hours) >= self.established_hours
            ),
            "settled": self.settled(now),
            "settles_at": self.settles_at,
            "restored": self.restored,
            "restored_gap_s": self.restored_gap_s,
        }

    def export(self, now: float) -> dict:
        return {
            "version": SNAPSHOT_VERSION,
            "saved_at": now,
            "devices": {
                name: {
                    "last_at": p.last_at,
                    "seen_at": p.seen_at or p.last_at,
                    "hours": sorted(p.hours),
                }
                for name, p in self._seen.items()
                if p.last_at > 0
            },
        }

    def restore(self, payload: Any, now: float) -> int:
        if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
            raise ValueError("not a presence snapshot this version can read")
        saved_at = _finite(payload.get("saved_at"))
        rows = payload.get("devices")
        if saved_at is None or saved_at <= 0 or not isinstance(rows, dict):
            raise ValueError("the presence snapshot is missing its time or its devices")
        gap = max(0.0, now - saved_at)
        if gap > max_gap_s(self.quiet_s):
            raise ValueError(f"the presence snapshot is {gap / HOUR_S:.1f}h old")

        restored: dict[str, Presence] = {}
        for name, row in rows.items():
            presence = _presence(row, saved_at, gap)
            if isinstance(name, str) and name and presence is not None:
                restored[name] = presence
        kept = sorted(restored.items(), key=lambda item: item[1].last_at, reverse=True)
        self._seen = dict(kept[:MAX_DEVICES])
        self.settles_at = now
        self.restored = len(self._seen)
        self.restored_gap_s = gap
        return self.restored


def max_gap_s(quiet_s: float) -> float:
    return LOOKBACK_HOURS * HOUR_S + quiet_s


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _presence(row: Any, saved_at: float, gap: float) -> Presence | None:
    if not isinstance(row, dict):
        return None
    last_at = _finite(row.get("last_at"))
    hours = row.get("hours")
    if last_at is None or last_at <= 0 or not isinstance(hours, list):
        return None
    last_at = min(last_at, saved_at)
    seen_at = _finite(row.get("seen_at"))
    seen_at = last_at if seen_at is None or seen_at <= 0 else min(seen_at, last_at)
    hour = int(last_at // HOUR_S)
    kept = {
        h
        for h in hours
        if isinstance(h, int) and not isinstance(h, bool) and hour - LOOKBACK_HOURS < h <= hour
    }
    shifted_at = last_at + gap
    shift = int(shifted_at // HOUR_S) - hour
    return Presence(
        last_at=shifted_at,
        hour=hour + shift,
        hours={h + shift for h in kept | {hour}},
        seen_at=seen_at,
    )


class PresenceFile:
    def __init__(self, metrics: Any, path: str, clock: Callable[[], float] = time.time) -> None:
        self._metrics = metrics
        self._path = path
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._soon: asyncio.Task | None = None
        self._again = False
        self.restored = 0
        self.saved = 0
        self.last_save_at = 0.0
        self.last_error = ""
        self.load_error = ""

    @property
    def configured(self) -> bool:
        return bool(self._path) and getattr(self._metrics, "presence", None) is not None

    def load(self) -> int:
        if not self.configured:
            return 0
        try:
            payload = json.loads(Path(self._path).read_text(encoding="utf-8"))
            self.restored = self._metrics.restore_presence(payload, self._clock())
        except FileNotFoundError:
            self.load_error = "no presence file yet"
            log.info("no presence file at %s, the quiet watch starts in grace", self._path)
            return 0
        except Exception as exc:
            self.load_error = str(exc)[:200]
            log.warning(
                "could not restore presence from %s (%s), the quiet watch starts in grace",
                self._path,
                exc,
            )
            return 0
        self.load_error = ""
        log.info("restored presence for %s device(s) from %s", self.restored, self._path)
        return self.restored

    def write(self) -> int:
        payload = self._metrics.export_presence(self._clock())
        if payload is None:
            return 0
        target = Path(self._path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".presence-")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(payload, out, separators=(",", ":"))
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise
        return len(payload["devices"])

    def start_background(self) -> None:
        if self.configured and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hydra-presence-snapshot")
            self._metrics.on_forget = self.save_soon

    def save_soon(self) -> None:
        if self._soon is not None and not self._soon.done():
            self._again = True
            return
        with contextlib.suppress(RuntimeError):
            self._soon = asyncio.get_running_loop().create_task(
                self._flush(), name="hydra-presence-forget"
            )

    async def _flush(self) -> None:
        while True:
            self._again = False
            await self.save()
            if not self._again:
                return

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(SAVE_EVERY_S)
            await self.save()

    async def save(self) -> int:
        if not self.configured:
            return 0
        try:
            written = await asyncio.to_thread(self.write)
        except Exception as exc:
            self.last_error = str(exc)[:200]
            log.warning("could not write the presence file: %s", exc)
            return 0
        self.saved = written
        self.last_save_at = self._clock()
        self.last_error = ""
        return written

    async def aclose(self) -> None:
        for task in (self._task, self._soon):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._task = None
        self._soon = None
        if getattr(self._metrics, "on_forget", None) == self.save_soon:
            self._metrics.on_forget = None
        await self.save()

    def snapshot(self) -> dict:
        return {
            "enabled": self.configured,
            "path": self._path,
            "restored": self.restored,
            "load_error": self.load_error,
            "saved": self.saved,
            "last_save_at": self.last_save_at or None,
            "last_error": self.last_error,
        }


def build(settings: object, clock: Callable[[], float] = time.time) -> QuietWatch | None:
    hours = float(getattr(settings, "quiet_hours", 8.0) or 0)
    if hours <= 0:
        return None
    return QuietWatch(
        quiet_s=hours * HOUR_S,
        established_hours=int(getattr(settings, "quiet_established_hours", 16)),
        clock=clock,
    )


def watched(registry: Any) -> list[Any]:
    if registry is None:
        return []
    held = getattr(registry, "pauses", None)
    return [d for d in registry.devices if not (held is not None and held.active(d.id))]


def quiet_devices(state: Any, now: float | None = None) -> dict[str, float]:
    finder = getattr(getattr(state, "metrics", None), "quiet_since", None)
    registry = getattr(state, "devices", None)
    if finder is None or registry is None:
        return {}
    moment = now if now is not None else time.time()
    return finder([d.id for d in watched(registry)], moment)


def quiet_names(state: Any, now: float | None = None) -> list[str]:
    found = quiet_devices(state, now)
    if not found:
        return []
    return [d.name for d in watched(getattr(state, "devices", None)) if d.id in found]
