from __future__ import annotations

import time
from collections import OrderedDict, deque

from . import suspicion

SKIP_UPSTREAMS = ("local zone", "rewrite", "device rule", "designated")


class FirstSeen:
    def __init__(
        self,
        per_device: int = 2000,
        devices: int = 50,
        recent: int = 100,
        warmup_s: int = 3600,
        threshold: int = 50,
    ) -> None:
        self._per_device = max(1, per_device)
        self._devices = max(1, devices)
        self._warmup_s = max(0, warmup_s)
        self.threshold = max(1, min(100, threshold))
        self._known: OrderedDict[str, OrderedDict[str, float]] = OrderedDict()
        self._watching: dict[str, float] = {}
        self._recent: deque[tuple[float, str, str, suspicion.Suspicion]] = deque(
            maxlen=max(1, recent)
        )
        self.counted = 0
        self.suspicious = 0

    @staticmethod
    def _worth_tracking(upstream: str) -> bool:
        return not any(upstream.startswith(prefix) for prefix in SKIP_UPSTREAMS)

    def note(self, device: str, name: str, upstream: str, at: float) -> bool:
        if not name or not self._worth_tracking(upstream):
            return False

        seen = self._known.get(device)
        if seen is None:
            seen = OrderedDict()
            self._known[device] = seen
            self._watching[device] = at
            while len(self._known) > self._devices:
                dropped, _ = self._known.popitem(last=False)
                self._watching.pop(dropped, None)

        self._known.move_to_end(device)

        if name in seen:
            seen.move_to_end(name)
            return False

        seen[name] = at
        while len(seen) > self._per_device:
            seen.popitem(last=False)

        if at - self._watching.get(device, at) < self._warmup_s:
            return False

        verdict = suspicion.score(name)
        self._recent.appendleft((at, device, name, verdict))
        self.counted += 1
        if verdict.score >= self.threshold:
            self.suspicious += 1
        return True

    def recent(
        self, limit: int = 50, device: str | None = None, suspicious: bool = False
    ) -> list[dict]:
        rows = [
            {
                "at": at,
                "device": who,
                "name": name,
                **verdict.as_dict(),
                "suspicious": verdict.score >= self.threshold,
            }
            for at, who, name, verdict in self._recent
            if (device is None or who == device)
            and (not suspicious or verdict.score >= self.threshold)
        ]
        return rows[: max(1, limit)]

    def first_at(self, device: str, name: str) -> float | None:
        seen = self._known.get(device)
        return seen.get(name) if seen is not None else None

    def since(self, device: str, since: float, limit: int = 10) -> tuple[int, list[dict]]:
        seen = self._known.get(device)
        if seen is None:
            return 0, []
        settled = self._watching.get(device, 0.0) + self._warmup_s
        fresh = [(at, name) for name, at in seen.items() if at >= since and at >= settled]
        fresh.sort(reverse=True)
        return len(fresh), [{"name": name, "at": at} for at, name in fresh[: max(1, limit)]]

    def watching_since(self, device: str) -> float:
        return self._watching.get(device, 0.0)

    def snapshot(self) -> dict:
        return {
            "counted": self.counted,
            "suspicious": self.suspicious,
            "threshold": self.threshold,
            "devices": len(self._known),
            "tracked": sum(len(seen) for seen in self._known.values()),
            "capacity": self._per_device * self._devices,
            "warmup_s": self._warmup_s,
            "recent": len(self._recent),
        }

    def clear(self) -> None:
        self._known.clear()
        self._watching.clear()
        self._recent.clear()
        self.counted = 0
        self.suspicious = 0


def build(settings: object) -> FirstSeen:
    return FirstSeen(
        per_device=getattr(settings, "first_seen_per_device", 2000),
        devices=getattr(settings, "metrics_top_devices", 50),
        warmup_s=getattr(settings, "first_seen_warmup_s", 3600),
        threshold=getattr(settings, "suspicious_score", 50),
    )


def now() -> float:
    return time.time()
