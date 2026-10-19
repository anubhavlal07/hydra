from __future__ import annotations

from collections import Counter

import httpx
import pytest
import respx

from hydra_dns.resolver.upstream import (
    BASE_COOLDOWN_S,
    FAILURE_THRESHOLD,
    Upstream,
    UpstreamError,
    UpstreamPool,
)


def make_pool(strategy: str = "round_robin", count: int = 3) -> UpstreamPool:
    pool = UpstreamPool(strategy=strategy, timeout_s=2.0)
    pool.set_upstreams(
        [
            Upstream(label=f"acct{i}", doh_url=f"https://acct{i}.test/dns-query")
            for i in range(count)
        ]
    )
    return pool


def test_round_robin_spreads_evenly() -> None:
    pool = make_pool("round_robin", count=3)
    picks = Counter(pool.order()[0].label for _ in range(300))
    assert set(picks) == {"acct0", "acct1", "acct2"}
    assert set(picks.values()) == {100}


def test_order_lists_every_upstream_so_failover_has_somewhere_to_go() -> None:
    pool = make_pool(count=3)
    assert len(pool.order()) == 3


def test_quota_weighting_favours_the_account_with_headroom() -> None:
    pool = make_pool("quota_weighted", count=2)
    rich, poor = pool.upstreams
    rich.quota_limit, rich.quota_used = 300_000, 0
    poor.quota_limit, poor.quota_used = 300_000, 270_000

    picks = Counter(pool.order()[0].label for _ in range(100))
    assert picks[rich.label] > picks[poor.label] * 5


def test_weighting_degrades_to_even_before_limits_are_known() -> None:
    pool = make_pool("quota_weighted", count=3)
    picks = Counter(pool.order()[0].label for _ in range(300))
    assert max(picks.values()) - min(picks.values()) <= 1


def test_exhausted_accounts_are_skipped() -> None:
    pool = make_pool("quota_weighted", count=2)
    spent, fresh = pool.upstreams
    spent.quota_limit, spent.quota_used = 300_000, 300_000
    fresh.quota_limit, fresh.quota_used = 300_000, 10

    assert spent.exhausted
    assert pool.order()[0].label == fresh.label


def test_every_account_spent_still_returns_candidates() -> None:
    pool = make_pool(count=2)
    for upstream in pool.upstreams:
        upstream.quota_limit, upstream.quota_used = 100, 100
    assert len(pool.order()) == 2


def test_least_used_picks_the_lowest_fraction() -> None:
    pool = make_pool("least_used", count=3)
    a, b, c = pool.upstreams
    for upstream, used in ((a, 90), (b, 10), (c, 50)):
        upstream.quota_limit, upstream.quota_used = 100, used
    assert pool.order()[0].label == b.label


def test_one_failure_does_not_bench_an_upstream() -> None:
    upstream = Upstream(label="a", doh_url="https://a.test")
    upstream.record_failure("connection reset")
    assert upstream.cooldown_until == 0.0


def test_repeated_failures_bench_it() -> None:
    upstream = Upstream(label="a", doh_url="https://a.test")
    for _ in range(FAILURE_THRESHOLD):
        upstream.record_failure("timeout")
    assert upstream.cooldown_until > 0


def test_cooldown_backs_off_exponentially() -> None:
    upstream = Upstream(label="a", doh_url="https://a.test")
    for _ in range(FAILURE_THRESHOLD):
        upstream.record_failure("timeout")
    first = upstream.cooldown_until
    upstream.record_failure("timeout")
    second = upstream.cooldown_until
    assert second - first >= BASE_COOLDOWN_S


def test_a_long_failure_streak_never_overflows_the_cooldown() -> None:
    upstream = Upstream(label="a", doh_url="https://a.test")
    for _ in range(5000):
        upstream.record_failure("timeout")
    assert upstream.consecutive_failures == 5000
    assert upstream.cooldown_until > 0


def test_success_clears_the_breaker() -> None:
    upstream = Upstream(label="a", doh_url="https://a.test")
    for _ in range(FAILURE_THRESHOLD):
        upstream.record_failure("timeout")
    upstream.record_success(12.0)
    assert upstream.cooldown_until == 0.0
    assert upstream.consecutive_failures == 0


def test_benched_upstreams_are_demoted_not_dropped() -> None:
    pool = make_pool(count=2)
    sick, healthy = pool.upstreams
    for _ in range(FAILURE_THRESHOLD):
        sick.record_failure("timeout")

    order = pool.order()
    assert order[0].label == healthy.label
    assert order[-1].label == sick.label


def test_counters_survive_a_pool_refresh() -> None:
    pool = make_pool(count=1)
    pool.upstreams[0].record_failure("timeout")
    pool.set_upstreams([Upstream(label="acct0", doh_url="https://acct0.test/new")])

    assert pool.upstreams[0].consecutive_failures == 1
    assert pool.upstreams[0].doh_url.endswith("/new")


@respx.mock
async def test_forward_falls_through_to_a_working_account() -> None:
    pool = make_pool(count=2)
    pool.attach_client(httpx.AsyncClient())

    respx.post("https://acct0.test/dns-query").mock(side_effect=httpx.ConnectError("down"))
    respx.post("https://acct1.test/dns-query").mock(
        return_value=httpx.Response(200, content=b"answer-bytes")
    )

    wire, upstream, _ = await pool.forward(b"query")
    assert wire == b"answer-bytes"
    assert upstream.label == "acct1"


@respx.mock
async def test_forward_raises_only_when_everything_failed() -> None:
    pool = make_pool(count=2)
    pool.attach_client(httpx.AsyncClient())
    respx.post("https://acct0.test/dns-query").mock(side_effect=httpx.ConnectError("down"))
    respx.post("https://acct1.test/dns-query").mock(return_value=httpx.Response(500))

    with pytest.raises(UpstreamError):
        await pool.forward(b"query")


@respx.mock
async def test_429_marks_the_account_spent_immediately() -> None:
    pool = make_pool("least_used", count=2)
    pool.attach_client(httpx.AsyncClient())
    first, second = pool.upstreams
    first.quota_limit, first.quota_used = 300_000, 100
    second.quota_limit, second.quota_used = 300_000, 200_000

    respx.post("https://acct0.test/dns-query").mock(return_value=httpx.Response(429))
    respx.post("https://acct1.test/dns-query").mock(return_value=httpx.Response(200, content=b"ok"))

    await pool.forward(b"query")
    assert first.exhausted


@respx.mock
async def test_empty_body_counts_as_a_failure() -> None:
    pool = make_pool(count=2)
    pool.attach_client(httpx.AsyncClient())
    respx.post("https://acct0.test/dns-query").mock(return_value=httpx.Response(200, content=b""))
    respx.post("https://acct1.test/dns-query").mock(
        return_value=httpx.Response(200, content=b"real")
    )

    wire, upstream, _ = await pool.forward(b"query")
    assert wire == b"real"
    assert upstream.label == "acct1"


async def test_forward_without_upstreams_explains_itself() -> None:
    pool = UpstreamPool(strategy="round_robin", timeout_s=1.0)
    pool.attach_client(httpx.AsyncClient())
    with pytest.raises(UpstreamError, match="no upstream is configured"):
        await pool.forward(b"query")


def test_manager_reports_degraded_while_any_account_lacks_an_upstream() -> None:
    import httpx as _httpx

    from hydra_dns.adguard.manager import REDISCOVER_MAX_S, REDISCOVER_MIN_S, AccountManager
    from hydra_dns.config import AccountConfig

    manager = AccountManager(UpstreamPool("round_robin", 2.0), _httpx.AsyncClient())
    manager.configure([AccountConfig(label="a", username="a@x.test", password="p")])

    assert manager.degraded, "an account with no discovered URL is degraded"

    manager.accounts[0].doh_url = "https://found.test/dns-query"
    assert not manager.degraded

    assert REDISCOVER_MIN_S <= 30
    assert REDISCOVER_MAX_S <= 300


async def test_a_reachable_account_has_its_cooldown_cleared(monkeypatch) -> None:
    import httpx

    from hydra_dns.adguard.manager import AccountManager
    from hydra_dns.config import AccountConfig
    from hydra_dns.resolver.upstream import Upstream, UpstreamPool

    pool = UpstreamPool(strategy="quota_weighted", timeout_s=5.0)
    pool.set_upstreams([Upstream(label="one", doh_url="https://one.test/dns-query")])
    for _ in range(6):
        pool.get("one").record_failure("boom")
    assert pool.ready is False

    async with httpx.AsyncClient() as client:
        manager = AccountManager(pool, client)
        manager.configure(
            [
                AccountConfig(
                    label="one",
                    username="a@b.test",
                    password="p",
                    doh_url="https://one.test/dns-query",
                )
            ]
        )

        async def ok() -> dict:
            return {"requests": {"used": 1, "limit": 300000}}

        monkeypatch.setattr(manager.accounts[0], "fetch_limits", ok)
        await manager.refresh_limits()

    assert pool.ready is True


def test_a_disabled_upstream_is_never_ordered() -> None:
    pool = make_pool("round_robin", count=3)
    pool.get("acct1").enabled = False

    assert [u.label for u in pool.order()] != []
    assert all(u.label != "acct1" for u in pool.order())
    assert "acct1" not in pool.shares()


def test_a_disabled_upstream_is_not_even_a_last_resort() -> None:
    pool = make_pool("round_robin", count=2)
    off, benched = pool.upstreams
    off.enabled = False
    for _ in range(FAILURE_THRESHOLD):
        benched.record_failure("boom")

    assert pool.ready is False
    assert [u.label for u in pool.order()] == [benched.label]


@respx.mock
async def test_a_query_never_reaches_a_disabled_upstream() -> None:
    pool = make_pool("round_robin", count=2)
    off, live = pool.upstreams
    off.enabled = False
    refused = respx.post(off.doh_url).mock(return_value=httpx.Response(200, content=b"nope"))
    answered = respx.post(live.doh_url).mock(return_value=httpx.Response(200, content=b"yes"))

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        for _ in range(10):
            body, used, _ = await pool.forward(b"wire")
            assert used.label == live.label
            assert body == b"yes"

    assert refused.call_count == 0
    assert answered.call_count == 10


def test_a_disabled_upstream_keeps_its_row_in_the_snapshot() -> None:
    pool = make_pool("round_robin", count=2)
    pool.get("acct0").enabled = False
    rows = {row["label"]: row for row in pool.snapshot()}

    assert rows["acct0"]["enabled"] is False
    assert rows["acct0"]["available"] is False
    assert rows["acct1"]["enabled"] is True


def test_disabling_the_only_upstream_hands_over_to_the_fallback() -> None:
    pool = UpstreamPool(strategy="round_robin", timeout_s=2.0, fallback_url="https://f.test/q")
    pool.set_upstreams([Upstream(label="one", doh_url="https://one.test/dns-query")])
    assert pool.spent is False

    pool.get("one").enabled = False

    assert pool.ready is False
    assert pool.spent is True
    assert pool.fallback_active is True
