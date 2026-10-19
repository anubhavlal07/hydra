from __future__ import annotations

import httpx
import pytest
import respx
from conftest import make_answer

from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamError, UpstreamPool

PRIMARY = "https://primary.test/dns-query"
FALLBACK = "https://fallback.test/dns-query"


def make_pool(fallback: str = FALLBACK, **kwargs) -> UpstreamPool:
    pool = UpstreamPool(strategy="quota_weighted", timeout_s=5.0, fallback_url=fallback)
    pool.set_upstreams([Upstream(label="one", doh_url=PRIMARY, **kwargs)])
    return pool


def exhausted_pool(fallback: str = FALLBACK) -> UpstreamPool:
    return make_pool(fallback, quota_used=300_000, quota_limit=300_000)


def mock(url: str, wire: bytes | None = None, status: int = 200) -> None:
    if wire is None:
        _, wire = make_answer()
    respx.post(url).mock(return_value=httpx.Response(status, content=wire))


def test_a_pool_with_quota_left_is_ready():
    assert make_pool().ready is True


def test_an_exhausted_pool_is_not_ready():
    pool = exhausted_pool()
    assert pool.ready is False
    assert pool.spent is True


def test_a_benched_pool_is_not_ready():
    pool = make_pool()
    for _ in range(5):
        pool.get("one").record_failure("boom")
    assert pool.ready is False
    assert pool.spent is True


def test_a_pool_with_no_upstreams_is_not_spent():
    pool = UpstreamPool(strategy="quota_weighted", timeout_s=5.0, fallback_url=FALLBACK)
    assert pool.ready is False
    assert pool.spent is False


def test_an_undiscovered_upstream_does_not_count_as_ready():
    pool = UpstreamPool(strategy="quota_weighted", timeout_s=5.0, fallback_url=FALLBACK)
    pool.set_upstreams([Upstream(label="one", doh_url="")])
    assert pool.ready is False


def test_readiness_before_quotas_are_known():
    pool = make_pool()
    assert pool.get("one").quota_limit == -1
    assert pool.ready is True


@respx.mock
async def test_a_healthy_pool_never_touches_the_fallback():
    mock(PRIMARY)
    route = respx.post(FALLBACK)
    pool = make_pool()
    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        _, upstream, _ = await pool.forward(b"\x00")
    assert upstream.label == "one"
    assert route.call_count == 0


@respx.mock
async def test_an_exhausted_pool_answers_from_the_fallback():
    primary = respx.post(PRIMARY)
    mock(FALLBACK)
    pool = exhausted_pool()
    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        _, upstream, _ = await pool.forward(b"\x00")
    assert upstream.is_fallback is True
    assert primary.call_count == 0


@respx.mock
async def test_the_fallback_catches_a_total_failure_too():
    mock(PRIMARY, status=500)
    mock(FALLBACK)
    pool = make_pool()
    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        _, upstream, _ = await pool.forward(b"\x00")
    assert upstream.is_fallback is True


@respx.mock
async def test_without_a_fallback_an_exhausted_pool_still_fails():
    mock(PRIMARY, status=500)
    pool = exhausted_pool(fallback="")
    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        with pytest.raises(UpstreamError):
            await pool.forward(b"\x00")
    assert pool.fallback is None
    assert pool.fallback_active is False


def test_fallback_active_only_when_the_pool_is_spent():
    assert make_pool().fallback_active is False
    assert exhausted_pool().fallback_active is True
    assert exhausted_pool(fallback="").fallback_active is False


@respx.mock
async def test_a_fallback_answer_is_not_cached():
    mock(FALLBACK)
    pool = exhausted_pool()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=300)
    resolver = Resolver(cache, pool, Metrics())
    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        import dns.message
        import dns.rdatatype

        wire = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
        result = await resolver.resolve(wire, "device")

    assert result.upstream == "fallback"
    assert cache.snapshot()["entries"] == 0


@respx.mock
async def test_a_normal_answer_is_still_cached():
    mock(PRIMARY)
    pool = make_pool()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=300)
    resolver = Resolver(cache, pool, Metrics())
    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        import dns.message
        import dns.rdatatype

        wire = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
        await resolver.resolve(wire, "device")

    assert cache.snapshot()["entries"] == 1


def test_state_reports_the_fallback():
    state = exhausted_pool().state()
    assert state["ready"] is False
    assert state["spent"] is True
    assert state["available"] == 0
    assert state["configured"] == 1
    assert state["fallback"]["active"] is True
    assert state["fallback"]["url"] == FALLBACK


def test_clearing_a_bench_makes_an_upstream_usable_again():
    pool = make_pool()
    for _ in range(6):
        pool.get("one").record_failure("boom")
    assert pool.ready is False

    pool.get("one").clear_bench()
    assert pool.ready is True


@respx.mock
async def test_the_fallback_failing_falls_through_to_a_primary():
    mock(FALLBACK, status=500)
    mock(PRIMARY)
    pool = make_pool()
    for _ in range(6):
        pool.get("one").record_failure("boom")
    assert pool.ready is False

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        _, upstream, _ = await pool.forward(b"\x00")

    assert upstream.label == "one"


@respx.mock
async def test_a_benched_fallback_is_skipped_for_unfiltered_queries():
    fallback = respx.post(FALLBACK).mock(return_value=httpx.Response(500))
    mock(PRIMARY)
    pool = make_pool()
    for _ in range(3):
        pool.fallback.record_failure("ConnectTimeout")

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        _, upstream, _ = await pool.forward_unfiltered(b"\x00")

    assert upstream.label == "one"
    assert fallback.call_count == 0


@respx.mock
async def test_unfiltered_queries_survive_a_long_dead_fallback():
    mock(FALLBACK, status=500)
    mock(PRIMARY)
    pool = make_pool()
    pool.fallback.consecutive_failures = 5000

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        _, upstream, _ = await pool.forward_unfiltered(b"\x00")

    assert upstream.label == "one"


def test_a_benched_upstream_is_retried_within_two_minutes():
    from hydra_dns.resolver.upstream import MAX_COOLDOWN_S

    pool = make_pool()
    for _ in range(40):
        pool.get("one").record_failure("boom")

    assert MAX_COOLDOWN_S <= 120.0
    remaining = pool.get("one").cooldown_until - __import__("time").monotonic()
    assert remaining <= MAX_COOLDOWN_S


def test_the_empty_pool_log_names_the_fallback(caplog):
    import logging

    import httpx

    from hydra_dns.adguard.manager import AccountManager
    from hydra_dns.config import AccountConfig

    pool = UpstreamPool(strategy="quota_weighted", timeout_s=5.0, fallback_url=FALLBACK)
    manager = AccountManager(pool, httpx.AsyncClient())
    manager.configure([AccountConfig(label="one", username="a@b.test", password="p")])

    with caplog.at_level(logging.ERROR):
        manager._track_emptiness()

    assert "SERVFAIL" not in caplog.text
    assert FALLBACK in caplog.text
    assert "unfiltered" in caplog.text


def test_the_empty_pool_log_still_says_servfail_without_a_fallback(caplog):
    import logging

    import httpx

    from hydra_dns.adguard.manager import AccountManager
    from hydra_dns.config import AccountConfig

    pool = UpstreamPool(strategy="quota_weighted", timeout_s=5.0, fallback_url="")
    manager = AccountManager(pool, httpx.AsyncClient())
    manager.configure([AccountConfig(label="one", username="a@b.test", password="p")])

    with caplog.at_level(logging.ERROR):
        manager._track_emptiness()

    assert "SERVFAIL" in caplog.text
