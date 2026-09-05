from __future__ import annotations

import time

import dns.flags
import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
from conftest import make_answer

from hydra_dns.resolver.cache import AnswerCache


def build_cache(**kwargs) -> AnswerCache:
    defaults = {"max_entries": 100, "min_ttl": 30, "max_ttl": 86_400}
    defaults.update(kwargs)
    return AnswerCache(**defaults)


def test_miss_then_hit(query_wire: bytes) -> None:
    cache = build_cache()
    query = dns.message.from_wire(query_wire)
    key = cache.key_for(query)

    assert cache.get(key, query.id) is None
    assert cache.stats.misses == 1

    response, wire = make_answer()
    assert cache.put(key, response, wire) == 300

    hit = cache.get(key, query.id)
    assert hit is not None
    assert cache.stats.hits == 1


def test_hit_carries_the_requesting_id_not_the_stored_one() -> None:
    cache = build_cache()
    first = dns.message.make_query("example.com", dns.rdatatype.A)
    first.id = 1111
    key = cache.key_for(first)
    response, wire = make_answer()
    cache.put(key, response, wire)

    second_id = 2222
    hit = cache.get(key, second_id)
    assert dns.message.from_wire(hit).id == second_id


def test_ttl_counts_down_while_cached(monkeypatch) -> None:
    cache = build_cache()
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    key = cache.key_for(query)
    response, wire = make_answer(ttl=300)

    clock = [1_000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    cache.put(key, response, wire)

    clock[0] += 100
    hit = dns.message.from_wire(cache.get(key, query.id))
    assert hit.answer[0].ttl == 200


def test_expired_entry_is_a_miss(monkeypatch) -> None:
    cache = build_cache()
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    key = cache.key_for(query)
    response, wire = make_answer(ttl=60)

    clock = [1_000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    cache.put(key, response, wire)

    clock[0] += 61
    assert cache.get(key, query.id) is None
    assert cache.stats.expired == 1


def test_dnssec_requests_do_not_share_an_entry() -> None:
    plain = dns.message.make_query("example.com", dns.rdatatype.A)
    signed = dns.message.make_query("example.com", dns.rdatatype.A, want_dnssec=True)
    cache = build_cache()
    assert cache.key_for(plain) != cache.key_for(signed)


def test_servfail_is_not_cached() -> None:
    cache = build_cache()
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    response, wire = make_answer(rcode=dns.rcode.SERVFAIL)
    assert cache.put(cache.key_for(query), response, wire) is None


def test_truncated_answers_are_not_cached() -> None:
    cache = build_cache()
    response, _ = make_answer()
    response.flags |= dns.flags.TC
    assert cache.ttl_for(response) is None


def test_ttl_is_clamped_to_the_configured_window() -> None:
    cache = build_cache(min_ttl=30, max_ttl=600)
    short, _ = make_answer(ttl=5)
    long, _ = make_answer(ttl=100_000)
    assert cache.ttl_for(short) == 30
    assert cache.ttl_for(long) == 600


def test_zero_ttl_is_honoured_as_do_not_cache() -> None:
    cache = build_cache()
    response, _ = make_answer(ttl=0)
    assert cache.ttl_for(response) is None


def test_lru_evicts_the_least_recently_used() -> None:
    cache = build_cache(max_entries=2)
    keys = []
    for name in ("a.test", "b.test", "c.test"):
        query = dns.message.make_query(name, dns.rdatatype.A)
        key = cache.key_for(query)
        keys.append((key, query.id))
        response, wire = make_answer(name=name)
        cache.put(key, response, wire)

    assert len(cache) == 2
    assert cache.stats.evictions == 1
    assert cache.get(*keys[0]) is None
    assert cache.get(*keys[2]) is not None


def test_negative_answers_use_the_soa_minimum() -> None:
    cache = build_cache(min_ttl=1)
    query = dns.message.make_query("nope.example.com", dns.rdatatype.A)
    response = dns.message.make_response(query)
    response.set_rcode(dns.rcode.NXDOMAIN)
    response.authority.append(
        dns.rrset.from_text(
            "example.com.",
            3600,
            "IN",
            "SOA",
            "ns.example.com. host.example.com. 1 7200 900 1209600 120",
        )
    )
    assert cache.ttl_for(response) == 120


def test_multi_question_queries_are_not_cacheable() -> None:
    cache = build_cache()
    query = dns.message.make_query("a.test", dns.rdatatype.A)
    query.question.append(dns.rrset.from_text("b.test.", 0, "IN", "A"))
    assert cache.key_for(query) is None
