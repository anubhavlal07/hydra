from __future__ import annotations

import dataclasses
import json
import time

import httpx
import pytest
import respx

from hydra_dns.metrics_store import MetricsStore
from hydra_dns.resolver.metrics import PENDING_QUERIES, Metrics, QueryRecord

RPC = "https://store.test/rest/v1/rpc"


def record(device="laptop", name="example.com", blocked=False, cached=False) -> QueryRecord:
    return QueryRecord(
        at=time.time(),
        device=device,
        name=name,
        qtype="A",
        rcode="NOERROR",
        cached=cached,
        blocked=blocked,
        upstream="moyen",
        latency_ms=12.5,
    )


def configure(monkeypatch, *, persist: bool = True) -> None:
    import dataclasses

    from hydra_dns import metrics_store as module

    monkeypatch.setattr(
        module,
        "settings",
        dataclasses.replace(
            module.settings,
            store_url="https://store.test",
            store_key="k",
            store_secret="s",
            metrics_persist=persist,
        ),
    )


def test_export_carries_absolute_totals() -> None:
    m = Metrics()
    for _ in range(3):
        m.record(record())
    m.record(record(blocked=True))
    m.record(record(cached=True))

    state = m.export_state()
    assert state["totals"]["queries"] == 5
    assert state["totals"]["blocked"] == 1
    assert state["totals"]["cached"] == 1


def test_export_includes_per_device_and_domains() -> None:
    m = Metrics()
    m.record(record(device="phone", name="ads.test", blocked=True))
    m.record(record(device="laptop", name="github.com"))

    state = m.export_state()
    assert {d["device"] for d in state["devices"]} == {"phone", "laptop"}
    assert {d["domain"] for d in state["domains"]} == {"ads.test", "github.com"}
    assert next(d for d in state["domains"] if d["domain"] == "ads.test")["blocked"] == 1


def test_a_snapshot_round_trips() -> None:
    original = Metrics()
    for i in range(10):
        original.record(record(device=f"d{i % 3}", name=f"host{i % 4}.test", blocked=i % 5 == 0))
    state = original.export_state()

    restored = Metrics()
    restored.restore(state)

    assert restored.summary()["queries"] == original.summary()["queries"]
    assert restored.summary()["blocked"] == original.summary()["blocked"]
    assert {d["device"] for d in restored.devices()} == {d["device"] for d in original.devices()}
    assert restored.timeline(5)[-1]["queries"] == original.timeline(5)[-1]["queries"]


def test_restore_assigns_rather_than_adds() -> None:
    m = Metrics()
    m.restore({"totals": {"queries": 100, "blocked": 20}})
    m.restore({"totals": {"queries": 100, "blocked": 20}})
    assert m.summary()["queries"] == 100


def test_restore_survives_a_malformed_row() -> None:
    m = Metrics()
    counts = m.restore(
        {
            "totals": {"queries": 5},
            "devices": [{"device": ""}, {"device": "ok", "queries": 5}],
            "queries": [{"at": "not-a-number"}, record().as_dict()],
        }
    )
    assert counts["devices"] == 1
    assert counts["queries"] == 1


def test_rows_stay_pending_until_they_are_marked_flushed() -> None:
    m = Metrics()
    for _ in range(4):
        m.record(record())
    assert m.pending_count == 4

    state = m.export_state()
    assert len(state["queries"]) == 4
    assert m.pending_count == 4

    m.mark_flushed(len(state["queries"]))
    assert m.pending_count == 0


def test_only_the_flushed_rows_are_dropped() -> None:
    m = Metrics()
    for _ in range(3):
        m.record(record())
    state = m.export_state()

    m.record(record(name="arrived-mid-flush.test"))
    m.mark_flushed(len(state["queries"]))

    assert m.pending_count == 1
    assert m.export_state()["queries"][0]["name"] == "arrived-mid-flush.test"


def test_the_pending_queue_is_bounded() -> None:
    m = Metrics()
    for _ in range(PENDING_QUERIES + 50):
        m.record(record())
    assert m.pending_count == PENDING_QUERIES


def test_the_display_window_is_separate_from_the_write_queue() -> None:
    m = Metrics()
    for _ in range(700):
        m.record(record())
    assert len(m.recent(1000)) <= 600
    assert m.pending_count == 700


@respx.mock
async def test_flush_marks_rows_written_only_on_success(monkeypatch) -> None:
    configure(monkeypatch)
    m = Metrics()
    for _ in range(3):
        m.record(record())

    route = respx.post(f"{RPC}/hydra_metrics_flush").mock(side_effect=httpx.ConnectError("down"))
    store = MetricsStore(httpx.AsyncClient(), m)

    assert await store.flush() is False
    assert m.pending_count == 3, "a failed write must not drop the rows it failed to write"

    route.mock(return_value=httpx.Response(204))
    assert await store.flush() is True
    assert m.pending_count == 0


@respx.mock
async def test_a_failed_load_starts_from_zero_rather_than_raising(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_metrics_load").mock(side_effect=httpx.ConnectError("down"))

    m = Metrics()
    store = MetricsStore(httpx.AsyncClient(), m)
    await store.load()

    assert m.summary()["queries"] == 0
    assert "down" in store.last_error


@respx.mock
async def test_a_failed_load_never_writes_this_runs_counters_over_the_stored_ones(
    monkeypatch,
) -> None:
    configure(monkeypatch)
    load = respx.post(f"{RPC}/hydra_metrics_load").mock(side_effect=httpx.ConnectError("down"))
    flush = respx.post(f"{RPC}/hydra_metrics_flush").mock(return_value=httpx.Response(204))

    m = Metrics()
    store = MetricsStore(httpx.AsyncClient(), m)
    await store.load()
    m.record(record(blocked=True))
    m.record(record())

    assert await store.flush() is True
    sent = json.loads(flush.calls.last.request.content)
    assert sent["p_totals"] == {}
    assert sent["p_devices"] == [] and sent["p_timeline"] == [] and sent["p_domains"] == []
    assert len(sent["p_queries"]) == 2
    assert m.pending_count == 0
    assert load.call_count == 2


@respx.mock
async def test_a_later_load_adds_the_stored_counters_to_this_run(monkeypatch) -> None:
    configure(monkeypatch)
    minute = int(time.time() // 60) * 60
    load = respx.post(f"{RPC}/hydra_metrics_load").mock(
        side_effect=[
            httpx.ConnectError("down"),
            httpx.Response(
                200,
                json={
                    "totals": {"queries": 4000, "blocked": 300},
                    "devices": [
                        {"device": "laptop", "queries": 4000, "blocked": 300, "last_seen": 5}
                    ],
                    "timeline": [{"at": minute, "queries": 7, "blocked": 1, "cached": 0}],
                    "domains": [{"domain": "example.com", "queries": 90, "blocked": 0}],
                },
            ),
        ]
    )
    flush = respx.post(f"{RPC}/hydra_metrics_flush").mock(return_value=httpx.Response(204))

    m = Metrics()
    store = MetricsStore(httpx.AsyncClient(), m)
    await store.load()
    m.record(dataclasses.replace(record(blocked=True), at=minute + 1))
    m.record(dataclasses.replace(record(), at=minute + 2))
    await store.flush()
    await store.flush()

    sent = json.loads(flush.calls.last.request.content)
    assert sent["p_totals"]["queries"] == 4002
    assert sent["p_totals"]["blocked"] == 301
    (laptop,) = sent["p_devices"]
    assert (laptop["queries"], laptop["blocked"]) == (4002, 301)
    assert laptop["last_seen"] > 5
    assert {"at": minute, "queries": 9, "blocked": 2, "cached": 0} in sent["p_timeline"]
    assert {"domain": "example.com", "queries": 92, "blocked": 1} in sent["p_domains"]
    assert load.call_count == 2


@respx.mock
async def test_load_restores_previous_counters(monkeypatch) -> None:
    configure(monkeypatch)
    respx.post(f"{RPC}/hydra_metrics_load").mock(
        return_value=httpx.Response(
            200,
            json={
                "totals": {"queries": 4321, "blocked": 100},
                "devices": [{"device": "phone", "queries": 4321, "blocked": 100}],
                "timeline": [],
                "domains": [],
                "queries": [],
            },
        )
    )
    m = Metrics()
    await MetricsStore(httpx.AsyncClient(), m).load()

    assert m.summary()["queries"] == 4321
    assert m.devices()[0]["device"] == "phone"


async def test_persistence_off_does_no_io(monkeypatch) -> None:
    configure(monkeypatch, persist=False)
    m = Metrics()
    m.record(record())
    store = MetricsStore(httpx.AsyncClient(), m)

    with respx.mock:
        await store.load()
        assert await store.flush() is False

    assert store.snapshot()["enabled"] is False


@pytest.mark.parametrize("count", [0, -1])
def test_mark_flushed_ignores_nonsense(count: int) -> None:
    m = Metrics()
    m.record(record())
    m.mark_flushed(count)
    assert m.pending_count == 1


def test_forgetting_a_device_keeps_the_next_flush_from_writing_it_back() -> None:
    m = Metrics()
    m.record(record(device="probe"))
    m.record(record(device="laptop"))

    assert m.forget_device("probe") is True
    state = m.export_state()
    assert {d["device"] for d in state["devices"]} == {"laptop"}
    assert {q["device"] for q in state["queries"]} == {"laptop"}
    assert {r["device"] for r in m.recent()} == {"laptop"}
    assert m.forget_device("probe") is False


def test_a_forget_during_a_flush_drops_only_the_rows_that_flush_wrote() -> None:
    m = Metrics()
    m.record(record(device="probe"))
    m.record(record(device="laptop", name="a.test"))
    state = m.export_state(max_queries=2)
    m.record(record(device="laptop", name="b.test"))

    m.forget_device("probe")
    m.mark_flushed(len(state["queries"]))

    assert [q["name"] for q in m.export_state()["queries"]] == ["b.test"]
