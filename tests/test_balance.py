from __future__ import annotations

import dataclasses

import pytest

from hydra_dns.config import settings
from hydra_dns.resolver.upstream import Upstream, UpstreamPool

LIMIT = 1_000


def make_pool(
    fractions: list[float],
    limits: list[int] | None = None,
    strategy: str = "balanced",
    floor: float = 0.01,
) -> UpstreamPool:
    pool = UpstreamPool(strategy=strategy, timeout_s=2.0, balance_floor=floor)
    limits = limits or [LIMIT] * len(fractions)
    upstreams = []
    for index, (fraction, limit) in enumerate(zip(fractions, limits, strict=True)):
        upstream = Upstream(label=f"a{index}", doh_url=f"https://a{index}.test/dns-query")
        upstream.quota_limit, upstream.quota_used = limit, int(fraction * limit)
        upstreams.append(upstream)
    pool.set_upstreams(upstreams)
    return pool


def drain(pool: UpstreamPool, queries: int) -> list[str]:
    served = []
    for _ in range(queries):
        head = pool.order()[0]
        if not head.available(0.0):
            break
        head.record_success(10.0)
        served.append(head.label)
    return served


def fractions_of(pool: UpstreamPool) -> list[float]:
    return [u.quota_fraction for u in pool.upstreams]


def spread_of(pool: UpstreamPool) -> float:
    found = fractions_of(pool)
    return max(found) - min(found)


def longest_run(served: list[str]) -> int:
    longest = run = 1
    for previous, current in zip(served, served[1:], strict=False):
        run = run + 1 if current == previous else 1
        longest = max(longest, run)
    return longest


def test_the_emptiest_account_is_served_first() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90])
    assert pool.order()[0].label == "a0"


def test_priority_runs_strictly_from_least_used_to_most_used() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90])
    shares = pool.shares()
    assert shares["a0"] > shares["a1"] > shares["a2"] > shares["a3"]


def test_the_shares_split_the_whole_of_the_traffic() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90])
    assert sum(pool.shares().values()) == pytest.approx(1.0, abs=1e-3)


def test_the_most_used_account_keeps_only_a_trickle() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90])
    share = pool.shares()["a3"]
    assert 0 < share < 0.02


def test_a_zero_floor_starves_the_most_used_account_entirely() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90], floor=0.0)
    assert pool.shares()["a3"] == 0.0
    assert "a3" not in set(drain(pool, 200))


def test_the_trickle_keeps_every_account_in_use() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90])
    assert set(drain(pool, 2_000)) == {"a0", "a1", "a2", "a3"}


def test_balancing_collapses_the_spread() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90])
    assert spread_of(pool) == pytest.approx(0.87, abs=0.01)
    drain(pool, 2_500)
    assert spread_of(pool) < 0.02


def test_a_balanced_pack_then_drains_in_lockstep() -> None:
    pool = make_pool([0.40, 0.40, 0.40])
    drain(pool, 900)
    assert spread_of(pool) < 0.01


def test_lockstep_holds_when_the_limits_differ() -> None:
    pool = make_pool([0.40, 0.40, 0.40], limits=[4 * LIMIT, 2 * LIMIT, LIMIT])
    drain(pool, 3_000)
    assert spread_of(pool) < 0.01


def test_unequal_limits_still_converge_on_one_fraction() -> None:
    pool = make_pool([0.03, 0.12, 0.50, 0.90], limits=[4 * LIMIT, 3 * LIMIT, 2 * LIMIT, LIMIT])
    drain(pool, 8_000)
    assert spread_of(pool) < 0.02


def test_balancing_strands_less_quota_than_round_robin() -> None:
    def stranded(strategy: str) -> int:
        pool = make_pool([0.03, 0.12, 0.50, 0.90], strategy=strategy)
        while all(u.available(0.0) for u in pool.upstreams):
            pool.order()[0].record_success(10.0)
        return sum(u.quota_remaining for u in pool.upstreams)

    assert stranded("balanced") < stranded("round_robin") / 2


def test_it_does_not_burst_onto_one_account() -> None:
    pool = make_pool([0.30, 0.32, 0.34])
    assert longest_run(drain(pool, 600)) <= 3


def test_least_used_bursts_where_balancing_does_not() -> None:
    greedy = make_pool([0.30, 0.32, 0.34], strategy="least_used")
    assert longest_run(drain(greedy, 600)) > 10


def test_an_even_split_until_the_limits_are_known() -> None:
    pool = UpstreamPool(strategy="balanced", timeout_s=2.0)
    pool.set_upstreams(
        [Upstream(label=f"a{i}", doh_url=f"https://a{i}.test/dns-query") for i in range(3)]
    )
    assert sorted(pool.shares().values()) == pytest.approx([1 / 3] * 3, abs=1e-3)


def test_an_account_with_no_limit_yet_gets_an_average_share() -> None:
    pool = make_pool([0.10, 0.50])
    pool.set_upstreams([*pool.upstreams, Upstream(label="new", doh_url="https://new.test/q")])
    shares = pool.shares()
    assert shares["a1"] < shares["new"] < shares["a0"]


def test_a_spent_account_is_left_out_of_the_split() -> None:
    pool = make_pool([0.10, 0.50, 1.0])
    assert "a2" not in pool.shares()
    assert sum(pool.shares().values()) == pytest.approx(1.0, abs=1e-3)


def test_a_benched_account_is_left_out_of_the_split() -> None:
    pool = make_pool([0.10, 0.50, 0.60])
    for _ in range(6):
        pool.get("a2").record_failure("timeout")
    assert "a2" not in pool.shares()


def test_balance_reports_the_target_and_the_spread() -> None:
    report = make_pool([0.03, 0.12, 0.50, 0.90]).balance()
    assert report["balancing"] is True
    assert report["target_fraction"] == pytest.approx(0.90)
    assert report["spread"] == pytest.approx(0.87)
    assert report["balanced"] is False
    assert report["reporting"] == 4


def test_balance_calls_a_settled_pack_balanced() -> None:
    assert make_pool([0.40, 0.40, 0.40]).balance()["balanced"] is True


def test_a_single_account_is_balanced_by_definition() -> None:
    assert make_pool([0.40]).balance()["balanced"] is True


def test_the_snapshot_carries_the_share_and_the_deficit() -> None:
    rows = {row["label"]: row for row in make_pool([0.03, 0.12, 0.50, 0.90]).snapshot()}
    assert rows["a0"]["deficit"] == pytest.approx(0.87)
    assert rows["a3"]["deficit"] == pytest.approx(0.0)
    assert rows["a0"]["share"] > rows["a3"]["share"]


def test_the_pool_state_carries_the_balance_report() -> None:
    state = make_pool([0.03, 0.90]).state()
    assert state["balance"]["spread"] == pytest.approx(0.87)


def test_balanced_is_an_accepted_strategy() -> None:
    assert not dataclasses.replace(settings, strategy="balanced").validate()


def test_a_floor_outside_zero_to_one_is_rejected() -> None:
    assert dataclasses.replace(settings, balance_floor=1.5).validate()
    assert dataclasses.replace(settings, balance_floor=-0.1).validate()
    assert not dataclasses.replace(settings, balance_floor=0.02).validate()


def test_an_unknown_strategy_is_rejected() -> None:
    assert dataclasses.replace(settings, strategy="vibes").validate()


def test_a_spent_leader_does_not_leave_phantom_deficits() -> None:
    pool = make_pool([0.50, 0.52, 1.0])
    assert pool.target_fraction() == pytest.approx(0.52)
    rows = {row["label"]: row for row in pool.snapshot()}
    assert rows["a0"]["deficit"] == pytest.approx(0.02)
    assert pool.balance()["reporting"] == 2


def test_balance_ignores_a_benched_account_it_cannot_use() -> None:
    pool = make_pool([0.10, 0.20, 0.90])
    for _ in range(6):
        pool.get("a2").record_failure("timeout")
    assert pool.balance()["target_fraction"] == pytest.approx(0.20)
