from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx
from test_doh import client, mock_upstream
from test_policy_api import FakeAccount, make_settings

from hydra_dns import alerts as alerts_module
from hydra_dns import watch
from hydra_dns.adguard import policy
from hydra_dns.adguard.manager import AccountManager
from hydra_dns.alerts import AlertDispatcher
from hydra_dns.config import AccountConfig, settings
from hydra_dns.drift import PolicyDrift
from hydra_dns.resolver.upstream import UpstreamPool

PASSWORD = "hunter2"
RULES = ["||ads.example^", "||tracker.example^"]


class Member(FakeAccount):
    def __init__(self, label: str, settings: dict, *, fails: str = "") -> None:
        super().__init__(label, settings, fails=fails)
        self.enabled = True
        self.doh_url = f"https://d.example.test/{label}"
        self.requests_limit = -1
        self.requests_used = -1

    def snapshot(self) -> dict:
        return {
            "label": self.label,
            "enabled": self.enabled,
            "auth": {"authenticated": True, "consecutive_failures": 0},
            "requests_used": 10,
            "requests_limit": 300_000,
        }


def pooled(label: str, rules: list[str] | None = None, **extra) -> Member:
    return Member(label, make_settings(list(RULES if rules is None else rules), **extra))


def defaults() -> dict:
    fresh = make_settings([], lists=["adguard_dns_filter", "other_list"])
    fresh["filter_lists_settings"]["enabled"] = False
    fresh["user_rules_settings"]["enabled"] = False
    fresh["ip_log_enabled"] = True
    fresh["block_chrome_prefetch"] = True
    return fresh


def test_a_filter_list_master_switch_that_is_off_is_drift() -> None:
    off = make_settings(RULES)
    off["filter_lists_settings"]["enabled"] = False
    summary = policy.summarise({"alpha": make_settings(RULES), "echo": off})

    assert "filter lists switch" in summary["drifting"]
    assert summary["filter_lists"]["in_sync"] is True
    assert summary["in_sync"] is False


def test_a_user_rules_master_switch_that_is_off_is_drift() -> None:
    off = make_settings(RULES)
    off["user_rules_settings"]["enabled"] = False
    summary = policy.summarise({"alpha": make_settings(RULES), "echo": off})

    assert "user rules switch" in summary["drifting"]
    assert summary["rules"]["in_sync"] is True


def test_the_odd_account_out_is_named_with_what_differs() -> None:
    odd, split = policy.outliers(
        {
            "alpha": make_settings(RULES),
            "bravo": make_settings(RULES),
            "charlie": make_settings(RULES),
            "echo": defaults(),
        }
    )

    assert split == []
    assert list(odd) == ["echo"]
    assert odd["echo"] == ["user rules", "filter lists", "filter lists switch", "user rules switch"]


def test_two_accounts_that_disagree_have_no_majority() -> None:
    odd, split = policy.outliers(
        {"alpha": make_settings(RULES), "bravo": make_settings(RULES, protection=False)}
    )

    assert odd == {}
    assert split == ["protection"]


def test_sync_restores_the_user_rules_switch() -> None:
    off = make_settings(RULES)
    off["user_rules_settings"]["enabled"] = False
    changes, _ = policy.agreed(
        {"a": make_settings(RULES), "b": make_settings(RULES)}, unanimous=True
    )

    body = policy.apply_to_settings(off, **changes)

    assert body["user_rules_settings"]["enabled"] is True
    assert body["filter_lists_settings"]["enabled"] is True


def test_rules_that_disagree_are_unioned_and_settings_are_left() -> None:
    changes, left = policy.agreed(
        {
            "alpha": make_settings(["||a.example^"]),
            "bravo": make_settings(["||b.example^"], protection=False),
        },
        unanimous=True,
    )

    assert changes["rules"] == ["||a.example^", "||b.example^"]
    assert "protection" not in changes
    assert left == ["protection"]


def test_a_majority_wins_when_unanimity_is_not_asked_for() -> None:
    changes, left = policy.agreed(
        {
            "alpha": make_settings(RULES),
            "bravo": make_settings(RULES),
            "echo": make_settings([], protection=False),
        },
        unanimous=False,
    )

    assert changes["protection"] is True
    assert changes["rules"] == RULES
    assert left == []


def manager_with(accounts: list[FakeAccount]) -> AccountManager:
    manager = AccountManager(UpstreamPool(strategy="balanced", timeout_s=2.0), httpx.AsyncClient())
    manager.accounts = list(accounts)
    return manager


def newcomer(monkeypatch, fresh: FakeAccount) -> None:
    from hydra_dns.adguard import manager as manager_module

    fresh.enabled = True
    fresh.doh_url = "https://d.example.test/echo"

    async def nothing() -> None:
        return None

    fresh.discover_upstream = nothing
    fresh.fetch_limits = nothing
    fresh.requests_limit = -1
    monkeypatch.setattr(manager_module, "AdGuardAccount", lambda config, client: fresh)


async def test_a_new_account_takes_the_pools_agreed_policy(monkeypatch) -> None:
    pool = [pooled("alpha"), pooled("bravo", ["||tracker.example^", "||extra.example^"])]
    disabled = pooled("charlie", [], protection=False)
    disabled.enabled = False
    fresh = Member("echo", defaults())
    newcomer(monkeypatch, fresh)
    manager = manager_with([*pool, disabled])

    await manager.add_account(AccountConfig(label="echo", username="e@x.test", password="p"))

    assert len(fresh.writes) == 1
    written = fresh.writes[0]
    assert written["user_rules_settings"]["rules"] == [
        "||ads.example^",
        "||tracker.example^",
        "||extra.example^",
    ]
    assert written["user_rules_settings"]["enabled"] is True
    assert written["filter_lists_settings"]["enabled"] is True
    lists = {f["filter_id"]: f["enabled"] for f in written["filter_lists_settings"]["filter_list"]}
    assert lists == {"adguard_dns_filter": True, "other_list": False}
    assert written["protection_enabled"] is True
    assert written["ip_log_enabled"] is True
    assert written["block_chrome_prefetch"] is True
    assert written["blocking_mode_settings"] == {"blocking_mode": "REFUSED"}
    assert "rules_count" not in written["user_rules_settings"]
    assert not any(a.writes for a in [*pool, disabled])
    adoption = manager.policy_adoption["echo"]
    assert adoption["synced"] is True
    assert adoption["from"] == ["alpha", "bravo"]
    assert fresh in manager.accounts


async def test_a_setting_the_pool_disagrees_on_is_left_alone(monkeypatch) -> None:
    fresh = Member("echo", defaults())
    fresh.settings["protection_enabled"] = False
    newcomer(monkeypatch, fresh)
    manager = manager_with([pooled("alpha"), pooled("bravo", protection=False)])

    await manager.add_account(AccountConfig(label="echo", username="e@x.test", password="p"))

    assert fresh.writes[0]["protection_enabled"] is False
    assert manager.policy_adoption["echo"]["left"] == ["protection"]


async def test_a_failed_sync_does_not_fail_the_add(monkeypatch) -> None:
    fresh = Member("echo", defaults(), fails="write")
    newcomer(monkeypatch, fresh)
    manager = manager_with([pooled("alpha"), pooled("bravo")])

    added = await manager.add_account(
        AccountConfig(label="echo", username="e@x.test", password="p")
    )

    assert added is fresh
    assert fresh in manager.accounts
    assert manager.policy_adoption["echo"]["synced"] is False
    assert "write blew up" in manager.policy_adoption["echo"]["error"]


async def test_a_first_account_has_nothing_to_adopt(monkeypatch) -> None:
    fresh = Member("echo", defaults())
    newcomer(monkeypatch, fresh)
    manager = manager_with([])

    await manager.add_account(AccountConfig(label="echo", username="e@x.test", password="p"))

    assert fresh.writes == []
    assert manager.policy_adoption["echo"]["synced"] is False


class Clock:
    def __init__(self) -> None:
        self.at = 1_800_000_000.0

    def __call__(self) -> float:
        return self.at


def reasons_of(manager: AccountManager, watcher: PolicyDrift) -> list[str]:
    return watch.reasons(
        accounts=[{"label": a.label, "auth": {"authenticated": True}} for a in manager.accounts],
        quota={"requests_remaining": 500_000, "requests_used": 10},
        pool_state={"spent": False},
        warn_days=5,
        drift=watcher.findings(),
    )


async def test_the_watch_raises_and_clears_a_drift_reason() -> None:
    accounts = [pooled("alpha"), pooled("bravo"), pooled("charlie")]
    echo = Member("echo", defaults())
    echo.enabled = True
    manager = manager_with([*accounts, echo])
    watcher = PolicyDrift(manager, 900, clock=Clock())

    await watcher.check()
    found = reasons_of(manager, watcher)

    assert found == [
        "echo filters differently from the other accounts (user rules, filter lists, "
        "filter lists switch, user rules switch); ads can get through on its share of queries"
    ]
    assert watch.is_drift(found[0])
    assert watch.once_per_episode(found[0])

    echo.settings = make_settings(RULES)
    manager.invalidate_stats_cache()
    await watcher.check()

    assert reasons_of(manager, watcher) == []
    assert watcher.snapshot()["in_sync"] is True


async def test_a_disabled_account_is_not_compared() -> None:
    echo = Member("echo", defaults())
    echo.enabled = False
    manager = manager_with([pooled("alpha"), pooled("bravo"), echo])
    watcher = PolicyDrift(manager, 900)

    await watcher.check()

    assert watcher.snapshot()["compared"] == ["alpha", "bravo"]
    assert reasons_of(manager, watcher) == []


async def test_switching_an_account_off_drops_its_reason_at_once() -> None:
    echo = Member("echo", defaults())
    echo.enabled = True
    manager = manager_with([pooled("alpha"), pooled("bravo"), echo])
    watcher = PolicyDrift(manager, 900)

    await watcher.check()
    assert len(reasons_of(manager, watcher)) == 1

    echo.enabled = False
    assert reasons_of(manager, watcher) == []


async def test_an_unreadable_account_keeps_its_last_finding() -> None:
    echo = Member("echo", defaults())
    echo.enabled = True
    manager = manager_with([pooled("alpha"), pooled("bravo"), echo])
    watcher = PolicyDrift(manager, 900)
    await watcher.check()

    echo.fails = "read"
    manager.invalidate_stats_cache()
    await watcher.check()

    assert list(watcher.findings()[0]) == ["echo"]
    assert "echo" in watcher.snapshot()["errors"]


def test_a_drift_episode_alerts_once(monkeypatch) -> None:
    monkeypatch.setattr(alerts_module, "settings", dataclasses.replace(settings, alert_repeat_s=60))
    reason = watch.drift_reasons({"echo": ["filter lists"]}, [])[0]
    other = "the pool is spent"
    sender = AlertDispatcher(None, lambda: [], once=watch.once_per_episode)

    assert sender.evaluate([reason, other], now=0)["opened"] == sorted([reason, other])
    later = sender.evaluate([reason, other], now=7200)
    assert later["repeated"] == [other]
    assert sender.evaluate([other], now=7300)["resolved"] == [reason]


def test_several_odd_accounts_share_one_reason() -> None:
    found = watch.drift_reasons(
        {"delta": ["filter lists"], "echo": ["filter lists"]}, ["safe browsing"]
    )

    assert found == [
        "delta and echo filter differently from the other accounts (filter lists); ads can get "
        "through on their share of queries",
        "the accounts filter differently from each other (safe browsing); ads can get through "
        "on part of the queries",
    ]


def test_zero_turns_the_watch_off() -> None:
    assert PolicyDrift(manager_with([]), 0).enabled is False


def pin(app, accounts) -> None:
    app.state.manager.accounts = accounts
    app.state.manager.invalidate_stats_cache()


@pytest.fixture
def drifting():
    echo = pooled("echo", [])
    echo.settings["filter_lists_settings"]["enabled"] = False
    return [pooled("alpha"), pooled("bravo"), echo]


@respx.mock
async def test_the_drift_reason_reaches_status_health_and_overview(drifting) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        pin(app, drifting)
        await app.state.policy_drift.check()

        status = (await http.get("/api/status")).json()
        health = (await http.get("/api/health")).json()
        overview = (await http.get("/api/overview")).json()

    reason = (
        "echo filters differently from the other accounts (user rules, filter lists switch); "
        "ads can get through on its share of queries"
    )
    assert status["state"] == watch.ATTENTION
    assert reason in health["attention"]
    assert reason in overview["attention"]
    assert health["policy_drift"]["outliers"] == {"echo": ["user rules", "filter lists switch"]}


@respx.mock
async def test_sync_writes_the_pools_policy_to_the_odd_account(drifting) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        pin(app, drifting)
        await app.state.policy_drift.check()
        await http.post("/api/lookup", json={"name": "example.com", "type": "A"})

        response = await http.post("/api/policy/sync")
        health = (await http.get("/api/health")).json()
        audit = (await http.get("/api/audit")).json()

    body = response.json()
    assert response.status_code == 200, body
    assert body["repaired"] == {"echo": ["user rules", "filter lists switch"]}
    assert body["left"] == []
    assert body["applied_to"] == ["alpha", "bravo", "echo"]
    assert body["cache_cleared"] >= 1
    echo = drifting[2].writes[-1]
    assert echo["user_rules_settings"]["rules"] == RULES
    assert echo["filter_lists_settings"]["enabled"] is True
    assert echo["blocking_mode_settings"] == {"blocking_mode": "REFUSED"}
    assert not any(watch.is_drift(r) for r in health["attention"])
    entry = next(e for e in audit["entries"] if e["action"] == "policy.sync")
    assert entry["detail"]["repaired"] == {"echo": ["user rules", "filter lists switch"]}


@respx.mock
async def test_sync_needs_a_session(drifting) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        pin(app, drifting)
        response = await http.post("/api/policy/sync")

    assert response.status_code == 401
    assert not any(a.writes for a in drifting)


@respx.mock
async def test_sync_ignores_a_disabled_account_when_choosing_the_policy() -> None:
    from hydra_dns.main import app

    off = pooled("charlie", [], protection=False)
    off.enabled = False
    accounts = [pooled("alpha"), pooled("bravo"), off]
    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        pin(app, accounts)
        body = (await http.post("/api/policy/sync")).json()

    assert body["repaired"] == {}
    assert accounts[0].writes[-1]["protection_enabled"] is True
    assert off.writes[-1]["protection_enabled"] is True
    assert off.writes[-1]["user_rules_settings"]["rules"] == RULES


@respx.mock
async def test_adding_an_account_reports_the_sync(monkeypatch, tmp_path) -> None:
    from test_accounts_api import mock_signin, store_of

    from hydra_dns import accounts_store
    from hydra_dns.main import app

    monkeypatch.setattr(
        accounts_store,
        "settings",
        dataclasses.replace(accounts_store.settings, accounts_file=str(tmp_path / "a.json")),
    )
    fresh = Member("echo", defaults(), fails="write")
    newcomer(monkeypatch, fresh)
    mock_upstream()
    mock_signin()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        store_of(app)
        pin(app, [pooled("alpha"), pooled("bravo")])
        response = await http.post(
            "/api/accounts", json={"username": "echo@example.test", "password": "pw"}
        )
        audit = (await http.get("/api/audit")).json()

    body = response.json()
    assert response.status_code == 201, body
    assert body["policy_sync"]["synced"] is False
    assert "write blew up" in body["policy_sync"]["error"]
    entry = next(e for e in audit["entries"] if e["action"] == "account.add")
    assert "write blew up" in entry["detail"]["policy_error"]
