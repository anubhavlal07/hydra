from __future__ import annotations

import sys
import time
from collections import deque

import pytest

from hydra_dns import telemetry
from hydra_dns.audit import IN_MEMORY_ENTRIES, AuditLog
from hydra_dns.config import settings
from hydra_dns.resolver.metrics import Metrics, QueryRecord

AUDIT_RING_MAX_KB = 400
SCRAPE_MAX_MS = 25.0
SCRAPE_MAX_KB = 32
CACHED_SCRAPE_MAX_MS = 1.0


def busy_metrics(devices: int = 200, queries: int = 5000) -> Metrics:
    metrics = Metrics()
    now = time.time()
    for index in range(queries):
        metrics.record(
            QueryRecord(
                at=now,
                device=f"device-{index % devices}",
                name=f"host{index % 800}.example.com",
                qtype="A",
                rcode="NOERROR",
                cached=bool(index % 2),
                blocked=index % 7 == 0,
                upstream="account-one",
                latency_ms=12.5,
            )
        )
    return metrics


class FakeState:
    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics


def retained_bytes(value: object, seen: set[int] | None = None) -> int:
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))

    total = sys.getsizeof(value)
    if isinstance(value, dict):
        for key, item in value.items():
            total += retained_bytes(key, seen) + retained_bytes(item, seen)
    elif isinstance(value, (list, tuple, set, frozenset, deque)):
        for item in value:
            total += retained_bytes(item, seen)
    elif hasattr(value, "__dict__"):
        total += retained_bytes(vars(value), seen)
    elif hasattr(value, "__slots__"):
        for slot in value.__slots__:
            total += retained_bytes(getattr(value, slot, None), seen)
    return total


def test_the_audit_ring_stays_small_enough_for_a_one_gig_host() -> None:
    trail = AuditLog(client=None)
    for index in range(IN_MEMORY_ENTRIES * 2):
        trail.record(
            actor="dashboard",
            address="203.0.113.10",
            action="device.rules",
            target=f"device-{index}",
            detail={"rules": ["||ads.example^"] * 5},
        )

    held_kb = retained_bytes(trail._entries) / 1024
    assert len(trail._entries) == IN_MEMORY_ENTRIES
    assert held_kb < AUDIT_RING_MAX_KB


def test_a_scrape_does_not_grow_with_the_device_count() -> None:
    few = telemetry.render(FakeState(busy_metrics(devices=5)), [])
    many = telemetry.render(FakeState(busy_metrics(devices=500)), [])

    capped = settings.metrics_top_devices
    assert many.count("hydra_device_queries_total{") == capped
    assert len(many) < SCRAPE_MAX_KB * 1024
    assert abs(len(many) - len(few)) < 4096


def test_a_scrape_never_carries_a_domain_series() -> None:
    text = telemetry.render(FakeState(busy_metrics()), [])
    assert "domain=" not in text


def test_a_scrape_is_cheap_enough_to_poll() -> None:
    state = FakeState(busy_metrics())
    telemetry.render(state, [])

    started = time.perf_counter()
    for _ in range(10):
        telemetry.render(state, [])
    per_scrape_ms = (time.perf_counter() - started) / 10 * 1000

    assert per_scrape_ms < SCRAPE_MAX_MS


def test_the_render_cache_makes_repeat_scrapes_free(monkeypatch) -> None:
    state = FakeState(busy_metrics())
    cache = telemetry.RenderCache()
    builds = 0

    def build() -> str:
        nonlocal builds
        builds += 1
        return telemetry.render(state, [])

    cache.get(build)
    started = time.perf_counter()
    for _ in range(200):
        cache.get(build)
    per_call_ms = (time.perf_counter() - started) / 200 * 1000

    assert builds == 1
    assert per_call_ms < CACHED_SCRAPE_MAX_MS


def test_the_render_cache_lets_go_once_it_is_stale(monkeypatch) -> None:
    import dataclasses

    monkeypatch.setattr(telemetry, "settings", dataclasses.replace(settings, metrics_cache_s=0))
    state = FakeState(busy_metrics(devices=5, queries=10))
    cache = telemetry.RenderCache()
    builds = 0

    def build() -> str:
        nonlocal builds
        builds += 1
        return telemetry.render(state, [])

    cache.get(build)
    cache.get(build)
    assert builds == 2


@pytest.mark.parametrize(
    ("name", "value", "ceiling"),
    [
        ("alert interval", "alert_every_s", None),
        ("export rows", "export_max_rows", 25_000),
        ("bulk devices", "bulk_max_devices", 100),
        ("export page", "export_page_rows", 500),
    ],
)
def test_the_defaults_suit_a_small_host(name: str, value: str, ceiling: int | None) -> None:
    setting = getattr(settings, value)
    if ceiling is not None:
        assert setting <= ceiling, name
    else:
        assert setting >= 300, name


def test_the_blocklist_series_are_bounded_by_the_list_ceiling() -> None:
    from hydra_dns.blocklists import Blocklist, Blocklists, ListState, parse

    lists = Blocklists(None, None)
    for index in range(settings.blocklist_max * 3):
        state = ListState(
            meta=Blocklist(id=f"list-{index}", name=f"List {index}", url="https://l.test/")
        )
        state.hold(parse(f"host{index}.example.com\n"))
        state.hits = index
        lists._states[state.meta.id] = state

    class State:
        blocklists = lists

    text = telemetry.render(State(), [])

    assert text.count("hydra_blocklist_hits_total{") == settings.blocklist_max
    assert text.count("hydra_blocklist_names{") == settings.blocklist_max
    assert "domain=" not in text
    assert "example.com" not in text


def test_the_blocklist_ceilings_suit_a_small_host() -> None:
    assert settings.blocklist_max <= 8
    assert settings.blocklist_max_names <= 400_000
    assert settings.blocklist_max_bytes <= 20 * 1024 * 1024
    assert settings.blocklist_refresh_s >= 3600


def test_everything_costly_is_off_until_it_is_asked_for() -> None:
    assert settings.audit_persist is False
    assert settings.metrics_persist is False
    assert settings.log_json is False
    assert settings.alert_webhook == ""


class _Accounts:
    def snapshot(self) -> list[dict]:
        return [
            {"label": "alpha", "requests_used": 1234, "requests_limit": 300000, "upstream": {}},
            {"label": "bravo", "requests_used": -1, "requests_limit": -1, "upstream": {}},
        ]


def test_account_request_gauges_read_the_account_counts() -> None:
    out = telemetry.Renderer()
    telemetry._account_metrics(out, _Accounts())
    text = out.text()
    assert 'hydra_account_requests_used{account="alpha"} 1234' in text
    assert 'hydra_account_requests_limit{account="alpha"} 300000' in text
    assert 'hydra_account_requests_used{account="bravo"}' not in text
    assert 'hydra_account_requests_limit{account="bravo"}' not in text


def test_the_quota_saved_series_are_a_fixed_small_set() -> None:
    from hydra_dns.resolver.savings import CAUSES

    metrics = busy_metrics()
    for index in range(10_000):
        metrics.savings.note(CAUSES[index % len(CAUSES)])
        metrics.savings.note(f"cause-{index}")

    text = telemetry.render(FakeState(metrics), [])

    assert text.count("hydra_quota_saved_total{") == len(CAUSES)
    assert len(metrics.savings.export()) == len(CAUSES) + 2
    assert retained_bytes(metrics.savings._counts) < 2048


def test_the_rebind_guard_is_one_series_and_does_not_grow_with_refusals() -> None:
    import dns.message
    import dns.rdatatype
    import dns.rrset

    from hydra_dns.rebind import MAX_ALLOW, RebindGuard

    guard = RebindGuard(allow=[f"s{i}.test" for i in range(MAX_ALLOW * 4)])
    before = retained_bytes(guard)
    for index in range(5000):
        name = f"host{index}.evil.example"
        response = dns.message.make_response(dns.message.make_query(name, dns.rdatatype.A))
        response.answer.append(dns.rrset.from_text(f"{name}.", 0, "IN", "A", "10.0.0.1"))
        if guard.refuses(name, response):
            guard.note()

    class Snapshots:
        @staticmethod
        def snapshot() -> dict:
            return {}

    class Resolver:
        rebind = guard
        budget = Snapshots()
        flight = Snapshots()

    class State:
        resolver = Resolver()

    text = telemetry.render(State(), [])

    assert guard.refused == 5000
    assert retained_bytes(guard) <= before + 64
    assert len(guard.allow) == MAX_ALLOW
    assert [line for line in text.splitlines() if line.startswith("hydra_rebind")] == [
        "hydra_rebind_refused_total 5000"
    ]
    assert "evil.example" not in text
