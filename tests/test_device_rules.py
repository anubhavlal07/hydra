from __future__ import annotations

import dns.message
import dns.rdatatype
import pytest
import respx
from test_doh import client, mock_upstream

from hydra_dns import rules

PASSWORD = "hunter2"


def test_a_bare_domain_blocks_itself_and_everything_under_it() -> None:
    parsed = rules.parse(["ads.example"])
    assert parsed.blocks("ads.example")
    assert parsed.blocks("img.ads.example")
    assert not parsed.blocks("example")
    assert not parsed.blocks("notads.example")


def test_an_allow_rule_beats_a_block() -> None:
    parsed = rules.parse(["example", "@@safe.example"])
    assert parsed.blocks("example")
    assert parsed.blocks("other.example")
    assert not parsed.blocks("safe.example")
    assert not parsed.blocks("deep.safe.example")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("||ads.example^", "ads.example"),
        ("*.ads.example", "ads.example"),
        ("  ADS.example. ", "ads.example"),
    ],
)
def test_the_usual_blocklist_spellings_all_mean_the_same_domain(line, expected) -> None:
    assert rules.parse([line]).block == (expected,)


def test_comments_and_blanks_are_ignored() -> None:
    assert not rules.parse(["! a comment", "# another", "", "   "])


def test_rubbish_is_named_rather_than_silently_dropped() -> None:
    assert rules.invalid(["ads.example", "not a domain", "http://x.example"]) == [
        "not a domain",
        "http://x.example",
    ]


def test_an_empty_ruleset_blocks_nothing() -> None:
    assert not rules.parse([]).blocks("anything.example")


@respx.mock
async def test_a_device_rule_is_answered_without_touching_an_upstream() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop", rules=("ads.example",))]
        store._compiled = {}

        query = dns.message.make_query("img.ads.example", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

        clean = await app.state.resolver.resolve(
            dns.message.make_query("example.com", dns.rdatatype.A).to_wire(), "laptop"
        )

    assert result.blocked is True
    assert result.upstream == "device rule → 0.0.0.0"
    assert dns.message.from_wire(result.wire).answer[0][0].to_text() == "0.0.0.0"
    assert clean.blocked is False
    assert clean.upstream == "mock"


@respx.mock
async def test_another_device_is_unaffected_by_that_rule() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [
            Device(id="laptop", name="Work Laptop", rules=("ads.example",)),
            Device(id="phone", name="Pixel"),
        ]
        store._compiled = {}

        wire = dns.message.make_query("ads.example", dns.rdatatype.A).to_wire()
        blocked = await app.state.resolver.resolve(wire, "laptop")
        allowed = await app.state.resolver.resolve(wire, "phone")

    assert blocked.blocked is True
    assert allowed.blocked is False


@respx.mock
async def test_a_rewrite_answers_locally() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        app.state.devices._rewrites = {"nas.home": "192.168.1.5"}
        query = dns.message.make_query("nas.home", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "rewrite"
    assert result.blocked is False
    assert dns.message.from_wire(result.wire).answer[0][0].to_text() == "192.168.1.5"


@respx.mock
async def test_a_rewrite_for_the_wrong_record_type_answers_empty() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        app.state.devices._rewrites = {"nas.home": "192.168.1.5"}
        query = dns.message.make_query("nas.home", dns.rdatatype.AAAA)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "rewrite"
    assert dns.message.from_wire(result.wire).answer == []


@respx.mock
async def test_a_name_with_no_rewrite_still_goes_upstream() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        app.state.devices._rewrites = {"nas.home": "192.168.1.5"}
        query = dns.message.make_query("example.com", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "mock"


async def test_a_rewrite_must_be_a_hostname_and_an_address() -> None:
    import httpx

    from hydra_dns.store import DeviceStore, StoreError

    store = DeviceStore(httpx.AsyncClient(), [])
    with pytest.raises(StoreError, match="not a hostname"):
        await store.set_rewrite("not a host", "192.168.1.5")
    with pytest.raises(StoreError, match="not an IP address"):
        await store.set_rewrite("nas.home", "nope")


@respx.mock
async def test_nxdomain_is_an_option_for_a_block() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        app.state.devices._devices = [
            Device(id="laptop", name="Laptop", rules=("ads.example",), block_mode="nxdomain")
        ]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("ads.example", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "laptop")

    assert result.blocked is True
    assert result.rcode == "NXDOMAIN"
    assert dns.message.from_wire(result.wire).answer == []


@respx.mock
async def test_a_block_can_answer_with_a_chosen_address() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        app.state.devices._devices = [
            Device(
                id="laptop",
                name="Laptop",
                rules=("ads.example",),
                block_mode="address",
                block_address="10.0.0.9",
            )
        ]
        app.state.devices._compiled = {}
        wire = dns.message.make_query("ads.example", dns.rdatatype.A).to_wire()
        result = await app.state.resolver.resolve(wire, "laptop")

    assert result.blocked is True
    assert dns.message.from_wire(result.wire).answer[0][0].to_text() == "10.0.0.9"
    assert result.upstream == "device rule → 10.0.0.9"
