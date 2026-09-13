from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx
from test_doh import client, mock_upstream

PASSWORD = "hunter2"
TOKEN_URL = "https://api.adguard-dns.io/oapi/v1/oauth_token"


@pytest.fixture(autouse=True)
def store_file(tmp_path, monkeypatch):
    from hydra_dns import accounts_store

    monkeypatch.setattr(
        accounts_store,
        "settings",
        dataclasses.replace(accounts_store.settings, accounts_file=str(tmp_path / "accounts.json")),
    )
    return tmp_path / "accounts.json"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


def mock_signin(ok: bool = True) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200 if ok else 401,
            json=(
                {"access_token": "t", "refresh_token": "r", "expires_in": 3600}
                if ok
                else {"error": "invalid_grant", "error_description": "bad password"}
            ),
        )
    )


def mock_discovery() -> None:
    respx.get("https://api.adguard-dns.io/oapi/v2/devices").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "dev1",
                        "name": "Hydra DNS",
                        "dns_server_id": "srv1",
                        "dns_addresses": {
                            "dns_over_https_url": "https://d.adguard-dns.test/dns-query/dev1"
                        },
                    }
                ]
            },
        )
    )
    respx.get("https://api.adguard-dns.io/oapi/v1/account/limits").mock(
        return_value=httpx.Response(200, json={"requests": {"used": 0, "limit": 300000}})
    )


def store_of(app):
    from hydra_dns.accounts_store import AccountStore

    fresh = AccountStore(app.state.account_store.accounts)
    fresh.load()
    app.state.account_store = fresh
    return fresh


@respx.mock
async def test_accounts_reports_whether_it_can_be_edited() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        await signed_in(http)
        body = (await http.get("/api/accounts")).json()

    assert body["store"]["configured"] is True
    assert body["store"]["count"] >= 1


@respx.mock
async def test_a_working_account_is_added_to_the_live_pool() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        before = len(app.state.manager.accounts)
        await signed_in(http)
        response = await http.post(
            "/api/accounts", json={"username": "New91101@Example.test", "password": "pw"}
        )

        assert response.status_code == 201, response.text
        assert len(app.state.manager.accounts) == before + 1
        assert any(a.label == "alpha" for a in store.accounts)


@respx.mock
async def test_health_counts_the_pool_it_has_now_not_the_one_it_booted_with() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        await signed_in(http)

        before = (await http.get("/api/health?detail=1")).json()["accounts"]
        assert before == len(app.state.manager.accounts)

        added = await http.post(
            "/api/accounts", json={"username": "New91101@Example.test", "password": "pw"}
        )
        assert added.status_code == 201, added.text

        after = (await http.get("/api/health?detail=1")).json()["accounts"]
        assert after == before + 1
        assert after == len(app.state.manager.accounts)


@respx.mock
async def test_the_address_is_normalised() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        await http.post(
            "/api/accounts", json={"username": "  MiXeD@Example.Test  ", "password": "pw"}
        )

    assert any(a.username == "mixed@example.test" for a in store.accounts)


@respx.mock
async def test_a_password_adguard_rejects_is_not_stored() -> None:
    mock_upstream()
    mock_signin(ok=False)
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        before = len(app.state.manager.accounts)
        await signed_in(http)
        response = await http.post(
            "/api/accounts", json={"username": "bad@example.test", "password": "wrong"}
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_credentials"
    assert not any(a.username == "bad@example.test" for a in store.accounts)
    assert len(app.state.manager.accounts) == before


@respx.mock
async def test_an_account_already_in_the_pool_is_refused() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        existing = store.accounts[0].username
        await signed_in(http)
        response = await http.post(
            "/api/accounts", json={"username": existing.upper(), "password": "pw"}
        )

    assert response.status_code == 409


@respx.mock
async def test_an_explicit_label_is_honoured() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        await http.post(
            "/api/accounts",
            json={"username": "x@example.test", "password": "pw", "label": "spare"},
        )

    assert any(a.label == "spare" for a in store.accounts)


@respx.mock
async def test_removing_takes_it_out_of_the_pool_and_the_store() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        await http.post("/api/accounts", json={"username": "gone@example.test", "password": "pw"})
        assert any(a.username == "gone@example.test" for a in store.accounts)

        response = await http.delete("/api/accounts/alpha")

        assert response.status_code == 200
        assert not any(a.username == "gone@example.test" for a in store.accounts)
        assert not any(a.label == "alpha" for a in app.state.manager.accounts)


@respx.mock
async def test_the_last_account_cannot_be_removed_through_the_api() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        labels = [a.label for a in store.accounts]
        for label in labels[:-1]:
            assert (await http.delete(f"/api/accounts/{label}")).status_code == 200

        response = await http.delete(f"/api/accounts/{labels[-1]}")

    assert response.status_code == 400
    assert "only account left" in response.json()["detail"]["message"]
    assert len(store.accounts) == 1


@respx.mock
async def test_removing_an_unknown_label_says_so() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        await signed_in(http)
        response = await http.delete("/api/accounts/nope")

    assert response.status_code == 400


@respx.mock
async def test_account_management_needs_a_session() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        assert (
            await http.post("/api/accounts", json={"username": "a@b.test", "password": "p"})
        ).status_code == 401
        assert (await http.delete("/api/accounts/one")).status_code == 401


@respx.mock
async def test_no_password_is_ever_returned() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        await signed_in(http)
        created = await http.post(
            "/api/accounts", json={"username": "secret@example.test", "password": "s3kr3t"}
        )
        listed = await http.get("/api/accounts")

    assert "s3kr3t" not in created.text
    assert "s3kr3t" not in listed.text
    assert "secret@example.test" not in listed.text


@respx.mock
async def test_turning_an_account_off_takes_it_out_of_the_rotation() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        label = store.accounts[0].label
        pool = app.state.pool

        response = await http.patch(f"/api/accounts/{label}", json={"enabled": False})

        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is False
        assert store.get(label).enabled is False
        account = next(a for a in app.state.manager.accounts if a.label == label)
        assert account.enabled is False
        assert all(u.label != label for u in pool.order())


@respx.mock
async def test_turning_it_back_on_puts_it_back() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        label = store.accounts[0].label

        await http.patch(f"/api/accounts/{label}", json={"enabled": False})
        response = await http.patch(f"/api/accounts/{label}", json={"enabled": True})

        assert response.status_code == 200, response.text
        assert response.json()["enabled"] is True
        assert store.get(label).enabled is True
        assert any(u.label == label for u in app.state.pool.order())


@respx.mock
async def test_the_last_enabled_account_cannot_be_turned_off_through_the_api() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        labels = [a.label for a in store.accounts]
        for label in labels[:-1]:
            assert (
                await http.patch(f"/api/accounts/{label}", json={"enabled": False})
            ).status_code == 200

        response = await http.patch(f"/api/accounts/{labels[-1]}", json={"enabled": False})

    assert response.status_code == 400
    assert "only account still taking traffic" in response.json()["detail"]["message"]
    assert store.enabled_count == 1


@respx.mock
async def test_the_listing_says_which_accounts_are_on() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        label = store.accounts[0].label
        await http.patch(f"/api/accounts/{label}", json={"enabled": False})
        body = (await http.get("/api/accounts")).json()

    rows = {row["label"]: row for row in body["accounts"]}
    assert rows[label]["enabled"] is False
    assert body["store"]["enabled_count"] == len(rows) - 1
    assert body["store"]["min_enabled"] == 1


@respx.mock
async def test_a_disabled_account_drops_out_of_the_pooled_headroom() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store = store_of(app)
        await signed_in(http)
        for account in app.state.manager.accounts:
            account.limits = {"requests": {"used": 1_000, "limit": 300_000}}

        before = (await http.get("/api/overview")).json()
        await http.patch(f"/api/accounts/{store.accounts[0].label}", json={"enabled": False})
        after = (await http.get("/api/overview")).json()

    assert after["quota"]["requests_limit"] < before["quota"]["requests_limit"]
    assert after["quota"]["accounts_disabled"] == 1
    assert after["accounts"]["enabled"] == before["accounts"]["enabled"] - 1


@respx.mock
async def test_toggling_an_unknown_label_says_so() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        await signed_in(http)
        response = await http.patch("/api/accounts/nope", json={"enabled": False})

    assert response.status_code == 400


@respx.mock
async def test_toggling_needs_a_session() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        store_of(app)
        assert (await http.patch("/api/accounts/one", json={"enabled": False})).status_code == 401
