from __future__ import annotations

import dataclasses

import pytest
import respx
from test_accounts_api import mock_discovery, mock_signin, signed_in
from test_doh import client, mock_upstream

BACKUP_VERSION = 1


@pytest.fixture(autouse=True)
def store_file(tmp_path, monkeypatch):
    from hydra_dns import accounts_store

    monkeypatch.setattr(
        accounts_store,
        "settings",
        dataclasses.replace(accounts_store.settings, accounts_file=str(tmp_path / "accounts.json")),
    )
    return tmp_path / "accounts.json"


@respx.mock
async def test_a_backup_needs_a_session() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.get("/api/backup")).status_code == 401


@respx.mock
async def test_a_backup_carries_the_devices_and_their_rules() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        body = (await http.get("/api/backup")).json()

    assert body["version"] == BACKUP_VERSION
    assert body["generated_at"] > 0
    ids = {d["id"] for d in body["devices"]}
    assert ids == {"laptop", "phone"}
    for device in body["devices"]:
        assert set(device) == {
            "id",
            "name",
            "rules",
            "safe_search",
            "safe_search_skip",
            "block_mode",
            "block_address",
        }


@respx.mock
async def test_accounts_come_with_their_passwords_by_default() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        body = (await http.get("/api/backup")).json()

    assert body["accounts_included"] is True
    assert {a["username"] for a in body["accounts"]} == {
        "first@example.test",
        "second@example.test",
    }
    assert all(a["password"] for a in body["accounts"])


@respx.mock
async def test_accounts_can_be_left_out() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        body = (await http.get("/api/backup?accounts=false")).json()

    assert body["accounts_included"] is False
    assert body["accounts"] == []


@respx.mock
async def test_a_restore_needs_a_session() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.post("/api/restore", json={"version": 1})).status_code == 401


@respx.mock
async def test_a_backup_from_another_version_is_refused() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.post("/api/restore", json={"version": 999})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "bad_version"


@respx.mock
async def test_an_empty_restore_changes_nothing() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        body = (await http.post("/api/restore", json={"version": 1})).json()

    assert body["restored"] == {"devices": 0, "rewrites": 0, "accounts": 0}
    assert body["skipped"] == []


@respx.mock
async def test_an_account_that_cannot_sign_in_is_skipped_not_stored() -> None:
    mock_upstream()
    mock_signin(ok=False)
    mock_discovery()

    async with client() as http:
        await signed_in(http)
        body = (
            await http.post(
                "/api/restore",
                json={
                    "version": 1,
                    "accounts": [{"username": "three@example.test", "password": "nope"}],
                },
            )
        ).json()
        accounts = (await http.get("/api/accounts")).json()

    assert body["restored"]["accounts"] == 0
    assert any("three@example.test" in note for note in body["skipped"])
    assert "three@example.test" not in {a["username"] for a in accounts["accounts"]}


@respx.mock
async def test_an_account_already_in_the_pool_is_left_alone() -> None:
    mock_upstream()
    mock_signin()
    mock_discovery()

    async with client() as http:
        await signed_in(http)
        body = (
            await http.post(
                "/api/restore",
                json={
                    "version": 1,
                    "accounts": [{"username": "first@example.test", "password": "pw1"}],
                },
            )
        ).json()

    assert body["restored"]["accounts"] == 0
    assert body["skipped"] == []


@respx.mock
async def test_a_backup_round_trips_through_a_restore() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        saved = (await http.get("/api/backup?accounts=false")).json()
        body = (await http.post("/api/restore", json=saved)).json()

    assert body["restored"]["accounts"] == 0
    assert isinstance(body["skipped"], list)
