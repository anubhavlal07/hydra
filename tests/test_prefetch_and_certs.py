from __future__ import annotations

import dataclasses
import datetime as dt
import time
from pathlib import Path

import dns.message
import dns.rdatatype
import httpx
import respx
from conftest import make_answer
from test_doh import UPSTREAM
from test_dot import write_wildcard_cert

from hydra_dns import certs, watch
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics, QueryRecord
from hydra_dns.resolver.prefetch import Prefetcher
from hydra_dns.resolver.upstream import Upstream, UpstreamPool


def test_the_expiry_comes_out_of_the_certificate(tmp_path: Path) -> None:
    cert_file, _ = write_wildcard_cert(tmp_path)
    certs._CACHE.clear()

    snapshot = certs.snapshot(cert_file)
    assert snapshot["known"] is True
    assert 0.9 < snapshot["days_left"] < 1.1

    when = dt.datetime.fromtimestamp(snapshot["expires_at"], dt.UTC)
    assert when > dt.datetime.now(dt.UTC)


def test_an_unreadable_certificate_is_unknown_rather_than_an_error(tmp_path: Path) -> None:
    certs._CACHE.clear()
    assert certs.snapshot(str(tmp_path / "nothing.pem")) == {
        "known": False,
        "expires_at": None,
        "days_left": None,
    }
    assert certs.snapshot("")["known"] is False


def test_rubbish_in_place_of_a_certificate_is_unknown(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pem"
    junk.write_text("-----BEGIN CERTIFICATE-----\nbm90IGEgY2VydA==\n-----END CERTIFICATE-----\n")
    certs._CACHE.clear()
    assert certs.snapshot(str(junk))["known"] is False


def test_an_expiring_certificate_is_worth_attention() -> None:
    found = watch.reasons(
        accounts=[{"label": "one", "auth": {"authenticated": True}}],
        quota={"requests_remaining": 100, "requests_used": 0},
        pool_state={"spent": False},
        warn_days=5,
        certificate={"known": True, "days_left": 9, "expires_at": 0},
        cert_warn_days=14,
    )
    assert any("expires in 9 days" in r for r in found)


def test_an_expired_certificate_says_so_plainly() -> None:
    found = watch.reasons(
        accounts=[{"label": "one", "auth": {"authenticated": True}}],
        quota={"requests_remaining": 100, "requests_used": 0},
        pool_state={"spent": False},
        warn_days=5,
        certificate={"known": True, "days_left": -2, "expires_at": 0},
    )
    assert any("has expired" in r for r in found)


def test_an_account_that_keeps_failing_is_named_before_it_drops_out() -> None:
    found = watch.reasons(
        accounts=[
            {"label": "one", "auth": {"authenticated": True, "consecutive_failures": 4}},
            {"label": "two", "auth": {"authenticated": True, "consecutive_failures": 0}},
        ],
        quota={"requests_remaining": 100, "requests_used": 0},
        pool_state={"spent": False},
        warn_days=5,
        warn_failures=3,
    )
    assert any("one keeps failing to renew" in r for r in found)


def test_a_healthy_pool_has_nothing_to_report() -> None:
    assert (
        watch.reasons(
            accounts=[{"label": "one", "auth": {"authenticated": True}}],
            quota={"requests_remaining": 100, "requests_used": 0},
            pool_state={"spent": False},
            warn_days=5,
            certificate={"known": True, "days_left": 60, "expires_at": 0},
        )
        == []
    )


def prefetch_setup(monkeypatch, **overrides):
    from hydra_dns.resolver import prefetch as module

    monkeypatch.setattr(module, "settings", dataclasses.replace(module.settings, **overrides))

    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600)
    pool = UpstreamPool(strategy="round_robin", timeout_s=2.0, fallback_url="")
    pool.set_upstreams([Upstream(label="mock", doh_url=UPSTREAM)])
    pool.attach_client(httpx.AsyncClient())
    metrics = Metrics()
    return cache, pool, metrics, Prefetcher(cache, pool, metrics)


def record(name: str) -> QueryRecord:
    return QueryRecord(
        at=time.time(),
        device="laptop",
        name=name,
        qtype="A",
        rcode="NOERROR",
        cached=False,
        blocked=False,
        upstream="mock",
        latency_ms=1.0,
    )


@respx.mock
async def test_a_hot_answer_about_to_expire_is_renewed(monkeypatch) -> None:
    cache, pool, metrics, prefetcher = prefetch_setup(
        monkeypatch, prefetch_min_hits=2, prefetch_window_s=600
    )

    response, wire = make_answer(ttl=5)
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=wire))

    query = dns.message.make_query("example.com", dns.rdatatype.A)
    key = cache.key_for(query)
    cache.put(key, response, wire)
    for _ in range(3):
        cache.get(key, 0)

    assert await prefetcher.run() == 1
    assert prefetcher.renewed == 1
    await pool._client.aclose()


@respx.mock
async def test_an_answer_read_too_few_times_is_left_to_expire(monkeypatch) -> None:
    cache, pool, metrics, prefetcher = prefetch_setup(
        monkeypatch, prefetch_min_hits=5, prefetch_window_s=600
    )

    response, wire = make_answer(ttl=5)
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=wire))

    query = dns.message.make_query("example.com", dns.rdatatype.A)
    key = cache.key_for(query)
    cache.put(key, response, wire)
    cache.get(key, 0)

    assert await prefetcher.run() == 0
    assert prefetcher.renewed == 0
    await pool._client.aclose()


@respx.mock
async def test_a_popular_domain_nobody_reads_any_more_is_not_renewed(monkeypatch) -> None:
    cache, pool, metrics, prefetcher = prefetch_setup(
        monkeypatch, prefetch_min_hits=2, prefetch_window_s=600
    )

    response, wire = make_answer(ttl=5)
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=wire))
    for _ in range(50):
        metrics.record(record("example.com"))

    query = dns.message.make_query("example.com", dns.rdatatype.A)
    cache.put(cache.key_for(query), response, wire)

    assert await prefetcher.run() == 0
    assert route.call_count == 0
    await pool._client.aclose()


@respx.mock
async def test_a_renewed_answer_must_be_read_again_to_be_renewed_again(monkeypatch) -> None:
    cache, pool, metrics, prefetcher = prefetch_setup(
        monkeypatch, prefetch_min_hits=2, prefetch_window_s=600
    )

    response, wire = make_answer(ttl=5)
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=wire))

    query = dns.message.make_query("example.com", dns.rdatatype.A)
    key = cache.key_for(query)
    cache.put(key, response, wire)
    for _ in range(2):
        cache.get(key, 0)

    assert await prefetcher.run() == 1
    assert await prefetcher.run() == 0
    assert route.call_count == 1

    for _ in range(2):
        cache.get(key, 0)
    assert await prefetcher.run() == 1
    assert route.call_count == 2
    await pool._client.aclose()


@respx.mock
async def test_nothing_is_renewed_before_the_pool_is_ready(monkeypatch) -> None:
    cache, pool, metrics, prefetcher = prefetch_setup(monkeypatch, prefetch_min_hits=1)
    pool.set_upstreams([])
    for _ in range(3):
        metrics.record(record("example.com"))

    assert await prefetcher.run() == 0
    await pool._client.aclose()


def test_the_cache_reports_what_is_about_to_expire() -> None:
    cache = AnswerCache(max_entries=10, min_ttl=1, max_ttl=3600)
    response, wire = make_answer(ttl=30)
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    cache.put(cache.key_for(query), response, wire)

    assert cache.due(within=5) == []
    assert len(cache.due(within=120)) == 1
    assert cache.due(within=120, min_reads=1) == []
