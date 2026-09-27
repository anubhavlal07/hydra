from __future__ import annotations

import datetime as dt

from hydra_dns.resolver.metrics import Metrics, QueryRecord

SEPT = dt.datetime(2026, 9, 30, 23, 59, tzinfo=dt.UTC).timestamp()
OCT = dt.datetime(2026, 10, 1, 0, 1, tzinfo=dt.UTC).timestamp()


class Clock:
    def __init__(self, at: float) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def record(at: float, device: str = "laptop", blocked: bool = False) -> QueryRecord:
    return QueryRecord(
        at=at,
        device=device,
        name="example.com",
        qtype="A",
        rcode="NOERROR",
        cached=False,
        blocked=blocked,
        upstream="moyen",
        latency_ms=1.0,
    )


def device(metrics: Metrics, name: str) -> dict:
    return next(row for row in metrics.devices() if row["device"] == name)


def test_a_device_counts_this_month_beside_its_lifetime() -> None:
    clock = Clock(SEPT)
    metrics = Metrics(clock)
    metrics.record(record(SEPT))
    metrics.record(record(SEPT, blocked=True))

    clock.at = OCT
    metrics.record(record(OCT))

    row = device(metrics, "laptop")
    assert row["queries"] == 3
    assert row["month"]["queries"] == 1
    assert row["month"]["blocked"] == 0
    assert metrics.summary()["month"]["queries"] == 1
    assert metrics.summary()["month"]["label"] == "2026-10"
    assert metrics.summary()["queries"] == 3


def test_a_device_quiet_since_last_month_reads_zero_this_month() -> None:
    clock = Clock(SEPT)
    metrics = Metrics(clock)
    metrics.record(record(SEPT, device="tv"))

    clock.at = OCT
    assert device(metrics, "tv")["month"]["queries"] == 0
    assert device(metrics, "tv")["queries"] == 1
    assert metrics.device_totals()["tv"]["month_queries"] == 0


def test_this_months_counts_survive_a_restart() -> None:
    clock = Clock(OCT)
    first = Metrics(clock)
    for _ in range(4):
        first.record(record(OCT))
    state = first.export_state()

    second = Metrics(clock)
    second.restore(state)
    assert device(second, "laptop")["month"]["queries"] == 4
    assert second.summary()["month"]["queries"] == 4


def test_last_months_persisted_counts_are_not_carried_into_this_one() -> None:
    clock = Clock(SEPT)
    first = Metrics(clock)
    first.record(record(SEPT))
    state = first.export_state()

    clock.at = OCT
    second = Metrics(clock)
    second.restore(state)
    assert device(second, "laptop")["queries"] == 1
    assert device(second, "laptop")["month"]["queries"] == 0
    assert second.summary()["month"]["queries"] == 0


def test_a_late_load_adds_this_months_stored_counts_to_this_run() -> None:
    clock = Clock(OCT)
    stored = Metrics(clock)
    stored.record(record(OCT))
    stored.record(record(OCT))

    running = Metrics(clock)
    running.record(record(OCT))
    running.absorb(stored.export_state())
    assert device(running, "laptop")["month"]["queries"] == 3
    assert running.summary()["month"]["queries"] == 3


def test_a_store_without_month_columns_still_restores_lifetime_counts() -> None:
    metrics = Metrics(Clock(OCT))
    metrics.restore({"totals": {"queries": 9}, "devices": [{"device": "old", "queries": 9}]})
    assert device(metrics, "old")["queries"] == 9
    assert device(metrics, "old")["month"]["queries"] == 0
