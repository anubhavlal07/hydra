from __future__ import annotations

import respx
from test_doh import client, mock_upstream

PASSWORD = "hunter2"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


@respx.mock
async def test_one_poll_carries_what_three_used_to() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/pulse")

    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"overview", "resolver", "health"}
    assert body["overview"]["service"]
    assert "summary" in body["resolver"]
    assert body["health"]["status"]


@respx.mock
async def test_each_part_matches_the_endpoint_it_replaces() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        pulse = (await http.get("/api/pulse?minutes=30")).json()
        overview = (await http.get("/api/overview")).json()
        resolver = (await http.get("/api/resolver?minutes=30")).json()

    assert pulse["overview"].keys() == overview.keys()
    assert pulse["resolver"].keys() == resolver.keys()
    assert pulse["overview"]["service"] == overview["service"]
    assert pulse["resolver"]["summary"].keys() == resolver["summary"].keys()


@respx.mock
async def test_the_window_is_passed_through() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        narrow = (await http.get("/api/pulse?minutes=5")).json()
        wide = (await http.get("/api/pulse?minutes=1440")).json()

    assert len(narrow["resolver"]["timeline"]) < len(wide["resolver"]["timeline"])


@respx.mock
async def test_a_silly_window_is_refused_the_same_way() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        assert (await http.get("/api/pulse?minutes=0")).status_code == 422
        assert (await http.get("/api/pulse?minutes=99999")).status_code == 422


@respx.mock
async def test_a_stranger_gets_nothing() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.get("/api/pulse")

    assert response.status_code == 401


@respx.mock
async def test_the_pulse_is_not_conditional() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/pulse")

    assert "etag" not in response.headers
