from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..config import AccountConfig, settings
from ..resolver.upstream import Upstream, UpstreamPool
from . import policy
from .client import AdGuardAccount, AdGuardError

log = logging.getLogger(__name__)

FAN_OUT_TIMEOUT_S = 20.0

REDISCOVER_MIN_S = 15.0
REDISCOVER_MAX_S = 300.0
WATCHDOG_S = 60.0


class AccountManager:
    def __init__(self, pool: UpstreamPool, client: httpx.AsyncClient) -> None:
        self._client = client
        self._pool = pool
        self.accounts: list[AdGuardAccount] = []
        self._tasks: list[asyncio.Task] = []
        self._stats_cache: dict[str, tuple[float, Any]] = {}
        self._stats_lock = asyncio.Lock()
        self.discovery_done = False
        self.last_refresh_at = 0.0
        self.empty_since = 0.0
        self.recoveries = 0
        self.policy_adoption: dict[str, dict[str, Any]] = {}

    def configure(self, configs: list[AccountConfig]) -> None:
        self.accounts = [AdGuardAccount(cfg, self._client) for cfg in configs]
        self._sync_pool()

    def start_background(self) -> None:
        self._tasks.append(asyncio.create_task(self._bootstrap(), name="hydra-bootstrap"))
        self._tasks.append(asyncio.create_task(self._refresh_loop(), name="hydra-refresh"))

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    async def _bootstrap(self) -> None:
        try:
            await self.discover_all()
        finally:
            self.discovery_done = True

        if not self.serving:
            log.warning("no upstream after the first discovery; retrying immediately")
            await asyncio.sleep(2)
            await self._force_recovery()

        await self.refresh_limits()
        self._track_emptiness()

    @property
    def degraded(self) -> bool:
        return any(not a.doh_url for a in self.accounts)

    @property
    def serving(self) -> bool:
        return any(a.doh_url and a.enabled for a in self.accounts)

    @property
    def answering(self) -> bool:
        return self._pool.ready

    @property
    def empty_for_s(self) -> float:
        return 0.0 if self.empty_since == 0.0 else time.monotonic() - self.empty_since

    def _track_emptiness(self) -> None:
        if self.serving:
            if self.empty_since:
                log.info("pool recovered after %.0fs with no upstream", self.empty_for_s)
            self.empty_since = 0.0
        elif self.empty_since == 0.0:
            self.empty_since = time.monotonic()
            if self._pool.fallback is not None:
                log.error(
                    "no account has an upstream; queries are being answered by the fallback "
                    "resolver %s, unfiltered, until this clears",
                    self._pool.fallback.doh_url,
                )
            else:
                log.error(
                    "no account has an upstream and no fallback is configured: "
                    "every query will answer SERVFAIL until this clears"
                )

    async def _force_recovery(self) -> None:
        self.recoveries += 1
        log.error(
            "forcing full re-authentication (attempt %s) after %.0fs with no upstream",
            self.recoveries,
            self.empty_for_s,
        )
        for account in self.accounts:
            account.force_reauth()
            account.forget_upstream()
        await self.discover_all()

    async def _refresh_loop(self) -> None:
        backoff = 0.0
        while True:
            self._track_emptiness()

            if self.degraded:
                backoff = (
                    REDISCOVER_MIN_S
                    if not self.serving
                    else min(max(REDISCOVER_MIN_S, backoff * 2), REDISCOVER_MAX_S)
                )
                delay = backoff
            elif not self.answering:
                backoff = 0.0
                delay = REDISCOVER_MIN_S
            else:
                backoff = 0.0
                delay = settings.limits_refresh_s
            await asyncio.sleep(delay)

            try:
                self._track_emptiness()
                if self.empty_for_s >= WATCHDOG_S:
                    await self._force_recovery()
                elif self.degraded:
                    log.warning(
                        "%s account(s) have no upstream; retrying discovery",
                        sum(1 for a in self.accounts if not a.doh_url),
                    )
                    await self.discover_all()
                await self.refresh_limits()
                self._track_emptiness()
            except Exception:
                log.exception("refresh cycle failed")

    async def discover_all(self) -> None:
        results = await asyncio.gather(
            *(a.discover_upstream() for a in self.accounts), return_exceptions=True
        )
        for account, result in zip(self.accounts, results, strict=True):
            if isinstance(result, Exception):
                log.warning(
                    "discovery failed for %s: %s",
                    account.label,
                    getattr(result, "detail", result),
                )
        self._sync_pool()
        usable = sum(1 for a in self.accounts if a.doh_url)
        log.info("discovery complete: %s of %s accounts usable", usable, len(self.accounts))

    async def refresh_limits(self) -> None:
        results = await asyncio.gather(
            *(a.fetch_limits() for a in self.accounts), return_exceptions=True
        )
        for account, result in zip(self.accounts, results, strict=True):
            if isinstance(result, Exception):
                log.warning(
                    "limits refresh failed for %s: %s",
                    account.label,
                    getattr(result, "detail", result),
                )
                continue
            upstream = self._pool.get(account.label)
            if upstream is not None and upstream.benched(time.monotonic()):
                log.info("%s: AdGuard is reachable again; clearing its cooldown", account.label)
                upstream.clear_bench()
        self._sync_pool()
        if any(not isinstance(result, Exception) for result in results):
            self.last_refresh_at = time.time()

    def _sync_pool(self) -> None:
        upstreams = [
            Upstream(label=a.label, doh_url=a.doh_url, enabled=a.enabled)
            for a in self.accounts
            if a.doh_url
        ]
        self._pool.set_upstreams(upstreams)
        for account in self.accounts:
            upstream = self._pool.get(account.label)
            if upstream is None:
                continue
            if account.requests_limit >= 0:
                upstream.quota_limit = account.requests_limit
                upstream.quota_used = account.requests_used

    async def add_account(self, config: AccountConfig) -> AdGuardAccount:
        account = AdGuardAccount(config, self._client)
        try:
            await account.discover_upstream()
        except AdGuardError:
            log.warning("%s: added, but discovery has not succeeded yet", config.label)
        with contextlib.suppress(AdGuardError):
            await account.fetch_limits()
        self.policy_adoption[account.label] = await self.adopt_policy(account)
        self.accounts.append(account)
        self._sync_pool()
        self.invalidate_stats_cache()
        log.info("account %s added to the pool (%s total)", config.label, len(self.accounts))
        return account

    async def adopt_policy(self, account: AdGuardAccount) -> dict[str, Any]:
        peers = [a for a in self.accounts if a.label != account.label and a.enabled]
        if not peers:
            return {"synced": False, "applied": [], "left": [], "from": [], "error": ""}

        read, read_errors = await self.fan_out(lambda a: a.server_settings(), peers)
        if not read:
            error = "could not read the policy of any other account: " + "; ".join(
                f"{k}: {v}" for k, v in sorted(read_errors.items())
            )
            log.warning("%s: policy not synced, %s", account.label, error)
            return {"synced": False, "applied": [], "left": [], "from": [], "error": error}

        changes, left = policy.agreed(read, unanimous=True)

        async def write(target: AdGuardAccount) -> bool:
            current = await target.server_settings()
            await target.update_server_settings(policy.apply_to_settings(current, **changes))
            return True

        written, errors = await self.fan_out(write, [account])
        error = errors.get(account.label, "")
        result = {
            "synced": bool(written),
            "applied": sorted(changes),
            "left": left,
            "from": sorted(read),
            "error": error,
        }
        if error:
            log.warning("%s: added, but its policy could not be synced: %s", account.label, error)
        else:
            log.info(
                "%s: policy synced from %s (%s)%s",
                account.label,
                ", ".join(sorted(read)),
                ", ".join(sorted(changes)),
                f"; left alone because the pool disagrees: {', '.join(left)}" if left else "",
            )
        return result

    async def set_enabled(self, label: str, enabled: bool) -> AdGuardAccount | None:
        account = next((a for a in self.accounts if a.label == label), None)
        if account is None:
            return None

        account.enabled = enabled
        if enabled and not account.doh_url:
            with contextlib.suppress(AdGuardError):
                await account.discover_upstream()
        self._sync_pool()
        self.invalidate_stats_cache()
        log.info(
            "account %s is now %s (%s of %s taking traffic)",
            label,
            "in the rotation" if enabled else "out of the rotation",
            sum(1 for a in self.accounts if a.enabled),
            len(self.accounts),
        )
        return account

    async def remove_account(self, label: str) -> bool:
        account = next((a for a in self.accounts if a.label == label), None)
        if account is None:
            return False

        self.accounts = [a for a in self.accounts if a.label != label]
        self.policy_adoption.pop(label, None)
        self._sync_pool()
        self.invalidate_stats_cache()
        with contextlib.suppress(Exception):
            await account.close()
        log.info("account %s removed from the pool (%s left)", label, len(self.accounts))
        return True

    async def fan_out(
        self,
        call: Callable[[AdGuardAccount], Awaitable[Any]],
        accounts: list[AdGuardAccount] | None = None,
    ) -> tuple[dict[str, Any], dict[str, str]]:
        targets = self.accounts if accounts is None else accounts
        if not targets:
            return {}, {}

        async def guarded(account: AdGuardAccount) -> tuple[str, Any, str]:
            try:
                async with asyncio.timeout(FAN_OUT_TIMEOUT_S):
                    return account.label, await call(account), ""
            except TimeoutError:
                return account.label, None, f"timed out after {FAN_OUT_TIMEOUT_S:.0f}s"
            except AdGuardError as exc:
                return account.label, None, exc.detail
            except Exception as exc:
                log.exception("fan-out call failed for %s", account.label)
                return account.label, None, f"{type(exc).__name__}: {exc}"

        gathered = await asyncio.gather(*(guarded(a) for a in targets))
        results = {label: value for label, value, error in gathered if not error}
        errors = {label: error for label, _, error in gathered if error}
        return results, errors

    async def cached_fan_out(
        self, key: str, call: Callable[[AdGuardAccount], Awaitable[Any]]
    ) -> tuple[dict[str, Any], dict[str, str]]:
        now = time.monotonic()
        async with self._stats_lock:
            hit = self._stats_cache.get(key)
            if hit and now - hit[0] < settings.stats_cache_s:
                return hit[1]

        payload = await self.fan_out(call)
        async with self._stats_lock:
            self._stats_cache[key] = (time.monotonic(), payload)
            if len(self._stats_cache) > 64:
                oldest = min(self._stats_cache, key=lambda k: self._stats_cache[k][0])
                del self._stats_cache[oldest]
        return payload

    def invalidate_stats_cache(self) -> None:
        self._stats_cache.clear()

    async def read_policy(self) -> tuple[dict[str, Any], dict[str, str]]:
        return await self.cached_fan_out("policy", lambda a: a.server_settings())

    async def apply_policy(self, **changes: Any) -> tuple[dict[str, Any], dict[str, str]]:

        async def write(account: AdGuardAccount) -> bool:
            current = await account.server_settings()
            await account.update_server_settings(policy.apply_to_settings(current, **changes))
            return True

        results, errors = await self.fan_out(write)
        self.invalidate_stats_cache()
        return results, errors

    def health(self) -> dict:
        return {
            "serving": self.serving,
            "degraded": self.degraded,
            "empty_for_s": round(self.empty_for_s, 1),
            "forced_recoveries": self.recoveries,
        }

    def snapshot(self) -> list[dict]:
        pool_by_label = {row["label"]: row for row in self._pool.snapshot()}
        rows = []
        for account in self.accounts:
            row = account.snapshot()
            row["upstream"] = pool_by_label.get(account.label)
            rows.append(row)
        return rows
