from __future__ import annotations

import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import httpx
import pytest
import respx
from conftest import make_answer
from test_fallback import PRIMARY, make_pool, mock

from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver

BLOCKED_MIN = 1800
NORMAL_MIN = 30


def cache() -> AnswerCache:
    return AnswerCache(
        max_entries=100, min_ttl=NORMAL_MIN, max_ttl=86_400, blocked_min_ttl=BLOCKED_MIN
    )


def answer(address: str, ttl: int = 30, name: str = "ads.example") -> dns.message.Message:
    query = dns.message.make_query(name, dns.rdatatype.A)
    response = dns.message.make_response(query)
    response.answer.append(dns.rrset.from_text(f"{name}.", ttl, "IN", "A", address))
    return response


def test_a_blocked_answer_gets_the_longer_floor():
    assert cache().ttl_for(answer("0.0.0.0", ttl=30), blocked=True) == BLOCKED_MIN


def test_a_normal_answer_keeps_the_short_floor():
    assert cache().ttl_for(answer("93.184.216.34", ttl=30), blocked=False) == NORMAL_MIN


def test_a_long_real_ttl_is_not_shortened_by_the_blocked_floor():
    assert cache().ttl_for(answer("0.0.0.0", ttl=7200), blocked=True) == 7200


def test_the_maximum_still_caps_a_blocked_answer():
    tight = AnswerCache(max_entries=10, min_ttl=30, max_ttl=60, blocked_min_ttl=BLOCKED_MIN)
    assert tight.ttl_for(answer("0.0.0.0", ttl=10), blocked=True) == 60


def test_an_untruncated_default_still_applies_without_the_flag():
    plain = AnswerCache(max_entries=10, min_ttl=NORMAL_MIN, max_ttl=86_400)
    assert plain.ttl_for(answer("0.0.0.0", ttl=5), blocked=True) == NORMAL_MIN


def test_a_blocked_entry_actually_outlives_the_normal_floor():
    store = cache()
    response = answer("0.0.0.0", ttl=5)
    key = store.key_for(dns.message.make_query("ads.example", dns.rdatatype.A))
    ttl = store.put(key, response, response.to_wire(), blocked=True)
    assert ttl == BLOCKED_MIN
    assert store.get(key, 1234) is not None


@respx.mock
async def test_the_resolver_holds_blocked_answers_for_the_long_floor():
    blocked = answer("0.0.0.0", ttl=10, name="ads.example")
    mock(PRIMARY, wire=blocked.to_wire())
    pool = make_pool(fallback="")
    store = cache()
    resolver = Resolver(store, pool, Metrics())

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        wire = dns.message.make_query("ads.example", dns.rdatatype.A).to_wire()
        result = await resolver.resolve(wire, "device")

    assert result.blocked is True
    assert result.ttl == BLOCKED_MIN


@respx.mock
async def test_an_allowed_answer_is_untouched_end_to_end():
    _, wire = make_answer(name="example.com", address="93.184.216.34", ttl=45)
    mock(PRIMARY, wire=wire)
    pool = make_pool(fallback="")
    store = cache()
    resolver = Resolver(store, pool, Metrics())

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
        result = await resolver.resolve(query, "device")

    assert result.blocked is False
    assert result.ttl == 45


def test_a_blocked_answer_is_held_long_but_advertised_short():
    store = cache()
    response = answer("0.0.0.0", ttl=10)
    key = store.key_for(dns.message.make_query("ads.example", dns.rdatatype.A))

    held = store.put(key, response, response.to_wire(), blocked=True)
    served = dns.message.from_wire(store.get(key, 99))

    assert held == BLOCKED_MIN
    assert served.answer[0].ttl == NORMAL_MIN


def test_the_advertised_ttl_does_not_decay_towards_one():
    store = cache()
    response = answer("0.0.0.0", ttl=10)
    key = store.key_for(dns.message.make_query("ads.example", dns.rdatatype.A))
    store.put(key, response, response.to_wire(), blocked=True)

    first = dns.message.from_wire(store.get(key, 1)).answer[0].ttl
    second = dns.message.from_wire(store.get(key, 2)).answer[0].ttl

    assert first == second == NORMAL_MIN


def test_unblocking_reaches_a_device_within_the_advertised_ttl():
    store = cache()
    response = answer("0.0.0.0", ttl=10)
    key = store.key_for(dns.message.make_query("ads.example", dns.rdatatype.A))
    store.put(key, response, response.to_wire(), blocked=True)

    advertised = dns.message.from_wire(store.get(key, 99)).answer[0].ttl
    assert advertised <= NORMAL_MIN, "a device must re-ask long before the hold expires"

    store.clear()
    assert store.get(key, 99) is None


def test_a_short_normal_answer_is_lifted_to_its_own_floor():
    store = cache()
    response = answer("93.184.216.34", ttl=5, name="ok.example")
    key = store.key_for(dns.message.make_query("ok.example", dns.rdatatype.A))
    store.put(key, response, response.to_wire(), blocked=False)

    served = dns.message.from_wire(store.get(key, 99))
    assert served.answer[0].ttl == NORMAL_MIN


def test_a_long_answer_is_never_inflated():
    store = cache()
    response = answer("93.184.216.34", ttl=7200, name="ok.example")
    key = store.key_for(dns.message.make_query("ok.example", dns.rdatatype.A))
    store.put(key, response, response.to_wire(), blocked=False)

    served = dns.message.from_wire(store.get(key, 99))
    assert served.answer[0].ttl == 7200


def blocked_answer(name: str, address: str, rdtype: str = "A"):
    import dns.message
    import dns.rdatatype
    import dns.rrset

    query = dns.message.make_query(name, dns.rdatatype.from_text(rdtype))
    response = dns.message.make_response(query)
    response.answer.append(dns.rrset.from_text(f"{name}.", 300, "IN", rdtype, address))
    return response


@pytest.mark.parametrize(
    "address,rdtype,expected",
    [
        ("0.0.0.0", "A", True),
        ("94.140.14.35", "A", True),
        ("94.140.14.36", "A", True),
        ("::", "AAAA", True),
        ("::ffff:5e8c:e23", "AAAA", True),
        ("::ffff:5e8c:e24", "AAAA", True),
        ("93.184.216.34", "A", False),
        ("94.140.14.14", "A", False),
        ("2606:2800:220:1:248:1893:25c8:1946", "AAAA", False),
    ],
)
def test_adguard_sinkholes_count_as_blocked(address, rdtype, expected) -> None:
    from hydra_dns.resolver.service import _looks_blocked

    assert _looks_blocked(blocked_answer("x.example", address, rdtype)) is expected


def negative(name: str, rdtype: str, mname: str, rcode=dns.rcode.NOERROR) -> dns.message.Message:
    response = dns.message.make_response(dns.message.make_query(name, rdtype))
    response.set_rcode(rcode)
    response.authority.append(
        dns.rrset.from_text(
            f"{name}.", 10, "IN", "SOA", f"{mname} hostmaster.{name}. 100500 1800 900 604800 86400"
        )
    )
    return response


@pytest.mark.parametrize("rdtype", ["HTTPS", "SVCB", "TXT", "MX"])
def test_adguards_empty_block_answer_for_other_types_counts_as_blocked(rdtype) -> None:
    from hydra_dns.resolver.service import _looks_blocked

    blocked = negative("beacons5.gvt3.com", rdtype, "fake-for-negative-caching.adguard.com.")
    assert _looks_blocked(blocked)


def test_the_fake_soa_also_marks_a_blocked_nxdomain() -> None:
    from hydra_dns.resolver.service import _looks_blocked

    blocked = negative(
        "ads.example", "A", "Fake-For-Negative-Caching.AdGuard.com.", dns.rcode.NXDOMAIN
    )
    assert _looks_blocked(blocked)


def test_a_real_empty_answer_is_not_a_block() -> None:
    from hydra_dns.resolver.service import _looks_blocked

    assert not _looks_blocked(negative("example.com", "HTTPS", "elliott.ns.cloudflare.com."))
    assert not _looks_blocked(
        negative("nope.example.com", "A", "elliott.ns.cloudflare.com.", dns.rcode.NXDOMAIN)
    )


def test_a_fake_soa_beside_a_real_answer_is_not_a_block() -> None:
    from hydra_dns.resolver.service import _looks_blocked

    response = negative("www.google.com", "HTTPS", "fake-for-negative-caching.adguard.com.")
    response.answer.append(
        dns.rrset.from_text("www.google.com.", 10, "IN", "CNAME", "forcesafesearch.google.com.")
    )
    assert not _looks_blocked(response)


@respx.mock
async def test_a_blocked_https_query_is_logged_and_held_as_blocked() -> None:
    blocked = negative("beacons5.gvt3.com", "HTTPS", "fake-for-negative-caching.adguard.com.")
    mock(PRIMARY, wire=blocked.to_wire())
    pool = make_pool(fallback="")
    resolver = Resolver(cache(), pool, Metrics())

    async with httpx.AsyncClient() as client:
        pool.attach_client(client)
        query = dns.message.make_query("beacons5.gvt3.com", "HTTPS").to_wire()
        first = await resolver.resolve(query, "phone")
        second = await resolver.resolve(query, "phone")

    assert first.blocked and not first.cached
    assert second.blocked and second.cached
    assert first.ttl == BLOCKED_MIN


def test_the_sentinel_list_is_matched_by_value_not_by_spelling() -> None:
    from hydra_dns.resolver.service import is_sentinel

    assert is_sentinel("94.140.14.35")
    assert is_sentinel("::ffff:5e8c:e23")
    assert is_sentinel("::ffff:94.140.14.35")
    assert not is_sentinel("94.140.14.37")
    assert not is_sentinel("not an address")
