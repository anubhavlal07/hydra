from __future__ import annotations

import asyncio
import dataclasses
import json

import httpx
import pytest
import respx

from hydra_dns import blocklists
from hydra_dns import rules as ruleset
from hydra_dns.blocklists import Blocklists, FetchError, guard
from hydra_dns.config import settings
from hydra_dns.store import Device, StoreError

LIST_URL = "https://lists.example.test/ads.txt"
PUBLIC_IP = "93.184.216.34"


class FakeStore:
    def __init__(self, devices: list[Device] | None = None, rows: list[dict] | None = None):
        self.configured = True
        self.writable = True
        self.lock = asyncio.Lock()
        self._devices = list(devices or [])
        self.rows: list[dict] = list(rows or [])
        self.down = False
        self.writes: list[dict] = []
        self.extra_rules: dict[str, tuple[str, ...]] = {}

    @property
    def roster(self) -> list[Device]:
        return self._devices

    def replace_devices(self, devices: list[Device]) -> None:
        self._devices = list(devices)

    def get(self, device_id: str) -> Device | None:
        return next((d for d in self._devices if d.id == device_id), None)

    def rules_for(self, device_id: str) -> ruleset.RuleSet:
        device = self.get(device_id)
        own = device.rules if device else ()
        return ruleset.parse([*own, *self.extra_rules.get(device_id, ())])

    async def blocklist_rows(self) -> object:
        if self.down:
            raise StoreError("could not reach device storage")
        return [dict(r) for r in self.rows]

    async def write_blocklist(self, row: dict) -> object:
        self.writes.append(dict(row))
        self.rows = [r for r in self.rows if r["id"] != row["id"]] + [dict(row)]
        return [row]

    async def delete_blocklist(self, list_id: str) -> object:
        self.rows = [r for r in self.rows if r["id"] != list_id]
        return [{"id": list_id}]


def row(list_id: str = "ads", **extra) -> dict:
    return {"id": list_id, "name": list_id.title(), "url": LIST_URL, "tags": [], **extra}


@pytest.fixture
def tuned(monkeypatch, tmp_path):
    def apply(**changes):
        changes.setdefault("blocklist_dir", str(tmp_path))
        patched = dataclasses.replace(settings, **changes)
        monkeypatch.setattr(blocklists, "settings", patched)
        return patched

    apply()
    return apply


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    monkeypatch.setattr(blocklists, "addresses_of", resolve)


async def loaded(store: FakeStore, body: str = "||ads.example.com^\ntracker.test\n") -> Blocklists:
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=body, headers={"etag": '"1"'}))
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()
    return lists


@respx.mock
async def test_a_list_is_fetched_parsed_and_enforced(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)

    state = lists.get("ads")
    assert state is not None and state.loaded
    assert len(state.names) == 2
    assert state.etag == '"1"'
    assert state.last_error == ""
    assert lists.match("tv", "img.ads.example.com").id == "ads"
    assert lists.match("tv", "example.com") is None


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.ConnectError("refused"),
        httpx.Response(500, text="oops"),
        httpx.Response(404, text="gone"),
        httpx.Response(200, text="! only a comment\n/regex/\n"),
    ],
    ids=["download", "http-500", "http-404", "zero-names"],
)
async def test_a_failed_refresh_keeps_the_previous_set(tuned, response) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)
    before = lists.get("ads").names

    route = respx.get(LIST_URL)
    if isinstance(response, Exception):
        route.mock(side_effect=response)
    else:
        route.mock(return_value=response)
    await lists.refresh("ads")

    state = lists.get("ads")
    assert state.names is before
    assert state.last_error
    assert lists.match("tv", "ads.example.com") is not None


@respx.mock
async def test_an_oversized_body_is_refused_and_the_previous_set_kept(tuned) -> None:
    tuned(blocklist_max_bytes=64)
    store = FakeStore(rows=[row()])
    lists = await loaded(store, "a.test\n")
    before = lists.get("ads").names

    big = "".join(f"host{i}.example.com\n" for i in range(50))
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text=big))
    await lists.refresh("ads")

    assert lists.get("ads").names is before
    assert "bytes" in lists.get("ads").last_error


@respx.mock
async def test_an_oversized_body_without_a_length_is_cut_off_while_streaming(tuned) -> None:
    tuned(blocklist_max_bytes=64)
    store = FakeStore(rows=[row()])

    async def chunks():
        for index in range(100):
            yield f"host{index}.example.com\n".encode()

    respx.get(LIST_URL).mock(return_value=httpx.Response(200, content=chunks()))
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert not lists.get("ads").loaded
    assert "over 64 bytes" in lists.get("ads").last_error


@respx.mock
async def test_an_unchanged_list_costs_a_304(tuned) -> None:
    store = FakeStore(rows=[row()])
    lists = await loaded(store)
    before = lists.get("ads").names
    lists.get("ads").fetched_at = 1.0

    route = respx.get(LIST_URL).mock(return_value=httpx.Response(304))
    await lists.refresh("ads")

    assert route.calls.last.request.headers["if-none-match"] == '"1"'
    assert lists.get("ads").names is before
    assert lists.get("ads").fetched_at > 1.0
    assert lists.get("ads").last_error == ""


@respx.mock
async def test_a_list_that_would_pass_the_name_ceiling_is_refused_not_truncated(tuned) -> None:
    tuned(blocklist_max_names=3)
    store = FakeStore(rows=[row("one"), row("two", url="https://lists.example.test/two.txt")])
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text="a.test\nb.test\n"))
    respx.get("https://lists.example.test/two.txt").mock(
        return_value=httpx.Response(200, text="c.test\nd.test\n")
    )
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert len(lists.get("one").names) == 2
    assert not lists.get("two").loaded
    assert "HYDRA_BLOCKLIST_MAX_NAMES" in lists.get("two").last_error
    assert lists.total_names == 2


async def test_the_list_ceiling_refuses_one_more(tuned) -> None:
    tuned(blocklist_max=1)
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.sync()

    with pytest.raises(StoreError, match="at most 1"):
        await lists.put("more", name="More", url=LIST_URL, tags=[], allow=[], enabled=True)


async def test_a_registry_holding_too_many_lists_only_applies_the_first(tuned) -> None:
    tuned(blocklist_max=2)
    store = FakeStore(rows=[row("a"), row("b"), row("c")])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.sync()

    assert [s.meta.id for s in lists.lists] == ["a", "b"]


@respx.mock
async def test_a_startup_with_the_registry_down_enforces_the_lists_from_disk(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV", tags=("kids",))], rows=[row(tags=["kids"])])
    await loaded(store)

    respx.reset()
    respx.get(LIST_URL).mock(side_effect=httpx.ConnectError("no network"))
    store.down = True
    restarted = Blocklists(httpx.AsyncClient(), store)
    assert await restarted.load() == 1
    await restarted.tick()

    assert restarted.match("tv", "ads.example.com").id == "ads"
    assert restarted.get("ads").meta.tags == ("kids",)
    assert restarted.last_error

    await restarted.refresh("ads")
    assert restarted.get("ads").last_error
    assert restarted.match("tv", "ads.example.com") is not None


@respx.mock
async def test_a_successful_empty_registry_read_removes_every_list(tuned, tmp_path) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)
    assert (tmp_path / "ads.txt").exists()

    store.rows = []
    await lists.sync()

    assert lists.lists == []
    assert lists.match("tv", "ads.example.com") is None
    assert list(tmp_path.iterdir()) == []


async def test_a_failed_registry_read_keeps_the_lists_in_hand(tuned) -> None:
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.sync()
    store.down = True

    await lists.sync()

    assert [s.meta.id for s in lists.lists] == ["ads"]


@respx.mock
async def test_without_a_directory_lists_live_in_memory_only(tuned, tmp_path) -> None:
    tuned(blocklist_dir="")
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)

    assert lists.match("tv", "ads.example.com") is not None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.8",
        "172.16.4.4",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
    ],
)
async def test_the_guard_refuses_a_host_that_resolves_somewhere_private(
    monkeypatch, address
) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP, address]

    monkeypatch.setattr(blocklists, "addresses_of", resolve)

    with pytest.raises(FetchError, match="not a public address"):
        await guard("https://lists.example.test/list.txt")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/list.txt",
        "https://169.254.169.254/opc/v2/instance/",
        "https://[::1]/list.txt",
        "https://10.1.2.3:8443/list.txt",
    ],
)
async def test_the_guard_refuses_a_literal_private_address(url) -> None:
    with pytest.raises(FetchError, match="not a public address"):
        await guard(url)


async def test_the_guard_refuses_anything_but_https() -> None:
    with pytest.raises(FetchError, match="https"):
        await guard("http://lists.example.test/list.txt")


async def test_the_guard_lets_a_public_host_through() -> None:
    await guard("https://lists.example.test/list.txt")


@respx.mock
async def test_a_redirect_to_the_metadata_service_is_refused(tuned) -> None:
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://169.254.169.254/opc/"})
    )
    metadata = respx.get("https://169.254.169.254/opc/").mock(
        return_value=httpx.Response(200, text="secret.test\n")
    )
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert not metadata.called
    assert not lists.get("ads").loaded
    assert "not a public address" in lists.get("ads").last_error


@respx.mock
async def test_a_redirect_to_a_host_resolving_privately_is_refused(tuned, monkeypatch) -> None:
    async def resolve(host: str, port: int) -> list[str]:
        return ["10.0.0.5"] if host == "internal.example.test" else [PUBLIC_IP]

    monkeypatch.setattr(blocklists, "addresses_of", resolve)
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(301, headers={"location": "https://internal.example.test/x"})
    )
    inside = respx.get("https://internal.example.test/x").mock(
        return_value=httpx.Response(200, text="a.test\n")
    )
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert not inside.called
    assert "not a public address" in lists.get("ads").last_error


@respx.mock
async def test_a_redirect_to_plain_http_is_refused(tuned) -> None:
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(302, headers={"location": "http://lists.example.test/a"})
    )
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert "not https" in lists.get("ads").last_error


@respx.mock
async def test_a_redirect_to_another_public_host_is_followed(tuned) -> None:
    respx.get(LIST_URL).mock(
        return_value=httpx.Response(302, headers={"location": "https://cdn.example.test/a"})
    )
    respx.get("https://cdn.example.test/a").mock(return_value=httpx.Response(200, text="a.test\n"))
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert lists.get("ads").names == {"a.test"}


@respx.mock
async def test_a_tagged_list_reaches_only_the_devices_carrying_its_tag(tuned) -> None:
    store = FakeStore(
        [Device(id="tv", name="TV", tags=("kids",)), Device(id="laptop", name="Laptop")],
        rows=[row(tags=["kids"])],
    )
    lists = await loaded(store)

    assert lists.match("tv", "ads.example.com") is not None
    assert lists.match("laptop", "ads.example.com") is None
    assert lists.reaches(lists.get("ads")) == ["tv"]


@respx.mock
async def test_the_reach_cache_follows_a_change_of_tags(tuned) -> None:
    store = FakeStore([Device(id="laptop", name="Laptop")], rows=[row(tags=["kids"])])
    lists = await loaded(store)
    assert lists.match("laptop", "ads.example.com") is None

    store.replace_devices([Device(id="laptop", name="Laptop", tags=("kids",))])

    assert lists.match("laptop", "ads.example.com") is not None


@respx.mock
async def test_a_device_exception_beats_every_list(tuned) -> None:
    store = FakeStore(
        [Device(id="tv", name="TV", rules=("@@ads.example.com",))], rows=[row(), row("two")]
    )
    lists = await loaded(store)

    assert lists.match("tv", "ads.example.com") is None
    assert lists.match("tv", "tracker.test") is not None


@respx.mock
async def test_a_schedule_exception_beats_every_list(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)
    store.extra_rules["tv"] = ("@@tracker.test",)

    assert lists.match("tv", "tracker.test") is None
    assert lists.match("tv", "ads.example.com") is not None


@respx.mock
async def test_the_lists_allow_beats_the_list(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row(allow=["tracker.test"])])
    lists = await loaded(store)

    assert lists.match("tv", "tracker.test") is None
    assert lists.match("tv", "ads.example.com") is not None


@respx.mock
async def test_a_disabled_list_blocks_nothing(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)
    store.rows = [row(enabled=False)]
    await lists.sync()

    assert lists.match("tv", "ads.example.com") is None


@respx.mock
async def test_adding_a_list_fetches_it_at_once(tuned) -> None:
    route = respx.get(LIST_URL).mock(return_value=httpx.Response(200, text="a.test\n"))
    store = FakeStore([Device(id="tv", name="TV")])
    lists = Blocklists(httpx.AsyncClient(), store)

    state = await lists.put("ads", name="Ads", url=LIST_URL, tags=[], allow=[], enabled=True)
    await asyncio.gather(*lists._kicked)

    assert route.called
    assert state.loaded
    assert lists.match("tv", "a.test") is not None


@respx.mock
async def test_a_new_url_drops_the_old_urls_error_but_keeps_the_set(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)
    before = lists.get("ads").names
    respx.get(LIST_URL).mock(return_value=httpx.Response(404, text="gone"))
    await lists.refresh("ads")
    assert lists.get("ads").last_error

    moved = "https://lists.example.test/moved.txt"
    route = respx.get(moved).mock(return_value=httpx.Response(200, text="b.test\n"))
    state = await lists.put("ads", name="Ads", url=moved, tags=[], allow=[], enabled=True)

    assert state.last_error == ""
    assert state.names is before
    assert lists.match("tv", "ads.example.com") is not None
    await asyncio.gather(*lists._kicked)
    assert route.called
    assert lists.match("tv", "b.test") is not None


@respx.mock
async def test_an_allow_edit_is_read_modify_write_and_keeps_the_rest(tuned) -> None:
    store = FakeStore(rows=[row(tags=["kids"], allow=["one.test"])])
    lists = await loaded(store)

    await asyncio.gather(
        lists.set_allow("ads", "two.test", "add"),
        lists.set_allow("ads", "three.test", "add"),
    )
    state = await lists.set_allow("ads", "one.test", "remove")

    assert state.meta.allow == ("two.test", "three.test")
    assert state.meta.tags == ("kids",)
    assert store.rows[0]["url"] == LIST_URL


async def test_an_allow_on_an_unknown_list_says_so(tuned) -> None:
    lists = Blocklists(httpx.AsyncClient(), FakeStore())

    with pytest.raises(blocklists.UnknownList):
        await lists.set_allow("nope", "a.test", "add")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"list_id": "Bad Id"}, "id"),
        ({"name": ""}, "name"),
        ({"url": "http://lists.example.test/a"}, "https://"),
        ({"allow": ["not a domain"]}, "not a domain"),
        ({"tags": [f"t{i}" for i in range(11)]}, "tags"),
    ],
)
async def test_a_bad_list_is_refused_before_it_is_written(tuned, changes, message) -> None:
    store = FakeStore()
    lists = Blocklists(httpx.AsyncClient(), store)
    wanted = {"list_id": "ads", "name": "Ads", "url": LIST_URL, "tags": [], "allow": []}
    wanted.update(changes)
    list_id = wanted.pop("list_id")

    with pytest.raises(StoreError, match=message):
        await lists.put(list_id, enabled=True, **wanted)
    assert store.writes == []


@respx.mock
async def test_removing_a_list_drops_its_set_and_its_files(tuned, tmp_path) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    lists = await loaded(store)

    assert await lists.remove("ads") is True
    assert lists.match("tv", "ads.example.com") is None
    assert list(tmp_path.iterdir()) == []


@respx.mock
async def test_the_record_on_disk_carries_the_status(tuned, tmp_path) -> None:
    store = FakeStore(rows=[row()])
    await loaded(store)

    record = json.loads((tmp_path / "ads.json").read_text())
    assert record["names"] == 2
    assert record["etag"] == '"1"'
    assert record["fetched_at"] > 0


@respx.mock
async def test_the_loop_retries_a_failing_list_sooner_than_a_day(tuned) -> None:
    respx.get(LIST_URL).mock(return_value=httpx.Response(503))
    store = FakeStore(rows=[row()])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()

    assert lists._next_wait() == blocklists.RETRY_S


@respx.mock
async def test_env_only_mode_still_enforces_what_the_disk_holds(tuned) -> None:
    store = FakeStore([Device(id="tv", name="TV")], rows=[row()])
    await loaded(store)

    offline = FakeStore([Device(id="tv", name="TV")])
    offline.configured = False
    offline.writable = False
    lists = Blocklists(httpx.AsyncClient(), offline)
    await lists.load()
    await lists.sync()

    assert not lists.editable
    assert lists.match("tv", "ads.example.com") is not None
