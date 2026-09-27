from __future__ import annotations

import time

import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
import respx
from test_apps import engine
from test_blocklist_resolver import ask, store_with, with_list
from test_doh import client, mock_upstream
from test_doh_headers import _over_doh

from hydra_dns import uncloak
from hydra_dns.apps import AppBlock
from hydra_dns.config import settings
from hydra_dns.digest import Digest
from hydra_dns.resolver import savings as saved
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamError
from hydra_dns.store import Device, RuleGroup
from hydra_dns.uncloak import Uncloaker

CHAINS = {
    "metrics.shop.example": [
        ("metrics.shop.example", "CNAME", "shop.edge.example"),
        ("shop.edge.example", "CNAME", "collect.tracker.net"),
        ("collect.tracker.net", "A", "203.0.113.7"),
    ],
    "www.shop.example": [
        ("www.shop.example", "CNAME", "shop.cdn.example"),
        ("shop.cdn.example", "A", "203.0.113.8"),
    ],
    "video.cloaked.example": [
        ("video.cloaked.example", "CNAME", "rr1.googlevideo.com"),
        ("rr1.googlevideo.com", "A", "203.0.113.9"),
    ],
}

CHECK_BOUND_US = 50.0


class ChainPool:
    def __init__(self) -> None:
        self.forwarded = 0
        self.fail = False
        self.upstream = Upstream(label="mock", doh_url="https://u.test")

    def answer(self, wire: bytes) -> bytes:
        query = dns.message.from_wire(wire)
        question = query.question[0]
        name = question.name.to_text(omit_final_dot=True)
        response = dns.message.make_response(query)
        for owner, kind, value in CHAINS.get(name, [(name, "A", "198.51.100.1")]):
            target = f"{value}." if kind == "CNAME" else value
            response.answer.append(dns.rrset.from_text(f"{owner}.", 300, "IN", kind, target))
        return response.to_wire()

    async def forward(self, wire: bytes):
        self.forwarded += 1
        if self.fail:
            raise UpstreamError("down")
        return self.answer(wire), self.upstream, 1.0

    async def forward_unfiltered(self, wire: bytes):
        return self.answer(wire), Upstream(label="fallback", doh_url="https://f.test"), 1.0

    def state(self) -> dict:
        return {"ready": True}


def build(*devices: Device, tags: tuple[str, ...] = (), allow=(), apps=None, enabled=True, stale=0):
    store = store_with(*(devices or (Device(id="tv", name="TV"),)))
    lists = with_list(store, ("tracker.net",), name="Trackers", tags=tags, allow=allow)
    pool = ChainPool()
    metrics = Metrics()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600, stale_window_s=stale)
    resolver = Resolver(
        cache,
        pool,
        metrics,
        store,
        blocklists=lists,
        apps=apps(store) if apps else None,
        uncloaker=Uncloaker(enabled=enabled),
    )
    return resolver, pool, metrics, lists, cache


def addresses(wire: bytes) -> list[str]:
    message = dns.message.from_wire(wire)
    return [
        r.to_text() for rrset in message.answer if rrset.rdtype == dns.rdatatype.A for r in rrset
    ]


LABEL = "cname collect.tracker.net · blocklist Trackers → 0.0.0.0"


async def test_a_cname_target_on_the_devices_list_blocks_the_answer() -> None:
    resolver, pool, metrics, lists, _ = build()

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is True
    assert result.upstream == LABEL
    assert addresses(result.wire) == ["0.0.0.0"]
    assert lists.get("ads").hits == 1
    assert resolver.uncloak.blocked == 1
    assert pool.forwarded == 1


async def test_a_clean_chain_passes_untouched() -> None:
    resolver, _, _, _, _ = build()

    result = await resolver.resolve(ask("www.shop.example"), "tv")

    assert result.blocked is False
    assert addresses(result.wire) == ["203.0.113.8"]
    assert resolver.uncloak.blocked == 0


async def test_a_device_the_list_does_not_reach_gets_the_real_answer() -> None:
    resolver, pool, _, _, _ = build(
        Device(id="kid", name="Kid", tags=("kids",)),
        Device(id="pc", name="PC"),
        tags=("kids",),
    )

    kid = await resolver.resolve(ask("metrics.shop.example"), "kid")
    pc = await resolver.resolve(ask("metrics.shop.example"), "pc")

    assert kid.upstream == LABEL
    assert pc.blocked is False
    assert pc.upstream == "cache"
    assert addresses(pc.wire) == ["203.0.113.7"]
    assert pool.forwarded == 1


async def test_a_cache_hit_is_checked_per_device() -> None:
    resolver, pool, metrics, _, cache = build(
        Device(id="kid", name="Kid", tags=("kids",)),
        Device(id="pc", name="PC", block_mode="nxdomain", tags=("kids",)),
        Device(id="tv", name="TV"),
        tags=("kids",),
    )

    tv = await resolver.resolve(ask("metrics.shop.example"), "tv")
    kid = await resolver.resolve(ask("metrics.shop.example"), "kid")
    pc = await resolver.resolve(ask("metrics.shop.example"), "pc")

    key = cache.key_for(dns.message.make_query("metrics.shop.example", "A"))
    assert addresses(cache.wire_of(key)) == ["203.0.113.7"]
    assert tv.blocked is False
    assert kid.cached is True
    assert kid.upstream == LABEL
    assert dns.message.from_wire(pc.wire).rcode() == dns.rcode.NXDOMAIN
    assert pc.upstream == "cname collect.tracker.net · blocklist Trackers → nxdomain"
    assert pool.forwarded == 1


async def test_a_device_allow_on_the_asked_name_wins() -> None:
    resolver, _, _, _, _ = build(
        Device(id="tv", name="TV", rules=("@@||metrics.shop.example^",)),
    )

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is False
    assert addresses(result.wire) == ["203.0.113.7"]


async def test_a_schedule_allow_on_the_asked_name_wins() -> None:
    resolver, _, _, _, _ = build(Device(id="tv", name="TV"))
    store = resolver._devices
    store._adopt_groups([RuleGroup(id="g", name="Open", rules=("@@||shop.example^",))])

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is False


async def test_a_device_allow_on_the_target_wins() -> None:
    resolver, _, _, _, _ = build(
        Device(id="tv", name="TV", rules=("@@||tracker.net^",)),
    )

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is False


async def test_the_lists_own_allow_for_the_asked_name_exempts_it() -> None:
    resolver, _, _, _, _ = build(allow=("metrics.shop.example",))

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is False


async def test_every_hop_of_a_chain_is_checked() -> None:
    store = store_with(Device(id="tv", name="TV"))
    lists = with_list(store, ("edge.example",), name="Edge")
    resolver = Resolver(
        AnswerCache(max_entries=10, min_ttl=1, max_ttl=3600),
        ChainPool(),
        Metrics(),
        store,
        blocklists=lists,
        uncloaker=Uncloaker(),
    )

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.upstream == "cname shop.edge.example · blocklist Edge → 0.0.0.0"


async def test_an_app_block_catches_a_cloaked_target() -> None:
    resolver, _, _, _, _ = build(apps=lambda store: engine(store, youtube=AppBlock(id="youtube")))

    result = await resolver.resolve(ask("video.cloaked.example"), "tv")

    assert result.upstream == "cname rr1.googlevideo.com · app YouTube → 0.0.0.0"
    assert resolver._apps.hits_of("youtube") == 1


async def test_it_adds_no_upstream_query_and_is_not_quota_saved() -> None:
    on, on_pool, on_metrics, _, _ = build()
    off, off_pool, off_metrics, _, _ = build(enabled=False)

    for resolver in (on, off):
        for name in ("metrics.shop.example", "www.shop.example", "metrics.shop.example"):
            await resolver.resolve(ask(name), "tv")

    assert on_pool.forwarded == off_pool.forwarded == 2
    assert on_metrics.savings.by_cause() == off_metrics.savings.by_cause()
    assert on_metrics.savings.by_cause()[saved.BLOCKLIST] == 0


async def test_turning_it_off_passes_the_answer() -> None:
    resolver, _, _, _, _ = build(enabled=False)

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is False
    assert addresses(result.wire) == ["203.0.113.7"]


async def test_a_stale_answer_is_checked_too() -> None:
    resolver, pool, _, _, cache = build(stale=3600)
    query = dns.message.make_query("metrics.shop.example", "A")
    key = cache.key_for(query)
    response = dns.message.from_wire(pool.answer(query.to_wire()))
    cache.put(key, response, response.to_wire())
    cache._entries[key].expires_at = time.monotonic() - 5
    pool.fail = True

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.upstream == LABEL
    assert result.cached is True


async def test_a_paused_device_skips_it() -> None:
    resolver, _, _, _, _ = build()
    resolver._pauses = type("P", (), {"active": staticmethod(lambda device: True)})()

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.blocked is False


async def test_an_exception_inside_the_check_passes_the_answer_through(monkeypatch) -> None:
    resolver, _, _, _, _ = build()

    def broken(message, asked):
        raise RuntimeError("boom")

    monkeypatch.setattr(uncloak, "targets", broken)

    result = await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert result.rcode == "NOERROR"
    assert addresses(result.wire) == ["203.0.113.7"]
    assert resolver.uncloak.failed == 1


async def test_it_is_in_the_log_the_askers_and_the_digest() -> None:
    resolver, _, metrics, _, _ = build()

    await resolver.resolve(ask("metrics.shop.example"), "tv")

    assert metrics.recent(limit=1)[0]["upstream"] == LABEL
    assert metrics.askers("metrics.shop.example")[0]["last_upstream"] == LABEL
    assert metrics.device_cloaked("tv") == {
        "metrics.shop.example": "cname collect.tracker.net · blocklist Trackers"
    }
    digest = Digest(metrics, resolver=resolver, devices=resolver._devices)
    rows = {row["name"]: row for row in digest._blocked_names("tv")}
    assert rows["metrics.shop.example"]["layer"] == "cname"
    assert rows["metrics.shop.example"]["label"] == "cname collect.tracker.net · blocklist Trackers"


def test_the_target_walk_is_cheap() -> None:
    pool = ChainPool()
    response = dns.message.from_wire(
        pool.answer(dns.message.make_query("metrics.shop.example", "A").to_wire())
    )
    rounds = 2000
    started = time.perf_counter()
    for _ in range(rounds):
        uncloak.targets(response, "metrics.shop.example")
    per_check_us = (time.perf_counter() - started) / rounds * 1_000_000

    assert uncloak.targets(response, "metrics.shop.example") == [
        "shop.edge.example",
        "collect.tracker.net",
    ]
    assert per_check_us < CHECK_BOUND_US


def test_it_is_on_by_default() -> None:
    assert settings.cname_uncloak is True
    assert uncloak.build(settings).enabled is True


@respx.mock
async def test_a_cloaked_block_over_real_doh_is_a_dns_answer_with_a_latin_1_header() -> None:
    from hydra_dns.main import app

    query = dns.message.make_query("metrics.shop.example", "A")
    mock_upstream(ChainPool().answer(query.to_wire()))
    async with client() as http:
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._compiled = {}
        with_list(store, ("tracker.net",), name="Werbung — 広告 ✓", lists=app.state.blocklists)
        before = app.state.resolver.uncloak.blocked
        first = await _over_doh(http, "metrics.shop.example")
        again = await _over_doh(http, "metrics.shop.example")
        blocked = app.state.resolver.uncloak.blocked - before
        app.state.blocklists._states.clear()
        app.state.blocklists._rebuild()

    header = first.headers["x-hydra-upstream"]
    assert first.status_code == again.status_code == 200
    assert header.startswith("cname collect.tracker.net \xb7 blocklist Werbung ")
    assert header.endswith(" -> 0.0.0.0")
    header.encode("latin-1")
    assert addresses(first.content) == addresses(again.content) == ["0.0.0.0"]
    assert blocked == 2
    assert respx.calls.call_count == 1
