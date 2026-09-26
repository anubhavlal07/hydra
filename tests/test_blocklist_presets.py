from __future__ import annotations

import dataclasses

import respx
from test_blocklist_api import signed_in
from test_blocklist_resolver import with_list
from test_doh import client, mock_upstream

from hydra_dns.blocklist_presets import PRESETS, catalog
from hydra_dns.blocklists import ID_PATTERN, MAX_NAME, MAX_URL, validate
from hydra_dns.config import settings


def by_id(entries: list[dict]) -> dict[str, dict]:
    return {entry["id"]: entry for entry in entries}


def test_the_catalog_carries_the_well_known_lists() -> None:
    names = {preset.name for preset in PRESETS}

    assert {
        "HaGeZi Light",
        "HaGeZi Multi Normal",
        "HaGeZi Pro",
        "HaGeZi Pro++",
        "HaGeZi TIF",
        "OISD small",
        "OISD big",
    } <= names


def test_every_preset_would_pass_the_list_validation() -> None:
    assert len({p.id for p in PRESETS}) == len(PRESETS)
    assert len({p.url for p in PRESETS}) == len(PRESETS)
    for preset in PRESETS:
        assert ID_PATTERN.match(preset.id)
        assert len(preset.name) <= MAX_NAME
        assert preset.url.startswith("https://") and len(preset.url) <= MAX_URL
        assert preset.format == "domains"
        assert preset.names > 0 and preset.bytes > 0
        validate(preset.id, preset.name, preset.url, [], [])


def test_a_preset_fits_only_inside_the_headroom() -> None:
    found = by_id(
        catalog(
            held_urls=set(),
            total_names=200_000,
            max_names=400_000,
            max_bytes=20 * 1024 * 1024,
            full=False,
        )
    )

    assert found["oisd-small"]["fits"] is True
    assert found["oisd-small"]["unavailable"] == ""
    assert found["hagezi-pro"]["fits"] is False
    assert found["hagezi-pro"]["unavailable"] == "only 200,000 names left"


def test_a_preset_too_large_to_download_says_so() -> None:
    found = by_id(
        catalog(
            held_urls=set(),
            total_names=0,
            max_names=10_000_000,
            max_bytes=20 * 1024 * 1024,
            full=False,
        )
    )

    assert found["hagezi-tif"]["fits"] is False
    assert "HYDRA_BLOCKLIST_MAX_BYTES" in found["hagezi-tif"]["unavailable"]


def test_a_subscribed_preset_is_marked_by_its_url() -> None:
    pro = next(p for p in PRESETS if p.id == "hagezi-pro")
    found = by_id(
        catalog(
            held_urls={pro.url},
            total_names=0,
            max_names=400_000,
            max_bytes=20 * 1024 * 1024,
            full=False,
        )
    )

    assert found["hagezi-pro"]["subscribed"] is True
    assert found["hagezi-pro"]["unavailable"] == "already added"
    assert found["hagezi-multi"]["subscribed"] is False


def test_nothing_is_offered_once_every_slot_is_taken() -> None:
    found = catalog(
        held_urls=set(), total_names=0, max_names=400_000, max_bytes=20 * 1024 * 1024, full=True
    )

    assert all(entry["unavailable"] for entry in found)


@respx.mock
async def test_the_route_reads_the_headroom_and_the_subscriptions() -> None:
    from hydra_dns.main import app

    pro = next(p for p in PRESETS if p.id == "hagezi-pro")
    mock_upstream()
    async with client() as http:
        refused = await http.get("/api/blocklists/presets")
        await signed_in(http)
        lists = with_list(app.state.devices, lists=app.state.blocklists)
        state = lists.get("ads")
        state.adopt(dataclasses.replace(state.meta, url=pro.url))
        body = (await http.get("/api/blocklists/presets")).json()

    assert refused.status_code == 401
    assert body["headroom"] == settings.blocklist_max_names - 2
    assert body["max_bytes"] == settings.blocklist_max_bytes
    found = by_id(body["presets"])
    assert set(found) == {p.id for p in PRESETS}
    assert found["hagezi-pro"]["subscribed"] is True
    assert found["hagezi-light"]["subscribed"] is False
    assert found["hagezi-light"]["fits"] is True
    assert set(found["hagezi-light"]) == {
        "id",
        "name",
        "url",
        "format",
        "names",
        "bytes",
        "subscribed",
        "fits",
        "unavailable",
    }
