from __future__ import annotations

from typing import Any

import pytest
import respx
from test_doh import client, mock_upstream

from hydra_dns.adguard.client import AdGuardError

PASSWORD = "hunter2"


def make_settings(rules: list[str], *, protection: bool = True, lists: list[str] | None = None):
    return {
        "protection_enabled": protection,
        "block_ttl_seconds": 30,
        "blocking_mode_settings": {"blocking_mode": "REFUSED"},
        "safebrowsing_settings": {"enabled": True},
        "parental_control_settings": {"enabled": False},
        "filter_lists_settings": {
            "enabled": True,
            "filter_list": [
                {"filter_id": name, "enabled": True} for name in (lists or ["adguard_dns_filter"])
            ],
        },
        "user_rules_settings": {"enabled": True, "rules": list(rules), "rules_count": len(rules)},
    }


class FakeAccount:
    def __init__(self, label: str, settings: dict[str, Any], *, fails: str = "") -> None:
        self.label = label
        self.settings = settings
        self.fails = fails
        self.writes: list[dict[str, Any]] = []

    async def server_settings(self) -> dict[str, Any]:
        if self.fails == "read":
            raise AdGuardError(self.label, "read blew up")
        return self.settings

    async def update_server_settings(self, body: dict[str, Any]) -> None:
        if self.fails == "write":
            raise AdGuardError(self.label, "write blew up")
        self.writes.append(body)
        self.settings = body

    async def filter_lists(self) -> list[dict[str, Any]]:
        return [{"filter_id": "adguard_dns_filter", "name": "AdGuard DNS filter"}]


@pytest.fixture
def accounts():
    return [
        FakeAccount("one", make_settings(["||ads.example^", "||tracker.example^"])),
        FakeAccount("two", make_settings(["||ads.example^"], protection=False)),
    ]


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


def pin(app, accounts):
    app.state.manager.accounts = accounts
    app.state.manager.invalidate_stats_cache()


@respx.mock
async def test_policy_reports_the_drift_between_accounts(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        body = (await http.get("/api/policy")).json()

    policy = body["policy"]
    assert policy["in_sync"] is False
    assert "user rules" in policy["drifting"]
    assert "protection" in policy["drifting"]
    partial = [r for r in policy["rules"]["rules"] if r["rule"] == "||tracker.example^"][0]
    assert partial["accounts"] == ["one"]
    assert partial["everywhere"] is False


@respx.mock
async def test_an_account_with_filtering_off_is_named(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        body = (await http.get("/api/policy")).json()

    assert body["policy"]["unprotected_accounts"] == ["two"]


@respx.mock
async def test_the_rules_come_back_ready_for_a_textarea(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        body = (await http.get("/api/policy")).json()

    assert body["rules_text"] == "||ads.example^\n||tracker.example^"


@respx.mock
async def test_policy_needs_a_session(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        assert (await http.get("/api/policy")).status_code == 401
        assert (await http.put("/api/policy", json={"protection": True})).status_code == 401
        assert (
            await http.post("/api/policy/rule", json={"domain": "x.example"})
        ).status_code == 401


@respx.mock
async def test_saving_rules_writes_the_same_list_to_every_account(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        response = await http.put("/api/policy", json={"rules": ["||new.example^"]})

    assert response.json()["applied_to"] == ["one", "two"]
    for account in accounts:
        assert account.writes[-1]["user_rules_settings"]["rules"] == ["||new.example^"]


@respx.mock
async def test_saving_leaves_settings_it_was_not_asked_about_alone(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.put("/api/policy", json={"rules": ["||new.example^"]})

    written = accounts[0].writes[-1]
    assert written["blocking_mode_settings"] == {"blocking_mode": "REFUSED"}
    assert written["block_ttl_seconds"] == 30
    assert accounts[1].writes[-1]["protection_enabled"] is False


@respx.mock
async def test_turning_protection_on_repairs_the_unprotected_account(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.put("/api/policy", json={"protection": True})
        body = (await http.get("/api/policy")).json()

    assert body["policy"]["unprotected_accounts"] == []


@respx.mock
async def test_an_empty_request_is_refused_rather_than_writing_nothing(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        response = await http.put("/api/policy", json={})

    assert response.status_code == 400


@respx.mock
async def test_a_partial_failure_names_the_account_that_refused() -> None:
    accounts = [
        FakeAccount("one", make_settings(["||a^"])),
        FakeAccount("two", make_settings(["||a^"]), fails="write"),
    ]
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        body = (await http.put("/api/policy", json={"rules": ["||b^"]})).json()

    assert body["applied_to"] == ["one"]
    assert "two" in body["errors"]
    assert body["in_sync"] is False


@respx.mock
async def test_a_total_failure_is_an_error_not_a_success() -> None:
    accounts = [FakeAccount("one", make_settings(["||a^"]), fails="write")]
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        response = await http.put("/api/policy", json={"rules": ["||b^"]})

    assert response.status_code == 502


@respx.mock
async def test_blocking_one_domain_applies_to_every_account(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        body = (
            await http.post("/api/policy/rule", json={"domain": "bad.example", "action": "block"})
        ).json()

    assert body["applied_to"] == ["one", "two"]
    for account in accounts:
        assert "||bad.example^" in account.writes[-1]["user_rules_settings"]["rules"]


@respx.mock
async def test_blocking_keeps_rules_only_one_account_had(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.post("/api/policy/rule", json={"domain": "bad.example", "action": "block"})

    written = accounts[1].writes[-1]["user_rules_settings"]["rules"]
    assert "||tracker.example^" in written, "the rule only account one held must survive"


@respx.mock
async def test_allowing_a_blocked_domain_replaces_the_block(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.post("/api/policy/rule", json={"domain": "ads.example", "action": "allow"})

    rules = accounts[0].writes[-1]["user_rules_settings"]["rules"]
    assert "@@||ads.example^" in rules
    assert "||ads.example^" not in rules


@respx.mock
async def test_clearing_removes_both_forms(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.post("/api/policy/rule", json={"domain": "ads.example", "action": "clear"})

    rules = accounts[0].writes[-1]["user_rules_settings"]["rules"]
    assert not any("ads.example" in rule for rule in rules)


@respx.mock
async def test_blocking_drops_the_cached_answer(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.post("/api/lookup", json={"name": "example.com", "type": "A"})
        assert app.state.cache.snapshot()["entries"] > 0

        await http.post("/api/policy/rule", json={"domain": "bad.example", "action": "block"})
        assert app.state.cache.snapshot()["entries"] == 0


@respx.mock
async def test_a_domain_that_is_not_a_domain_is_refused(accounts) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        response = await http.post(
            "/api/policy/rule", json={"domain": "https://example.com/path", "action": "block"}
        )

    assert response.status_code == 400
    assert not accounts[0].writes, "nothing should have been written"


@respx.mock
async def test_a_block_is_refused_when_no_account_can_be_read() -> None:
    accounts = [FakeAccount("one", make_settings(["||keep^"]), fails="read")]
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        response = await http.post("/api/policy/rule", json={"domain": "bad.example"})

    assert response.status_code == 503


@respx.mock
async def test_parental_control_can_be_synced_from_the_api() -> None:
    accounts = [
        FakeAccount("one", make_settings(["||a^"])),
        FakeAccount("two", make_settings(["||a^"])),
    ]
    accounts[1].settings["parental_control_settings"] = {"enabled": True}
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        before = (await http.get("/api/policy")).json()["policy"]
        assert "parental control" in before["drifting"]

        await http.put("/api/policy", json={"parental_control": True})
        after = (await http.get("/api/policy")).json()["policy"]

    assert "parental control" not in after["drifting"]


@respx.mock
async def test_safe_browsing_can_be_synced_from_the_api() -> None:
    accounts = [
        FakeAccount("one", make_settings(["||a^"])),
        FakeAccount("two", make_settings(["||a^"])),
    ]
    accounts[1].settings["safebrowsing_settings"] = {"enabled": False}
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        await http.put("/api/policy", json={"safe_browsing": True})
        after = (await http.get("/api/policy")).json()["policy"]

    assert "safe browsing" not in after["drifting"]
    assert after["safe_browsing"]["value"] is True


@respx.mock
async def test_youtube_restricted_mode_is_turned_off_on_every_account() -> None:
    accounts = [
        FakeAccount("one", make_settings(["||a^"])),
        FakeAccount("two", make_settings(["||a^"])),
    ]
    for account in accounts:
        account.settings["parental_control_settings"] = {
            "enabled": True,
            "youtube_safe_search_enabled": account.label == "two",
            "engines_safe_search_enabled": True,
        }
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        pin(app, accounts)
        await signed_in(http)
        before = (await http.get("/api/policy")).json()["policy"]
        assert "YouTube restricted mode" in before["drifting"]

        await http.put("/api/policy", json={"youtube_safe_search": False})
        after = (await http.get("/api/policy")).json()["policy"]

    assert after["youtube_safe_search"] == {
        "value": False,
        "in_sync": True,
        "per_account": {"one": False, "two": False},
    }
    assert after["engines_safe_search"]["value"] is True
    assert all(a.settings["parental_control_settings"]["enabled"] for a in accounts)
