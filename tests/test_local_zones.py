from __future__ import annotations

import dns.message
import dns.rcode
import dns.rdatatype
import pytest
import respx
from test_doh import client, mock_upstream

from hydra_dns import localzones

ZONES = localzones.Zones(localzones.DEFAULT_ZONES)


@pytest.mark.parametrize(
    "name,zone",
    [
        ("printer.local", "local"),
        ("_companion-link._tcp.local", "local"),
        ("LOCAL", "local"),
        ("nas.home.arpa", "home.arpa"),
        ("thing.lan", "lan"),
        ("1.0.0.10.in-addr.arpa", "10.in-addr.arpa"),
        ("4.3.2.1.168.192.in-addr.arpa", "168.192.in-addr.arpa"),
        ("9.31.172.in-addr.arpa", "31.172.in-addr.arpa"),
        (
            "1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.b.e.f.ip6.arpa",
            "b.e.f.ip6.arpa",
        ),
    ],
)
def test_a_special_use_name_is_matched_to_its_zone(name, zone) -> None:
    assert ZONES.zone_for(name) == zone


@pytest.mark.parametrize("name", ["example.com", "notlocal.com", "arpa", "in-addr.arpa", ""])
def test_an_ordinary_name_matches_nothing(name) -> None:
    assert ZONES.zone_for(name) is None


def test_the_public_172_ranges_are_left_alone() -> None:
    assert ZONES.zone_for("1.2.15.172.in-addr.arpa") is None
    assert ZONES.zone_for("1.2.32.172.in-addr.arpa") is None


def test_localhost_resolves_to_loopback_rather_than_nowhere() -> None:
    assert ZONES.verdict_for("localhost", "A").address == "127.0.0.1"
    assert ZONES.verdict_for("localhost", "AAAA").address == "::1"
    assert ZONES.verdict_for("localhost", "MX").exists is True
    assert ZONES.verdict_for("printer.local", "A").exists is False


def test_the_reserved_dev_tld_is_left_resolvable_by_default() -> None:
    assert ZONES.zone_for("app.test") is None
    assert localzones.Zones(localzones.parse("test")).zone_for("app.test") == "test"


def test_an_empty_setting_turns_the_whole_thing_off() -> None:
    assert not localzones.Zones(localzones.parse(""))
    assert localzones.Zones(localzones.parse("")).zone_for("printer.local") is None


def test_the_parser_drops_blanks_dots_and_duplicates() -> None:
    assert localzones.parse(" LAN. , lan, , home.arpa ") == ("lan", "home.arpa")


@respx.mock
async def test_a_special_use_name_never_reaches_an_upstream() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        query = dns.message.make_query("printer.local", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    response = dns.message.from_wire(result.wire)
    assert result.upstream == "local zone"
    assert result.rcode == "NXDOMAIN"
    assert result.blocked is False
    assert response.authority[0].rdtype == dns.rdatatype.SOA
    assert respx.calls.call_count == 0


@respx.mock
async def test_a_private_reverse_lookup_never_reaches_an_upstream() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        query = dns.message.make_query("1.0.0.10.in-addr.arpa", dns.rdatatype.PTR)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.rcode == "NXDOMAIN"
    assert result.upstream == "local zone"
    assert respx.calls.call_count == 0


@respx.mock
async def test_localhost_is_answered_with_the_loopback_address() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        query = dns.message.make_query("localhost", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    response = dns.message.from_wire(result.wire)
    assert result.rcode == "NOERROR"
    assert result.upstream == "local zone → 127.0.0.1"
    assert response.answer[0][0].to_text() == "127.0.0.1"
    assert respx.calls.call_count == 0


@respx.mock
async def test_an_ordinary_name_still_goes_upstream() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        query = dns.message.make_query("example.com", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "mock"
    assert respx.calls.call_count == 1


@respx.mock
async def test_a_rewrite_still_wins_over_a_local_zone() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._rewrites = {"nas.lan": "192.168.1.9"}

        query = dns.message.make_query("nas.lan", dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "rewrite"
    assert dns.message.from_wire(result.wire).answer[0][0].to_text() == "192.168.1.9"
