from __future__ import annotations

import datetime as dt

import pytest
import respx
from test_doh import client, mock_upstream

from hydra_dns import watch

PASSWORD = "hunter2"
MID_MONTH = dt.datetime(2026, 9, 11, tzinfo=dt.UTC)


def account(label: str, *, authed: bool = True, exhausted: bool = False) -> dict:
    return {
        "label": label,
        "auth": {"authenticated": authed},
        "upstream": {"exhausted": exhausted},
    }


def quota(used: int, limit: int = 1_200_000) -> dict:
    return {"requests_used": used, "requests_remaining": max(0, limit - used)}


HEALTHY_POOL = {"spent": False, "fallback": None}


def check(accounts, q, pool=None, warn_days=5, now=MID_MONTH, **over):
    return watch.reasons(
        accounts=accounts,
        quota=q,
        pool_state=pool or HEALTHY_POOL,
        warn_days=warn_days,
        now=now,
        **over,
    )


def test_a_listener_that_is_enabled_but_down_needs_attention():
    found = check([account("one")], quota(0), silent_listeners=["DoT"])
    assert found == ["DoT is enabled but not listening; devices pointed at it cannot resolve"]


def test_both_listeners_down_read_as_one_sentence():
    found = check([account("one")], quota(0), silent_listeners=["DoT", "DoQ"])
    assert found == [
        "DoT and DoQ are enabled but not listening; devices pointed at them cannot resolve"
    ]


def test_listeners_that_are_up_say_nothing():
    assert check([account("one")], quota(0), silent_listeners=[]) == []


def test_a_healthy_pool_needs_no_attention():
    assert check([account("one"), account("two")], quota(100_000)) == []


def test_no_accounts_at_all_is_flagged():
    assert check([], quota(0)) == ["no AdGuard account is configured"]


def test_an_account_that_cannot_sign_in_is_named():
    found = check([account("one"), account("two", authed=False)], quota(100_000))
    assert any("two cannot sign in" in r for r in found)


def test_several_failures_read_as_a_sentence():
    found = check(
        [account("one", authed=False), account("two", authed=False), account("three")],
        quota(100_000),
    )
    assert any("one and two cannot sign in" in r for r in found)


def test_one_spent_account_is_worth_saying():
    found = check([account("one", exhausted=True), account("two")], quota(100_000))
    assert any("one has spent its monthly allowance" in r for r in found)


def test_every_account_spent_is_reported_as_the_pool_being_down():
    found = check(
        [account("one", exhausted=True), account("two", exhausted=True)],
        quota(1_200_000),
        pool={"spent": True, "fallback": {"active": True}},
    )
    assert any("fallback resolver unfiltered" in r for r in found)


def test_a_spent_pool_without_a_fallback_says_so():
    found = check(
        [account("one", exhausted=True)],
        quota(1_200_000),
        pool={"spent": True, "fallback": None},
    )
    assert any("no fallback" in r for r in found)


def test_headroom_projects_from_the_month_so_far():
    assert watch.days_of_headroom(remaining=200, used_this_month=100, now=MID_MONTH) == 20.0


def test_headroom_is_unknown_before_anything_is_used():
    assert watch.days_of_headroom(remaining=100, used_this_month=0, now=MID_MONTH) is None


def test_running_low_is_flagged_before_it_runs_out():
    found = check([account("one")], quota(used=1_150_000), warn_days=5)
    assert any("days of allowance left" in r for r in found)


def test_comfortable_headroom_is_silent():
    assert check([account("one")], quota(used=200_000), warn_days=5) == []


@respx.mock
async def test_status_is_public_and_says_almost_nothing() -> None:
    mock_upstream()
    async with client() as http:
        body = (await http.get("/api/status")).json()

    assert set(body) == {"state"}
    assert body["state"] in {watch.NOMINAL, watch.ATTENTION}


@respx.mock
async def test_status_turns_to_attention_when_the_pool_cannot_answer() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        for upstream in app.state.pool.upstreams:
            upstream.quota_limit = 300_000
            upstream.quota_used = 300_000
        body = (await http.get("/api/status")).json()

    assert body["state"] == watch.ATTENTION


@respx.mock
async def test_attention_detail_needs_a_session() -> None:
    mock_upstream()
    async with client() as http:
        public = (await http.get("/api/health")).json()
        assert "attention" not in public

        await http.post("/api/login", json={"password": PASSWORD})
        private = (await http.get("/api/health")).json()

    assert isinstance(private["attention"], list)


@pytest.mark.parametrize("elapsed", [1, 15, 28])
def test_headroom_scales_with_the_month_so_far(elapsed):
    when = dt.datetime(2026, 9, 1, tzinfo=dt.UTC) + dt.timedelta(days=elapsed)
    assert watch.days_of_headroom(remaining=100, used_this_month=10 * elapsed, now=when) == 10.0


def test_headroom_reads_the_first_hour_of_the_month_as_an_hour():
    when = dt.datetime(2026, 9, 1, 1, 0, tzinfo=dt.UTC)
    assert watch.days_of_headroom(remaining=2400, used_this_month=100, now=when) == 1.0


def test_headroom_waits_for_an_hour_before_projecting():
    when = dt.datetime(2026, 9, 1, 0, 1, tzinfo=dt.UTC)
    assert watch.days_of_headroom(remaining=100, used_this_month=10, now=when) is None
