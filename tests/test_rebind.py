from __future__ import annotations

import time

import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import httpx
import pytest
import respx

from hydra_dns import rebind
from hydra_dns.firstseen import FirstSeen
from hydra_dns.rebind import RebindGuard
from hydra_dns.resolver import savings as saved
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.prefetch import Prefetcher
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamError
from hydra_dns.store import Device, DeviceStore

CHECK_BOUND_US = 50.0
CHECK_SHARE_OF_PARSE = 0.5


class AnsweringPool:
    def __init__(self, records: dict[tuple[str, str], list[str]], fallback: bool = False) -> None:
        self.records = records
        self.forwarded = 0
        self.unfiltered = 0
        self.fail = False
        self.upstream = Upstream(label="mock", doh_url="https://u.test", is_fallback=fallback)

    def answer(self, wire: bytes) -> bytes:
        query = dns.message.from_wire(wire)
        question = query.question[0]
        name = question.name.to_text(omit_final_dot=True)
        qtype = dns.rdatatype.to_text(question.rdtype)
        response = dns.message.make_response(query)
        values = self.records.get((name, qtype), [])
        if values:
            response.answer.append(
                dns.rrset.from_text_list(question.name, 300, "IN", qtype, values)
            )
        return response.to_wire()

    async def forward(self, wire: bytes):
        self.forwarded += 1
        if self.fail:
            raise UpstreamError("down")
        return self.answer(wire), self.upstream, 1.0

    async def forward_unfiltered(self, wire: bytes):
        self.unfiltered += 1
        return self.answer(wire), Upstream(label="fallback", doh_url="https://f.test"), 1.0

    def state(self) -> dict:
        return {"ready": True}


def store_with(*devices: Device) -> DeviceStore:
    store = DeviceStore(httpx.AsyncClient(), [])
    store._adopt(list(devices))
    return store


def build(records, guard: RebindGuard | None = None, devices=None, fallback=False, stale=0):
    pool = AnsweringPool(records, fallback=fallback)
    metrics = Metrics()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600, stale_window_s=stale)
    store = store_with(*(devices or [Device(id="tv", name="TV")]))
    guard = guard or RebindGuard(enabled=True, allow=("plex.direct",))
    resolver = Resolver(cache, pool, metrics, store, guard=guard)
    return resolver, pool, metrics, cache


def ask(name: str, qtype: str = "A") -> bytes:
    return dns.message.make_query(name, dns.rdatatype.from_text(qtype)).to_wire()


def addresses(wire: bytes) -> list[str]:
    return [r.to_text() for rrset in dns.message.from_wire(wire).answer for r in rrset]


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.9.10",
        "10.0.0.5",
        "172.16.0.1",
        "172.31.255.254",
        "192.168.1.1",
        "100.64.0.1",
        "100.127.255.254",
        "169.254.169.254",
        "169.254.1.1",
        "0.0.0.1",
    ],
)
async def test_each_private_ipv4_class_is_refused(address: str) -> None:
    resolver, pool, _, _ = build({("evil.example", "A"): [address]})

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert result.blocked is True
    assert result.upstream == "rebind guard → 0.0.0.0"
    assert addresses(result.wire) == ["0.0.0.0"]
    assert address not in addresses(result.wire)
    assert pool.forwarded == 1
    assert resolver.rebind.refused == 1


@pytest.mark.parametrize(
    "address",
    [
        "::1",
        "fe80::1",
        "fc00::1",
        "fd12:3456::1",
        "fec0::1",
        "::ffff:192.168.1.1",
        "::ffff:127.0.0.1",
        "::ffff:169.254.169.254",
        "::10.0.0.1",
        "64:ff9b::a00:1",
        "::ffff:0:10.1.2.3",
    ],
)
async def test_each_private_ipv6_class_and_mapped_form_is_refused(address: str) -> None:
    resolver, _, _, _ = build({("evil.example", "AAAA"): [address]})

    result = await resolver.resolve(ask("evil.example", "AAAA"), "tv")

    assert result.upstream == "rebind guard → ::"
    assert addresses(result.wire) == ["::"]


@pytest.mark.parametrize(
    "qtype,address",
    [
        ("A", "93.184.216.34"),
        ("A", "100.128.0.1"),
        ("A", "172.32.0.1"),
        ("A", "192.169.0.1"),
        ("A", "8.8.8.8"),
        ("AAAA", "2606:4700::1111"),
        ("AAAA", "::ffff:8.8.8.8"),
        ("AAAA", "64:ff9b::808:808"),
    ],
)
async def test_public_addresses_pass_untouched(qtype: str, address: str) -> None:
    resolver, _, _, _ = build({("example.com", qtype): [address]})

    result = await resolver.resolve(ask("example.com", qtype), "tv")

    assert result.blocked is False
    assert result.upstream == "mock"
    assert addresses(result.wire) == [
        dns.rrset.from_text("x.", 1, "IN", qtype, address)[0].to_text()
    ]
    assert resolver.rebind.refused == 0


async def test_one_private_address_among_public_ones_refuses_the_answer() -> None:
    resolver, _, _, _ = build({("evil.example", "A"): ["93.184.216.34", "192.168.0.10"]})

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert result.upstream == "rebind guard → 0.0.0.0"


async def test_a_cname_chain_ending_private_is_refused() -> None:
    resolver, _, _, _ = build({})

    async def forward(wire: bytes):
        query = dns.message.from_wire(wire)
        response = dns.message.make_response(query)
        response.answer.append(
            dns.rrset.from_text("evil.example.", 60, "IN", "CNAME", "1-2-3-4.x.plex.direct.")
        )
        response.answer.append(
            dns.rrset.from_text("1-2-3-4.x.plex.direct.", 60, "IN", "A", "192.168.1.4")
        )
        return response.to_wire(), Upstream(label="mock", doh_url="https://u.test"), 1.0

    resolver._pool.forward = forward

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert result.upstream == "rebind guard → 0.0.0.0"


@pytest.mark.parametrize("qtype", ["HTTPS", "SVCB"])
async def test_service_binding_hints_are_checked(qtype: str) -> None:
    resolver, _, _, _ = build(
        {
            ("evil.example", qtype): ['1 . alpn="h2" ipv4hint="10.0.0.1"'],
            ("evil6.example", qtype): ['1 . ipv6hint="fd00::1"'],
            ("fine.example", qtype): ['1 . alpn="h2" ipv4hint="93.184.216.34"'],
        }
    )

    refused = await resolver.resolve(ask("evil.example", qtype), "tv")
    refused6 = await resolver.resolve(ask("evil6.example", qtype), "tv")
    passed = await resolver.resolve(ask("fine.example", qtype), "tv")

    assert refused.upstream == "rebind guard"
    assert dns.message.from_wire(refused.wire).answer == []
    assert refused6.upstream == "rebind guard"
    assert passed.upstream == "mock"


async def test_the_refusal_honours_the_devices_block_mode() -> None:
    resolver, _, _, _ = build(
        {("evil.example", "A"): ["192.168.1.1"]},
        devices=[Device(id="tv", name="TV", block_mode="nxdomain")],
    )

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert dns.message.from_wire(result.wire).rcode() == dns.rcode.NXDOMAIN
    assert result.upstream == "rebind guard → nxdomain"


async def test_the_allowlist_passes_names_under_it() -> None:
    resolver, _, _, _ = build(
        {
            ("1-2-3-4.abc.plex.direct", "A"): ["192.168.1.4"],
            ("plex.direct", "A"): ["10.0.0.1"],
            ("notplex.direct", "A"): ["10.0.0.1"],
        }
    )

    under = await resolver.resolve(ask("1-2-3-4.abc.plex.direct"), "tv")
    apex = await resolver.resolve(ask("plex.direct"), "tv")
    lookalike = await resolver.resolve(ask("notplex.direct"), "tv")

    assert addresses(under.wire) == ["192.168.1.4"]
    assert addresses(apex.wire) == ["10.0.0.1"]
    assert lookalike.upstream == "rebind guard → 0.0.0.0"


async def test_reverse_zones_and_localhost_are_exempt() -> None:
    guard = RebindGuard(enabled=True, allow=())

    assert guard.exempt("1.1.168.192.in-addr.arpa")
    assert guard.exempt("1.0.0.0.ip6.arpa")
    assert guard.exempt("localhost")
    assert guard.exempt("app.localhost")
    assert not guard.exempt("example.com")


async def test_hydras_own_answers_are_never_checked() -> None:
    store = store_with(Device(id="tv", name="TV"))
    store.rewrite_for = lambda name: "192.168.1.9" if name == "nas.example.com" else None
    pool = AnsweringPool({})
    resolver = Resolver(
        AnswerCache(max_entries=10, min_ttl=1, max_ttl=60),
        pool,
        Metrics(),
        store,
        guard=RebindGuard(enabled=True, allow=()),
    )

    rewrite = await resolver.resolve(ask("nas.example.com"), "tv")
    local = await resolver.resolve(ask("localhost"), "tv")

    assert addresses(rewrite.wire) == ["192.168.1.9"]
    assert rewrite.upstream == "rewrite"
    assert addresses(local.wire) == ["127.0.0.1"]
    assert resolver.rebind.refused == 0
    assert pool.forwarded == 0


async def test_turning_it_off_passes_private_answers() -> None:
    resolver, _, _, _ = build(
        {("evil.example", "A"): ["192.168.1.1"]}, guard=RebindGuard(enabled=False)
    )

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert addresses(result.wire) == ["192.168.1.1"]
    assert result.upstream == "mock"


async def test_an_upstream_block_answer_is_not_relabelled() -> None:
    resolver, _, _, _ = build({("ads.example", "A"): ["0.0.0.0"]})

    result = await resolver.resolve(ask("ads.example"), "tv")

    assert result.blocked is True
    assert result.upstream == "mock"
    assert resolver.rebind.refused == 0


async def test_a_refused_answer_is_cached_and_never_re_costs_quota() -> None:
    resolver, pool, _, cache = build({("evil.example", "A"): ["192.168.1.1"]})

    first = await resolver.resolve(ask("evil.example"), "tv")
    second = await resolver.resolve(ask("evil.example"), "tv")
    third = await resolver.resolve(ask("evil.example"), "tv")

    assert pool.forwarded == 1
    assert first.cached is False
    assert second.cached is True and third.cached is True
    assert second.upstream == "rebind guard → 0.0.0.0"
    assert addresses(second.wire) == ["0.0.0.0"]
    assert resolver.rebind.refused == 3
    assert cache.stats.hits == 2


async def test_a_refusal_served_from_the_cache_counts_as_quota_saved() -> None:
    resolver, pool, metrics, _ = build({("evil.example", "A"): ["192.168.1.1"]})

    fixed = dns.message.make_query("evil.example", "A")
    fixed.id = 7

    first = await resolver.resolve(ask("evil.example"), "tv")
    assert metrics.savings.by_cause().get(saved.CACHE, 0) == 0
    second = await resolver.resolve(fixed.to_wire(), "tv")
    third = await resolver.resolve(fixed.to_wire(), "tv")

    assert pool.forwarded == 1
    assert metrics.savings.by_cause()[saved.CACHE] == 2
    assert second.wire == third.wire
    assert dns.message.from_wire(second.wire).id == 7
    assert addresses(second.wire) == addresses(first.wire) == ["0.0.0.0"]


async def test_the_cache_holds_the_raw_answer_so_each_device_gets_its_own_block_mode() -> None:
    resolver, pool, _, cache = build(
        {("evil.example", "A"): ["192.168.1.1"]},
        devices=[Device(id="tv", name="TV"), Device(id="pc", name="PC", block_mode="nxdomain")],
    )

    tv = await resolver.resolve(ask("evil.example"), "tv")
    pc = await resolver.resolve(ask("evil.example"), "pc")

    key = cache.key_for(dns.message.make_query("evil.example", "A"))
    assert addresses(cache.wire_of(key)) == ["192.168.1.1"]
    assert addresses(tv.wire) == ["0.0.0.0"]
    assert dns.message.from_wire(pc.wire).rcode() == dns.rcode.NXDOMAIN
    assert pool.forwarded == 1


async def test_a_refused_ttl_zero_answer_is_held_at_the_blocked_floor() -> None:
    pool = AnsweringPool({("evil.example", "A"): ["192.168.1.1"]})
    cache = AnswerCache(max_entries=10, min_ttl=30, max_ttl=3600, blocked_min_ttl=1800)
    resolver = Resolver(
        cache, pool, Metrics(), store_with(Device(id="tv", name="TV")), guard=RebindGuard()
    )

    async def forward(wire: bytes):
        query = dns.message.from_wire(wire)
        response = dns.message.make_response(query)
        response.answer.append(dns.rrset.from_text("evil.example.", 0, "IN", "A", "192.168.1.1"))
        return response.to_wire(), pool.upstream, 1.0

    pool.forward = forward
    await resolver.resolve(ask("evil.example"), "tv")

    key = cache.key_for(dns.message.make_query("evil.example", "A"))
    assert cache.peek(key)["ttl"] > 1700


async def test_a_stale_private_answer_is_refused_too() -> None:
    resolver, pool, _, cache = build({("evil.example", "A"): ["10.1.1.1"]}, stale=3600)
    query = dns.message.make_query("evil.example", "A")
    key = cache.key_for(query)
    response = dns.message.from_wire(pool.answer(query.to_wire()))
    cache.put(key, response, response.to_wire())
    cache._entries[key].expires_at = time.monotonic() - 5
    pool.fail = True

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert result.upstream == "rebind guard → 0.0.0.0"
    assert result.cached is True


async def test_a_fallback_answer_is_checked_and_not_cached() -> None:
    resolver, pool, _, cache = build({("evil.example", "A"): ["192.168.1.1"]}, fallback=True)

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert result.upstream == "rebind guard → 0.0.0.0"
    assert len(cache) == 0


async def test_a_paused_device_is_still_protected() -> None:
    resolver, pool, _, _ = build({("evil.example", "A"): ["192.168.1.1"]})
    resolver._pauses = type("P", (), {"active": staticmethod(lambda device: True)})()

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert pool.unfiltered == 1
    assert result.upstream == "rebind guard → 0.0.0.0"


async def test_the_guard_adds_no_upstream_query() -> None:
    records = {("evil.example", "A"): ["192.168.1.1"], ("example.com", "A"): ["93.184.216.34"]}
    guarded, guarded_pool, _, _ = build(records)
    bare, bare_pool, _, _ = build(records, guard=RebindGuard(enabled=False))

    for resolver in (guarded, bare):
        for name in ("evil.example", "example.com", "evil.example", "example.com"):
            await resolver.resolve(ask(name), "tv")

    assert guarded_pool.forwarded == bare_pool.forwarded == 2


async def test_an_exception_inside_the_check_passes_the_answer_through(monkeypatch) -> None:
    resolver, _, _, _ = build({("evil.example", "A"): ["192.168.1.1"]})

    def broken(message):
        raise RuntimeError("boom")

    monkeypatch.setattr(rebind, "_carries_private", broken)

    result = await resolver.resolve(ask("evil.example"), "tv")

    assert addresses(result.wire) == ["192.168.1.1"]
    assert result.rcode == "NOERROR"
    assert resolver.rebind.failed == 1


async def test_the_refusal_is_in_the_log_and_the_askers() -> None:
    resolver, _, metrics, _ = build({("evil.example", "A"): ["192.168.1.1"]})

    await resolver.resolve(ask("evil.example"), "tv")

    row = metrics.recent(limit=1)[0]
    assert row["upstream"] == "rebind guard → 0.0.0.0"
    assert row["blocked"] is True
    assert metrics.askers("evil.example")[0]["last_upstream"] == "rebind guard → 0.0.0.0"


def test_a_refused_name_is_still_noted_by_first_seen() -> None:
    watcher = FirstSeen(warmup_s=0)

    assert watcher.note("tv", "evil.example", "rebind guard → 0.0.0.0", time.time()) is True


async def test_prefetch_does_not_renew_a_refused_answer(monkeypatch) -> None:
    resolver, pool, metrics, cache = build({("evil.example", "A"): ["192.168.1.1"]})
    for _ in range(6):
        await resolver.resolve(ask("evil.example"), "tv")
    forwarded = pool.forwarded
    prefetcher = Prefetcher(cache, pool, metrics, guard=resolver.rebind)
    monkeypatch.setattr(
        cache, "due", lambda within, min_reads=0: [(k, 0.0) for k in cache._entries]
    )

    renewed = await prefetcher.run()

    assert renewed == 0
    assert pool.forwarded == forwarded
    assert prefetcher.snapshot()["skipped_rebind"] == 1


def test_the_allowlist_is_normalised_and_bounded() -> None:
    guard = RebindGuard(
        allow=[" Plex.Direct. ", "plex.direct", ""] + [f"s{i}.test" for i in range(200)]
    )

    assert guard.allow[0] == "plex.direct"
    assert len(guard.allow) == rebind.MAX_ALLOW


def test_the_defaults_are_on_with_plex_direct() -> None:
    from hydra_dns.config import settings

    assert settings.rebind_guard is True
    assert settings.rebind_allow == ("plex.direct",)


def test_the_check_is_cheap_on_the_hot_path() -> None:
    guard = RebindGuard(allow=("plex.direct", "example.net"))
    query = dns.message.make_query("cdn.example.com", "A")
    response = dns.message.make_response(query)
    response.answer.append(
        dns.rrset.from_text("cdn.example.com.", 60, "IN", "CNAME", "edge.example.org.")
    )
    response.answer.append(
        dns.rrset.from_text_list(
            "edge.example.org.", 60, "IN", "A", ["93.184.216.34", "93.184.216.35", "8.8.8.8"]
        )
    )
    six = dns.message.make_response(dns.message.make_query("cdn.example.com", "AAAA"))
    six.answer.append(
        dns.rrset.from_text_list(
            "cdn.example.com.", 60, "IN", "AAAA", ["2606:4700::1111", "2606:4700::1001"]
        )
    )

    wires = (response.to_wire(), six.to_wire())
    rounds = 20_000

    started = time.perf_counter()
    for _ in range(rounds):
        guard.refuses("cdn.example.com", response)
        guard.refuses("cdn.example.com", six)
    checking = time.perf_counter() - started

    started = time.perf_counter()
    for _ in range(rounds):
        for wire in wires:
            dns.message.from_wire(wire)
    parsing = time.perf_counter() - started

    per_check_us = checking / (rounds * 2) * 1_000_000
    assert per_check_us < CHECK_BOUND_US
    assert checking < parsing * CHECK_SHARE_OF_PARSE


@respx.mock
async def test_a_refusal_over_real_doh_is_a_dns_answer_with_a_readable_header() -> None:
    from conftest import make_answer
    from test_doh import client, mock_upstream
    from test_doh_headers import _over_doh

    from hydra_dns.main import app

    _, wire = make_answer(name="evil.example", address="192.168.1.1")
    mock_upstream(wire)
    async with client() as http:
        before = app.state.resolver.rebind.refused
        response = await _over_doh(http, "evil.example")
        again = await _over_doh(http, "evil.example")
        refused = app.state.resolver.rebind.refused - before

    assert response.status_code == 200
    assert again.status_code == 200
    assert response.headers["x-hydra-upstream"] == "rebind guard -> 0.0.0.0"
    assert addresses(response.content) == ["0.0.0.0"]
    assert addresses(again.content) == ["0.0.0.0"]
    assert refused == 2
    assert respx.calls.call_count == 1
