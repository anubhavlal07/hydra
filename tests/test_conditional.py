from __future__ import annotations

import pytest
import respx
from test_doh import client, mock_upstream

PASSWORD = "hunter2"

POLLED = ["/api/status", "/api/devices", "/api/accounts", "/api/rewrites", "/api/rule-groups"]


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


@respx.mock
@pytest.mark.parametrize("path", POLLED)
async def test_a_repeat_poll_is_answered_with_a_bare_304(path) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)

        first = await http.get(path)
        assert first.status_code == 200
        etag = first.headers.get("etag")
        assert etag

        again = await http.get(path, headers={"if-none-match": etag})

    assert again.status_code == 304
    assert again.content == b""
    assert again.headers.get("etag") == etag
    assert "content-length" not in again.headers


@respx.mock
async def test_a_stale_validator_gets_the_whole_body_back() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        fresh = await http.get("/api/devices", headers={"if-none-match": '"not-the-one"'})

    assert fresh.status_code == 200
    assert fresh.json()["devices"]
    assert fresh.headers.get("etag")


@respx.mock
async def test_the_validator_changes_when_the_fleet_does() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        before = (await http.get("/api/devices")).headers["etag"]

        app.state.devices._devices = [Device(id="laptop", name="Renamed Laptop")]
        after = await http.get("/api/devices", headers={"if-none-match": before})

    assert after.status_code == 200
    assert after.headers["etag"] != before


@respx.mock
async def test_the_live_counters_are_deliberately_not_conditional() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        overview = await http.get("/api/overview")
        resolver = await http.get("/api/resolver")

    assert overview.status_code == 200
    assert resolver.status_code == 200
    assert "etag" not in overview.headers
    assert "etag" not in resolver.headers


@respx.mock
async def test_a_304_keeps_the_hardening_and_the_request_id() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        etag = (await http.get("/api/status")).headers["etag"]
        again = await http.get("/api/status", headers={"if-none-match": etag})

    assert again.status_code == 304
    assert again.headers["cache-control"] == "no-store"
    assert again.headers["x-content-type-options"] == "nosniff"
    assert again.headers["x-request-id"]


@respx.mock
async def test_an_unauthenticated_poll_is_still_refused() -> None:
    mock_upstream()
    async with client() as http:
        first = await http.get("/api/devices")
        again = await http.get("/api/devices", headers={"if-none-match": '"anything"'})

    assert first.status_code == 401
    assert again.status_code == 401
    assert "etag" not in again.headers
