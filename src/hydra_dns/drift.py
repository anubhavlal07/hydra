from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from .adguard import policy

log = logging.getLogger(__name__)

READY_WAIT_S = 2.0
READY_ATTEMPTS = 60


class PolicyDrift:
    def __init__(
        self, manager: Any, every_s: float, clock: Callable[[], float] = time.time
    ) -> None:
        self._manager = manager
        self.every_s = max(0.0, float(every_s))
        self._clock = clock
        self._task: asyncio.Task | None = None
        self.outliers: dict[str, list[str]] = {}
        self.split: list[str] = []
        self.compared: list[str] = []
        self.checked_at = 0.0
        self.checks = 0
        self.errors: dict[str, str] = {}

    @property
    def enabled(self) -> bool:
        return self.every_s > 0

    def _serving(self) -> set[str]:
        return {a.label for a in self._manager.accounts if getattr(a, "enabled", True)}

    async def check(self) -> dict[str, Any]:
        serving = self._serving()
        results, errors = await self._manager.read_policy()
        read = {label: s for label, s in results.items() if label in serving}
        self.errors = {label: e for label, e in errors.items() if label in serving}
        self.checks += 1

        if len(read) < 2:
            if not self.errors:
                self.outliers, self.split, self.compared = {}, [], sorted(read)
                self.checked_at = self._clock()
            return self.snapshot()

        odd, split = policy.outliers(read)
        for label in self.errors:
            if label in self.outliers:
                odd[label] = self.outliers[label]

        if odd != self.outliers or split != self.split:
            if odd or split:
                log.warning(
                    "accounts filter differently: %s",
                    "; ".join(
                        [f"{label} ({', '.join(names)})" for label, names in odd.items()]
                        + ([f"no majority on {', '.join(split)}"] if split else [])
                    ),
                )
            else:
                log.info("every account taking traffic filters the same again")

        self.outliers, self.split, self.compared = odd, split, sorted(read)
        self.checked_at = self._clock()
        return self.snapshot()

    def findings(self) -> tuple[dict[str, list[str]], list[str]]:
        serving = self._serving()
        odd = {label: names for label, names in self.outliers.items() if label in serving}
        return odd, list(self.split) if len(serving) >= 2 else []

    def start_background(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._loop(), name="hydra-policy-drift")

    async def _loop(self) -> None:
        for _ in range(READY_ATTEMPTS):
            if self._manager.discovery_done:
                break
            await asyncio.sleep(READY_WAIT_S)
        while True:
            try:
                await self.check()
            except Exception:
                log.exception("policy drift check failed")
            await asyncio.sleep(self.every_s)

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    def snapshot(self) -> dict[str, Any]:
        odd, split = self.findings()
        return {
            "enabled": self.enabled,
            "every_s": self.every_s,
            "checked_at": self.checked_at or None,
            "checks": self.checks,
            "compared": self.compared,
            "outliers": odd,
            "split": split,
            "in_sync": (not odd and not split) if len(self.compared) >= 2 else None,
            "errors": self.errors,
        }


def build(manager: Any, settings: object) -> PolicyDrift:
    return PolicyDrift(manager, float(getattr(settings, "policy_check_s", 900.0) or 0))


def recheck_soon(state: Any) -> None:
    watcher = getattr(state, "policy_drift", None)
    if watcher is None or not watcher.enabled:
        return
    task = asyncio.create_task(_quietly(watcher), name="hydra-policy-recheck")
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def recheck(state: Any) -> None:
    watcher = getattr(state, "policy_drift", None)
    if watcher is not None and watcher.enabled:
        await _quietly(watcher)


_pending: set[asyncio.Task] = set()


async def _quietly(watcher: PolicyDrift) -> None:
    try:
        await watcher.check()
    except Exception:
        log.exception("policy drift recheck failed")


def findings(state: Any) -> tuple[dict[str, list[str]], list[str]]:
    watcher = getattr(state, "policy_drift", None)
    if watcher is None:
        return {}, []
    return watcher.findings()
