from __future__ import annotations

import asyncio
import dataclasses
import json
import time

import httpx
import pytest
import respx
from test_doh import client, mock_upstream
from test_domain_api import PASSWORD, RPC, Registry

from hydra_dns import rules
from hydra_dns.store import MAX_TEMPORARY_RULES, Device, DeviceStore

NOW = 1_790_000_000.0


def later(seconds: float) -> str:
    return rules.stamp(NOW + seconds)


class Clock:
    def __init__(self, monkeypatch, at: float = NOW) -> None:
        self.at = at
        monkeypatch.setattr(time, "time", lambda: self.at)


def configure(monkeypatch, cache: str = "") -> None:
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
            device_cache_file=cache,
        ),
    )


def test_a_temporary_allow_beats_a_permanent_block_until_it_lapses() -> None:
    lines = ["ads.example", f"@@ads.example$expires={later(3600)}"]

    live = rules.parse(lines, now=NOW)
    assert not live.blocks("ads.example")
    assert live.allows("img.ads.example")
    assert live.next_expiry == NOW + 3600
    assert live.expires_at("ads.example", True) == NOW + 3600

    lapsed = rules.parse(lines, now=NOW + 3600)
    assert lapsed.blocks("ads.example")
    assert lapsed.next_expiry == rules.NEVER
    assert lapsed.temporary_allow == ()


def test_a_temporary_block_beats_a_permanent_allow() -> None:
    parsed = rules.parse(["@@video.example", f"video.example$expires={later(60)}"], now=NOW)
    assert parsed.blocks("video.example")
    assert not parsed.allows("video.example")
    assert not rules.parse(
        ["@@video.example", f"video.example$expires={later(60)}"], now=NOW + 61
    ).blocks("video.example")


def test_only_a_well_formed_expiry_is_a_rule() -> None:
    assert rules.invalid(
        [
            f"@@ok.example$expires={later(60)}",
            f"||ok.example^$expires={later(60)}",
            "bad.example$important",
            "bad.example$expires=tomorrow",
            f"not a domain$expires={later(60)}",
        ]
    ) == [
        "bad.example$important",
        "bad.example$expires=tomorrow",
        f"not a domain$expires={later(60)}",
    ]


def test_a_temporary_write_keeps_the_permanent_entry_and_replaces_older_temporaries() -> None:
    first = rules.edit(["keep.example", "ads.example"], "ads.example", "allow", NOW + 60)
    assert first == ["keep.example", "ads.example", f"@@ads.example$expires={later(60)}"]

    again = rules.edit(first, "ads.example", "block", NOW + 120)
    assert again == ["keep.example", "ads.example"]

    moved = rules.edit(first, "ads.example", "allow", NOW + 600)
    assert moved == ["keep.example", "ads.example", f"@@ads.example$expires={later(600)}"]


def test_a_permanent_write_replaces_the_temporary_and_clear_removes_both() -> None:
    lines = ["ads.example", f"@@ads.example$expires={later(60)}"]
    assert rules.edit(lines, "ads.example", "allow") == ["@@ads.example"]
    assert rules.edit(lines, "ads.example", "block") == ["ads.example"]
    assert rules.edit(lines, "ads.example", "clear") == []


def test_a_temporary_clear_keeps_the_permanent_entry() -> None:
    lines = ["ads.example", f"@@ads.example$expires={later(60)}", "other.example"]
    kept = rules.edit(lines, "ads.example", "clear", only_temporary=True)
    assert kept == ["ads.example", "other.example"]


def test_prune_drops_only_what_has_lapsed() -> None:
    lines = ["keep.example", f"a.example$expires={later(-1)}", f"b.example$expires={later(60)}"]
    assert rules.prune(lines, NOW) == ["keep.example", f"b.example$expires={later(60)}"]
    assert rules.lapsed(lines, NOW)
    assert not rules.lapsed(rules.prune(lines, NOW), NOW)


def a_store(*devices: Device) -> DeviceStore:
    store = DeviceStore(httpx.AsyncClient(), [])
    store._adopt(list(devices))
    return store


def test_the_hot_path_stops_honouring_an_entry_the_second_it_lapses(monkeypatch) -> None:
    clock = Clock(monkeypatch)
    store = a_store(
        Device(id="tv", name="TV", rules=("ads.example", f"@@ads.example$expires={later(3600)}"))
    )

    allowed = store.explain("tv", "ads.example")
    assert allowed.layer == "allowed"
    assert allowed.expires_at == NOW + 3600
    assert allowed.as_dict()["expires_at"] == NOW + 3600
    assert not store.blocks("tv", "ads.example")
    compiled = store.rules_for("tv")
    assert store.rules_for("tv") is compiled

    clock.at = NOW + 3599.9
    assert store.rules_for("tv") is compiled

    clock.at = NOW + 3600
    assert store.blocks("tv", "ads.example")
    blocked = store.explain("tv", "ads.example")
    assert blocked.layer == "device rule"
    assert blocked.expires_at is None
    assert "expires_at" not in blocked.as_dict()
    assert store.rules_for("tv") is not compiled


def test_a_temporary_block_is_explained_with_its_expiry(monkeypatch) -> None:
    Clock(monkeypatch)
    store = a_store(Device(id="tv", name="TV", rules=(f"video.example$expires={later(600)}",)))

    decision = store.explain("tv", "cdn.video.example")
    assert decision.blocked is True
    assert decision.rule == "video.example"
    assert decision.expires_at == NOW + 600


@respx.mock
async def test_the_resolver_decision_carries_the_expiry_and_lapses() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        store = app.state.devices
        soon = time.time() + 120
        store._adopt(
            [Device(id="tv", name="TV", rules=(f"video.example$expires={rules.stamp(soon)}",))]
        )
        decision = app.state.resolver.decide("video.example", "A", "tv")
        assert decision.blocked is True
        assert decision.expires_at == float(int(soon))

        real = time.time
        try:
            time.time = lambda: real() + 3600
            assert app.state.resolver.decide("video.example", "A", "tv") is None
        finally:
            time.time = real


@respx.mock
async def test_an_expiry_survives_a_restart_on_the_disk_cache(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    configure(monkeypatch, cache=str(cache))
    Clock(monkeypatch)
    line = f"@@ads.example$expires={later(3600)}"
    listed = respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(
            200, json=[{"id": "tv", "name": "TV", "rules": ["ads.example", line]}]
        )
    )
    respx.post(f"{RPC}/hydra_rule_groups_list").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{RPC}/hydra_rewrites_list").mock(return_value=httpx.Response(200, json=[]))

    store = DeviceStore(httpx.AsyncClient(), [])
    await store.sync()
    assert json.loads(cache.read_text())["devices"][0]["rules"] == ["ads.example", line]

    listed.mock(side_effect=httpx.ConnectError("down"))
    restarted = DeviceStore(httpx.AsyncClient(), [])
    await restarted.bootstrap()

    assert restarted.get("tv").source == "cache"
    assert restarted.explain("tv", "ads.example").expires_at == NOW + 3600


def mounted(monkeypatch, devices: list[dict]) -> Registry:
    configure(monkeypatch)
    registry = Registry(devices=devices, groups=[])
    registry.mount()
    return registry


@respx.mock
async def test_the_sweep_removes_lapsed_entries_from_the_registry(monkeypatch) -> None:
    clock = Clock(monkeypatch)
    lapsing = f"a.example$expires={later(60)}"
    lasting = f"@@b.example$expires={later(7200)}"
    registry = mounted(
        monkeypatch,
        [
            {"id": "tv", "name": "TV", "rules": ["keep.example", lapsing, lasting]},
            {"id": "pi", "name": "Pi", "rules": ["other.example"]},
        ],
    )
    store = DeviceStore(httpx.AsyncClient(), [])
    await store.sync()

    assert await store.sweep_expired() == 0
    assert registry.writes == 0

    clock.at = NOW + 61
    assert store.get("tv").as_dict()["rules"] == ["keep.example", lasting]
    assert await store.sweep_expired() == 1
    assert registry.devices["tv"]["rules"] == ["keep.example", lasting]
    assert registry.devices["pi"]["rules"] == ["other.example"]
    assert store.get("tv").rules == ("keep.example", lasting)
    assert await store.sweep_expired() == 0
    assert registry.writes == 1


@respx.mock
async def test_a_failed_sweep_changes_nothing_and_the_hot_path_still_ignores_the_entry(
    monkeypatch,
) -> None:
    clock = Clock(monkeypatch)
    lapsing = f"@@ads.example$expires={later(60)}"
    registry = mounted(monkeypatch, [{"id": "tv", "name": "TV", "rules": ["ads.example", lapsing]}])
    store = DeviceStore(httpx.AsyncClient(), [])
    await store.sync()
    assert not store.blocks("tv", "ads.example")

    respx.post(f"{RPC}/hydra_device_rules_set").mock(side_effect=httpx.ConnectError("down"))
    clock.at = NOW + 60
    assert await store.sweep_expired() == 0
    assert store.get("tv").rules == ("ads.example", lapsing)
    assert store.blocks("tv", "ads.example")
    assert registry.writes == 0


async def signed_in(http) -> None:
    assert (await http.post("/api/login", json={"password": PASSWORD})).status_code == 200


@respx.mock
async def test_a_timed_entry_lands_shows_on_the_sheet_and_is_audited(monkeypatch) -> None:
    from hydra_dns.main import app

    registry = mounted(monkeypatch, [{"id": "tv", "name": "TV", "rules": ["ads.example"]}])
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await app.state.devices.sync()

        before = time.time()
        timed = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "ads.example", "action": "allow", "minutes": 60},
        )
        sheet = (await http.get("/api/domain", params={"name": "ads.example"})).json()
        permanent = await http.post(
            "/api/devices/tv/rules/entry", json={"domain": "ads.example", "action": "allow"}
        )
        entries = [e for e in app.state.audit.recent(limit=10) if e["action"] == "device.rule"]

    assert timed.status_code == 200
    stored = timed.json()["rules"]
    assert stored[0] == "ads.example"
    assert stored[1].startswith("@@ads.example$expires=")
    tv = next(d for d in sheet["devices"] if d["id"] == "tv")
    assert tv["blocked"] is False
    assert before + 3599 <= tv["decision"]["expires_at"] <= time.time() + 3600
    assert permanent.json()["rules"] == ["@@ads.example"]
    assert registry.devices["tv"]["rules"] == ["@@ads.example"]
    minutes = {e["detail"].get("minutes") for e in entries}
    assert minutes == {60, None}


@respx.mock
async def test_removing_a_temporary_entry_leaves_the_permanent_line(monkeypatch) -> None:
    from hydra_dns.main import app

    brief = f"@@ads.example$expires={rules.stamp(time.time() + 3600)}"
    registry = mounted(
        monkeypatch,
        [{"id": "tv", "name": "TV", "rules": ["ads.example", brief, "keep.example"]}],
    )
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await app.state.devices.sync()

        refused = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "ads.example", "action": "block", "temporary": True},
        )
        removed = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "ads.example", "action": "clear", "temporary": True},
        )
        entries = [e for e in app.state.audit.recent(limit=10) if e["action"] == "device.rule"]

    assert refused.status_code == 400
    assert removed.status_code == 200
    assert removed.json()["rules"] == ["ads.example", "keep.example"]
    assert registry.devices["tv"]["rules"] == ["ads.example", "keep.example"]
    assert registry.writes == 1
    assert entries[0]["detail"]["temporary"] is True
    assert app.state.devices.blocks("tv", "ads.example")


@respx.mock
async def test_a_timed_entry_is_bounded_in_length_and_in_number(monkeypatch) -> None:
    from hydra_dns.main import app

    full = [
        f"d{i}.example$expires={rules.stamp(time.time() + 3600)}"
        for i in range(MAX_TEMPORARY_RULES)
    ]
    registry = mounted(monkeypatch, [{"id": "tv", "name": "TV", "rules": full}])
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await app.state.devices.sync()

        short = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "a.example", "action": "block", "minutes": 4},
        )
        long = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "a.example", "action": "block", "minutes": 7 * 24 * 60 + 1},
        )
        crowded = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "a.example", "action": "block", "minutes": 60},
        )
        pasted = await http.put(
            "/api/devices/tv/rules", json={"rules": [*full, f"z.example$expires={later(10**8)}"]}
        )
        replaced = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "d0.example", "action": "allow", "minutes": 30},
        )

    assert short.status_code == 422
    assert long.status_code == 422
    assert crowded.status_code == 400
    assert "temporary" in crowded.json()["detail"]["message"]
    assert pasted.status_code == 400
    assert replaced.status_code == 200
    assert registry.writes == 1


@respx.mock
async def test_concurrent_timed_writes_all_land(monkeypatch) -> None:
    from hydra_dns.main import app

    registry = mounted(monkeypatch, [{"id": "tv", "name": "TV", "rules": ["keep.example"]}])
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await app.state.devices.sync()

        results = await asyncio.gather(
            http.post(
                "/api/devices/tv/rules/entry",
                json={"domain": "a.example", "action": "allow", "minutes": 60},
            ),
            http.post(
                "/api/devices/tv/rules/entry",
                json={"domain": "b.example", "action": "block", "minutes": 480},
            ),
            http.post(
                "/api/devices/tv/rules/entry", json={"domain": "c.example", "action": "block"}
            ),
        )

    assert all(r.status_code == 200 for r in results)
    stored = registry.devices["tv"]["rules"]
    assert stored[0] == "keep.example"
    assert sorted(rules.read_line(line).domain for line in stored) == [
        "a.example",
        "b.example",
        "c.example",
        "keep.example",
    ]
    assert len(rules.temporary(stored)) == 2


@pytest.mark.parametrize("minutes", [60, None])
@respx.mock
async def test_a_timed_write_needs_a_session(minutes) -> None:
    mock_upstream()
    async with client() as http:
        response = await http.post(
            "/api/devices/tv/rules/entry",
            json={"domain": "ads.example", "action": "allow", "minutes": minutes},
        )
    assert response.status_code == 401


def test_a_schedule_refuses_a_temporary_entry() -> None:
    from hydra_dns.store import StoreError, _group_row

    with pytest.raises(StoreError, match="one device"):
        _group_row("g", "G", [f"a.example$expires={later(60)}"], [], [], "", "", True)
