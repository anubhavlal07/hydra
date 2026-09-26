from __future__ import annotations

import dataclasses
import threading

import httpx
import pytest
import respx
from test_blocklists import PUBLIC_IP, FakeStore, row

from hydra_dns import blocklist_overlap, blocklists
from hydra_dns.blocklist_overlap import measure, shared, unique
from hydra_dns.blocklists import Blocklist, Blocklists, ListState, Parsed

ONE = "https://lists.example.test/one.txt"
TWO = "https://lists.example.test/two.txt"
THREE = "https://lists.example.test/three.txt"


@pytest.fixture
def tuned(monkeypatch, tmp_path):
    def apply(**changes):
        changes.setdefault("blocklist_dir", str(tmp_path))
        patched = dataclasses.replace(blocklists.settings, **changes)
        monkeypatch.setattr(blocklists, "settings", patched)
        return patched

    apply()
    return apply


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    monkeypatch.setattr(blocklists, "addresses_of", resolve)


def names(*values: str) -> frozenset[str]:
    return frozenset(values)


def test_shared_counts_the_names_both_lists_hold() -> None:
    assert shared(names("a.test", "b.test", "c.test"), names("b.test", "c.test", "d.test")) == 2
    assert shared(names("a.test"), names()) == 0


def test_unique_counts_what_no_other_list_holds() -> None:
    held = names("a.test", "b.test", "c.test", "d.test")

    assert unique(held, [names("a.test"), names("b.test", "x.test")]) == 2
    assert unique(held, []) == 4


def test_measure_pairs_every_list_once_and_leaves_disabled_ones_out_of_unique() -> None:
    held = {
        "pro": names("a.test", "b.test", "c.test", "d.test"),
        "light": names("a.test", "b.test"),
        "off": names("c.test", "d.test"),
    }

    found = measure(held, enabled=frozenset({"pro", "light"}))

    assert found["pro"]["overlap"] == [
        {"id": "light", "shared": 2},
        {"id": "off", "shared": 2},
    ]
    assert found["light"]["overlap"] == [{"id": "pro", "shared": 2}, {"id": "off", "shared": 0}]
    assert found["pro"]["unique"] == 2
    assert found["light"]["unique"] == 0
    assert found["off"]["unique"] == 0


def test_eight_lists_make_twenty_eight_pairs() -> None:
    held = {f"l{i}": names(f"{i}.test", "shared.test") for i in range(8)}

    found = measure(held, enabled=frozenset(held))

    assert sum(len(entry["overlap"]) for entry in found.values()) == 2 * 28
    assert all(entry["unique"] == 1 for entry in found.values())


def serve(url: str, body: str) -> None:
    respx.get(url).mock(return_value=httpx.Response(200, text=body))


async def settled(lists: Blocklists) -> None:
    while lists._overlap_task is not None and not lists._overlap_task.done():
        await lists._overlap_task


@respx.mock
async def test_the_overlap_is_measured_off_the_loop_once_the_lists_load(tuned, monkeypatch):
    threads: list[int] = []
    real = blocklist_overlap.measure

    def spy(held, enabled):
        threads.append(threading.get_ident())
        return real(held, enabled)

    monkeypatch.setattr(blocklist_overlap, "measure", spy)
    serve(ONE, "a.test\nb.test\nc.test\nd.test\n")
    serve(TWO, "a.test\nb.test\nc.test\n")
    store = FakeStore(rows=[row("pro", url=ONE), row("light", url=TWO)])
    lists = Blocklists(httpx.AsyncClient(), store)

    await lists.tick()
    await settled(lists)

    assert threads and threading.get_ident() not in threads
    pro = lists.as_dict(lists.get("pro"))
    light = lists.as_dict(lists.get("light"))
    assert pro["overlap"] == [{"id": "light", "shared": 3}]
    assert pro["unique"] == 1
    assert light["overlap"] == [{"id": "pro", "shared": 3}]
    assert light["unique"] == 0
    await lists.aclose()


@respx.mock
async def test_a_refreshed_list_is_measured_again(tuned) -> None:
    serve(ONE, "a.test\nb.test\n")
    serve(TWO, "a.test\n")
    store = FakeStore(rows=[row("pro", url=ONE), row("light", url=TWO)])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()
    await settled(lists)
    assert lists.as_dict(lists.get("light"))["overlap"] == [{"id": "pro", "shared": 1}]

    serve(TWO, "a.test\nb.test\nz.test\n")
    await lists.refresh("light")
    await settled(lists)

    light = lists.as_dict(lists.get("light"))
    assert light["overlap"] == [{"id": "pro", "shared": 2}]
    assert light["unique"] == 1
    await lists.aclose()


@respx.mock
async def test_switching_a_list_off_changes_what_counts_as_unique(tuned) -> None:
    serve(ONE, "a.test\nb.test\n")
    serve(TWO, "a.test\n")
    store = FakeStore(rows=[row("pro", url=ONE), row("light", url=TWO)])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()
    await settled(lists)
    assert lists.as_dict(lists.get("light"))["unique"] == 0

    store.rows = [row("pro", url=ONE, enabled=False), row("light", url=TWO)]
    await lists.sync()
    await settled(lists)

    assert lists.as_dict(lists.get("light"))["unique"] == 1
    assert lists.as_dict(lists.get("pro"))["overlap"] == [{"id": "light", "shared": 1}]
    await lists.aclose()


@respx.mock
async def test_a_removed_list_leaves_the_others_overlap(tuned) -> None:
    serve(ONE, "a.test\nb.test\n")
    serve(TWO, "a.test\n")
    serve(THREE, "b.test\n")
    store = FakeStore(rows=[row("pro", url=ONE), row("light", url=TWO), row("mini", url=THREE)])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()
    await settled(lists)

    store.rows = [row("pro", url=ONE), row("light", url=TWO)]
    await lists.sync()

    assert [e["id"] for e in lists.as_dict(lists.get("pro"))["overlap"]] == ["light"]
    await settled(lists)
    assert lists.as_dict(lists.get("pro"))["unique"] == 1
    await lists.aclose()


@respx.mock
async def test_a_list_not_loaded_reports_no_overlap(tuned) -> None:
    respx.get(ONE).mock(return_value=httpx.Response(404))
    store = FakeStore(rows=[row("pro", url=ONE)])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()
    await settled(lists)

    entry = lists.as_dict(lists.get("pro"))
    assert entry["overlap"] == []
    assert entry["unique"] is None
    await lists.aclose()


@respx.mock
async def test_a_failed_measurement_keeps_the_previous_one(tuned, monkeypatch) -> None:
    serve(ONE, "a.test\nb.test\n")
    serve(TWO, "a.test\n")
    store = FakeStore(rows=[row("pro", url=ONE), row("light", url=TWO)])
    lists = Blocklists(httpx.AsyncClient(), store)
    await lists.tick()
    await settled(lists)

    def broken(held, enabled):
        raise MemoryError

    monkeypatch.setattr(blocklist_overlap, "measure", broken)
    serve(TWO, "a.test\nb.test\n")
    await lists.refresh("light")
    await settled(lists)

    assert lists.as_dict(lists.get("pro"))["overlap"] == [{"id": "light", "shared": 1}]
    await lists.aclose()


async def test_no_more_lists_are_measured_than_the_cap_holds(tuned) -> None:
    tuned(blocklist_max=8)
    lists = Blocklists(None, None)
    for i in range(9):
        state = ListState(meta=Blocklist(id=f"l{i}", name=f"L{i}", url=f"https://l{i}.test/"))
        state.hold(Parsed(names=names(f"{i}.test"), allow=frozenset(), skipped=0))
        lists._states[state.meta.id] = state
    lists._rebuild()
    await settled(lists)

    assert len(lists._overlap) == 8
    await lists.aclose()
