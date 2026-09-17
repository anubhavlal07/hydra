from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx
from conftest import make_answer

from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics, QueryRecord
from hydra_dns.resolver.prefetch import Prefetcher
from hydra_dns.resolver.upstream import Upstream, UpstreamPool

UPSTREAM = "https://upstream.test/dns-query"


def pool() -> UpstreamPool:
    client = httpx.AsyncClient()
    built = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    built.attach_client(client)
    built.set_upstreams([Upstream(label="one", doh_url=UPSTREAM)])
    return built


def metrics_with(domains: dict[str, int]) -> Metrics:
    metrics = Metrics()
    metrics.restore(
        {
            "totals": {},
            "domains": [{"domain": d, "queries": n, "blocked": 0} for d, n in domains.items()],
        }
    )
    return metrics


@pytest.fixture
def warm_settings(monkeypatch):
    from hydra_dns.resolver import prefetch as module

    def apply(**changes):
        monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, **changes))

    return apply


@respx.mock
async def test_the_top_domains_are_resolved_at_startup(warm_settings) -> None:
    warm_settings(cache_warm_count=3)
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    prefetcher = Prefetcher(cache, pool(), metrics_with({"a.test": 90, "b.test": 40}))

    assert await prefetcher.warm() == 2
    assert prefetcher.warmed == 2
    assert len(cache) == 2


@respx.mock
async def test_warming_stops_at_the_configured_count(warm_settings) -> None:
    warm_settings(cache_warm_count=2)
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    domains = {f"host{n}.test": 100 - n for n in range(10)}
    prefetcher = Prefetcher(cache, pool(), metrics_with(domains))

    assert await prefetcher.warm() == 2


@respx.mock
async def test_a_cache_that_came_back_from_disk_is_not_warmed_again(warm_settings) -> None:
    warm_settings(cache_warm_count=2)
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    for name in ("x.test", "y.test", "z.test"):
        response, wire = make_answer(name=name, ttl=300)
        import dns.message
        import dns.rdatatype

        key = AnswerCache.key_for(dns.message.make_query(name, dns.rdatatype.A))
        cache.put(key, response, wire)

    prefetcher = Prefetcher(cache, pool(), metrics_with({"a.test": 90}))

    assert await prefetcher.warm() == 0
    assert route.call_count == 0


@respx.mock
async def test_warming_is_off_when_the_count_is_zero(warm_settings) -> None:
    warm_settings(cache_warm_count=0)
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    prefetcher = Prefetcher(
        AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600),
        pool(),
        metrics_with({"a.test": 90}),
    )

    assert await prefetcher.warm() == 0
    assert route.call_count == 0


@respx.mock
async def test_nothing_is_warmed_while_no_account_can_answer(warm_settings) -> None:
    warm_settings(cache_warm_count=3)

    empty = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    empty.attach_client(httpx.AsyncClient())

    prefetcher = Prefetcher(
        AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600),
        empty,
        metrics_with({"a.test": 90}),
    )

    assert await prefetcher.warm() == 0


@respx.mock
async def test_the_warm_count_is_reported(warm_settings) -> None:
    warm_settings(cache_warm_count=3)
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    prefetcher = Prefetcher(
        AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600),
        pool(),
        metrics_with({"a.test": 90}),
    )
    await prefetcher.warm()

    assert prefetcher.snapshot()["warmed"] == 1


def test_a_query_record_still_feeds_the_top_domains() -> None:
    metrics = Metrics()
    metrics.record(
        QueryRecord(
            at=0,
            device="d",
            name="counted.test",
            qtype="A",
            rcode="NOERROR",
            cached=False,
            blocked=False,
            upstream="one",
            latency_ms=1.0,
        )
    )
    assert metrics.top_domains(limit=5)[0]["domain"] == "counted.test"
