from __future__ import annotations

import time

import dns.message
import dns.rdatatype
import httpx
import respx
from conftest import make_answer
from test_doh import FALLBACK_URL, UPSTREAM, client, mock_upstream

from hydra_dns import pauses

PASSWORD = "hunter2"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


def mock_fallback(name: str = "example.com") -> respx.Route:
    from hydra_dns.main import app

    app.state.pool.set_fallback(FALLBACK_URL)
    return respx.post(FALLBACK_URL).mock(
        return_value=httpx.Response(200, content=make_answer(name, "203.0.113.7")[1])
    )


def test_a_hold_expires_on_its_own() -> None:
    held = pauses.Pauses()
    held.hold("laptop", 15)
    assert held.active("laptop")

    held._until["laptop"] = time.time() - 1
    assert not held.active("laptop")
    assert held.until("laptop") == 0.0
    assert held.snapshot() == {}


def test_a_hold_is_bounded_at_both_ends() -> None:
    held = pauses.Pauses()
    assert held.hold("a", 0) - time.time() <= 61
    assert held.hold("b", 99_999) - time.time() <= pauses.MAX_MINUTES * 60 + 1


def test_releasing_says_whether_there_was_anything_to_release() -> None:
    held = pauses.Pauses()
    assert held.release("laptop") is False
    held.hold("laptop", 5)
    assert held.release("laptop") is True
    assert not held.active("laptop")


def test_one_device_pausing_leaves_the_others_filtered() -> None:
    held = pauses.Pauses()
    held.hold("laptop", 5)
    assert held.active("laptop")
    assert not held.active("phone")


@respx.mock
async def test_a_paused_device_stops_being_filtered_then_starts_again() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        mock_fallback()
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop", rules=("ads.example",))]
        store._compiled = {}
        store.pauses.clear()

        query = dns.message.make_query("img.ads.example", dns.rdatatype.A).to_wire()
        before = await app.state.resolver.resolve(query, "laptop")

        paused = await http.post("/api/devices/laptop/pause", json={"minutes": 15})
        during = await app.state.resolver.resolve(query, "laptop")

        resumed = await http.delete("/api/devices/laptop/pause")
        after = await app.state.resolver.resolve(query, "laptop")

    assert before.blocked is True
    assert paused.status_code == 200
    assert paused.json()["paused_until"] > time.time()
    assert during.blocked is False
    assert during.upstream == "paused → fallback"
    assert resumed.json()["paused_until"] is None
    assert after.blocked is True


@respx.mock
async def test_a_pause_lapsing_puts_the_filtering_back_with_no_second_call() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop", rules=("ads.example",))]
        store._compiled = {}
        store.pauses.clear()

        await http.post("/api/devices/laptop/pause", json={"minutes": 15})
        store.pauses._until["laptop"] = time.time() - 1

        query = dns.message.make_query("img.ads.example", dns.rdatatype.A).to_wire()
        after = await app.state.resolver.resolve(query, "laptop")

    assert after.blocked is True


@respx.mock
async def test_safe_search_is_paused_with_everything_else() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop", safe_search=True)]
        store._compiled = {}
        store.pauses.clear()
        mock_fallback("www.google.com")

        await http.post("/api/devices/laptop/pause", json={"minutes": 15})
        query = dns.message.make_query("www.google.com", dns.rdatatype.A).to_wire()
        during = await app.state.resolver.resolve(query, "laptop")

    assert during.upstream == "paused → fallback"


@respx.mock
async def test_a_paused_device_gets_past_the_accounts_own_blocklists() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream(make_answer("telemetry.example", "0.0.0.0")[1])
    async with client() as http:
        await signed_in(http)
        fallback = mock_fallback("telemetry.example")
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop"), Device(id="tv", name="TV")]
        store._compiled = {}
        store.pauses.clear()

        query = dns.message.make_query("telemetry.example", dns.rdatatype.A).to_wire()
        warmed = await app.state.resolver.resolve(query, "tv")

        await http.post("/api/devices/laptop/pause", json={"minutes": 15})
        paused = await app.state.resolver.resolve(query, "laptop")
        other = await app.state.resolver.resolve(query, "tv")

    assert warmed.blocked is True
    assert paused.blocked is False
    assert paused.cached is False
    assert fallback.call_count == 1
    assert other.blocked is True
    assert other.cached is True


@respx.mock
async def test_a_pause_still_answers_when_the_fallback_is_down() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.pool.set_fallback(FALLBACK_URL)
        respx.post(FALLBACK_URL).mock(side_effect=httpx.ConnectError("down"))
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._compiled = {}
        store.pauses.clear()

        await http.post("/api/devices/laptop/pause", json={"minutes": 15})
        query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
        during = await app.state.resolver.resolve(query, "laptop")

    assert during.rcode == "NOERROR"
    assert during.upstream == "paused → mock"
    assert respx.calls.last.request.url == UPSTREAM


@respx.mock
async def test_the_fleet_reports_which_devices_are_paused() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop"), Device(id="tv", name="TV")]
        store.pauses.clear()

        await http.post("/api/devices/laptop/pause", json={"minutes": 30})
        listed = (await http.get("/api/devices")).json()["devices"]

    held = {d["id"]: d["paused_until"] for d in listed}
    assert held["laptop"] > time.time()
    assert held["tv"] is None


@respx.mock
async def test_an_unknown_device_cannot_be_paused() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        missing = await http.post("/api/devices/nobody/pause", json={"minutes": 15})

    assert missing.status_code == 404


@respx.mock
async def test_a_silly_pause_is_refused() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        short = await http.post("/api/devices/laptop/pause", json={"minutes": 0})
        long = await http.post("/api/devices/laptop/pause", json={"minutes": 10_000})

    assert short.status_code == 422
    assert long.status_code == 422


@respx.mock
async def test_a_stranger_cannot_pause_anything() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.post("/api/devices/laptop/pause", json={"minutes": 15})

    assert response.status_code == 401


@respx.mock
async def test_pausing_is_written_to_the_audit_trail() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.devices.pauses.clear()
        await http.post("/api/devices/laptop/pause", json={"minutes": 15})
        await http.delete("/api/devices/laptop/pause")
        trail = (await http.get("/api/audit")).json()

    actions = [entry["action"] for entry in trail["entries"]]
    assert "device.pause" in actions
    assert "device.resume" in actions
