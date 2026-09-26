from __future__ import annotations

import time

import dns.message
import dns.rcode
import dns.rdatatype
import httpx
import respx
from conftest import make_answer
from test_doh import client, mock_upstream

from hydra_dns.blocklists import Blocklist, Blocklists, ListState, parse
from hydra_dns.firstseen import FirstSeen
from hydra_dns.resolver.budget import DeviceBudget
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.prefetch import Prefetcher
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream
from hydra_dns.store import Device, DeviceStore, RuleGroup

LOOKUP_BOUND_US = 50.0


class CountingPool:
    def __init__(self) -> None:
        self.forwarded = 0
        self.unfiltered = 0

    async def forward(self, wire: bytes):
        self.forwarded += 1
        query = dns.message.from_wire(wire)
        name = query.question[0].name.to_text(omit_final_dot=True)
        _, answer = make_answer(name=name)
        response = dns.message.from_wire(answer)
        response.id = query.id
        return response.to_wire(), Upstream(label="mock", doh_url="https://u.test"), 1.0

    async def forward_unfiltered(self, wire: bytes):
        self.unfiltered += 1
        answer, _, latency = await self.forward(wire)
        self.forwarded -= 1
        return answer, Upstream(label="fallback", doh_url="https://f.test"), latency

    def state(self) -> dict:
        return {"ready": True}


def store_with(*devices: Device) -> DeviceStore:
    store = DeviceStore(httpx.AsyncClient(), [])
    store._adopt(list(devices))
    return store


def with_list(
    store: DeviceStore,
    names: tuple[str, ...] = ("ads.example.com", "tracker.test"),
    *,
    list_id: str = "ads",
    name: str = "Ads",
    tags: tuple[str, ...] = (),
    allow: tuple[str, ...] = (),
    lists: Blocklists | None = None,
) -> Blocklists:
    lists = lists or Blocklists(None, store)
    state = ListState(
        meta=Blocklist(id=list_id, name=name, url="https://l.test/", tags=tags, allow=allow)
    )
    state.hold(parse("\n".join(names)))
    lists._states[list_id] = state
    lists._rebuild()
    return lists


def build(store: DeviceStore, lists: Blocklists, budget: DeviceBudget | None = None):
    pool = CountingPool()
    metrics = Metrics()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600)
    resolver = Resolver(cache, pool, metrics, store, budget=budget, blocklists=lists)
    return resolver, pool, metrics


def ask(name: str, qtype: str = "A") -> bytes:
    return dns.message.make_query(name, dns.rdatatype.from_text(qtype)).to_wire()


async def test_a_listed_name_is_answered_in_process_and_labelled() -> None:
    store = store_with(Device(id="tv", name="TV"))
    resolver, pool, _ = build(store, with_list(store))

    result = await resolver.resolve(ask("img.ads.example.com"), "tv")
    aaaa = await resolver.resolve(ask("tracker.test", "AAAA"), "tv")

    assert result.blocked is True
    assert result.upstream == "blocklist Ads → 0.0.0.0"
    assert dns.message.from_wire(result.wire).answer[0][0].to_text() == "0.0.0.0"
    assert aaaa.upstream == "blocklist Ads → ::"
    assert pool.forwarded == 0


async def test_a_list_answer_honours_the_devices_block_mode() -> None:
    store = store_with(Device(id="tv", name="TV", block_mode="nxdomain"))
    resolver, pool, _ = build(store, with_list(store))

    result = await resolver.resolve(ask("ads.example.com"), "tv")

    assert dns.message.from_wire(result.wire).rcode() == dns.rcode.NXDOMAIN
    assert result.upstream == "blocklist Ads → nxdomain"
    assert pool.forwarded == 0


async def test_a_list_hit_is_counted_on_the_list() -> None:
    store = store_with(Device(id="tv", name="TV"))
    lists = with_list(store)
    resolver, _, _ = build(store, lists)

    for _ in range(3):
        await resolver.resolve(ask("ads.example.com"), "tv")
    await resolver.resolve(ask("example.org"), "tv")

    assert lists.get("ads").hits == 3


async def test_a_blocked_name_is_not_charged_to_the_budget_and_never_reaches_the_pool() -> None:
    store = store_with(Device(id="tv", name="TV", hourly_budget=2))
    budget = DeviceBudget(0)
    resolver, pool, _ = build(store, with_list(store), budget=budget)

    for _ in range(10):
        result = await resolver.resolve(ask("ads.example.com"), "tv")
        assert result.upstream == "blocklist Ads → 0.0.0.0"
    assert pool.forwarded == 0

    first = await resolver.resolve(ask("one.example.org"), "tv")
    second = await resolver.resolve(ask("two.example.org"), "tv")
    assert first.upstream == second.upstream == "mock"
    assert pool.forwarded == 2


async def test_a_device_rule_is_checked_before_any_list() -> None:
    store = store_with(Device(id="tv", name="TV", rules=("ads.example.com",)))
    resolver, _, _ = build(store, with_list(store))

    result = await resolver.resolve(ask("ads.example.com"), "tv")

    assert result.upstream == "device rule → 0.0.0.0"


async def test_a_device_exception_beats_the_list() -> None:
    store = store_with(Device(id="tv", name="TV", rules=("@@ads.example.com",)))
    resolver, pool, _ = build(store, with_list(store))

    result = await resolver.resolve(ask("img.ads.example.com"), "tv")

    assert result.upstream == "mock"
    assert pool.forwarded == 1


async def test_a_schedule_exception_beats_the_list() -> None:
    store = store_with(Device(id="tv", name="TV", tags=("kids",)))
    store._adopt_groups(
        [RuleGroup(id="homework", name="Homework", rules=("@@tracker.test",), tags=("kids",))]
    )
    resolver, _, _ = build(store, with_list(store))

    allowed = await resolver.resolve(ask("tracker.test"), "tv")
    blocked = await resolver.resolve(ask("ads.example.com"), "tv")

    assert allowed.upstream == "mock"
    assert blocked.upstream == "blocklist Ads → 0.0.0.0"


async def test_a_lists_allow_beats_that_list_but_not_another() -> None:
    store = store_with(Device(id="tv", name="TV"))
    lists = with_list(store, allow=("ads.example.com",))
    with_list(store, ("ads.example.com",), list_id="strict", name="Strict", lists=lists)
    resolver, _, _ = build(store, lists)

    result = await resolver.resolve(ask("ads.example.com"), "tv")

    assert result.upstream == "blocklist Strict → 0.0.0.0"


async def test_a_list_reaches_only_the_devices_carrying_its_tag() -> None:
    store = store_with(Device(id="tv", name="TV", tags=("kids",)), Device(id="laptop", name="L"))
    resolver, _, _ = build(store, with_list(store, tags=("kids",)))

    tv = await resolver.resolve(ask("ads.example.com"), "tv")
    laptop = await resolver.resolve(ask("ads.example.com"), "laptop")

    assert tv.blocked and tv.upstream.startswith("blocklist Ads")
    assert laptop.upstream == "mock"


async def test_a_paused_device_is_not_blocked_by_a_list() -> None:
    store = store_with(Device(id="tv", name="TV"))
    resolver, pool, _ = build(store, with_list(store))
    store.pauses.hold("tv", 15)

    result = await resolver.resolve(ask("ads.example.com"), "tv")

    assert not result.upstream.startswith("blocklist")
    assert result.upstream == "paused → fallback"
    assert pool.unfiltered == 1


async def test_first_seen_notes_a_blocklist_answer() -> None:
    store = store_with(Device(id="tv", name="TV"))
    resolver, _, metrics = build(store, with_list(store))
    metrics.first_seen = FirstSeen(warmup_s=0)
    await resolver.resolve(ask("warmup.example.org"), "tv")

    await resolver.resolve(ask("ads.example.com"), "tv")

    assert any(row["name"] == "ads.example.com" for row in metrics.first_seen.recent())


async def test_a_broken_list_means_not_blocked_rather_than_an_error() -> None:
    store = store_with(Device(id="tv", name="TV"))
    lists = with_list(store)
    lists._states["ads"].names = None
    resolver, pool, _ = build(store, lists)

    result = await resolver.resolve(ask("ads.example.com"), "tv")

    assert result.rcode == "NOERROR"
    assert result.upstream == "mock"
    assert pool.forwarded == 1


async def test_a_blocklist_object_that_raises_is_swallowed() -> None:
    class Exploding:
        def match(self, device, name):
            raise RuntimeError("boom")

    store = store_with(Device(id="tv", name="TV"))
    resolver, pool, _ = build(store, Exploding())

    result = await resolver.resolve(ask("ads.example.com"), "tv")

    assert result.upstream == "mock"


def test_the_check_stays_fast_against_a_large_list() -> None:
    store = store_with(Device(id="tv", name="TV", tags=("kids",)))
    lists = Blocklists(None, store)
    state = ListState(meta=Blocklist(id="big", name="Big", url="https://l.test/"))
    state.hold(parse("\n".join(f"host{i}.tracker{i % 997}.example" for i in range(200_000))))
    lists._states["big"] = state
    lists._rebuild()
    assert len(state.names) == 200_000

    names = [f"a.b.c.host{i}.tracker{i % 997}.example" for i in range(0, 200_000, 20)] + [
        f"a.b.c.unlisted{i}.example.org" for i in range(10_000)
    ]
    lists.match("tv", names[0])
    started = time.perf_counter()
    blocked = sum(1 for name in names if lists.match("tv", name) is not None)
    per_query_us = (time.perf_counter() - started) / len(names) * 1_000_000

    assert blocked == 10_000
    assert per_query_us < LOOKUP_BOUND_US


def prefetcher_for(lists: Blocklists) -> tuple[Prefetcher, AnswerCache, CountingPool, Metrics]:
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600)
    pool = CountingPool()
    metrics = Metrics()
    return Prefetcher(cache, pool, metrics, lists), cache, pool, metrics


def prime(cache: AnswerCache, metrics: Metrics, name: str) -> None:
    from hydra_dns.resolver.metrics import QueryRecord

    for _ in range(10):
        metrics.record(
            QueryRecord(
                at=time.time(),
                device="tv",
                name=name,
                qtype="A",
                rcode="NOERROR",
                cached=False,
                blocked=False,
                upstream="mock",
                latency_ms=1.0,
            )
        )
    response, wire = make_answer(name=name, ttl=5)
    key = cache.key_for(dns.message.make_query(name, dns.rdatatype.A))
    cache.put(key, response, wire)
    for _ in range(10):
        cache.get(key, 0)


async def test_prefetch_skips_a_name_covered_by_an_untagged_list() -> None:
    store = store_with(Device(id="tv", name="TV"))
    prefetcher, cache, pool, metrics = prefetcher_for(with_list(store))
    prime(cache, metrics, "ads.example.com")
    prime(cache, metrics, "example.org")

    renewed = await prefetcher.run()

    assert renewed == 1
    assert pool.forwarded == 1
    assert prefetcher.skipped_listed == 1


async def test_prefetch_still_renews_a_name_only_a_tagged_list_covers() -> None:
    store = store_with(Device(id="tv", name="TV", tags=("kids",)), Device(id="laptop", name="L"))
    prefetcher, cache, pool, metrics = prefetcher_for(with_list(store, tags=("kids",)))
    prime(cache, metrics, "ads.example.com")

    assert await prefetcher.run() == 1
    assert pool.forwarded == 1


async def test_prefetch_renews_a_listed_name_a_device_has_excepted() -> None:
    store = store_with(Device(id="tv", name="TV", rules=("@@ads.example.com",)))
    prefetcher, cache, pool, metrics = prefetcher_for(with_list(store))
    prime(cache, metrics, "ads.example.com")

    assert await prefetcher.run() == 1


async def test_startup_warm_skips_a_name_covered_by_an_untagged_list(monkeypatch) -> None:
    import dataclasses

    from hydra_dns.resolver import prefetch as module

    monkeypatch.setattr(
        module, "settings", dataclasses.replace(module.settings, cache_warm_count=5)
    )
    store = store_with(Device(id="tv", name="TV"))
    prefetcher, cache, pool, metrics = prefetcher_for(with_list(store))
    prime(cache, metrics, "ads.example.com")
    prime(cache, metrics, "example.org")
    cache.clear()

    assert await prefetcher.warm() == 1
    assert pool.forwarded == 1


@respx.mock
async def test_a_list_name_that_is_not_latin_1_still_answers_over_doh() -> None:
    from test_doh_headers import _over_doh

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._compiled = {}
        with_list(store, name="Werbung — 広告 ✓", lists=app.state.blocklists)
        before = [u["queries"] for u in app.state.pool.snapshot()]
        response = await _over_doh(http, "img.ads.example.com")
        after = [u["queries"] for u in app.state.pool.snapshot()]

    assert response.status_code == 200
    assert response.headers["x-hydra-upstream"].startswith("blocklist Werbung ")
    assert response.headers["x-hydra-upstream"].endswith(" -> 0.0.0.0")
    response.headers["x-hydra-upstream"].encode("latin-1")
    assert dns.message.from_wire(response.content).answer[0][0].to_text() == "0.0.0.0"
    assert before == after
    assert respx.calls.call_count == 0
