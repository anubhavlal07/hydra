from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field

SLOT_S = 60.0
SLOTS = 60


@dataclass
class _Spend:
    hourly: int
    minute: int
    slots: list[int] = field(default_factory=lambda: [0] * SLOTS)
    denied: int = 0
    breached_at: float = 0.0

    def roll(self, minute: int) -> None:
        if minute <= self.minute:
            return
        if minute - self.minute >= SLOTS:
            self.slots = [0] * SLOTS
        else:
            for step in range(1, minute - self.minute + 1):
                self.slots[(self.minute + step) % SLOTS] = 0
        self.minute = minute

    @property
    def used(self) -> int:
        return sum(self.slots)


class DeviceBudget:
    def __init__(self, hourly: int) -> None:
        self._hourly = max(0, hourly)
        self._spends: dict[str, _Spend] = {}
        self._ceilings: dict[str, int] = {}
        self.denied = 0

    @property
    def enabled(self) -> bool:
        return self._hourly > 0 or bool(self._ceilings) or bool(self._spends)

    @property
    def hourly(self) -> int:
        return self._hourly

    def ceiling(self, device: str, hourly: int | None = None) -> int:
        if hourly is not None and hourly > 0:
            return hourly
        return self._hourly

    def set_ceilings(self, ceilings: Mapping[str, int]) -> None:
        self._ceilings = {str(d): int(c) for d, c in ceilings.items() if int(c) > 0}
        for device in list(self._spends):
            limit = self._ceilings.get(device) or self._hourly
            if limit <= 0:
                del self._spends[device]
                continue
            self._spends[device].hourly = limit

    def allow(self, device: str, hourly: int | None = None) -> bool:
        limit = self.ceiling(device, hourly)
        if limit <= 0:
            self._spends.pop(device, None)
            return True

        minute = int(time.monotonic() // SLOT_S)
        spend = self._spends.get(device)
        if spend is None:
            spend = _Spend(hourly=limit, minute=minute)
            self._spends[device] = spend
        else:
            spend.roll(minute)
            spend.hourly = limit

        if spend.used >= limit:
            spend.denied += 1
            spend.breached_at = time.time()
            self.denied += 1
            return False

        spend.slots[minute % SLOTS] += 1
        return True

    def breaching(self, within_s: float = 300.0) -> list[str]:
        if not self.enabled:
            return []
        cutoff = time.time() - within_s
        return [name for name, spend in self._spends.items() if spend.breached_at >= cutoff]

    def snapshot(self) -> dict:
        if not self.enabled:
            return {"enabled": False, "hourly": 0, "denied": 0, "devices": []}

        minute = int(time.monotonic() // SLOT_S)
        devices = []
        for name, spend in self._spends.items():
            spend.roll(minute)
            used = spend.used
            devices.append(
                {
                    "device": name,
                    "hourly": spend.hourly,
                    "remaining": max(0, spend.hourly - used),
                    "used": used,
                    "denied": spend.denied,
                    "breached_at": spend.breached_at or None,
                }
            )

        for device, limit in self._ceilings.items():
            if device not in self._spends:
                devices.append(
                    {
                        "device": device,
                        "hourly": limit,
                        "remaining": limit,
                        "used": 0,
                        "denied": 0,
                        "breached_at": None,
                    }
                )

        devices.sort(key=lambda d: d["remaining"])
        return {
            "enabled": True,
            "hourly": self._hourly,
            "denied": self.denied,
            "devices": devices,
        }
