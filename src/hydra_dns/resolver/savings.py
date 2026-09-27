from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from threading import Lock
from typing import Any

APP = "app"
BLOCKLIST = "blocklist"
RULE = "rule"
CACHE = "cache"
COALESCED = "coalesced"
CAUSES = (APP, BLOCKLIST, RULE, CACHE, COALESCED)

PREFIX = "saved_"
MONTH_KEY = "saved_month"
PREFETCH_KEY = "saved_prefetch_cost"


def month_of(at: float) -> tuple[int, float, float]:
    moment = dt.datetime.fromtimestamp(at, dt.UTC)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        following = start.replace(year=start.year + 1, month=1)
    else:
        following = start.replace(month=start.month + 1)
    return start.year * 100 + start.month, start.timestamp(), following.timestamp()


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


class QuotaSavings:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = Lock()
        self._month, self._since, self._until = month_of(clock())
        self._counts = dict.fromkeys(CAUSES, 0)
        self._prefetch = 0
        self.restored = False

    def _roll(self, now: float) -> None:
        if now < self._until:
            return
        month, since, until = month_of(now)
        self._month, self._since, self._until = month, since, until
        self._counts = dict.fromkeys(CAUSES, 0)
        self._prefetch = 0

    def note(self, cause: str) -> None:
        if cause not in self._counts:
            return
        now = self._clock()
        with self._lock:
            self._roll(now)
            self._counts[cause] += 1

    def note_prefetch(self) -> None:
        now = self._clock()
        with self._lock:
            self._roll(now)
            self._prefetch += 1

    def export(self) -> dict[str, int]:
        with self._lock:
            self._roll(self._clock())
            out = {MONTH_KEY: self._month, PREFETCH_KEY: self._prefetch}
            out.update({f"{PREFIX}{cause}": n for cause, n in self._counts.items()})
        return out

    def merge(self, totals: dict) -> bool:
        with self._lock:
            if self.restored:
                return False
            self._roll(self._clock())
            self.restored = True
            if _count(totals.get(MONTH_KEY)) != self._month:
                return False
            for cause in CAUSES:
                self._counts[cause] += _count(totals.get(f"{PREFIX}{cause}"))
            self._prefetch += _count(totals.get(PREFETCH_KEY))
        return True

    def by_cause(self) -> dict[str, int]:
        with self._lock:
            self._roll(self._clock())
            return dict(self._counts)

    def snapshot(self, allowance: int = 0, partial: bool = False) -> dict:
        with self._lock:
            self._roll(self._clock())
            counts = dict(self._counts)
            prefetch = self._prefetch
            month, since, until = self._month, self._since, self._until
        gross = sum(counts.values())
        net = max(0, gross - prefetch)
        return {
            "month": f"{month // 100:04d}-{month % 100:02d}",
            "since": since,
            "until": until,
            "by_cause": counts,
            "gross": gross,
            "prefetch_cost": prefetch,
            "net": net,
            "allowance": allowance if allowance > 0 else None,
            "fraction": round(net / allowance, 4) if allowance > 0 else None,
            "partial": partial,
        }
