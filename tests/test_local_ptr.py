from __future__ import annotations

import dns.message
import dns.rdatatype
import respx
from test_doh import client, mock_upstream

from hydra_dns import localzones


def test_the_index_inverts_a_rewrite() -> None:
    index = localzones.ptr_index({"nas.lan": "192.168.1.9"})
    assert index["9.1.168.192.in-addr.arpa"] == ("nas.lan",)


def test_two_names_on_one_address_both_come_back() -> None:
    index = localzones.ptr_index({"nas.lan": "192.168.1.9", "store.lan": "192.168.1.9"})
    assert index["9.1.168.192.in-addr.arpa"] == ("nas.lan", "store.lan")


def test_an_unparseable_address_is_skipped_rather_than_raising() -> None:
    assert localzones.ptr_index({"bad.lan": "not-an-address"}) == {}
    assert localzones.reverse_name("") == ""


def test_an_ipv6_rewrite_indexes_under_ip6_arpa() -> None:
    index = localzones.ptr_index({"v6.lan": "fd00::1"})
    assert next(iter(index)).endswith("d.f.ip6.arpa")


@respx.mock
async def test_a_reverse_lookup_for_a_rewritten_address_is_answered_here() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._rewrites = {"nas.lan": "192.168.1.9"}
        store._ptrs = localzones.ptr_index(store._rewrites)

        query = dns.message.make_query("9.1.168.192.in-addr.arpa", dns.rdatatype.PTR)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    response = dns.message.from_wire(result.wire)
    assert result.rcode == "NOERROR"
    assert result.upstream == "rewrite"
    assert response.answer[0][0].to_text() == "nas.lan."
    assert respx.calls.call_count == 0


@respx.mock
async def test_a_private_address_with_no_rewrite_is_still_nxdomain() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._rewrites = {}
        store._ptrs = {}

        query = dns.message.make_query("77.1.168.192.in-addr.arpa", dns.rdatatype.PTR)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.rcode == "NXDOMAIN"
    assert result.upstream == "local zone"
    assert respx.calls.call_count == 0


@respx.mock
async def test_a_public_reverse_lookup_still_goes_upstream() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._rewrites = {}
        store._ptrs = {}

        query = dns.message.make_query("8.8.8.8.in-addr.arpa", dns.rdatatype.PTR)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "mock"
    assert respx.calls.call_count == 1


@respx.mock
async def test_the_index_follows_a_rewrite_being_added() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client():
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._rewrites = {}
        store._ptrs = {}
        assert store.ptr_for("9.1.168.192.in-addr.arpa") == ()

        store._rewrites = {"nas.lan": "192.168.1.9"}
        store._ptrs = localzones.ptr_index(store._rewrites)
        assert store.ptr_for("9.1.168.192.in-addr.arpa") == ("nas.lan",)
