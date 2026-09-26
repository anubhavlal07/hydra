from __future__ import annotations

import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import pytest
from conftest import make_answer
from test_blocklist_resolver import build, store_with, with_list

from hydra_dns import designated, localzones
from hydra_dns.config import settings
from hydra_dns.store import Device, RuleGroup

QUERY_ID = 4242


def ask(name: str, qtype: str = "A") -> dns.message.Message:
    query = dns.message.make_query(name, dns.rdatatype.from_text(qtype))
    query.id = QUERY_ID
    return query


def answered(query: dns.message.Message, qtype: str, *values: str, ttl: int) -> bytes:
    response = dns.message.make_response(query)
    if values:
        response.answer.append(
            dns.rrset.from_text_list(query.question[0].name, ttl, "IN", qtype, list(values))
        )
    return response.to_wire()


def refused_as(query: dns.message.Message, rcode: int) -> bytes:
    response = dns.message.make_response(query)
    response.set_rcode(rcode)
    return response.to_wire()


def forwarded(query: dns.message.Message) -> bytes:
    response, _ = make_answer(name=query.question[0].name.to_text(omit_final_dot=True))
    response.id = QUERY_ID
    return response.to_wire()


def zone_answer(query: dns.message.Message, zone: str, *, nxdomain: bool) -> bytes:
    ttl = settings.local_zone_ttl_s
    response = dns.message.make_response(query)
    if nxdomain:
        response.set_rcode(dns.rcode.NXDOMAIN)
    response.authority.append(
        dns.rrset.from_text(
            f"{zone}.", ttl, "IN", "SOA", f"localhost. nobody.invalid. 1 3600 900 604800 {ttl}"
        )
    )
    return response.to_wire()


def fleet():
    store = store_with(
        Device(id="tv", name="TV", tags=("kids",), rules=("own.test",)),
        Device(id="nx", name="NX", block_mode="nxdomain", rules=("own.test",)),
        Device(id="pixel", name="Pixel", rules=("@@ads.example.com",)),
        Device(id="kid", name="Kid", safe_search=True),
        Device(id="laptop", name="Laptop"),
    )
    store._adopt_groups(
        [
            RuleGroup(id="bedtime", name="Bedtime", rules=("tiktok.test",), tags=("kids",)),
            RuleGroup(id="homework", name="Homework", rules=("@@tracker.test",), tags=("kids",)),
        ]
    )
    store._rewrites = {"nas.lan.test": "192.168.1.10"}
    store._ptrs = localzones.ptr_index(store._rewrites)
    lists = with_list(store, name="hagezi-pro", list_id="hagezi-pro")
    return store, lists


CASES = [
    ("nas.lan.test", "A", "laptop", "rewrite"),
    ("nas.lan.test", "AAAA", "laptop", "rewrite"),
    ("10.1.168.192.in-addr.arpa", "PTR", "laptop", "rewrite"),
    ("printer.home.arpa", "A", "laptop", "local zone"),
    ("own.test", "A", "tv", "device rule → 0.0.0.0"),
    ("own.test", "AAAA", "tv", "device rule → ::"),
    ("own.test", "A", "nx", "device rule → nxdomain"),
    ("tiktok.test", "A", "tv", "device rule → 0.0.0.0"),
    ("img.ads.example.com", "A", "tv", "blocklist hagezi-pro → 0.0.0.0"),
    ("ads.example.com", "A", "pixel", "mock"),
    ("tracker.test", "A", "tv", "mock"),
    ("example.org", "A", "laptop", "mock"),
]


def expected_for(query: dns.message.Message, upstream: str, device: str) -> bytes:
    qtype = dns.rdatatype.to_text(query.question[0].rdtype)
    if upstream == "rewrite" and qtype == "PTR":
        return answered(query, "PTR", "nas.lan.test.", ttl=settings.rewrite_ttl_s)
    if upstream == "rewrite":
        values = ("192.168.1.10",) if qtype == "A" else ()
        return answered(query, qtype, *values, ttl=settings.rewrite_ttl_s)
    if upstream == "local zone":
        return zone_answer(query, "home.arpa", nxdomain=True)
    if upstream.endswith("nxdomain"):
        return refused_as(query, dns.rcode.NXDOMAIN)
    if "→" in upstream:
        return answered(query, qtype, upstream.split("→ ")[1], ttl=settings.cache_min_ttl_s)
    return forwarded(query)


@pytest.mark.parametrize("name,qtype,device,upstream", CASES)
async def test_resolve_answers_are_byte_identical_through_decide(
    name, qtype, device, upstream
) -> None:
    store, lists = fleet()
    resolver, _, _ = build(store, lists)
    query = ask(name, qtype)

    result = await resolver.resolve(query.to_wire(), device)

    assert result.upstream == upstream
    assert result.wire == expected_for(query, upstream, device)


async def test_a_paused_device_answers_through_the_fallback_unchanged() -> None:
    store, lists = fleet()
    resolver, pool, _ = build(store, lists)
    store.pauses.hold("tv", 15)
    query = ask("own.test")

    result = await resolver.resolve(query.to_wire(), "tv")

    assert result.upstream == "paused → fallback"
    assert result.wire == forwarded(query)
    assert pool.unfiltered == 1


async def test_safe_search_still_stands_in_under_the_asked_for_name() -> None:
    store, lists = fleet()
    resolver, _, _ = build(store, lists)
    query = ask("www.google.com")

    result = await resolver.resolve(query.to_wire(), "kid")

    assert result.upstream == "safe search"
    assert result.wire == answered(query, "A", "93.184.216.34", ttl=settings.safe_search_ttl_s)


def layer_of(resolver, name: str, device: str, qtype: str = "A"):
    decision = resolver.decide(name, qtype, device)
    return None if decision is None else (decision.layer, decision.label, decision.source)


def test_decide_names_every_layer_in_precedence() -> None:
    store, lists = fleet()
    resolver, _, _ = build(store, lists)

    assert layer_of(resolver, "nas.lan.test", "tv") == ("rewrite", "rewrite", None)
    assert layer_of(resolver, designated.NAME, "tv", "SVCB") == ("designated", "designated", None)
    assert layer_of(resolver, "10.1.168.192.in-addr.arpa", "tv", "PTR") == (
        "local ptr",
        "local ptr",
        None,
    )
    assert layer_of(resolver, "printer.home.arpa", "tv") == ("local zone", "local zone", None)
    assert layer_of(resolver, "own.test", "tv") == ("device rule", "device rule", None)
    assert layer_of(resolver, "tiktok.test", "tv") == ("schedule", "schedule Bedtime", "bedtime")
    assert layer_of(resolver, "tracker.test", "tv") == (
        "allowed",
        "allowed by @@ schedule Homework",
        "homework",
    )
    assert layer_of(resolver, "ads.example.com", "pixel") == (
        "allowed",
        "allowed by @@ device",
        None,
    )
    assert layer_of(resolver, "ads.example.com", "tv") == (
        "blocklist",
        "blocklist hagezi-pro",
        "hagezi-pro",
    )
    assert layer_of(resolver, "www.google.com", "kid") == ("safe search", "safe search", None)
    assert layer_of(resolver, "www.google.com", "laptop") is None
    assert layer_of(resolver, "example.org", "tv") is None


def test_a_device_rule_outranks_a_list_that_names_the_same_host() -> None:
    store, lists = fleet()
    store._adopt([Device(id="tv", name="TV", rules=("ads.example.com",))])
    resolver, _, _ = build(store, lists)

    assert layer_of(resolver, "ads.example.com", "tv") == ("device rule", "device rule", None)


def test_a_pause_outranks_every_rule_and_list_but_not_a_rewrite() -> None:
    store, lists = fleet()
    resolver, _, _ = build(store, lists)
    store.pauses.hold("tv", 15)

    for name in ("own.test", "tiktok.test", "ads.example.com", "example.org"):
        assert layer_of(resolver, name, "tv") == ("paused", "paused", None)
    assert layer_of(resolver, "nas.lan.test", "tv") == ("rewrite", "rewrite", None)


def test_the_blocked_flag_follows_the_layer() -> None:
    store, lists = fleet()
    resolver, _, _ = build(store, lists)

    assert resolver.decide("own.test", "A", "tv").blocked is True
    assert resolver.decide("tiktok.test", "A", "tv").blocked is True
    assert resolver.decide("ads.example.com", "A", "tv").blocked is True
    assert resolver.decide("ads.example.com", "A", "pixel").blocked is False
    assert resolver.decide("www.google.com", "A", "kid").blocked is False
    assert resolver.decide("ads.example.com", "A", "tv").as_dict() == {
        "layer": "blocklist",
        "label": "blocklist hagezi-pro",
        "blocked": True,
        "source": "hagezi-pro",
        "rule": None,
    }


def test_deciding_never_counts_a_hit() -> None:
    store, lists = fleet()
    resolver, pool, _ = build(store, lists)

    for _ in range(5):
        resolver.decide("ads.example.com", "A", "tv")

    assert lists.get("hagezi-pro").hits == 0
    assert pool.forwarded == 0


async def test_resolving_counts_the_hit_once() -> None:
    store, lists = fleet()
    resolver, _, _ = build(store, lists)

    await resolver.resolve(ask("ads.example.com").to_wire(), "tv")

    assert lists.get("hagezi-pro").hits == 1


def test_explain_carries_the_rule_that_decided() -> None:
    store = store_with(
        Device(
            id="tv",
            name="TV",
            rules=(
                "example.com",
                "own.test",
                "@@safe.example.com",
                "@@kid.test",
                "ads.example.com",
            ),
        )
    )

    assert store.explain("tv", "own.test").rule == "own.test"
    assert store.explain("tv", "tracker.example.com").rule == "example.com"
    assert store.explain("tv", "ads.example.com").rule == "example.com"
    assert store.explain("tv", "safe.example.com").rule == "safe.example.com"
    assert store.explain("tv", "a.kid.test").rule == "kid.test"

    fleet_store, _ = fleet()
    assert fleet_store.explain("tv", "www.tiktok.test").rule == "tiktok.test"
    assert fleet_store.explain("tv", "tracker.test").rule == "tracker.test"


def test_explain_names_the_rule_or_group_behind_blocks() -> None:
    store, _ = fleet()

    assert store.explain("tv", "own.test").label == "device rule"
    assert store.explain("tv", "www.tiktok.test").source == "bedtime"
    assert store.explain("tv", "tracker.test").label == "allowed by @@ schedule Homework"
    assert store.explain("laptop", "tiktok.test") is None
    assert store.explain("laptop", "example.org") is None
    for device, name in [("tv", "own.test"), ("tv", "tiktok.test"), ("laptop", "tiktok.test")]:
        explained = store.explain(device, name)
        assert store.blocks(device, name) == bool(explained and explained.blocked)
