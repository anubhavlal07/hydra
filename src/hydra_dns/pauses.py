from __future__ import annotations

import time

MAX_MINUTES = 480


class Pauses:
    def __init__(self) -> None:
        self._until: dict[str, float] = {}

    def hold(self, device_id: str, minutes: int) -> float:
        span = max(1, min(MAX_MINUTES, int(minutes)))
        until = time.time() + span * 60
        self._until[device_id] = until
        return until

    def release(self, device_id: str) -> bool:
        return self._until.pop(device_id, None) is not None

    def until(self, device_id: str) -> float:
        held = self._until.get(device_id)
        if held is None:
            return 0.0
        if held <= time.time():
            del self._until[device_id]
            return 0.0
        return held

    def active(self, device_id: str) -> bool:
        return self.until(device_id) > 0.0

    def snapshot(self) -> dict[str, float]:
        now = time.time()
        for device_id in [d for d, until in self._until.items() if until <= now]:
            del self._until[device_id]
        return dict(self._until)

    def clear(self) -> int:
        count = len(self._until)
        self._until.clear()
        return count
