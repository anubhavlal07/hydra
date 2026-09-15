from __future__ import annotations

import dns.message
import dns.rdatatype
import dns.rrset
import httpx
import pytest
import respx
from test_doh import UPSTREAM, client, mock_upstream

from hydra_dns import safesearch


@pytest.mark.parametrize(
    "name,expected",
    [
        ("www.google.com", "forcesafesearch.google.com"),
        ("google.com", "forcesafesearch.google.com"),
        ("google.co.in", "forcesafesearch.google.com"),
        ("google.de", "forcesafesearch.google.com"),
        ("www.youtube.com", "restrictmoderate.youtube.com"),
        ("m.youtube.com", "restrictmoderate.youtube.com"),
        ("youtubei.googleapis.com", "restrictmoderate.youtube.com"),
        ("www.bing.com", "strict.bing.com"),
        ("duckduckgo.com", "safe.duckduckgo.com"),
    ],
)
def test_the_search_engines_have_a_safe_stand_in(name, expected) -> None:
    assert safesearch.target_for(name) == expected


@pytest.mark.parametrize(
    "name",
    ["example.com", "notgoogle.com", "google.com.evil.example", "mail.google.com", ""],
)
def test_everything_else_is_left_alone(name) -> None:
    assert safesearch.target_for(name) is None


def answer_for(name: str, address: str) -> bytes:
    query = dns.message.make_query(name, dns.rdatatype.A)
    response = dns.message.make_response(query)
    response.answer.append(dns.rrset.from_text(f"{name}.", 300, "IN", "A", address))
    return response.to_wire()


@respx.mock
async def test_a_device_with_safe_search_gets_the_safe_address_under_the_asked_for_name() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    respx.post(UPSTREAM).mock(
        return_value=httpx.Response(
            200, content=answer_for("forcesafesearch.google.com", "216.239.38.120")
        )
    )

    async with client():
        app.state.devices._devices = [Device(id="kid", name="Kid", safe_search=True)]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("www.google.com", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "kid")

    assert result.upstream == "safe search"
    assert result.blocked is False

    answer = dns.message.from_wire(result.wire).answer[0]
    assert answer.name.to_text(omit_final_dot=True) == "www.google.com"
    assert answer[0].to_text() == "216.239.38.120"


@respx.mock
async def test_a_device_without_it_resolves_google_normally() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        app.state.devices._devices = [Device(id="laptop", name="Laptop", safe_search=False)]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("www.google.com", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "laptop")

    assert result.upstream == "mock"


@respx.mock
async def test_a_blocked_domain_stays_blocked_even_with_safe_search_on() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        app.state.devices._devices = [
            Device(id="kid", name="Kid", safe_search=True, rules=("google.com",))
        ]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("www.google.com", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "kid")

    assert result.blocked is True


@respx.mock
async def test_an_unresolvable_safe_host_falls_through_rather_than_failing() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    empty = dns.message.make_response(
        dns.message.make_query("forcesafesearch.google.com", dns.rdatatype.A)
    )
    seen = {"n": 0}

    def reply(request):
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(200, content=empty.to_wire())
        return httpx.Response(200, content=answer_for("www.google.com", "142.250.0.1"))

    respx.post(UPSTREAM).mock(side_effect=reply)

    async with client():
        app.state.devices._devices = [Device(id="kid", name="Kid", safe_search=True)]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("www.google.com", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "kid")

    assert result.upstream == "mock"
    assert result.blocked is False


async def test_a_block_address_must_be_an_address_or_a_hostname() -> None:
    from hydra_dns.store import DeviceStore, StoreError

    store = DeviceStore(httpx.AsyncClient(), [])
    with pytest.raises(StoreError, match="block mode must be"):
        await store.set_policy("laptop", False, "sideways", "")
    with pytest.raises(StoreError, match="needs somewhere to point"):
        await store.set_policy("laptop", False, "address", "")
    with pytest.raises(StoreError, match="not an address or a hostname"):
        await store.set_policy("laptop", False, "address", "not a host!")


@respx.mock
async def test_the_devices_list_reports_each_policy() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    async with client() as http:
        await http.post("/api/login", json={"password": "hunter2"})
        app.state.devices._devices = [
            Device(id="kid", name="Kid", safe_search=True),
            Device(id="tv", name="TV", block_mode="nxdomain"),
            Device(id="pc", name="PC", block_mode="address", block_address="10.0.0.9"),
        ]
        app.state.devices._compiled = {}
        body = (await http.get("/api/devices")).json()

    found = {d["id"]: d for d in body["devices"]}
    assert found["kid"]["safe_search"] is True
    assert found["tv"]["block_mode"] == "nxdomain"
    assert found["pc"]["block_address"] == "10.0.0.9"
    assert found["kid"]["block_mode"] == "zero"
    assert found["tv"]["safe_search"] is False


@pytest.mark.parametrize(
    "name",
    ["www.youtube.com", "m.youtube.com", "youtubei.googleapis.com", "youtube.googleapis.com"],
)
def test_a_skipped_platform_is_left_alone(name) -> None:
    assert safesearch.target_for(name, ("youtube",)) is None


def test_skipping_youtube_still_filters_google() -> None:
    assert safesearch.target_for("www.google.com", ("youtube",)) == "forcesafesearch.google.com"
    assert safesearch.target_for("www.google.com", ("google",)) is None


def test_every_platform_can_be_skipped() -> None:
    assert safesearch.normalise_skip(["Pixiv", "youtube", "youtube"]) == ("youtube", "pixiv")
    with pytest.raises(ValueError, match="does not cover tiktok"):
        safesearch.normalise_skip(["tiktok"])


@respx.mock
async def test_a_device_skipping_youtube_resolves_it_normally() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        app.state.devices._devices = [
            Device(id="moto", name="Moto", safe_search=True, safe_search_skip=("youtube",))
        ]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("youtubei.googleapis.com", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "moto")

    assert result.upstream == "mock"


STORE_RPC = "https://store.test/rest/v1/rpc"


def configured_store(monkeypatch):
    import dataclasses

    from hydra_dns import store as store_module
    from hydra_dns.store import Device, DeviceStore

    monkeypatch.setattr(
        store_module,
        "settings",
        dataclasses.replace(
            store_module.settings,
            store_url="https://store.test",
            store_key="test-key",
            store_secret="test-secret",
            device_cache_file="",
        ),
    )
    store = DeviceStore(httpx.AsyncClient(), [])
    store._adopt([Device(id="moto", name="Moto", safe_search=True)])
    respx.post(f"{STORE_RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "moto", "name": "Moto", "safe_search": True, "safe_search_skip": ["youtube"]}
            ],
        )
    )
    return store


@respx.mock
async def test_a_policy_write_sends_the_skip_list(monkeypatch) -> None:
    import json

    store = configured_store(monkeypatch)
    written = respx.post(f"{STORE_RPC}/hydra_device_policy_set").mock(
        return_value=httpx.Response(200, json=[])
    )

    device = await store.set_policy("moto", True, "zero", "", ["YouTube"])

    assert json.loads(written.calls[0].request.content)["p_safe_search_skip"] == ["youtube"]
    assert device.safe_search_skip == ("youtube",)


@respx.mock
async def test_a_policy_write_without_a_skip_list_keeps_the_stored_one(monkeypatch) -> None:
    import json

    store = configured_store(monkeypatch)
    written = respx.post(f"{STORE_RPC}/hydra_device_policy_set").mock(
        return_value=httpx.Response(200, json=[])
    )

    await store.set_policy("moto", True, "nxdomain", "")

    assert "p_safe_search_skip" not in json.loads(written.calls[0].request.content)


@respx.mock
async def test_an_unknown_platform_is_refused_before_the_write(monkeypatch) -> None:
    from hydra_dns.store import StoreError

    store = configured_store(monkeypatch)
    written = respx.post(f"{STORE_RPC}/hydra_device_policy_set")

    with pytest.raises(StoreError, match="does not cover"):
        await store.set_policy("moto", True, "zero", "", ["tiktok"])
    assert not written.called
