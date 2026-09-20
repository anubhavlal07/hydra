from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .config import settings

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_ESCAPES = str.maketrans({"\\": "\\\\", '"': '\\"', "\n": "\\n"})


def _label(value: object) -> str:
    return str(value)[:120].translate(_ESCAPES)


def _number(value: float) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    return repr(round(float(value), 6))


class Renderer:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def metric(self, name: str, kind: str, help_text: str) -> None:
        self._lines.append(f"# HELP {name} {help_text}")
        self._lines.append(f"# TYPE {name} {kind}")

    def sample(self, name: str, value: float, labels: dict[str, object] | None = None) -> None:
        if labels:
            pairs = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            self._lines.append(f"{name}{{{pairs}}} {_number(value)}")
        else:
            self._lines.append(f"{name} {_number(value)}")

    def counter(self, name: str, help_text: str, value: float) -> None:
        self.metric(name, "counter", help_text)
        self.sample(name, value)

    def gauge(self, name: str, help_text: str, value: float) -> None:
        self.metric(name, "gauge", help_text)
        self.sample(name, value)

    def series(
        self,
        name: str,
        kind: str,
        help_text: str,
        rows: list[tuple[dict[str, object], float]],
    ) -> None:
        self.metric(name, kind, help_text)
        for labels, value in rows:
            self.sample(name, value, labels)

    def text(self) -> str:
        return "\n".join(self._lines) + "\n"


def _resolver_metrics(out: Renderer, metrics: Any) -> None:
    summary = metrics.summary()
    out.counter("hydra_queries_total", "DNS queries answered.", summary["queries"])
    out.counter("hydra_cache_hits_total", "Queries answered from cache.", summary["cached"])
    out.counter("hydra_blocked_total", "Queries answered as blocked.", summary["blocked"])
    out.counter("hydra_errors_total", "Queries that ended in an error.", summary["errors"])
    out.counter("hydra_refused_total", "Requests refused before resolving.", summary["refused"])
    out.gauge("hydra_uptime_seconds", "Seconds since the resolver started.", summary["uptime_s"])
    out.gauge("hydra_cache_hit_ratio", "Cache hits over queries.", summary["cache_hit_rate"])
    out.gauge("hydra_block_ratio", "Blocked answers over queries.", summary["block_rate"])

    out.series(
        "hydra_query_latency_seconds",
        "gauge",
        "Resolver latency percentiles over the recent sample window.",
        [
            ({"quantile": quantile}, summary["latency"][key] / 1000)
            for quantile, key in (("0.5", "p50"), ("0.95", "p95"), ("0.99", "p99"))
        ],
    )

    totals = metrics.device_totals()
    ranked = sorted(totals.items(), key=lambda kv: kv[1]["queries"], reverse=True)
    top = ranked[: max(0, settings.metrics_top_devices)]

    out.series(
        "hydra_device_queries_total",
        "counter",
        "Queries per device, capped at the busiest devices.",
        [({"device": device}, row["queries"]) for device, row in top],
    )
    out.series(
        "hydra_device_blocked_total",
        "counter",
        "Blocked answers per device, capped at the busiest devices.",
        [({"device": device}, row["blocked"]) for device, row in top],
    )
    out.gauge("hydra_devices_seen", "Distinct devices with recorded traffic.", len(totals))

    savings = getattr(metrics, "savings", None)
    if savings is not None:
        snapshot = savings.snapshot()
        out.series(
            "hydra_quota_saved_total",
            "counter",
            "AdGuard queries answered by Hydra itself this billing month, by cause.",
            [({"cause": cause}, n) for cause, n in snapshot["by_cause"].items()],
        )
        out.counter(
            "hydra_quota_prefetch_spent_total",
            "AdGuard queries the prefetcher spent this billing month.",
            snapshot["prefetch_cost"],
        )


def _cache_metrics(out: Renderer, cache: Any) -> None:
    snapshot = cache.snapshot()
    out.gauge(
        "hydra_cache_entries", "Entries held in the answer cache.", snapshot.get("entries", 0)
    )
    out.gauge(
        "hydra_cache_live_entries",
        "Cache entries still inside their TTL.",
        snapshot.get("live", 0),
    )
    out.gauge(
        "hydra_cache_stale_entries",
        "Cache entries past TTL but inside the stale window.",
        snapshot.get("stale", 0),
    )
    out.counter(
        "hydra_cache_stale_hits_total",
        "Answers served from an expired entry.",
        snapshot.get("stale_hits", 0),
    )
    out.counter(
        "hydra_cache_evictions_total",
        "Entries dropped to stay under the cap.",
        snapshot.get("evictions", 0),
    )


def _account_metrics(out: Renderer, manager: Any) -> None:
    accounts = manager.snapshot()

    def upstream_of(account: dict) -> dict:
        return account.get("upstream") or {}

    def known(account: dict, key: str) -> bool:
        value = account.get(key)
        return isinstance(value, int) and value >= 0

    out.series(
        "hydra_account_requests_used",
        "gauge",
        "Monthly requests spent per account.",
        [
            ({"account": a["label"]}, a["requests_used"])
            for a in accounts
            if known(a, "requests_used")
        ],
    )
    out.series(
        "hydra_account_requests_limit",
        "gauge",
        "Monthly request allowance per account.",
        [
            ({"account": a["label"]}, a["requests_limit"])
            for a in accounts
            if known(a, "requests_limit")
        ],
    )
    out.series(
        "hydra_account_available",
        "gauge",
        "1 when an account can currently answer.",
        [({"account": a["label"]}, 1 if upstream_of(a).get("available") else 0) for a in accounts],
    )
    out.series(
        "hydra_account_authenticated",
        "gauge",
        "1 when an account holds a valid AdGuard token.",
        [
            ({"account": a["label"]}, 1 if (a.get("auth") or {}).get("authenticated") else 0)
            for a in accounts
        ],
    )


def _blocklist_metrics(out: Renderer, lists: Any) -> None:
    held = list(lists.lists)[: max(0, settings.blocklist_max)]
    out.series(
        "hydra_blocklist_hits_total",
        "counter",
        "Answers given by each Hydra blocklist since start.",
        [({"list": s.meta.id}, s.hits) for s in held],
    )
    out.series(
        "hydra_blocklist_names",
        "gauge",
        "Names each Hydra blocklist holds in memory.",
        [({"list": s.meta.id}, len(s.names)) for s in held],
    )


def _app_metrics(out: Renderer, apps: Any) -> None:
    blocks = apps.blocks
    out.series(
        "hydra_app_hits_total",
        "counter",
        "Answers given by each app block since start.",
        [({"app": b.id}, apps.hits_of(b.id)) for b in blocks],
    )
    out.metric("hydra_app_blocks", "gauge", "App blocks held, and how many are in force now.")
    out.sample("hydra_app_blocks", len(blocks), {"state": "held"})
    out.sample("hydra_app_blocks", len(apps.active()), {"state": "active"})


def render(state: Any, attention: list[str], certificate_days: float | None = None) -> str:
    out = Renderer()

    out.metric("hydra_build_info", "gauge", "Build and instance identity, always 1.")
    out.sample(
        "hydra_build_info",
        1,
        {
            "version": "0.1.0",
            "instance": settings.instance_id,
            "strategy": settings.strategy,
        },
    )

    metrics = getattr(state, "metrics", None)
    if metrics is not None:
        _resolver_metrics(out, metrics)

    cache = getattr(state, "cache", None)
    if cache is not None:
        _cache_metrics(out, cache)

    manager = getattr(state, "manager", None)
    if manager is not None:
        _account_metrics(out, manager)

    pool = getattr(state, "pool", None)
    if pool is not None:
        pool_state = pool.state()
        fallback = (pool_state.get("fallback") or {}).get("active")
        out.gauge(
            "hydra_pool_spent",
            "1 when no account can answer.",
            1 if pool_state.get("spent") else 0,
        )
        out.gauge(
            "hydra_pool_fallback_active",
            "1 when queries are going to the fallback resolver.",
            1 if fallback else 0,
        )

    resolver = getattr(state, "resolver", None)
    if resolver is not None:
        budget = resolver.budget.snapshot()
        out.gauge(
            "hydra_budget_devices_breaching",
            "Devices that have spent their hourly budget.",
            len(budget.get("breaching") or []),
        )
        flight = resolver.flight.snapshot()
        out.counter(
            "hydra_inflight_shared_total",
            "Queries answered by joining a lookup already in flight.",
            flight.get("shared", 0),
        )
        out.gauge("hydra_inflight_active", "Lookups currently in flight.", flight.get("active", 0))
        guard = getattr(resolver, "rebind", None)
        if guard is not None:
            out.counter(
                "hydra_rebind_refused_total",
                "Upstream answers refused for pointing a public name at a private address.",
                guard.refused,
            )
        cloaks = getattr(resolver, "uncloak", None)
        if cloaks is not None:
            out.counter(
                "hydra_cname_uncloaked_total",
                "Answers blocked because a CNAME target is on a Hydra blocklist or app block.",
                cloaks.blocked,
            )

    prefetch = getattr(state, "prefetch", None)
    if prefetch is not None:
        snapshot = prefetch.snapshot()
        out.counter(
            "hydra_prefetch_warmed_total",
            "Entries warmed by the prefetcher.",
            snapshot.get("warmed", 0),
        )
        out.counter(
            "hydra_prefetch_runs_total", "Prefetcher passes completed.", snapshot.get("runs", 0)
        )

    devices = getattr(state, "devices", None)
    if devices is not None:
        out.gauge("hydra_registry_devices", "Devices in the registry.", len(devices.devices))
        since = time.time() - devices.last_sync_at if devices.last_sync_at else -1
        out.gauge(
            "hydra_registry_last_sync_seconds",
            "Seconds since the registry last synced, -1 when it never has.",
            max(-1.0, since),
        )

    lists = getattr(state, "blocklists", None)
    if lists is not None:
        _blocklist_metrics(out, lists)

    apps = getattr(state, "apps", None)
    if apps is not None:
        _app_metrics(out, apps)

    store = getattr(state, "metrics_store", None)
    if store is not None:
        snapshot = store.snapshot()
        out.gauge(
            "hydra_metrics_pending_queries",
            "Query rows waiting to be flushed to Postgres.",
            snapshot.get("pending_queries", 0),
        )
        out.counter(
            "hydra_metrics_flushes_total", "Metrics flushes completed.", snapshot.get("flushes", 0)
        )

    trail = getattr(state, "audit", None)
    if trail is not None:
        snapshot = trail.snapshot()
        out.counter(
            "hydra_audit_writes_total", "Audit entries persisted.", snapshot.get("writes", 0)
        )
        out.gauge(
            "hydra_audit_pending",
            "Audit entries waiting to be persisted.",
            snapshot.get("pending", 0),
        )

    alerts = getattr(state, "alerts", None)
    if alerts is not None:
        snapshot = alerts.snapshot()
        out.counter(
            "hydra_alerts_sent_total", "Alert deliveries attempted.", snapshot.get("sent", 0)
        )
        out.counter(
            "hydra_alerts_failed_total",
            "Alert deliveries that failed.",
            snapshot.get("failures", 0),
        )
        out.gauge(
            "hydra_alerts_active", "Conditions currently alerting.", snapshot.get("active", 0)
        )

    if certificate_days is not None:
        out.gauge(
            "hydra_certificate_days_left",
            "Days until the TLS certificate expires.",
            certificate_days,
        )

    out.gauge(
        "hydra_attention_reasons",
        "Number of reasons the service wants attention.",
        len(attention),
    )

    return out.text()


class RenderCache:
    def __init__(self) -> None:
        self._text = ""
        self._at = 0.0

    def get(self, build: Callable[[], str]) -> str:
        now = time.monotonic()
        if self._text and now - self._at < max(0, settings.metrics_cache_s):
            return self._text
        self._text = build()
        self._at = now
        return self._text
