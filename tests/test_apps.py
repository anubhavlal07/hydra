from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import time

import dns.message
import dns.rcode
import httpx
import pytest
import respx
from test_blocklist_resolver import CountingPool, ask, store_with
from test_blocklists import FakeStore
from test_doh import client, mock_upstream

from hydra_dns import apps as appmod
from hydra_dns import blocklists, schedule
from hydra_dns.apps import AppBlock, Apps, parse_catalog, read_rules
from hydra_dns.config import settings
from hydra_dns.decision import APP
from hydra_dns.resolver import savings
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.prefetch import Prefetcher
from hydra_dns.resolver.service import Resolver
from hydra_dns.store import Device, StoreError

PASSWORD = "hunter2"
ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><path d="M0 0h1v1H0z"/></svg>'


def catalog_json(extra: list[dict] | None = None) -> str:
    services = [
        {
            "id": "youtube",
            "name": "YouTube",
            "group": "streaming",
            "icon_svg": ICON,
            "rules": ["||youtube.com^", "||googlevideo.com^", "||ytimg.com^"],
        },
        {
            "id": "qq",
            "name": "QQ",
            "group": "messenger",
            "icon_svg": "<script>alert(1)</script>",
            "rules": [
                "||qq.com^$denyallow=wx.qq.com|weixin.qq.com",
                "|exact.qq.example^",
                "||qqwild-*.example^",
                "/qq[0-9]+/",
                "||cdn.qq.example^$dnstype=~CNAME",
            ],
        },
        {
            "id": "tiktok",
            "name": "TikTok",
            "group": "social_network",
            "icon_svg": ICON,
            "rules": ["||tiktok.com^", "||tiktokcdn.com^$dnsrewrite=NXDOMAIN;;"],
        },
        *(
            {
                "id": f"filler{i}",
                "name": f"Filler {i}",
                "group": "gaming",
                "rules": [f"||f{i}.test^"],
            }
            for i in range(20)
        ),
        *(extra or []),
    ]
    groups = [{"id": g} for g in ("streaming", "messenger", "social_network", "gaming")]
    return json.dumps({"blocked_services": services, "groups": groups})


def catalog():
    return parse_catalog(catalog_json(), "bundled")


def engine(store, **blocks: AppBlock) -> Apps:
    found = Apps(None, store, catalog=catalog())
    found._blocks = dict(blocks)
    found._forget_reach()
    return found


def build(store, apps: Apps):
    pool = CountingPool()
    metrics = Metrics()
    cache = AnswerCache(max_entries=100, min_ttl=1, max_ttl=3600)
    resolver = Resolver(cache, pool, metrics, store, apps=apps)
    return resolver, pool, metrics


@pytest.fixture
def tuned(monkeypatch, tmp_path):
    def apply(**changes):
        changes.setdefault("app_dir", str(tmp_path))
        patched = dataclasses.replace(settings, **changes)
        monkeypatch.setattr(appmod, "settings", patched)
        return patched

    apply()
    return apply


def test_rules_cover_subtrees_exact_names_and_denyallow_and_skip_the_rest() -> None:
    found = read_rules(
        [
            "||youtube.com^",
            "||espn.in",
            "|exact.example^",
            "||qq.com^$denyallow=wx.qq.com|weixin.qq.com",
            "||tiktokcdn.com^$dnsrewrite=NXDOMAIN;;",
            "||amazonaws.com^$dnstype=~CNAME",
            "||awsdns-*.com^",
            "/_spotify-connect._tcp.local/",
            "",
        ]
    )

    assert found.names == {"youtube.com", "espn.in", "qq.com", "tiktokcdn.com"}
    assert found.exact == {"exact.example"}
    assert found.allow == {"wx.qq.com", "weixin.qq.com"}
    assert found.skipped == 4


def test_an_app_covers_its_subtree_and_honours_its_exceptions() -> None:
    qq = catalog().apps["qq"]

    assert qq.covers("mail.qq.com")
    assert qq.covers("qq.com")
    assert not qq.covers("wx.qq.com")
    assert not qq.covers("a.weixin.qq.com")
    assert qq.covers("exact.qq.example")
    assert not qq.covers("sub.exact.qq.example")
    assert qq.icon == ""


def test_the_catalog_is_sorted_by_name_and_keeps_the_listed_group_order() -> None:
    found = catalog()

    assert list(found.apps)[:3] == ["filler0", "filler1", "filler10"]
    assert found.groups == ("streaming", "messenger", "social_network", "gaming")
    assert found.owners("rr1.googlevideo.com") == ("youtube",)
    assert found.owners("example.org") == ()


def test_a_catalog_too_small_or_not_json_is_refused() -> None:
    with pytest.raises(ValueError, match="fewer than"):
        parse_catalog(json.dumps({"blocked_services": [{"id": "a", "rules": ["||a.test^"]}]}), "x")
    with pytest.raises(ValueError, match="not JSON"):
        parse_catalog("<html>", "x")
    with pytest.raises(ValueError, match="blocked_services"):
        parse_catalog("[]", "x")


def test_the_bundled_catalog_parses_and_covers_the_obvious_names() -> None:
    found = appmod.bundled()

    assert found.source == "bundled"
    assert len(found.apps) >= 100
    assert found.apps["youtube"].covers("rr3---sn-abc.googlevideo.com")
    assert found.apps["instagram"].covers("scontent.cdninstagram.com")
    assert all(app.icon.startswith("<svg") for app in found.apps.values() if app.icon)


def test_the_lookup_costs_labels_not_catalog_size() -> None:
    found = appmod.bundled()
    started = time.perf_counter()
    for _ in range(10_000):
        found.owners("a.b.c.d.e.f.example.com")
    per_lookup_us = (time.perf_counter() - started) / 10_000 * 1e6

    assert per_lookup_us < 50


async def test_a_blocked_app_is_answered_in_process_only_on_the_tagged_device() -> None:
    store = store_with(Device(id="tv", name="TV", tags=("kids",)), Device(id="laptop", name="L"))
    apps = engine(store, youtube=AppBlock(id="youtube", tags=("kids",)))
    resolver, pool, metrics = build(store, apps)

    blocked = await resolver.resolve(ask("rr1.googlevideo.com"), "tv")
    passed = await resolver.resolve(ask("rr1.googlevideo.com"), "laptop")

    assert blocked.blocked is True
    assert blocked.upstream == "app YouTube → 0.0.0.0"
    assert passed.blocked is False
    assert pool.forwarded == 1
    assert apps.hits_of("youtube") == 1
    assert metrics.savings.by_cause()[savings.APP] == 1


async def test_an_app_answer_honours_the_devices_block_mode() -> None:
    store = store_with(Device(id="tv", name="TV", block_mode="nxdomain"))
    resolver, pool, _ = build(store, engine(store, tiktok=AppBlock(id="tiktok")))

    result = await resolver.resolve(ask("www.tiktok.com"), "tv")

    assert dns.message.from_wire(result.wire).rcode() == dns.rcode.NXDOMAIN
    assert result.upstream == "app TikTok → nxdomain"
    assert pool.forwarded == 0


async def test_a_device_exception_beats_an_app_block() -> None:
    store = store_with(Device(id="tv", name="TV", rules=("@@||music.youtube.com^",)))
    resolver, pool, _ = build(store, engine(store, youtube=AppBlock(id="youtube")))

    excepted = await resolver.resolve(ask("music.youtube.com"), "tv")
    blocked = await resolver.resolve(ask("www.youtube.com"), "tv")

    assert excepted.blocked is False
    assert blocked.blocked is True
    assert pool.forwarded == 1


async def test_a_device_rule_labels_the_answer_before_an_app_does() -> None:
    store = store_with(Device(id="tv", name="TV", rules=("||youtube.com^",)))
    resolver, _, _ = build(store, engine(store, youtube=AppBlock(id="youtube")))

    result = await resolver.resolve(ask("youtube.com"), "tv")

    assert result.upstream.startswith("device rule")


async def test_a_disabled_block_and_a_paused_device_both_pass_through() -> None:
    store = store_with(Device(id="tv", name="TV"), Device(id="pc", name="PC"))
    apps = engine(
        store, youtube=AppBlock(id="youtube", enabled=False), tiktok=AppBlock(id="tiktok")
    )
    resolver, pool, _ = build(store, apps)
    store.pauses.hold("pc", 60)

    off = await resolver.resolve(ask("youtube.com"), "tv")
    paused = await resolver.resolve(ask("tiktok.com"), "pc")

    assert off.blocked is False
    assert paused.blocked is False
    assert paused.upstream.startswith("paused")
    assert pool.forwarded == 1


async def test_a_scheduled_block_applies_only_inside_its_window(monkeypatch) -> None:
    store = store_with(Device(id="tv", name="TV"))
    apps = engine(store, youtube=AppBlock(id="youtube", starts_at="21:00", ends_at="07:00"))
    resolver, _, _ = build(store, apps)

    monkeypatch.setattr(schedule, "now", lambda: dt.datetime(2026, 10, 1, 22, 30))
    inside = await resolver.resolve(ask("youtube.com"), "tv")

    monkeypatch.setattr(schedule, "now", lambda: dt.datetime(2026, 10, 2, 12, 0))
    apps._forget_reach()
    outside = await resolver.resolve(ask("m.youtube.com"), "tv")

    assert inside.blocked is True
    assert outside.blocked is False


async def test_a_block_for_an_app_the_catalog_no_longer_holds_is_inert() -> None:
    store = store_with(Device(id="tv", name="TV"))
    apps = engine(store, gone=AppBlock(id="gone"))
    resolver, _, _ = build(store, apps)

    result = await resolver.resolve(ask("youtube.com"), "tv")

    assert result.blocked is False
    assert apps.as_dict(apps.block("gone"))["known"] is False


async def test_an_exception_inside_the_check_answers_as_unblocked() -> None:
    store = store_with(Device(id="tv", name="TV"))
    apps = engine(store, youtube=AppBlock(id="youtube"))

    def broken(device_id, name):
        raise RuntimeError("boom")

    apps.match = broken
    resolver, pool, _ = build(store, apps)

    result = await resolver.resolve(ask("youtube.com"), "tv")

    assert result.blocked is False
    assert pool.forwarded == 1


async def test_every_answer_notes_which_app_a_device_was_using() -> None:
    store = store_with(Device(id="tv", name="TV"), Device(id="pc", name="PC"))
    apps = engine(store, tiktok=AppBlock(id="tiktok"))
    resolver, _, _ = build(store, apps)

    await resolver.resolve(ask("i.ytimg.com"), "tv")
    await resolver.resolve(ask("youtube.com"), "pc")
    await resolver.resolve(ask("tiktok.com"), "tv")
    await resolver.resolve(ask("wx.qq.com"), "tv")

    seen = {row["id"]: row["devices"] for row in apps.seen()}
    assert seen == {"youtube": 2, "tiktok": 1}
    [tiktok] = [b for b in apps.blocks if b.id == "tiktok"]
    assert [s["name"] for s in apps.as_dict(tiktok)["seen"]] == ["TV"]


def test_the_seen_devices_per_app_are_capped() -> None:
    apps = engine(store_with())
    for index in range(appmod.MAX_SEEN_DEVICES + 10):
        apps.note(f"d{index}", "youtube.com", float(index))

    assert len(apps._seen["youtube"]) == appmod.MAX_SEEN_DEVICES
    assert "d0" not in apps._seen["youtube"]


def test_prefetch_skips_only_names_blocked_everywhere_all_the_time() -> None:
    store = store_with(Device(id="tv", name="TV"), Device(id="pc", name="PC", rules=("@@qq.com",)))
    apps = engine(
        store,
        youtube=AppBlock(id="youtube"),
        tiktok=AppBlock(id="tiktok", tags=("kids",)),
        qq=AppBlock(id="qq"),
        filler1=AppBlock(id="filler1", starts_at="21:00", ends_at="07:00"),
    )
    prefetcher = Prefetcher(AnswerCache(10, 1, 60), CountingPool(), Metrics(), apps=apps)

    assert prefetcher._listed_everywhere("youtube.com") is True
    assert prefetcher._listed_everywhere("tiktok.com") is False
    assert prefetcher._listed_everywhere("qq.com") is False
    assert prefetcher._listed_everywhere("f1.test") is False
    assert prefetcher.skipped_listed == 1


def test_the_domain_sheet_names_every_app_that_covers_a_name() -> None:
    apps = engine(store_with(), youtube=AppBlock(id="youtube", tags=("kids",)))

    assert apps.naming("i.ytimg.com") == [
        {
            "id": "youtube",
            "name": "YouTube",
            "group": "streaming",
            "blocked": True,
            "enabled": True,
            "tags": ["kids"],
            "when": "always",
        }
    ]
    assert apps.naming("wx.qq.com") == []


class AppStore(FakeStore):
    def __init__(self, devices=None, app_rows=None):
        super().__init__(devices)
        self.app_rows: list[dict] = list(app_rows or [])

    async def app_block_rows(self) -> object:
        if self.down:
            raise StoreError("could not reach device storage")
        return [dict(r) for r in self.app_rows]

    async def write_app_block(self, row: dict) -> object:
        self.writes.append(dict(row))
        self.app_rows = [r for r in self.app_rows if r["id"] != row["id"]] + [dict(row)]
        return [row]

    async def delete_app_block(self, app_id: str) -> object:
        self.app_rows = [r for r in self.app_rows if r["id"] != app_id]
        return [{"id": app_id}]


async def test_a_failed_registry_read_keeps_the_blocks_and_an_empty_one_is_authoritative(
    tuned,
) -> None:
    store = AppStore(app_rows=[{"id": "youtube", "tags": ["kids"]}])
    apps = Apps(None, store, catalog=catalog())

    assert await apps.sync() is True
    assert [b.id for b in apps.blocks] == ["youtube"]

    store.down = True
    assert await apps.sync() is False
    assert [b.id for b in apps.blocks] == ["youtube"]
    assert "could not reach" in apps.last_error

    store.down = False
    store.app_rows = []
    assert await apps.sync() is True
    assert apps.blocks == []


async def test_blocks_survive_a_restart_through_the_disk_mirror(tuned, tmp_path) -> None:
    store = AppStore(app_rows=[{"id": "tiktok", "days": [5, 6], "enabled": True}])
    first = Apps(None, store, catalog=catalog())
    await first.sync()

    second = Apps(None, AppStore(), catalog=catalog())
    assert await second.load() == 1
    [block] = second.blocks
    assert block.id == "tiktok"
    assert block.days == (5, 6)
    assert (tmp_path / "blocks.json").is_file()


async def test_a_write_validates_the_window_and_the_app(tuned) -> None:
    store = AppStore()
    apps = Apps(None, store, catalog=catalog())

    with pytest.raises(appmod.UnknownApp):
        await apps.put("nope", tags=[], days=[], starts_at="", ends_at="", enabled=True)
    with pytest.raises(StoreError, match="both a start and an end"):
        await apps.put("youtube", tags=[], days=[], starts_at="21:00", ends_at="", enabled=True)
    with pytest.raises(StoreError, match="24-hour"):
        await apps.put("youtube", tags=[], days=[], starts_at="9pm", ends_at="7am", enabled=True)

    block = await apps.put(
        "YouTube", tags=["Kids"], days=[6, 5, 9], starts_at="21:00", ends_at="07:00", enabled=True
    )
    assert block.id == "youtube"
    assert block.tags == ("kids",)
    assert block.days == (5, 6)
    assert apps.when(block) == "21:00–07:00, Sat Sun"
    assert store.writes[-1]["days"] == [5, 6]

    assert await apps.remove("youtube") is True
    with pytest.raises(appmod.UnknownApp):
        await apps.remove("youtube")


async def test_a_downloaded_catalog_replaces_the_bundled_one_and_a_bad_one_does_not(
    tuned, monkeypatch, tmp_path
) -> None:
    tuned(app_catalog_url="https://catalog.test/services.json")
    extra = [{"id": "newapp", "name": "New App", "group": "ai", "rules": ["||new.test^"]}]
    bodies = [catalog_json(extra).encode(), b"<html>not json</html>", None]
    seen_etags: list[str] = []

    async def fake_download(shared, url, *, etag, last_modified, cap):
        seen_etags.append(etag)
        body = bodies.pop(0)
        return None if body is None else (body, '"v1"', "")

    monkeypatch.setattr(blocklists, "download", fake_download)
    apps = Apps(httpx.AsyncClient(), AppStore(), catalog=catalog())

    assert await apps.refresh_catalog() is True
    assert "newapp" in apps.catalog.apps
    assert apps.catalog.source == "downloaded"
    assert (tmp_path / "catalog.json").is_file()

    assert await apps.refresh_catalog() is False
    assert "not JSON" in apps.catalog_error
    assert "newapp" in apps.catalog.apps

    assert await apps.refresh_catalog() is False
    assert apps.catalog_error == ""
    assert seen_etags == ["", '"v1"', '"v1"']

    reloaded = Apps(None, AppStore(), catalog=catalog())
    await reloaded.load()
    assert "newapp" in reloaded.catalog.apps


async def test_no_catalog_url_means_no_download(tuned, monkeypatch) -> None:
    tuned(app_catalog_url="")

    async def refuse(*args, **kwargs):
        raise AssertionError("must not download")

    monkeypatch.setattr(blocklists, "download", refuse)
    apps = Apps(httpx.AsyncClient(), AppStore(), catalog=catalog())

    assert await apps.refresh_catalog() is False


@pytest.fixture
def writable(monkeypatch):
    from hydra_dns.api import apps as routes

    monkeypatch.setattr(routes, "_guard_writable", lambda store: None)

    def attach(app, fake: AppStore) -> AppStore:
        app.state.apps._store = fake
        return fake

    return attach


async def signed_in(http) -> None:
    assert (await http.post("/api/login", json={"password": PASSWORD})).status_code == 200


@respx.mock
async def test_the_app_endpoints_have_the_contract_shape_and_refuse_writes_without_a_registry():
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        state = (await http.get("/api/apps")).json()
        catalog_response = await http.get("/api/apps/catalog")
        again = await http.get(
            "/api/apps/catalog", headers={"if-none-match": catalog_response.headers["etag"]}
        )
        refused = await http.put("/api/apps/youtube", json={})

    assert set(state) == {"blocks", "seen", "editable", "tags", "timezone", "catalog"}
    assert state["editable"] is False
    assert state["catalog"]["apps"] >= 100
    body = catalog_response.json()
    assert set(body) == {"fingerprint", "source", "fetched_at", "groups", "apps"}
    assert set(body["apps"][0]) == {"id", "name", "group", "icon", "domains"}
    assert again.status_code == 304
    assert refused.status_code == 503


@respx.mock
async def test_an_app_can_be_blocked_edited_and_unblocked(writable) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        writable(app, AppStore([Device(id="tv", name="TV", tags=("kids",))]))

        created = await http.put("/api/apps/youtube", json={"tags": ["kids"]})
        edited = await http.put(
            "/api/apps/youtube",
            json={"tags": [], "days": [0, 1], "starts_at": "21:00", "ends_at": "07:00"},
        )
        unknown = await http.put("/api/apps/not-an-app", json={})
        bad = await http.put("/api/apps/youtube", json={"starts_at": "21:00"})
        listed = (await http.get("/api/apps")).json()
        removed = await http.delete("/api/apps/youtube")
        missing = await http.delete("/api/apps/youtube")

    assert created.status_code == 200
    assert created.json()["reaches"] == ["tv"]
    assert created.json()["when"] == "always"
    assert edited.json()["when"] == "21:00–07:00, Mon Tue"
    assert unknown.status_code == 404
    assert bad.status_code == 400
    [block] = listed["blocks"]
    assert block["name"] == "YouTube"
    assert set(block) >= {"id", "tags", "days", "known", "active_now", "hits", "reaches", "seen"}
    assert removed.json() == {"removed": True, "id": "youtube"}
    assert missing.status_code == 404


@respx.mock
async def test_an_app_block_answers_over_doh_with_a_latin_1_label() -> None:
    from test_doh_headers import _over_doh

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        store = app.state.devices
        store._devices = [Device(id="laptop", name="Work Laptop")]
        store._compiled = {}
        apps = app.state.apps
        apps._blocks = {"youtube": AppBlock(id="youtube")}
        apps._forget_reach()
        response = await _over_doh(http, "www.youtube.com")
        apps._blocks = {}
        apps._forget_reach()

    assert response.status_code == 200
    assert response.headers["x-hydra-upstream"] == "app YouTube -> 0.0.0.0"
    assert dns.message.from_wire(response.content).answer[0][0].to_text() == "0.0.0.0"
    assert respx.calls.call_count == 0


@respx.mock
async def test_backup_carries_app_blocks_and_restore_writes_them(writable) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        fake = writable(app, AppStore([Device(id="tv", name="TV")]))
        await http.put("/api/apps/tiktok", json={"days": [5, 6]})
        backup = (await http.get("/api/backup", params={"accounts": "false"})).json()
        fake.app_rows = []
        await app.state.apps.sync()
        restored = await http.post(
            "/api/restore", json={"version": 1, "apps": backup["apps"], "devices": []}
        )

    assert backup["apps"] == [
        {
            "id": "tiktok",
            "tags": [],
            "days": [5, 6],
            "starts_at": "",
            "ends_at": "",
            "enabled": True,
        }
    ]
    assert restored.json()["restored"]["apps"] == 1
    assert [r["id"] for r in fake.app_rows] == ["tiktok"]


def test_decision_layer_is_registered() -> None:
    from hydra_dns.decision import LAYERS

    assert APP in LAYERS
    assert LAYERS.index(APP) < LAYERS.index("blocklist")


async def test_the_background_tick_syncs_and_refreshes_the_catalog_once_per_period(
    tuned, monkeypatch
) -> None:
    tuned(app_catalog_url="https://catalog.test/services.json", app_catalog_refresh_s=3600)
    calls: list[str] = []

    async def fake_download(shared, url, *, etag, last_modified, cap):
        calls.append(url)
        return None

    monkeypatch.setattr(blocklists, "download", fake_download)
    apps = Apps(httpx.AsyncClient(), AppStore(app_rows=[{"id": "youtube"}]), catalog=catalog())

    await apps.tick()
    await apps.tick()
    await asyncio.sleep(0)

    assert calls == ["https://catalog.test/services.json"]
    assert [b.id for b in apps.blocks] == ["youtube"]
