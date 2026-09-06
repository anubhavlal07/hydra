from __future__ import annotations

from hydra_dns import watch
from hydra_dns.adguard.aggregate import (
    decode_cursor,
    encode_cursor,
    merge_counted,
    merge_query_log,
    merge_time_series,
    quota_rollup,
)


def point(at: int, queries: int, blocked: int = 0) -> dict:
    return {"time_millis": at, "value": {"queries": queries, "blocked": blocked, "companies": 1}}


def test_aligned_buckets_are_summed() -> None:
    merged = merge_time_series([[point(1000, 10, 2)], [point(1000, 5, 1)]])
    assert merged == [{"at": 1000, "queries": 15, "blocked": 3}]


def test_unaligned_buckets_stay_separate_rather_than_being_misattributed() -> None:
    merged = merge_time_series([[point(1000, 10)], [point(2000, 5)]])
    assert merged == [
        {"at": 1000, "queries": 10, "blocked": 0},
        {"at": 2000, "queries": 5, "blocked": 0},
    ]


def test_series_come_back_in_time_order() -> None:
    merged = merge_time_series([[point(3000, 1), point(1000, 1)], [point(2000, 1)]])
    assert [p["at"] for p in merged] == [1000, 2000, 3000]


def test_empty_and_missing_series_are_tolerated() -> None:
    assert merge_time_series([[], None, [point(1, 1)]]) == [{"at": 1, "queries": 1, "blocked": 0}]


def test_counts_for_the_same_domain_are_added_across_accounts() -> None:
    rows = merge_counted(
        [
            [{"domain": "ads.test", "value": {"queries": 10, "blocked": 10}}],
            [{"domain": "ads.test", "value": {"queries": 5, "blocked": 5}}],
        ],
        lambda e: e.get("domain", ""),
        "domain",
    )
    assert rows == [{"domain": "ads.test", "queries": 15, "blocked": 15, "companies": 0}]


def test_ranking_is_by_merged_total_not_per_account_rank() -> None:
    rows = merge_counted(
        [
            [
                {"domain": "a.test", "value": {"queries": 10}},
                {"domain": "b.test", "value": {"queries": 9}},
            ],
            [
                {"domain": "c.test", "value": {"queries": 10}},
                {"domain": "b.test", "value": {"queries": 9}},
            ],
        ],
        lambda e: e.get("domain", ""),
        "domain",
    )
    assert rows[0]["domain"] == "b.test"
    assert rows[0]["queries"] == 18


def test_limit_is_applied_after_merging() -> None:
    rows = merge_counted(
        [[{"domain": f"{i}.test", "value": {"queries": i}} for i in range(20)]],
        lambda e: e.get("domain", ""),
        "domain",
        limit=3,
    )
    assert len(rows) == 3
    assert rows[0]["domain"] == "19.test"


def test_flat_category_shape_is_understood() -> None:
    rows = merge_counted(
        [
            [{"category_type": "ADVERTISING", "queries": 7}],
            [{"category_type": "ADVERTISING", "queries": 3}],
        ],
        lambda e: str(e.get("category_type", "")),
        "category",
    )
    assert rows[0]["queries"] == 10


def test_query_log_interleaves_newest_first() -> None:
    merged, _ = merge_query_log(
        {
            "one": (
                [{"time_millis": 100, "domain": "a"}, {"time_millis": 300, "domain": "c"}],
                None,
            ),
            "two": ([{"time_millis": 200, "domain": "b"}], None),
        },
        limit=10,
    )
    assert [i["domain"] for i in merged] == ["c", "b", "a"]


def test_query_log_items_carry_their_account() -> None:
    merged, _ = merge_query_log({"one": ([{"time_millis": 1, "domain": "a"}], None)}, limit=10)
    assert merged[0]["account"] == "one"


def test_query_log_respects_the_limit() -> None:
    merged, _ = merge_query_log({"one": ([{"time_millis": i} for i in range(50)], None)}, limit=5)
    assert len(merged) == 5


def test_composite_cursor_round_trips() -> None:
    cursors = {"one": "abc:1", "two": "def:2"}
    assert decode_cursor(encode_cursor(cursors)) == cursors


def test_cursor_is_only_issued_for_accounts_with_more_to_give() -> None:
    _, cursor = merge_query_log(
        {"one": ([], "next-one"), "two": ([], None)},
        limit=10,
    )
    assert decode_cursor(cursor) == {"one": "next-one"}


def test_exhausted_pagination_yields_no_cursor() -> None:
    _, cursor = merge_query_log({"one": ([], None)}, limit=10)
    assert cursor is None


def test_a_malformed_cursor_restarts_rather_than_raising() -> None:
    assert decode_cursor("not-base64!!") == {}
    assert decode_cursor("eyJhIjogMX0") == {"a": "1"}
    assert decode_cursor(None) == {}


def test_rollup_sums_only_accounts_that_reported() -> None:
    rollup = quota_rollup(
        [
            {"requests_limit": 300_000, "requests_used": 100_000},
            {"requests_limit": 300_000, "requests_used": 50_000},
            {"requests_limit": -1, "requests_used": -1},
        ]
    )
    assert rollup["accounts_reporting"] == 2
    assert rollup["requests_limit"] == 600_000
    assert rollup["requests_remaining"] == 450_000


def test_cache_multiplier_reflects_what_the_hit_rate_buys() -> None:
    rollup = quota_rollup([{"requests_limit": 100_000, "requests_used": 0}], cache_hit_rate=0.75)
    assert rollup["cache_multiplier"] == 4.0
    assert rollup["effective_remaining"] == 400_000


def test_total_hit_rate_does_not_claim_infinite_capacity() -> None:
    rollup = quota_rollup([{"requests_limit": 100, "requests_used": 0}], cache_hit_rate=1.0)
    assert rollup["cache_multiplier"] == 100.0


def test_rollup_with_no_accounts_is_all_zeroes_not_an_error() -> None:
    rollup = quota_rollup([])
    assert rollup["requests_limit"] == 0
    assert rollup["used_fraction"] == 0.0


def test_the_rollup_carries_the_headroom_the_dashboard_used_to_recompute() -> None:
    rollup = quota_rollup([{"requests_limit": 300_000, "requests_used": 60_000}])

    assert rollup["headroom_days"] is not None
    assert rollup["headroom_days"] == round(
        watch.days_of_headroom(rollup["requests_remaining"], rollup["requests_used"]), 2
    )


def test_headroom_is_unknown_until_something_has_been_spent() -> None:
    rollup = quota_rollup([{"requests_limit": 300_000, "requests_used": 0}])

    assert rollup["headroom_days"] is None
