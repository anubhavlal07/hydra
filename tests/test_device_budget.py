from __future__ import annotations

import dns.message
import dns.rdatatype
import httpx
import respx
from conftest import make_answer

from hydra_dns.resolver.budget import SLOTS, DeviceBudget
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamPool

UPSTREAM = "https://upstream.test/dns-query"


def build(hourly: int, stale_window_s: int = 600) -> Resolver:
    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="one", doh_url=UPSTREAM)])
    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600, stale_window_s=stale_window_s)
    return Resolver(cache, pool, Metrics(), budget=DeviceBudget(hourly))


def query(name: str = "example.com") -> bytes:
    return dns.message.make_query(name, dns.rdatatype.A).to_wire()


def test_a_budget_of_zero_is_off() -> None:
    budget = DeviceBudget(0)
    assert not budget.enabled
    for _ in range(1000):
        assert budget.allow("phone")
    assert budget.snapshot() == {"enabled": False, "hourly": 0, "denied": 0, "devices": []}


def test_a_device_gets_its_hour_and_no_more() -> None:
    budget = DeviceBudget(3)
    assert [budget.allow("phone") for _ in range(5)] == [True, True, True, False, False]
    assert budget.denied == 2


def test_devices_have_their_own_budgets() -> None:
    budget = DeviceBudget(2)
    assert budget.allow("phone") and budget.allow("phone")
    assert not budget.allow("phone")
    assert budget.allow("laptop")


def test_a_breach_is_reported_for_the_dashboard() -> None:
    budget = DeviceBudget(1)
    budget.allow("phone")
    assert budget.breaching() == []
    budget.allow("phone")
    assert budget.breaching() == ["phone"]


def test_an_old_breach_falls_out_of_the_report() -> None:
    import time

    budget = DeviceBudget(1)
    budget.allow("phone")
    budget.allow("phone")
    assert budget.breaching() == ["phone"]

    budget._spends["phone"].breached_at = time.time() - 600
    assert budget.breaching(within_s=300) == []


@respx.mock
async def test_a_query_inside_the_budget_is_answered_normally() -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    resolver = build(hourly=10)

    result = await resolver.resolve(query(), "phone")
    assert result.rcode == "NOERROR"
    assert result.upstream == "one"


@respx.mock
async def test_a_cache_hit_is_free_and_never_counted() -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    resolver = build(hourly=1)

    first = await resolver.resolve(query(), "phone")
    assert not first.cached

    for _ in range(20):
        again = await resolver.resolve(query(), "phone")
        assert again.cached

    assert resolver.budget.denied == 0


@respx.mock
async def test_a_spent_device_is_served_stale_rather_than_refused() -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer(ttl=1)[1]))
    resolver = build(hourly=1)

    await resolver.resolve(query(), "phone")

    key = AnswerCache.key_for(dns.message.make_query("example.com", dns.rdatatype.A))
    import time

    resolver._cache._entries[key].expires_at = time.monotonic() - 5

    result = await resolver.resolve(query(), "phone")
    assert result.stale
    assert result.upstream == "stale (budget)"
    assert result.rcode == "NOERROR"


@respx.mock
async def test_a_spent_device_with_nothing_cached_gets_servfail() -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    resolver = build(hourly=1)

    await resolver.resolve(query("first.test"), "phone")
    result = await resolver.resolve(query("second.test"), "phone")

    assert result.rcode == "SERVFAIL"
    assert result.upstream == "device budget"


@respx.mock
async def test_one_runaway_device_does_not_stop_the_others() -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    resolver = build(hourly=1)

    await resolver.resolve(query("a.test"), "phone")
    spent = await resolver.resolve(query("b.test"), "phone")
    fine = await resolver.resolve(query("c.test"), "laptop")

    assert spent.rcode == "SERVFAIL"
    assert fine.rcode == "NOERROR"


@respx.mock
async def test_a_device_rule_block_costs_nothing(monkeypatch) -> None:
    from hydra_dns.config import DeviceConfig
    from hydra_dns.store import DeviceStore

    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="one", doh_url=UPSTREAM)])
    store = DeviceStore(client, [DeviceConfig(id="phone", name="Phone")])
    store._adopt(
        [type(store.devices[0])(id="phone", name="Phone", source="env", rules=("blocked.test",))]
    )

    resolver = Resolver(
        AnswerCache(max_entries=10, min_ttl=30, max_ttl=3600),
        pool,
        Metrics(),
        store,
        budget=DeviceBudget(1),
    )

    for _ in range(10):
        result = await resolver.resolve(query("blocked.test"), "phone")
        assert result.blocked

    assert resolver.budget.denied == 0
    await client.aclose()


def test_the_snapshot_lists_the_emptiest_device_first() -> None:
    budget = DeviceBudget(10)
    for _ in range(8):
        budget.allow("phone")
    budget.allow("laptop")

    snapshot = budget.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["hourly"] == 10
    assert [d["device"] for d in snapshot["devices"]] == ["phone", "laptop"]
    assert snapshot["devices"][0]["used"] == 8


def test_a_device_with_its_own_ceiling_overrides_the_default() -> None:
    budget = DeviceBudget(10)

    assert [budget.allow("tv", 2) for _ in range(4)] == [True, True, False, False]
    assert budget.allow("laptop") is True
    assert budget.ceiling("tv", 2) == 2
    assert budget.ceiling("laptop") == 10


def test_a_ceiling_works_with_no_default_at_all() -> None:
    budget = DeviceBudget(0)
    assert not budget.enabled

    assert [budget.allow("tv", 1) for _ in range(3)] == [True, False, False]
    assert budget.enabled
    assert budget.allow("laptop") is True


def test_the_snapshot_carries_each_device_own_ceiling() -> None:
    budget = DeviceBudget(10)
    budget.allow("tv", 2)
    budget.allow("laptop")

    rows = {row["device"]: row for row in budget.snapshot()["devices"]}
    assert rows["tv"]["hourly"] == 2
    assert rows["tv"]["used"] == 1
    assert rows["laptop"]["hourly"] == 10
    assert rows["laptop"]["used"] == 1


def test_lowering_a_ceiling_does_not_hand_back_what_was_spent() -> None:
    budget = DeviceBudget(0)
    for _ in range(9):
        budget.allow("tv", 10)

    assert budget.allow("tv", 2) is False
    assert budget.allow("tv", 20) is True


def test_a_ceiling_removed_is_a_ceiling_gone_from_the_report() -> None:
    budget = DeviceBudget(0)
    for _ in range(3):
        budget.allow("moto", 5000)
    assert [row["device"] for row in budget.snapshot()["devices"]] == ["moto"]

    budget.set_ceilings({})

    assert budget.snapshot() == {"enabled": False, "hourly": 0, "denied": 0, "devices": []}


def test_a_ceiling_removed_stops_being_enforced() -> None:
    budget = DeviceBudget(0)
    assert [budget.allow("tv", 1) for _ in range(2)] == [True, False]

    assert budget.allow("tv") is True
    assert budget.snapshot()["devices"] == []


def test_a_ceiling_is_reported_before_the_device_asks_for_anything() -> None:
    budget = DeviceBudget(0)
    assert not budget.enabled

    budget.set_ceilings({"moto": 5000, "laptop": 0})

    snapshot = budget.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["devices"] == [
        {
            "device": "moto",
            "hourly": 5000,
            "remaining": 5000,
            "used": 0,
            "denied": 0,
            "breached_at": None,
        }
    ]


def test_a_device_falls_back_to_the_default_when_its_own_ceiling_goes() -> None:
    budget = DeviceBudget(10)
    for _ in range(2):
        budget.allow("tv", 2)

    budget.set_ceilings({})

    rows = {row["device"]: row for row in budget.snapshot()["devices"]}
    assert rows["tv"]["hourly"] == 10
    assert budget.enabled


def test_spend_only_climbs_while_its_hour_lasts() -> None:
    budget = DeviceBudget(0)
    for _ in range(5):
        budget.allow("moto", 5000)

    assert budget.snapshot()["devices"][0]["used"] == 5
    assert budget.snapshot()["devices"][0]["used"] == 5

    for _ in range(3):
        budget.allow("moto", 5000)

    assert budget.snapshot()["devices"][0]["used"] == 8


def test_spend_leaves_the_report_only_when_its_hour_is_over() -> None:
    budget = DeviceBudget(0)
    for _ in range(4):
        budget.allow("moto", 5000)

    spend = budget._spends["moto"]
    spend.roll(spend.minute + SLOTS - 1)
    assert budget.snapshot()["devices"][0]["used"] == 4

    spend.roll(spend.minute + 1)
    assert budget.snapshot()["devices"][0]["used"] == 0


def test_a_breach_of_a_device_ceiling_is_reported() -> None:
    budget = DeviceBudget(0)
    budget.allow("tv", 1)
    assert budget.breaching() == []
    budget.allow("tv", 1)
    assert budget.breaching() == ["tv"]


@respx.mock
async def test_the_resolver_uses_the_device_own_ceiling(monkeypatch) -> None:
    from hydra_dns.config import DeviceConfig
    from hydra_dns.store import Device, DeviceStore

    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="one", doh_url=UPSTREAM)])

    store = DeviceStore(client, [DeviceConfig(id="tv", name="TV")])
    store._adopt(
        [
            Device(id="tv", name="TV", source="env", hourly_budget=1),
            Device(id="laptop", name="Laptop", source="env"),
        ]
    )

    resolver = Resolver(
        AnswerCache(max_entries=50, min_ttl=30, max_ttl=3600),
        pool,
        Metrics(),
        store,
        budget=DeviceBudget(10),
    )

    await resolver.resolve(query("a.test"), "tv")
    spent = await resolver.resolve(query("b.test"), "tv")
    assert spent.rcode == "SERVFAIL"
    assert spent.upstream == "device budget"

    for name in ("c.test", "d.test", "e.test"):
        fine = await resolver.resolve(query(name), "laptop")
        assert fine.rcode == "NOERROR"

    await client.aclose()


async def test_the_report_follows_the_registry_not_the_traffic() -> None:
    from hydra_dns.config import DeviceConfig
    from hydra_dns.store import Device, DeviceStore

    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    store = DeviceStore(client, [DeviceConfig(id="moto", name="Moto")])
    store._adopt([Device(id="moto", name="Moto", source="env", hourly_budget=5000)])

    resolver = Resolver(
        AnswerCache(max_entries=50, min_ttl=30, max_ttl=3600),
        pool,
        Metrics(),
        store,
        budget=DeviceBudget(0),
    )

    listed = resolver.budget_snapshot()
    assert listed["enabled"] is True
    assert [row["device"] for row in listed["devices"]] == ["moto"]
    assert listed["devices"][0]["hourly"] == 5000

    store._adopt([Device(id="moto", name="Moto", source="env")])

    assert resolver.budget_snapshot() == {
        "enabled": False,
        "hourly": 0,
        "denied": 0,
        "devices": [],
    }
    await client.aclose()
