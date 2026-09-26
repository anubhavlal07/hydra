from __future__ import annotations

import asyncio
import dataclasses
import json
import time

import dns.message
import dns.rdatatype
import httpx
import pytest
import respx
from conftest import make_answer
from test_blocklist_resolver import with_list
from test_doh import client, mock_upstream

from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import QueryRecord
from hydra_dns.store import Device, RuleGroup

PASSWORD = "hunter2"
RPC = "https://store.test/rest/v1/rpc"


async def signed_in(http) -> None:
    assert (await http.post("/api/login", json={"password": PASSWORD})).status_code == 200


class Registry:
    def __init__(self, devices: list[dict], groups: list[dict]) -> None:
        self.devices = {d["id"]: d for d in devices}
        self.groups = {g["id"]: g for g in groups}
        self.writes = 0

    def body(self, request: httpx.Request) -> dict:
        return json.loads(request.content)

    def list_devices(self, request):
        return httpx.Response(200, json=list(self.devices.values()))

    async def set_rules(self, request):
        body = self.body(request)
        await asyncio.sleep(0.01)
        self.devices[body["p_id"]] = {**self.devices[body["p_id"]], "rules": body["p_rules"]}
        self.writes += 1
        return httpx.Response(200, json=True)

    def list_groups(self, request):
        return httpx.Response(200, json=list(self.groups.values()))

    async def set_group(self, request):
        body = self.body(request)
        await asyncio.sleep(0.01)
        self.groups[body["p_id"]] = {
            "id": body["p_id"],
            "name": body["p_name"],
            "rules": body["p_rules"],
            "tags": body["p_tags"],
            "days": body["p_days"],
            "starts_at": body["p_starts_at"],
            "ends_at": body["p_ends_at"],
            "enabled": body["p_enabled"],
        }
        self.writes += 1
        return httpx.Response(200, json=True)

    def mount(self) -> None:
        respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=self.list_devices)
        respx.post(f"{RPC}/hydra_device_rules_set").mock(side_effect=self.set_rules)
        respx.post(f"{RPC}/hydra_rule_groups_list").mock(side_effect=self.list_groups)
        respx.post(f"{RPC}/hydra_rule_group_set").mock(side_effect=self.set_group)
        respx.post(f"{RPC}/hydra_rewrites_list").mock(return_value=httpx.Response(200, json=[]))
        respx.post(f"{RPC}/hydra_blocklists_list").mock(return_value=httpx.Response(200, json=[]))


def a_registry() -> Registry:
    return Registry(
        devices=[
            {
                "id": "tv",
                "name": "TV",
                "rules": ["keep.test", "@@also-keep.test"],
                "tags": ["kids"],
                "safe_search": True,
                "block_mode": "nxdomain",
                "hourly_budget": 500,
            },
            {"id": "laptop", "name": "Laptop", "rules": []},
        ],
        groups=[
            {
                "id": "bedtime",
                "name": "Bedtime",
                "rules": ["tiktok.test"],
                "tags": ["kids"],
                "days": [0, 1, 2],
                "starts_at": "22:00",
                "ends_at": "07:00",
                "enabled": False,
            }
        ],
    )


@pytest.fixture
def registry(monkeypatch) -> Registry:
    from hydra_dns import store as store_module

    monkeypatch.setattr(
        store_module,
        "settings",
        dataclasses.replace(
            store_module.settings,
            store_url="https://store.test",
            store_key="test-key",
            store_secret="test-secret",
            store_seed=False,
            device_cache_file="",
        ),
    )
    return a_registry()


async def ready(app) -> None:
    await app.state.devices.sync()
    assert app.state.devices.writable


@respx.mock
async def test_a_device_entry_blocks_allows_and_clears_and_keeps_the_rest(registry) -> None:
    from hydra_dns.main import app

    registry.mount()
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await ready(app)

        blocked = await http.post(
            "/api/devices/tv/rules/entry", json={"domain": "Ads.Example.", "action": "block"}
        )
        after_block = list(registry.devices["tv"]["rules"])
        allowed = await http.post(
            "/api/devices/tv/rules/entry", json={"domain": "ads.example", "action": "allow"}
        )
        after_allow = list(registry.devices["tv"]["rules"])
        cleared = await http.post(
            "/api/devices/tv/rules/entry", json={"domain": "ads.example", "action": "clear"}
        )
        entries = app.state.audit.recent(limit=10)
        device = app.state.devices.get("tv")

    assert blocked.status_code == 200
    assert blocked.json() == {"id": "tv", "rules": ["keep.test", "@@also-keep.test", "ads.example"]}
    assert after_block == ["keep.test", "@@also-keep.test", "ads.example"]
    assert after_allow == ["keep.test", "@@also-keep.test", "@@ads.example"]
    assert allowed.json()["rules"] == after_allow
    assert cleared.json() == {"id": "tv", "rules": ["keep.test", "@@also-keep.test"]}
    assert device.tags == ("kids",)
    assert device.safe_search is True
    assert device.block_mode == "nxdomain"
    assert device.hourly_budget == 500
    audited = [e for e in entries if e["action"] == "device.rule"]
    assert {e["detail"]["action"] for e in audited} == {"block", "allow", "clear"}
    assert all(e["detail"]["domain"] for e in audited)


@respx.mock
async def test_a_schedule_entry_blocks_allows_and_clears_and_keeps_every_other_field(
    registry,
) -> None:
    from hydra_dns.main import app

    registry.mount()
    mock_upstream()
    before = {k: v for k, v in registry.groups["bedtime"].items() if k != "rules"}
    async with client() as http:
        await signed_in(http)
        await ready(app)

        blocked = await http.post(
            "/api/rule-groups/bedtime/rules/entry", json={"domain": "yt.test", "action": "block"}
        )
        allowed = await http.post(
            "/api/rule-groups/bedtime/rules/entry",
            json={"domain": "tiktok.test", "action": "allow"},
        )
        cleared = await http.post(
            "/api/rule-groups/bedtime/rules/entry", json={"domain": "yt.test", "action": "clear"}
        )
        timed = await http.post(
            "/api/rule-groups/bedtime/rules/entry",
            json={"domain": "yt.test", "action": "block", "minutes": 60},
        )
        entries = app.state.audit.recent(limit=10)

    assert timed.status_code == 422
    assert blocked.status_code == 200
    assert blocked.json()["rules"] == ["tiktok.test", "yt.test"]
    assert allowed.json()["rules"] == ["yt.test", "@@tiktok.test"]
    assert cleared.json()["rules"] == ["@@tiktok.test"]
    assert cleared.json()["when"] == blocked.json()["when"]
    after = {k: v for k, v in registry.groups["bedtime"].items() if k != "rules"}
    assert after == before
    assert any(e["action"] == "schedule.rule" for e in entries)


@respx.mock
async def test_two_concurrent_entry_writes_both_land(registry) -> None:
    from hydra_dns.main import app

    registry.mount()
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await ready(app)

        first, second, third, fourth = await asyncio.gather(
            http.post(
                "/api/devices/laptop/rules/entry", json={"domain": "a.test", "action": "block"}
            ),
            http.post(
                "/api/devices/laptop/rules/entry", json={"domain": "b.test", "action": "allow"}
            ),
            http.post(
                "/api/rule-groups/bedtime/rules/entry", json={"domain": "c.test", "action": "block"}
            ),
            http.post(
                "/api/rule-groups/bedtime/rules/entry", json={"domain": "d.test", "action": "block"}
            ),
        )

    assert all(r.status_code == 200 for r in (first, second, third, fourth))
    assert sorted(registry.devices["laptop"]["rules"]) == ["@@b.test", "a.test"]
    assert sorted(registry.groups["bedtime"]["rules"]) == ["c.test", "d.test", "tiktok.test"]


@respx.mock
async def test_entry_routes_refuse_bad_input_and_unknown_ids(registry) -> None:
    from hydra_dns.main import app

    registry.mount()
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await ready(app)

        bad_name = await http.post(
            "/api/devices/tv/rules/entry", json={"domain": "not a domain", "action": "block"}
        )
        bad_action = await http.post(
            "/api/devices/tv/rules/entry", json={"domain": "ads.test", "action": "nuke"}
        )
        no_device = await http.post(
            "/api/devices/ghost/rules/entry", json={"domain": "ads.test", "action": "block"}
        )
        no_group = await http.post(
            "/api/rule-groups/ghost/rules/entry", json={"domain": "ads.test", "action": "block"}
        )

    assert bad_name.status_code == 400
    assert bad_name.json()["detail"]["code"] == "store_error"
    assert bad_action.status_code == 422
    assert no_device.status_code == 404
    assert no_group.status_code == 404
    assert registry.writes == 0


@respx.mock
async def test_entry_routes_need_a_writable_registry() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        device = await http.post(
            "/api/devices/laptop/rules/entry", json={"domain": "ads.test", "action": "block"}
        )
        group = await http.post(
            "/api/rule-groups/bedtime/rules/entry", json={"domain": "ads.test", "action": "block"}
        )

    assert device.status_code == 503
    assert group.status_code == 503


def remember(metrics, device: str, name: str, upstream: str, *, at: float, blocked=False) -> None:
    metrics.record(
        QueryRecord(
            at=at,
            device=device,
            name=name,
            qtype="A",
            rcode="NOERROR",
            cached=False,
            blocked=blocked,
            upstream=upstream,
            latency_ms=1.0,
        )
    )


def put_answer(cache, name: str) -> None:
    query = dns.message.make_query(name, dns.rdatatype.A)
    response, wire = make_answer(name=name)
    cache.put(AnswerCache.key_for(query), response, wire)


@respx.mock
async def test_the_domain_sheet_reads_state_and_never_asks_anyone() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        store = app.state.devices
        store._adopt(
            [
                Device(id="tv", name="TV", tags=("kids",)),
                Device(id="pixel", name="Pixel", rules=("@@ads.example.com",)),
                Device(id="laptop", name="Laptop", rules=("ads.example.com",)),
            ]
        )
        store._adopt_groups([RuleGroup(id="bedtime", name="Bedtime", rules=(), tags=("kids",))])
        lists = with_list(
            store, name="hagezi-pro", list_id="hagezi-pro", lists=app.state.blocklists
        )
        with_list(
            store,
            ("ads.example.com",),
            list_id="lenient",
            name="Lenient",
            allow=("ads.example.com",),
            lists=lists,
        )
        now = time.time()
        metrics = app.state.metrics
        metrics.first_seen.clear()
        label = "blocklist hagezi-pro → 0.0.0.0"
        remember(metrics, "tv", "ads.example.com", label, at=now - 60, blocked=True)
        remember(metrics, "tv", "ads.example.com", label, at=now - 5, blocked=True)
        remember(metrics, "laptop", "ads.example.com", "alpha", at=now - 30)
        remember(metrics, "laptop", "other.example.com", "alpha", at=now - 30)
        put_answer(app.state.cache, "ads.example.com")

        pool = app.state.pool

        async def refuse(*args, **kwargs):
            raise AssertionError("the domain sheet must not resolve anything")

        pool.forward = refuse
        pool.forward_unfiltered = refuse
        stats_before = dataclasses.asdict(app.state.cache.stats)
        hits_before = lists.get("hagezi-pro").hits
        calls_before = len(respx.calls)

        response = await http.get("/api/domain", params={"name": "Ads.Example.com."})

        calls_after = len(respx.calls)
        stats_after = dataclasses.asdict(app.state.cache.stats)
        hits_after = lists.get("hagezi-pro").hits

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "name",
        "devices",
        "askers",
        "cache",
        "lists",
        "apps",
        "suspicion",
        "editable",
    }
    assert body["name"] == "ads.example.com"
    by_id = {d["id"]: d for d in body["devices"]}
    assert set(by_id["tv"]) == {"id", "name", "tags", "decision", "blocked"}
    assert by_id["tv"]["decision"] == {
        "layer": "blocklist",
        "label": "blocklist hagezi-pro",
        "blocked": True,
        "source": "hagezi-pro",
        "rule": None,
    }
    assert by_id["tv"]["blocked"] is True
    assert by_id["tv"]["tags"] == ["kids"]
    assert by_id["pixel"]["decision"]["label"] == "allowed by @@ device"
    assert by_id["pixel"]["blocked"] is False
    assert by_id["laptop"]["decision"]["layer"] == "device rule"
    assert by_id["laptop"]["decision"]["rule"] == "ads.example.com"
    assert by_id["pixel"]["decision"]["rule"] == "ads.example.com"

    assert [a["device"] for a in body["askers"]] == ["tv", "laptop"]
    tv = body["askers"][0]
    assert set(tv) == {
        "device",
        "name",
        "count",
        "last_at",
        "last_upstream",
        "last_result",
        "first_at",
    }
    assert tv["name"] == "TV"
    assert tv["count"] == 2
    assert tv["last_at"] == pytest.approx(now - 5, abs=0.01)
    assert tv["last_upstream"] == "blocklist hagezi-pro → 0.0.0.0"
    assert tv["last_result"] == "blocked"
    assert tv["first_at"] == pytest.approx(now - 60, abs=0.01)

    assert body["cache"]["A"]["state"] == "live"
    assert 0 < body["cache"]["A"]["ttl"] <= 300
    assert body["cache"]["AAAA"] == {"state": "absent", "ttl": 0}
    assert body["lists"] == [
        {"id": "hagezi-pro", "name": "hagezi-pro", "allowed": False, "enabled": True},
        {"id": "lenient", "name": "Lenient", "allowed": True, "enabled": True},
    ]
    assert body["editable"] is False

    assert calls_after == calls_before
    assert stats_after == stats_before
    assert hits_after == hits_before


@respx.mock
async def test_the_domain_sheet_refuses_a_name_that_is_not_one() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        bad = await http.get("/api/domain", params={"name": "not a name"})
        missing = await http.get("/api/domain")

    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "bad_name"
    assert missing.status_code == 422


@respx.mock
async def test_the_domain_sheet_caps_the_devices_it_lists(monkeypatch) -> None:
    from hydra_dns.api import dashboard
    from hydra_dns.main import app

    monkeypatch.setattr(
        dashboard, "settings", dataclasses.replace(dashboard.settings, bulk_max_devices=2)
    )
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.devices._adopt([Device(id=f"d{i}", name=f"D{i}") for i in range(5)])
        body = (await http.get("/api/domain", params={"name": "example.com"})).json()

    assert [d["id"] for d in body["devices"]] == ["d0", "d1"]
    assert all(d["decision"] is None and d["blocked"] is False for d in body["devices"])


def test_peek_leaves_lru_order_and_counters_alone() -> None:
    cache = AnswerCache(max_entries=2, min_ttl=1, max_ttl=3600, stale_window_s=600)
    first = AnswerCache.key_for(dns.message.make_query("first.test", dns.rdatatype.A))
    second = AnswerCache.key_for(dns.message.make_query("second.test", dns.rdatatype.A))
    missing = AnswerCache.key_for(dns.message.make_query("missing.test", dns.rdatatype.A))
    for name, key in (("first.test", first), ("second.test", second)):
        response, wire = make_answer(name=name, ttl=120)
        cache.put(key, response, wire)
    stats = dataclasses.asdict(cache.stats)

    for _ in range(5):
        assert cache.peek(first)["state"] == "live"
    assert cache.peek(missing) == {"state": "absent", "ttl": 0}

    assert dataclasses.asdict(cache.stats) == stats
    assert list(cache._entries) == [first, second]
    third = AnswerCache.key_for(dns.message.make_query("third.test", dns.rdatatype.A))
    response, wire = make_answer(name="third.test")
    cache.put(third, response, wire)
    assert first not in cache._entries


def test_peek_reports_live_stale_and_absent_with_the_seconds_left() -> None:
    cache = AnswerCache(max_entries=10, min_ttl=1, max_ttl=3600, stale_window_s=600)
    key = AnswerCache.key_for(dns.message.make_query("example.com", dns.rdatatype.A))
    response, wire = make_answer(ttl=120)
    cache.put(key, response, wire)

    live = cache.peek(key)
    assert live["state"] == "live"
    assert 119 <= live["ttl"] <= 120

    cache._entries[key].expires_at = time.monotonic() - 30
    stale = cache.peek(key)
    assert stale["state"] == "stale"
    assert -32 <= stale["ttl"] <= -30

    cache._entries[key].expires_at = time.monotonic() - 900
    assert cache.peek(key) == {"state": "absent", "ttl": 0}
    assert key in cache._entries
