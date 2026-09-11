from __future__ import annotations

import dataclasses
import json
import time

import httpx
import pytest
import respx

from hydra_dns import rules
from hydra_dns.config import DeviceConfig, device_token
from hydra_dns.store import Device, DeviceStore, StoreError, slugify

RPC = "https://store.test/rest/v1/rpc"
SEED = [DeviceConfig(id="laptop", name="Laptop"), DeviceConfig(id="phone", name="Phone")]


def configure(
    monkeypatch,
    *,
    url: str = "https://store.test",
    seed: bool = False,
    cache: str = "",
) -> None:
    import dataclasses

    from hydra_dns import store as store_module

    monkeypatch.setattr(
        store_module,
        "settings",
        dataclasses.replace(
            store_module.settings,
            store_url=url,
            store_key="test-key" if url else "",
            store_secret="test-secret" if url else "",
            store_seed=seed,
            device_cache_file=cache,
        ),
    )


def make_store() -> DeviceStore:
    return DeviceStore(httpx.AsyncClient(), SEED)


def rows(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": i, "name": n, "created_at": "2026-09-14T00:00:00Z"} for i, n in pairs]


def test_slugify_makes_a_url_safe_id() -> None:
    assert slugify("Work Laptop", set()) == "work-laptop"
    assert slugify("Pixel 9 Pro!", set()) == "pixel-9-pro"


def test_slugify_avoids_collisions() -> None:
    assert slugify("Laptop", {"laptop"}) == "laptop-2"
    assert slugify("Laptop", {"laptop", "laptop-2"}) == "laptop-3"


def test_slugify_survives_a_name_with_nothing_usable_in_it() -> None:
    assert slugify("!!!", set()) == "device"


def test_token_is_derived_from_the_id_not_stored() -> None:
    store = make_store()
    device = store.get("laptop")
    assert device is not None
    assert device.token == device_token("laptop")


def test_by_token_finds_the_right_device() -> None:
    store = make_store()
    assert store.by_token(device_token("phone")).id == "phone"


def test_by_token_rejects_an_unknown_token() -> None:
    assert make_store().by_token("nope") is None


def test_by_token_does_no_io() -> None:
    with respx.mock:
        store = make_store()
        assert store.by_token(device_token("laptop")) is not None


@respx.mock
async def test_sync_replaces_the_list_on_success(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("tv", "Living Room TV")))
    )
    store = make_store()
    await store.sync()
    assert [d.id for d in store.devices] == ["tv"]


@respx.mock
async def test_a_failed_sync_keeps_the_previous_list(monkeypatch) -> None:
    configure(monkeypatch)
    route = respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("tv", "TV"), ("pi", "Pi")))
    )
    store = make_store()
    await store.sync()
    assert len(store.devices) == 2

    route.mock(side_effect=httpx.ConnectError("database unreachable"))
    await store.sync()

    assert [d.id for d in store.devices] == ["tv", "pi"]
    assert "could not reach" in store.last_error


@respx.mock
async def test_an_empty_read_is_authoritative(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_devices_list").mock(return_value=httpx.Response(200, json=[]))
    store = make_store()
    await store.sync()
    assert store.devices == []


@respx.mock
async def test_rows_with_unusable_ids_are_skipped(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "good", "name": "Good"},
                {"id": "bad id/with slash", "name": "Bad"},
            ],
        )
    )
    store = make_store()
    await store.sync()
    assert [d.id for d in store.devices] == ["good"]


@respx.mock
async def test_rejected_secret_is_reported_in_plain_words(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(401, json={"code": "28000", "message": "unauthorized"})
    )
    store = make_store()
    await store.sync()
    assert "shared secret" in store.last_error


@respx.mock
async def test_bootstrap_seeds_when_permitted(monkeypatch) -> None:
    configure(monkeypatch, seed=True)
    seeded = respx.post(f"{RPC}/hydra_devices_seed").mock(
        return_value=httpx.Response(200, json=rows(("laptop", "Laptop"), ("phone", "Phone")))
    )
    store = make_store()
    await store.bootstrap()

    assert seeded.called
    sent = seeded.calls[0].request.content.decode()
    assert "laptop" in sent and "phone" in sent
    assert store.writable


@respx.mock
async def test_bootstrap_failure_leaves_the_seed_serving(monkeypatch) -> None:
    configure(monkeypatch, seed=True)
    respx.post(f"{RPC}/hydra_devices_seed").mock(side_effect=httpx.ConnectError("down"))
    store = make_store()
    await store.bootstrap()

    assert [d.id for d in store.devices] == ["laptop", "phone"]
    assert not store.writable
    assert store.snapshot()["using_seed"] is True


@respx.mock
async def test_bootstrap_does_not_seed_by_default(monkeypatch) -> None:
    configure(monkeypatch)
    seeded = respx.post(f"{RPC}/hydra_devices_seed").mock(
        return_value=httpx.Response(200, json=rows(("laptop", "Laptop")))
    )
    listed = respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("real", "A Real Device")))
    )

    store = make_store()
    await store.bootstrap()

    assert not seeded.called, "must not write to a registry it was not told to seed"
    assert listed.called
    assert [d.id for d in store.devices] == ["real"]
    assert store.writable


@respx.mock
async def test_a_successful_read_is_mirrored_to_disk(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("tv", "TV"), ("pi", "Pi")))
    )

    store = make_store()
    await store.sync()

    written = json.loads(cache.read_text())
    assert [d["id"] for d in written["devices"]] == ["tv", "pi"]
    assert written["written_at"] > 0


@respx.mock
async def test_startup_without_postgres_serves_the_cache(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    cache.write_text(json.dumps({"written_at": 1, "devices": [{"id": "moto", "name": "Moto"}]}))
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=httpx.ConnectError("down"))

    store = make_store()
    await store.bootstrap()

    assert [d.id for d in store.devices] == ["moto"]
    assert all(d.source == "cache" for d in store.devices)
    assert store.by_token(device_token("moto")) is not None
    assert store.by_token(device_token("laptop")) is None


@respx.mock
async def test_every_stored_device_field_survives_a_restart_on_the_cache(
    monkeypatch, tmp_path
) -> None:
    cache = tmp_path / "devices.json"
    configure(monkeypatch, cache=str(cache))
    listed = respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "moto",
                    "name": "Moto Edge 60",
                    "rules": ["ads.example"],
                    "safe_search": True,
                    "block_mode": "address",
                    "block_address": "10.0.0.1",
                    "tags": ["personal"],
                    "hourly_budget": 5000,
                    "safe_search_skip": ["youtube"],
                }
            ],
        )
    )

    store = make_store()
    await store.sync()
    live = store.get("moto")
    assert live.hourly_budget == 5000
    assert live.safe_search_skip == ("youtube",)

    written = json.loads(cache.read_text())["devices"][0]
    assert set(written) == {f.name for f in dataclasses.fields(Device) if f.name != "source"}

    listed.mock(side_effect=httpx.ConnectError("down"))
    restarted = make_store()
    await restarted.bootstrap()

    assert restarted.get("moto") == dataclasses.replace(live, source="cache")


@respx.mock
async def test_the_cache_wins_over_the_environment(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    cache.write_text(json.dumps({"written_at": 1, "devices": [{"id": "newer", "name": "Newer"}]}))
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=httpx.ConnectError("down"))

    store = make_store()
    await store.bootstrap()

    assert [d.id for d in store.devices] == ["newer"]
    assert store.snapshot()["source"] == "cache"


@respx.mock
async def test_a_corrupt_cache_falls_through_to_the_environment(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    cache.write_text('{"devices": [{"id": "trunc')
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=httpx.ConnectError("down"))

    store = make_store()
    await store.bootstrap()

    assert [d.id for d in store.devices] == ["laptop", "phone"]
    assert store.snapshot()["source"] == "env"


@respx.mock
async def test_cache_rows_with_unusable_ids_are_skipped(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    cache.write_text(
        json.dumps(
            {
                "written_at": 1,
                "devices": [{"id": "good", "name": "G"}, {"id": "BAD ID", "name": "B"}],
            }
        )
    )
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=httpx.ConnectError("down"))

    store = make_store()
    await store.bootstrap()
    assert [d.id for d in store.devices] == ["good"]


@respx.mock
async def test_an_unwritable_cache_path_does_not_break_resolving(monkeypatch, tmp_path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    configure(monkeypatch, cache=str(blocker / "devices.json"))
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("tv", "TV")))
    )

    store = make_store()
    await store.sync()
    assert [d.id for d in store.devices] == ["tv"]


@respx.mock
async def test_no_cache_configured_writes_nothing(monkeypatch, tmp_path) -> None:
    configure(monkeypatch, cache="")
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("tv", "TV")))
    )
    store = make_store()
    await store.sync()
    assert list(tmp_path.iterdir()) == []


@respx.mock
async def test_a_lookup_before_the_fallback_does_not_pin_the_seed_rules(
    monkeypatch, tmp_path
) -> None:
    lapses = rules.stamp(time.time() + 3600)
    cache = tmp_path / "devices.json"
    cache.write_text(
        json.dumps(
            {
                "written_at": 1,
                "devices": [
                    {
                        "id": "laptop",
                        "name": "Laptop",
                        "rules": ["ads.example", f"brief.example$expires={lapses}"],
                    }
                ],
            }
        )
    )
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=httpx.ConnectError("down"))

    store = make_store()
    assert not store.blocks("laptop", "ads.example")
    assert not store.blocks("laptop", "brief.example")

    await store.bootstrap()

    assert store.blocks("laptop", "ads.example")
    assert store.blocks("laptop", "brief.example")
    assert store.explain("laptop", "brief.example").expires_at is not None


@respx.mock
async def test_falling_back_to_the_seed_drops_rules_compiled_earlier(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(
            200, json=[{"id": "laptop", "name": "Laptop", "rules": ["ads.example"]}]
        )
    )
    respx.post(f"{RPC}/hydra_rule_groups_list").mock(return_value=httpx.Response(200, json=[]))
    respx.post(f"{RPC}/hydra_rewrites_list").mock(return_value=httpx.Response(200, json=[]))

    store = make_store()
    await store.sync()
    assert store.blocks("laptop", "ads.example")

    store._fall_back()

    assert not store.get("laptop").rules
    assert not store.blocks("laptop", "ads.example")


@respx.mock
async def test_rewrites_from_the_cache_answer_reverse_lookups(monkeypatch, tmp_path) -> None:
    cache = tmp_path / "devices.json"
    cache.write_text(
        json.dumps(
            {
                "written_at": 1,
                "devices": [{"id": "moto", "name": "Moto"}],
                "rewrites": {"nas.home": "192.168.1.20"},
            }
        )
    )
    configure(monkeypatch, cache=str(cache))
    respx.post(f"{RPC}/hydra_devices_list").mock(side_effect=httpx.ConnectError("down"))

    store = make_store()
    await store.bootstrap()

    assert store.rewrites == {"nas.home": "192.168.1.20"}
    assert store.ptr_for("20.1.168.192.in-addr.arpa") == ("nas.home",)


@respx.mock
async def test_add_derives_an_id_and_refreshes(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_device_add").mock(
        return_value=httpx.Response(200, json=rows(("work-laptop", "Work Laptop")))
    )
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(
            200, json=rows(("laptop", "Laptop"), ("work-laptop", "Work Laptop"))
        )
    )
    store = make_store()
    device = await store.add("Work Laptop")

    assert device.id == "work-laptop"
    assert device.token == device_token("work-laptop")
    assert "work-laptop" in {d.id for d in store.devices}


@respx.mock
async def test_add_rejects_a_duplicate_explicit_id(monkeypatch) -> None:
    configure(monkeypatch)
    store = make_store()
    with pytest.raises(StoreError, match="already in use"):
        await store.add("Another Laptop", device_id="laptop")


@respx.mock
async def test_add_rejects_an_id_that_cannot_live_in_a_url(monkeypatch) -> None:
    configure(monkeypatch)
    store = make_store()
    with pytest.raises(StoreError, match="letters, digits"):
        await store.add("Bad", device_id="has/slash")


async def test_add_without_a_name_is_refused() -> None:
    store = make_store()
    with pytest.raises(StoreError, match="needs a name"):
        await store.add("   ")


@respx.mock
async def test_remove_revokes_the_token(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_device_remove").mock(return_value=httpx.Response(200, json=True))
    respx.post(f"{RPC}/hydra_devices_list").mock(
        return_value=httpx.Response(200, json=rows(("phone", "Phone")))
    )
    store = make_store()
    token = device_token("laptop")
    assert store.by_token(token) is not None

    await store.remove("laptop")
    assert store.by_token(token) is None


def test_unconfigured_store_serves_the_seed_and_refuses_writes(monkeypatch) -> None:
    configure(monkeypatch, url="")
    store = make_store()

    assert not store.configured
    assert not store.writable
    assert [d.id for d in store.devices] == ["laptop", "phone"]


async def test_unconfigured_store_sync_is_a_no_op(monkeypatch) -> None:
    configure(monkeypatch, url="")
    store = make_store()
    with respx.mock:
        await store.sync()
    assert len(store.devices) == 2


def test_seed_devices_are_marked_as_such() -> None:
    assert all(d.source == "env" for d in make_store().devices)
    assert Device(id="x", name="X").source == "store"
