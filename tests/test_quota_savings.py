from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

import dns.message
import dns.rdatatype
import httpx
import pytest
import respx
from conftest import make_answer
from test_blocklist_resolver import CountingPool, ask, build, store_with, with_list
from test_doh import client, mock_upstream
from test_footprint import retained_bytes
from test_metrics_persistence import RPC, configure

from hydra_dns import telemetry
from hydra_dns.metrics_store import MetricsStore
from hydra_dns.resolver import savings
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.prefetch import Prefetcher
from hydra_dns.resolver.savings import CAUSES, QuotaSavings, month_of
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamPool
from hydra_dns.store import Device

SEPT = dt.datetime(2026, 9, 25, 12, tzinfo=dt.UTC).timestamp()
OCT = dt.datetime(2026, 10, 1, 0, 0, 1, tzinfo=dt.UTC).timestamp()


class Clock:
    def __init__(self, at: float) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def test_the_month_is_the_utc_calendar_month_adguard_resets_on() -> None:
    month, since, until = month_of(SEPT)
    assert month == 202609
    assert since == dt.datetime(2026, 9, 1, tzinfo=dt.UTC).timestamp()
    assert until == dt.datetime(2026, 10, 1, tzinfo=dt.UTC).timestamp()
    assert month_of(dt.datetime(2026, 12, 31, 23, 59, tzinfo=dt.UTC).timestamp())[0] == 202612
    assert month_of(dt.datetime(2027, 1, 1, tzinfo=dt.UTC).timestamp())[0] == 202701


async def test_each_cause_is_counted_where_hydra_answers_itself() -> None:
    store = store_with(
        Device(id="tv", name="TV"),
        Device(id="laptop", name="Laptop", rules=("ads.example",)),
    )
    resolver, pool, metrics = build(store, with_list(store))

    await resolver.resolve(ask("tracker.test"), "tv")
    await resolver.resolve(ask("ads.example"), "laptop")
    await resolver.resolve(ask("github.com"), "tv")
    await resolver.resolve(ask("github.com"), "tv")
    await resolver.resolve(ask("github.com"), "laptop")

    assert pool.forwarded == 1
    assert metrics.savings.by_cause() == {
        "app": 0,
        "blocklist": 1,
        "rule": 1,
        "cache": 2,
        "coalesced": 0,
    }


async def test_local_answers_are_not_counted_as_saved() -> None:
    store = store_with(Device(id="tv", name="TV"))
    resolver, pool, metrics = build(store, with_list(store))

    await resolver.resolve(ask("localhost"), "tv")
    await resolver.resolve(ask("anything.home.arpa"), "tv")

    assert pool.forwarded == 0
    assert sum(metrics.savings.by_cause().values()) == 0


async def test_an_answer_that_went_upstream_saves_nothing() -> None:
    store = store_with(Device(id="tv", name="TV"))
    resolver, pool, metrics = build(store, with_list(store))

    await resolver.resolve(ask("one.test"), "tv")
    await resolver.resolve(ask("two.test"), "tv")

    assert pool.forwarded == 2
    assert sum(metrics.savings.by_cause().values()) == 0


async def test_joining_a_lookup_in_flight_counts_as_coalesced() -> None:
    upstream = "https://upstream.test/dns-query"
    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="one", doh_url=upstream)])
    metrics = Metrics()
    resolver = Resolver(AnswerCache(100, 30, 3600), pool, metrics)
    held = asyncio.Event()

    async def slow(request):
        await held.wait()
        return httpx.Response(200, content=make_answer()[1])

    wire = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
    with respx.mock:
        respx.post(upstream).mock(side_effect=slow)
        waiting = [asyncio.create_task(resolver.resolve(wire, "d")) for _ in range(4)]
        await asyncio.sleep(0.05)
        held.set()
        await asyncio.gather(*waiting)

    assert metrics.savings.by_cause()["coalesced"] == 3


async def test_prefetch_is_counted_as_the_cost_it_is() -> None:
    metrics = Metrics()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600)
    pool = CountingPool()
    prefetch = Prefetcher(cache, pool, metrics)

    assert await prefetch._renew(("example.com.", 1, 1, False)) is True
    snapshot = metrics.savings.snapshot(1000)

    assert snapshot["prefetch_cost"] == 1
    assert snapshot["net"] == 0


def test_the_net_figure_is_measured_against_the_pooled_allowance() -> None:
    counted = QuotaSavings(Clock(SEPT))
    for _ in range(50):
        counted.note(savings.CACHE)
    for _ in range(10):
        counted.note(savings.BLOCKLIST)
    for _ in range(5):
        counted.note_prefetch()

    snapshot = counted.snapshot(1000)

    assert snapshot["month"] == "2026-09"
    assert snapshot["gross"] == 60
    assert snapshot["net"] == 55
    assert snapshot["fraction"] == 0.055
    assert counted.snapshot(0)["fraction"] is None
    assert counted.snapshot(0)["allowance"] is None


def test_the_count_resets_at_the_month_boundary() -> None:
    clock = Clock(SEPT)
    counted = QuotaSavings(clock)
    counted.note(savings.RULE)
    counted.note_prefetch()

    clock.at = OCT
    assert counted.by_cause()[savings.RULE] == 0
    counted.note(savings.CACHE)

    snapshot = counted.snapshot()
    assert snapshot["month"] == "2026-10"
    assert snapshot["gross"] == 1
    assert snapshot["prefetch_cost"] == 0


def test_a_clock_stepping_back_does_not_wipe_the_month() -> None:
    clock = Clock(OCT)
    counted = QuotaSavings(clock)
    counted.note(savings.CACHE)

    clock.at = SEPT
    counted.note(savings.CACHE)

    assert counted.by_cause()[savings.CACHE] == 2


def test_the_count_round_trips_through_the_persisted_totals() -> None:
    clock = Clock(SEPT)
    before = Metrics()
    before.savings = QuotaSavings(clock)
    for cause in CAUSES:
        before.savings.note(cause)
    before.savings.note_prefetch()
    state = before.export_state()

    assert state["totals"]["saved_month"] == 202609
    assert all(isinstance(v, int) for v in state["totals"].values())

    after = Metrics()
    after.savings = QuotaSavings(clock)
    after.savings.note(savings.CACHE)
    after.restore(state)

    assert after.savings.restored is True
    assert after.savings.by_cause() == {
        "app": 1,
        "blocklist": 1,
        "rule": 1,
        "cache": 2,
        "coalesced": 1,
    }
    assert after.savings.snapshot()["prefetch_cost"] == 1


def test_a_persisted_count_from_last_month_is_not_carried_over() -> None:
    clock = Clock(SEPT)
    before = QuotaSavings(clock)
    before.note(savings.CACHE)
    totals = before.export()

    clock.at = OCT
    after = QuotaSavings(clock)
    assert after.merge(totals) is False
    assert after.restored is True
    assert sum(after.by_cause().values()) == 0


def test_merging_twice_does_not_double_the_count() -> None:
    counted = QuotaSavings(Clock(SEPT))
    totals = {"saved_month": 202609, "saved_cache": 7}

    counted.merge(totals)
    counted.merge(totals)

    assert counted.by_cause()[savings.CACHE] == 7


def test_a_malformed_persisted_count_reads_as_zero() -> None:
    counted = QuotaSavings(Clock(SEPT))
    counted.merge({"saved_month": 202609, "saved_cache": "lots", "saved_rule": -4})
    assert sum(counted.by_cause().values()) == 0


@respx.mock
async def test_a_failed_load_never_writes_over_the_persisted_count(monkeypatch) -> None:
    configure(monkeypatch)
    load = respx.post(f"{RPC}/hydra_metrics_load").mock(side_effect=httpx.ConnectError("down"))
    flush = respx.post(f"{RPC}/hydra_metrics_flush").mock(return_value=httpx.Response(204))

    metrics = Metrics()
    store = MetricsStore(httpx.AsyncClient(), metrics)
    await store.load()
    metrics.savings.note(savings.CACHE)

    assert await store.flush() is True
    sent = json.loads(flush.calls.last.request.content)["p_totals"]
    assert not any(key.startswith("saved_") for key in sent)
    assert metrics.savings.restored is False
    assert load.call_count == 2


@respx.mock
async def test_a_later_load_adds_the_persisted_count_to_this_run(monkeypatch) -> None:
    configure(monkeypatch)
    month = month_of(time.time())[0]
    respx.post(f"{RPC}/hydra_metrics_load").mock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(200, json={"totals": {"saved_month": month, "saved_cache": 40}}),
        ]
    )
    flush = respx.post(f"{RPC}/hydra_metrics_flush").mock(return_value=httpx.Response(204))

    metrics = Metrics()
    store = MetricsStore(httpx.AsyncClient(), metrics)
    await store.load()
    metrics.savings.note(savings.CACHE)
    await store.flush()

    sent = json.loads(flush.calls.last.request.content)["p_totals"]
    assert sent["saved_cache"] == 41
    assert sent["saved_month"] == month
    assert metrics.savings.restored is True


@respx.mock
async def test_a_successful_empty_load_is_authoritative(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_metrics_load").mock(
        return_value=httpx.Response(200, json={"totals": {}})
    )
    flush = respx.post(f"{RPC}/hydra_metrics_flush").mock(return_value=httpx.Response(204))

    metrics = Metrics()
    store = MetricsStore(httpx.AsyncClient(), metrics)
    await store.load()
    metrics.savings.note(savings.RULE)
    await store.flush()

    sent = json.loads(flush.calls.last.request.content)["p_totals"]
    assert sent["saved_rule"] == 1


def test_the_scrape_carries_one_series_per_cause() -> None:
    metrics = Metrics()
    metrics.savings.note(savings.BLOCKLIST)
    text = telemetry.render(type("S", (), {"metrics": metrics})(), [])

    assert text.count("hydra_quota_saved_total{") == len(CAUSES)
    assert 'hydra_quota_saved_total{cause="blocklist"} 1' in text
    assert "hydra_quota_prefetch_spent_total 0" in text


@pytest.mark.parametrize("notes", [10, 100_000])
def test_the_count_holds_the_same_footprint_however_busy(notes: int) -> None:
    counted = QuotaSavings(Clock(SEPT))
    for index in range(notes):
        counted.note(CAUSES[index % len(CAUSES)])
        counted.note(f"made-up-{index}")

    assert set(counted.by_cause()) == set(CAUSES)
    assert retained_bytes(counted._counts) < 2048
    assert len(counted.export()) == len(CAUSES) + 2


@respx.mock
async def test_the_overview_reports_the_month_against_the_pooled_allowance() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        await http.post("/api/login", json={"password": "hunter2"})
        for account in app.state.manager.accounts:
            account.limits = {"requests": {"used": 1_000, "limit": 300_000}}
        wire = dns.message.make_query("cached.example", dns.rdatatype.A).to_wire()
        await app.state.resolver.resolve(wire, "laptop")
        await app.state.resolver.resolve(wire, "laptop")

        body = (await http.get("/api/overview")).json()

    saved = body["saved"]
    allowance = body["quota"]["requests_limit"]
    assert saved["by_cause"]["cache"] >= 1
    assert saved["month"] == time.strftime("%Y-%m", time.gmtime())
    assert saved["allowance"] == allowance
    assert saved["fraction"] == round(saved["net"] / allowance, 4)
    assert saved["partial"] is False
