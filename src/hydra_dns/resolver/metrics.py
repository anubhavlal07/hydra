from __future__ import annotations

import time
from collections import Counter, deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from threading import Lock

from .. import firstseen, quiet, rebind, uncloak
from ..config import settings
from .savings import QuotaSavings, month_of

RECENT_QUERIES = 600
LATENCY_SAMPLES = 1000
TOP_DOMAINS = 500
PENDING_QUERIES = 5000
BLOCKED_PER_DEVICE = 100
MONTH_KEY = "month"
MONTH_PREFIX = "month_"


def _whole(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass
class QueryRecord:
    at: float
    device: str
    name: str
    qtype: str
    rcode: str
    cached: bool
    blocked: bool
    upstream: str
    latency_ms: float
    error: bool = False

    @property
    def result(self) -> str:
        if self.blocked:
            return "blocked"
        return "allowed" if self.rcode == "NOERROR" else "error"

    def as_dict(self) -> dict:
        return {
            "at": round(self.at, 3),
            "device": self.device,
            "name": self.name,
            "type": self.qtype,
            "rcode": self.rcode,
            "cached": self.cached,
            "blocked": self.blocked,
            "upstream": self.upstream,
            "latency_ms": round(self.latency_ms, 1),
            "error": self.error,
        }


@dataclass
class Tally:
    queries: int = 0
    blocked: int = 0
    cached: int = 0
    errors: int = 0

    def count(self, record: QueryRecord) -> None:
        self.queries += 1
        if record.blocked:
            self.blocked += 1
        if record.cached:
            self.cached += 1
        if record.error:
            self.errors += 1

    def as_dict(self) -> dict:
        return {
            "queries": self.queries,
            "blocked": self.blocked,
            "cached": self.cached,
            "errors": self.errors,
        }

    def take(self, row: dict, prefix: str = "") -> None:
        self.queries += _whole(row.get(f"{prefix}queries"))
        self.blocked += _whole(row.get(f"{prefix}blocked"))
        self.cached += _whole(row.get(f"{prefix}cached"))
        self.errors += _whole(row.get(f"{prefix}errors"))


@dataclass
class DeviceMetrics:
    queries: int = 0
    blocked: int = 0
    cached: int = 0
    errors: int = 0
    last_seen: float = 0.0
    month: int = 0
    this_month: Tally = field(default_factory=Tally)
    domains: Counter = field(default_factory=Counter)
    blocked_domains: Counter = field(default_factory=Counter)
    rebound: set[str] = field(default_factory=set)
    cloaked: dict[str, str] = field(default_factory=dict)


class Metrics:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._lock = Lock()
        self._clock = clock
        self.started_at = clock()
        self._month, self._month_since, self._month_until = month_of(self.started_at)
        self.this_month = Tally()

        self.queries = 0
        self.cached = 0
        self.blocked = 0
        self.errors = 0
        self.refused = 0
        self.savings = QuotaSavings()

        self.first_seen = firstseen.build(settings) if settings.first_seen else None
        self.presence = quiet.build(settings, clock)
        self.on_forget: Callable[[], None] | None = None

        self._latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self._recent: deque[QueryRecord] = deque(maxlen=RECENT_QUERIES)
        self._pending: deque[QueryRecord] = deque(maxlen=PENDING_QUERIES)
        self._exported: set[int] = set()
        self._devices: dict[str, DeviceMetrics] = {}
        self._domains: Counter = Counter()
        self._blocked_domains: Counter = Counter()
        self._qtypes: Counter = Counter()
        self._timeline: dict[int, list[int]] = {}

    def _roll(self, now: float) -> None:
        if now < self._month_until:
            return
        self._month, self._month_since, self._month_until = month_of(now)
        self.this_month = Tally()

    def _monthly(self, device: DeviceMetrics) -> Tally:
        if device.month != self._month:
            device.month = self._month
            device.this_month = Tally()
        return device.this_month

    def _month_of_device(self, device: DeviceMetrics) -> dict:
        if device.month != self._month:
            return Tally().as_dict()
        return device.this_month.as_dict()

    def record(self, record: QueryRecord) -> None:
        with self._lock:
            self._roll(record.at)
            self.this_month.count(record)
            self.queries += 1
            if record.cached:
                self.cached += 1
            if record.blocked:
                self.blocked += 1
            if record.error:
                self.errors += 1
            self._latencies.append(record.latency_ms)
            self._recent.appendleft(record)
            self._pending.append(record)
            self._qtypes[record.qtype] += 1

            self._domains[record.name] += 1
            if record.blocked:
                self._blocked_domains[record.name] += 1
            if len(self._domains) > TOP_DOMAINS * 2:
                self._domains = Counter(dict(self._domains.most_common(TOP_DOMAINS)))
            if len(self._blocked_domains) > TOP_DOMAINS * 2:
                self._blocked_domains = Counter(
                    dict(self._blocked_domains.most_common(TOP_DOMAINS))
                )

            device = self._devices.setdefault(record.device, DeviceMetrics())
            self._monthly(device).count(record)
            device.queries += 1
            device.last_seen = record.at
            if record.blocked:
                device.blocked += 1
                device.blocked_domains[record.name] += 1
                if rebind.refused_by_guard(record.upstream):
                    device.rebound.add(record.name)
                else:
                    device.rebound.discard(record.name)
                if uncloak.uncloaked(record.upstream):
                    device.cloaked[record.name] = uncloak.reason_of(record.upstream)
                else:
                    device.cloaked.pop(record.name, None)
                if len(device.blocked_domains) > BLOCKED_PER_DEVICE * 2:
                    device.blocked_domains = Counter(
                        dict(device.blocked_domains.most_common(BLOCKED_PER_DEVICE))
                    )
                    device.rebound &= device.blocked_domains.keys()
                    device.cloaked = {
                        k: v for k, v in device.cloaked.items() if k in device.blocked_domains
                    }
            if record.cached:
                device.cached += 1
            if record.error:
                device.errors += 1
            if self.first_seen is not None:
                self.first_seen.note(record.device, record.name, record.upstream, record.at)
            if self.presence is not None:
                self.presence.note(record.device, record.at)

            device.domains[record.name] += 1
            if len(device.domains) > TOP_DOMAINS * 2:
                device.domains = Counter(dict(device.domains.most_common(TOP_DOMAINS)))

            minute = int(record.at // 60) * 60
            bucket = self._timeline.setdefault(minute, [0, 0, 0])
            bucket[0] += 1
            if record.blocked:
                bucket[1] += 1
            if record.cached:
                bucket[2] += 1
            if len(self._timeline) > 1500:
                cutoff = minute - 86_400
                self._timeline = {k: v for k, v in self._timeline.items() if k >= cutoff}

    def record_refused(self) -> None:
        with self._lock:
            self.refused += 1

    def percentiles(self) -> dict[str, float]:
        with self._lock:
            samples = sorted(self._latencies)
        if not samples:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        def at(fraction: float) -> float:
            index = min(len(samples) - 1, int(len(samples) * fraction))
            return round(samples[index], 1)

        return {"p50": at(0.50), "p95": at(0.95), "p99": at(0.99)}

    def summary(self) -> dict:
        with self._lock:
            self._roll(self._clock())
            queries, cached, blocked = self.queries, self.cached, self.blocked
            errors, refused = self.errors, self.refused
            started = self.started_at
            month = {
                "label": f"{self._month // 100:04d}-{self._month % 100:02d}",
                "since": self._month_since,
                **self.this_month.as_dict(),
            }
        return {
            "queries": queries,
            "cached": cached,
            "blocked": blocked,
            "errors": errors,
            "refused": refused,
            "cache_hit_rate": round(cached / queries, 4) if queries else 0.0,
            "block_rate": round(blocked / queries, 4) if queries else 0.0,
            "uptime_s": round(time.time() - started, 1),
            "latency": self.percentiles(),
            "month": month,
        }

    def recent(
        self,
        limit: int = 100,
        device: str | None = None,
        result: str | None = None,
        hide: Callable[[str], bool] | None = None,
        search: str | None = None,
    ) -> list[dict]:
        with self._lock:
            records = list(self._recent)
        if device:
            records = [r for r in records if r.device == device]
        if result:
            records = [r for r in records if r.result == result]
        if search:
            needle = search.strip().lower()
            records = [r for r in records if needle in r.name]
        if hide is not None:
            records = [r for r in records if not hide(r.name)]
        return [r.as_dict() for r in records[:limit]]

    def askers(self, name: str) -> list[dict]:
        with self._lock:
            records = [r for r in self._recent if r.name == name]
        found: dict[str, dict] = {}
        for record in records:
            row = found.get(record.device)
            if row is None:
                found[record.device] = {
                    "device": record.device,
                    "count": 1,
                    "last_at": round(record.at, 3),
                    "last_upstream": record.upstream,
                    "last_result": record.result,
                }
            else:
                row["count"] += 1
        return sorted(found.values(), key=lambda r: (-r["count"], -r["last_at"]))

    def devices(self) -> list[dict]:
        with self._lock:
            self._roll(self._clock())
            items = [
                {
                    "device": name,
                    "queries": m.queries,
                    "blocked": m.blocked,
                    "cached": m.cached,
                    "errors": m.errors,
                    "last_seen": m.last_seen or None,
                    "month": self._month_of_device(m),
                    "top_domains": [
                        {"domain": d, "queries": c} for d, c in m.domains.most_common(10)
                    ],
                }
                for name, m in self._devices.items()
            ]
        return sorted(items, key=lambda d: d["queries"], reverse=True)

    def device_totals(self) -> dict[str, dict]:
        with self._lock:
            self._roll(self._clock())
            return {
                name: {
                    "queries": m.queries,
                    "blocked": m.blocked,
                    "last_seen": m.last_seen or None,
                    "month_queries": self._month_of_device(m)["queries"],
                }
                for name, m in self._devices.items()
            }

    def device_blocked(self, device: str, limit: int = 10) -> list[tuple[str, int]]:
        with self._lock:
            entry = self._devices.get(device)
            return entry.blocked_domains.most_common(max(1, limit)) if entry is not None else []

    def device_rebound(self, device: str) -> frozenset[str]:
        with self._lock:
            entry = self._devices.get(device)
            return frozenset(entry.rebound) if entry is not None else frozenset()

    def device_cloaked(self, device: str) -> dict[str, str]:
        with self._lock:
            entry = self._devices.get(device)
            return dict(entry.cloaked) if entry is not None else {}

    def quiet_since(self, devices: list[str], now: float) -> dict[str, float]:
        with self._lock:
            if self.presence is None:
                return {}
            return self.presence.quiet(devices, now)

    def quiet_snapshot(self) -> dict | None:
        with self._lock:
            return self.presence.snapshot(self._clock()) if self.presence is not None else None

    def export_presence(self, now: float) -> dict | None:
        with self._lock:
            return self.presence.export(now) if self.presence is not None else None

    def restore_presence(self, payload: object, now: float) -> int:
        with self._lock:
            return self.presence.restore(payload, now) if self.presence is not None else 0

    def _forgotten(self, dropped: bool) -> None:
        if dropped and self.on_forget is not None:
            self.on_forget()

    def forget_presence(self, device: str) -> bool:
        with self._lock:
            dropped = self.presence.forget(device) if self.presence is not None else False
        self._forgotten(dropped)
        return dropped

    def forget_device(self, device: str) -> bool:
        with self._lock:
            known = self._devices.pop(device, None) is not None
            dropped = self.presence.forget(device) if self.presence is not None else False
            self._pending = deque(
                (r for r in self._pending if r.device != device), maxlen=PENDING_QUERIES
            )
            self._recent = deque(
                (r for r in self._recent if r.device != device), maxlen=RECENT_QUERIES
            )
        self._forgotten(dropped)
        return known

    def forget_device_blocked(self) -> None:
        with self._lock:
            for entry in self._devices.values():
                entry.blocked_domains = Counter()
                entry.rebound = set()
                entry.cloaked = {}

    def first_seen_since(self, device: str, since: float, limit: int = 10) -> tuple[int, list]:
        with self._lock:
            watcher = self.first_seen
            if watcher is None:
                return 0, []
            return watcher.since(device, since, limit)

    def top_domains(self, limit: int = 20, blocked_only: bool = False) -> list[dict]:
        with self._lock:
            source = self._blocked_domains if blocked_only else self._domains
            common = source.most_common(limit)
            blocked_counts = dict(self._blocked_domains)
        return [
            {"domain": domain, "queries": count, "blocked": blocked_counts.get(domain, 0)}
            for domain, count in common
        ]

    def qtypes(self, limit: int = 10) -> list[dict]:
        with self._lock:
            common = self._qtypes.most_common(limit)
        return [{"type": name, "queries": count} for name, count in common]

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def export_state(self, max_queries: int = 500) -> dict:
        with self._lock:
            self._roll(self._clock())
            totals = {
                "queries": self.queries,
                "cached": self.cached,
                "blocked": self.blocked,
                "errors": self.errors,
                "refused": self.refused,
                MONTH_KEY: self._month,
                **{f"{MONTH_PREFIX}{k}": v for k, v in self.this_month.as_dict().items()},
                **self.savings.export(),
            }
            devices = [
                {
                    "device": name,
                    "queries": m.queries,
                    "blocked": m.blocked,
                    "cached": m.cached,
                    "errors": m.errors,
                    "last_seen": m.last_seen or None,
                    MONTH_KEY: self._month,
                    **{f"{MONTH_PREFIX}{k}": v for k, v in self._month_of_device(m).items()},
                }
                for name, m in self._devices.items()
            ]
            timeline = [
                {"at": minute, "queries": v[0], "blocked": v[1], "cached": v[2]}
                for minute, v in self._timeline.items()
            ]
            blocked_counts = dict(self._blocked_domains)
            domains = [
                {"domain": d, "queries": c, "blocked": blocked_counts.get(d, 0)}
                for d, c in self._domains.most_common(TOP_DOMAINS)
            ]
            batch = list(self._pending)[:max_queries]
            self._exported = {id(r) for r in batch}
            queries = [r.as_dict() for r in batch]

        return {
            "totals": totals,
            "devices": devices,
            "timeline": timeline,
            "domains": domains,
            "queries": queries,
        }

    def mark_flushed(self, count: int) -> None:
        if count <= 0:
            return
        with self._lock:
            for _ in range(min(count, len(self._pending))):
                if id(self._pending[0]) not in self._exported:
                    break
                self._pending.popleft()

    def restore(self, state: dict) -> dict:
        counts = {"queries": 0, "devices": 0, "timeline": 0, "domains": 0}
        with self._lock:
            totals = state.get("totals") or {}
            self.queries = int(totals.get("queries", 0) or 0)
            self.cached = int(totals.get("cached", 0) or 0)
            self.blocked = int(totals.get("blocked", 0) or 0)
            self.errors = int(totals.get("errors", 0) or 0)
            self.refused = int(totals.get("refused", 0) or 0)
            self.savings.merge(totals)
            self._roll(self._clock())
            self.this_month = Tally()
            if _whole(totals.get(MONTH_KEY)) == self._month:
                self.this_month.take(totals, MONTH_PREFIX)

            for row in state.get("devices") or []:
                name = str(row.get("device") or "")
                if not name:
                    continue
                entry = self._devices.setdefault(name, DeviceMetrics())
                entry.queries = int(row.get("queries", 0) or 0)
                entry.blocked = int(row.get("blocked", 0) or 0)
                entry.cached = int(row.get("cached", 0) or 0)
                entry.errors = int(row.get("errors", 0) or 0)
                entry.last_seen = float(row.get("last_seen") or 0)
                entry.month = self._month
                entry.this_month = Tally()
                if _whole(row.get(MONTH_KEY)) == self._month:
                    entry.this_month.take(row, MONTH_PREFIX)
                counts["devices"] += 1

            for row in state.get("timeline") or []:
                minute = int(row.get("at", 0) or 0)
                if minute:
                    self._timeline[minute] = [
                        int(row.get("queries", 0) or 0),
                        int(row.get("blocked", 0) or 0),
                        int(row.get("cached", 0) or 0),
                    ]
                    counts["timeline"] += 1

            for row in state.get("domains") or []:
                domain = str(row.get("domain") or "")
                if not domain:
                    continue
                self._domains[domain] = int(row.get("queries", 0) or 0)
                blocked = int(row.get("blocked", 0) or 0)
                if blocked:
                    self._blocked_domains[domain] = blocked
                counts["domains"] += 1

            for row in state.get("queries") or []:
                try:
                    self._recent.append(
                        QueryRecord(
                            at=float(row.get("at") or 0),
                            device=str(row.get("device") or "?"),
                            name=str(row.get("name") or "?"),
                            qtype=str(row.get("type") or ""),
                            rcode=str(row.get("rcode") or ""),
                            cached=bool(row.get("cached")),
                            blocked=bool(row.get("blocked")),
                            upstream=str(row.get("upstream") or ""),
                            error=bool(row.get("error")),
                            latency_ms=float(row.get("latency_ms") or 0),
                        )
                    )
                    counts["queries"] += 1
                except (TypeError, ValueError):
                    continue

        return counts

    def absorb(self, state: dict) -> None:
        with self._lock:
            totals = state.get("totals") or {}
            self.queries += _whole(totals.get("queries"))
            self.cached += _whole(totals.get("cached"))
            self.blocked += _whole(totals.get("blocked"))
            self.errors += _whole(totals.get("errors"))
            self.refused += _whole(totals.get("refused"))
            self.savings.merge(totals)
            self._roll(self._clock())
            if _whole(totals.get(MONTH_KEY)) == self._month:
                self.this_month.take(totals, MONTH_PREFIX)

            for row in state.get("devices") or []:
                name = str(row.get("device") or "")
                if not name:
                    continue
                entry = self._devices.setdefault(name, DeviceMetrics())
                entry.queries += _whole(row.get("queries"))
                entry.blocked += _whole(row.get("blocked"))
                entry.cached += _whole(row.get("cached"))
                entry.errors += _whole(row.get("errors"))
                if _whole(row.get(MONTH_KEY)) == self._month:
                    self._monthly(entry).take(row, MONTH_PREFIX)
                with suppress(TypeError, ValueError):
                    entry.last_seen = max(entry.last_seen, float(row.get("last_seen") or 0))

            for row in state.get("timeline") or []:
                minute = _whole(row.get("at"))
                if minute:
                    bucket = self._timeline.setdefault(minute, [0, 0, 0])
                    bucket[0] += _whole(row.get("queries"))
                    bucket[1] += _whole(row.get("blocked"))
                    bucket[2] += _whole(row.get("cached"))

            for row in (state.get("domains") or [])[:TOP_DOMAINS]:
                domain = str(row.get("domain") or "")
                if not domain:
                    continue
                self._domains[domain] += _whole(row.get("queries"))
                blocked = _whole(row.get("blocked"))
                if blocked:
                    self._blocked_domains[domain] += blocked

    def timeline(self, minutes: int = 60) -> list[dict]:
        now_minute = int(time.time() // 60) * 60
        with self._lock:
            buckets = dict(self._timeline)
        series = []
        for offset in range(minutes - 1, -1, -1):
            minute = now_minute - offset * 60
            queries, blocked, cached = buckets.get(minute, [0, 0, 0])
            series.append({"at": minute, "queries": queries, "blocked": blocked, "cached": cached})
        return series
