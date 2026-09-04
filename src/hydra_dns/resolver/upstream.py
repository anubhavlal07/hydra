from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
BASE_COOLDOWN_S = 20.0
MAX_COOLDOWN_S = 120.0
QUOTA_EXHAUSTED_AT = 0.995
FALLBACK_LABEL = "fallback"

BALANCE_FLOOR = 0.01
BALANCED_WITHIN = 0.01
UNKNOWN_WEIGHT = 1000.0


@dataclass
class Upstream:
    label: str
    doh_url: str
    quota_used: int = -1
    quota_limit: int = -1

    queries: int = 0
    errors: int = 0
    latency_ewma_ms: float = 0.0
    last_latency_ms: float = 0.0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    last_error: str = ""
    last_used_at: float = 0.0

    current_weight: float = 0.0
    is_fallback: bool = False
    enabled: bool = True

    @property
    def quota_remaining(self) -> int:
        if self.quota_limit < 0 or self.quota_used < 0:
            return -1
        return max(0, self.quota_limit - self.quota_used)

    @property
    def quota_known(self) -> bool:
        return self.quota_limit > 0 and self.quota_used >= 0

    @property
    def quota_fraction(self) -> float:
        if self.quota_limit <= 0 or self.quota_used < 0:
            return 0.0
        return min(1.0, self.quota_used / self.quota_limit)

    @property
    def exhausted(self) -> bool:
        return self.quota_limit > 0 and self.quota_fraction >= QUOTA_EXHAUSTED_AT

    def benched(self, now: float) -> bool:
        return now < self.cooldown_until

    def available(self, now: float) -> bool:
        return self.enabled and not self.benched(now) and not self.exhausted

    def record_success(self, latency_ms: float) -> None:
        self.queries += 1
        self.last_latency_ms = latency_ms
        if self.latency_ewma_ms == 0.0:
            self.latency_ewma_ms = latency_ms
        else:
            self.latency_ewma_ms = 0.8 * self.latency_ewma_ms + 0.2 * latency_ms
        self.consecutive_failures = 0
        self.cooldown_until = 0.0
        self.last_error = ""
        self.last_used_at = time.time()
        if self.quota_used >= 0:
            self.quota_used += 1

    def clear_bench(self) -> None:
        self.consecutive_failures = 0
        self.cooldown_until = 0.0

    def record_failure(self, reason: str) -> None:
        self.queries += 1
        self.errors += 1
        self.consecutive_failures += 1
        self.last_error = reason[:200]
        self.last_used_at = time.time()
        if self.consecutive_failures >= FAILURE_THRESHOLD:
            over = self.consecutive_failures - FAILURE_THRESHOLD
            self.cooldown_until = time.monotonic() + min(
                BASE_COOLDOWN_S * (2**over), MAX_COOLDOWN_S
            )

    def snapshot(self, now: float) -> dict:
        return {
            "label": self.label,
            "configured": bool(self.doh_url),
            "enabled": self.enabled,
            "available": self.available(now),
            "benched": self.benched(now),
            "benched_for_s": max(0.0, round(self.cooldown_until - now, 1)),
            "exhausted": self.exhausted,
            "queries": self.queries,
            "errors": self.errors,
            "error_rate": round(self.errors / self.queries, 4) if self.queries else 0.0,
            "latency_ms": round(self.latency_ewma_ms, 1),
            "quota_used": self.quota_used,
            "quota_limit": self.quota_limit,
            "quota_remaining": self.quota_remaining,
            "quota_fraction": round(self.quota_fraction, 4),
            "last_error": self.last_error,
            "last_used_at": self.last_used_at or None,
        }


class UpstreamError(RuntimeError):
    pass


class UpstreamPool:
    def __init__(
        self,
        strategy: str,
        timeout_s: float,
        fallback_url: str = "",
        balance_floor: float = BALANCE_FLOOR,
    ) -> None:
        self._upstreams: list[Upstream] = []
        self._strategy = strategy
        self._timeout_s = timeout_s
        self._floor = max(0.0, balance_floor)
        self._rr_index = 0
        self._swrr_labels: tuple[str, ...] = ()
        self._client: httpx.AsyncClient | None = None
        self._fallback = (
            Upstream(label=FALLBACK_LABEL, doh_url=fallback_url, is_fallback=True)
            if fallback_url
            else None
        )

    def attach_client(self, client: httpx.AsyncClient) -> None:
        self._client = client

    def set_fallback(self, url: str) -> None:
        self._fallback = (
            Upstream(label=FALLBACK_LABEL, doh_url=url, is_fallback=True) if url else None
        )

    def set_upstreams(self, upstreams: list[Upstream]) -> None:
        existing = {u.label: u for u in self._upstreams}
        merged: list[Upstream] = []
        for fresh in upstreams:
            prior = existing.get(fresh.label)
            if prior is not None:
                prior.doh_url = fresh.doh_url
                prior.enabled = fresh.enabled
                merged.append(prior)
            else:
                merged.append(fresh)
        self._upstreams = merged

    @property
    def upstreams(self) -> list[Upstream]:
        return list(self._upstreams)

    def get(self, label: str) -> Upstream | None:
        return next((u for u in self._upstreams if u.label == label), None)

    @property
    def fallback(self) -> Upstream | None:
        return self._fallback

    def configured(self) -> list[Upstream]:
        return [u for u in self._upstreams if u.doh_url]

    def available(self, now: float | None = None) -> list[Upstream]:
        moment = time.monotonic() if now is None else now
        return [u for u in self.configured() if u.available(moment)]

    @property
    def ready(self) -> bool:
        return bool(self.available())

    @property
    def spent(self) -> bool:
        return bool(self.configured()) and not self.ready

    @property
    def fallback_active(self) -> bool:
        return self.spent and self._fallback is not None

    def order(self) -> list[Upstream]:
        now = time.monotonic()
        usable = [u for u in self.configured() if u.enabled]
        if not usable:
            return []

        preferred = [u for u in usable if u.available(now)]
        demoted = [u for u in usable if not u.available(now)]

        if not preferred:
            return sorted(demoted, key=lambda u: u.cooldown_until)

        if self._strategy == "round_robin":
            ordered = self._order_round_robin(preferred)
        elif self._strategy == "least_used":
            ordered = sorted(preferred, key=lambda u: (u.quota_fraction, u.latency_ewma_ms))
        else:
            ordered = self._order_weighted(preferred)

        return ordered + demoted

    def _order_round_robin(self, candidates: list[Upstream]) -> list[Upstream]:
        self._rr_index = (self._rr_index + 1) % len(candidates)
        return candidates[self._rr_index :] + candidates[: self._rr_index]

    def _order_weighted(self, candidates: list[Upstream]) -> list[Upstream]:
        weights = self._weights(candidates)
        head = self._smooth_pick(candidates, weights)
        rest = sorted(
            (u for u in candidates if u is not head),
            key=lambda u: (-weights[u.label], u.latency_ewma_ms),
        )
        return [head, *rest]

    def _smooth_pick(self, candidates: list[Upstream], weights: dict[str, float]) -> Upstream:
        labels = tuple(sorted(weights))
        if labels != self._swrr_labels:
            for upstream in self._upstreams:
                upstream.current_weight = 0.0
            self._swrr_labels = labels

        total = sum(weights.values())
        for upstream in candidates:
            upstream.current_weight += weights[upstream.label]
        best = max(candidates, key=lambda u: u.current_weight)
        best.current_weight -= total
        return best

    def _weights(self, candidates: list[Upstream]) -> dict[str, float]:
        weights = (
            self._balance_weights(candidates)
            if self._strategy == "balanced"
            else self._headroom_weights(candidates)
        )
        if sum(weights.values()) <= 0:
            return {u.label: 1.0 for u in candidates}
        return weights

    def _headroom_weights(self, candidates: list[Upstream]) -> dict[str, float]:
        weights: dict[str, float] = {}
        for upstream in candidates:
            remaining = upstream.quota_remaining
            weights[upstream.label] = (
                max(1.0, float(remaining)) if remaining >= 0 else UNKNOWN_WEIGHT
            )
        return weights

    def _balance_weights(self, candidates: list[Upstream]) -> dict[str, float]:
        known = [u for u in candidates if u.quota_known]
        if not known:
            return {u.label: UNKNOWN_WEIGHT for u in candidates}

        target = max(u.quota_fraction for u in known)
        weights = {
            u.label: (self._floor + target - u.quota_fraction) * u.quota_limit for u in known
        }
        spare = sum(weights.values()) / len(weights)
        return {u.label: weights.get(u.label, spare) for u in candidates}

    def target_fraction(self) -> float:
        return max((u.quota_fraction for u in self._balancing()), default=0.0)

    def _balancing(self) -> list[Upstream]:
        return [u for u in self.available() if u.quota_known]

    def shares(self) -> dict[str, float]:
        candidates = self.available()
        if not candidates:
            return {}
        if self._strategy == "round_robin":
            even = round(1.0 / len(candidates), 4)
            return {u.label: even for u in candidates}
        if self._strategy == "least_used":
            head = min(candidates, key=lambda u: (u.quota_fraction, u.latency_ewma_ms))
            return {u.label: 1.0 if u is head else 0.0 for u in candidates}
        weights = self._weights(candidates)
        total = sum(weights.values())
        return {u.label: round(weights[u.label] / total, 4) for u in candidates}

    def balance(self) -> dict:
        known = self._balancing()
        fractions = [u.quota_fraction for u in known]
        target = max(fractions, default=0.0)
        spread = target - min(fractions, default=0.0)
        return {
            "strategy": self._strategy,
            "balancing": self._strategy == "balanced",
            "floor": round(self._floor, 4),
            "reporting": len(known),
            "target_fraction": round(target, 4),
            "spread": round(spread, 4),
            "balanced": len(known) < 2 or spread <= BALANCED_WITHIN,
            "shares": self.shares(),
        }

    async def forward(self, wire: bytes) -> tuple[bytes, Upstream, float]:
        if self._client is None:
            raise UpstreamError("HTTP client is not attached to the pool")

        candidates = self.order()
        if self._fallback is not None:
            candidates = (
                [*candidates, self._fallback] if self.available() else [self._fallback, *candidates]
            )

        if not candidates:
            raise UpstreamError(
                "no upstream is configured yet -- account discovery has not finished or "
                "every account failed to authenticate"
            )

        return await self._first_answer(candidates, wire)

    async def forward_unfiltered(self, wire: bytes) -> tuple[bytes, Upstream, float]:
        if self._client is None or self._fallback is None:
            return await self.forward(wire)
        try:
            return await self._first_answer([self._fallback], wire)
        except UpstreamError:
            return await self.forward(wire)

    async def _first_answer(
        self, candidates: list[Upstream], wire: bytes
    ) -> tuple[bytes, Upstream, float]:
        assert self._client is not None
        failures: list[str] = []
        for upstream in candidates:
            started = time.perf_counter()
            try:
                response = await self._client.post(
                    upstream.doh_url,
                    content=wire,
                    headers={
                        "content-type": "application/dns-message",
                        "accept": "application/dns-message",
                    },
                    timeout=self._timeout_s,
                )
            except httpx.HTTPError as exc:
                reason = f"{type(exc).__name__}: {exc}"
                upstream.record_failure(reason)
                failures.append(f"{upstream.label}: {reason}")
                continue

            latency_ms = (time.perf_counter() - started) * 1000

            if response.status_code != 200:
                reason = f"HTTP {response.status_code}"
                if response.status_code == 429 and upstream.quota_limit > 0:
                    upstream.quota_used = upstream.quota_limit
                upstream.record_failure(reason)
                failures.append(f"{upstream.label}: {reason}")
                continue

            if not response.content:
                upstream.record_failure("empty response body")
                failures.append(f"{upstream.label}: empty body")
                continue

            upstream.record_success(latency_ms)
            return response.content, upstream, latency_ms

        raise UpstreamError("; ".join(failures) or "no upstream answered")

    def snapshot(self) -> list[dict]:
        now = time.monotonic()
        shares = self.shares()
        target = self.target_fraction()
        rows = []
        for upstream in self._upstreams:
            row = upstream.snapshot(now)
            row["share"] = shares.get(upstream.label, 0.0)
            row["deficit"] = (
                round(max(0.0, target - upstream.quota_fraction), 4)
                if upstream.quota_known
                else 0.0
            )
            rows.append(row)
        return rows

    def fallback_snapshot(self) -> dict | None:
        if self._fallback is None:
            return None
        snap = self._fallback.snapshot(time.monotonic())
        snap["active"] = self.fallback_active
        snap["url"] = self._fallback.doh_url
        return snap

    def state(self) -> dict:
        return {
            "ready": self.ready,
            "spent": self.spent,
            "configured": len(self.configured()),
            "available": len(self.available()),
            "balance": self.balance(),
            "fallback": self.fallback_snapshot(),
        }
