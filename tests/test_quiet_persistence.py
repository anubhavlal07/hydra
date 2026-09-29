from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import stat
import sys
import time

import pytest
import respx

from hydra_dns import quiet
from hydra_dns.config import settings
from hydra_dns.quiet import HOUR_S, PresenceFile, QuietWatch
from hydra_dns.resolver import metrics as metrics_module
from hydra_dns.resolver.metrics import Metrics, QueryRecord

START = 1_800_000_000.0


class Clock:
    def __init__(self, at: float = START) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def record(device: str, at: float) -> QueryRecord:
    return QueryRecord(
        at=at,
        device=device,
        name="example.com",
        qtype="A",
        rcode="NOERROR",
        cached=False,
        blocked=False,
        upstream="one",
        latency_ms=1.0,
    )


@pytest.fixture
def tuned(monkeypatch):
    monkeypatch.setattr(
        metrics_module,
        "settings",
        dataclasses.replace(settings, quiet_hours=8, quiet_established_hours=4),
    )


def regular(metrics: Metrics, device: str, hours: int, start: float = START) -> float:
    last = start
    for hour in range(hours):
        last = start + hour * HOUR_S + 60
        metrics.record(record(device, last))
    return last


def stored(tmp_path, metrics: Metrics, clock: Clock) -> PresenceFile:
    return PresenceFile(metrics, str(tmp_path / "presence.json"), clock=clock)


def reboot(tmp_path, at: float) -> tuple[Metrics, PresenceFile]:
    clock = Clock(at)
    metrics = Metrics(clock=clock)
    presence = stored(tmp_path, metrics, clock)
    presence.load()
    return metrics, presence


def test_a_round_trip_keeps_each_device_rhythm_and_last_query(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    last = regular(before, "phone", 6)
    regular(before, "tv", 2)
    clock.at = last + 1
    assert stored(tmp_path, before, clock).write() == 2

    after, presence = reboot(tmp_path, last + 1)

    assert presence.restored == 2
    assert after.presence.established("phone") is True
    assert after.presence.established("tv") is False
    original = before.export_presence(last + 1)["devices"]
    assert after.export_presence(last + 1)["devices"] == original


def test_a_restored_regular_device_is_quiet_the_moment_it_boots(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    last = regular(before, "phone", 6)
    clock.at = last + 9 * HOUR_S
    assert before.quiet_since(["phone"], clock.at) == {"phone": last}
    stored(tmp_path, before, clock).write()

    after, _ = reboot(tmp_path, clock.at + 30)

    assert after.presence.settled(clock.at + 30) is True
    assert after.quiet_since(["phone"], clock.at + 30) == {"phone": last}
    assert after.quiet_snapshot()["restored"] == 1


def test_one_query_after_boot_clears_it(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    last = regular(before, "phone", 6)
    clock.at = last + 9 * HOUR_S
    stored(tmp_path, before, clock).write()

    boot = clock.at + 30
    after, _ = reboot(tmp_path, boot)
    after.record(record("phone", boot + 5))

    assert after.quiet_since(["phone"], boot + 10) == {}


def test_the_time_hydra_was_down_is_not_counted_as_silence(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    last = regular(before, "phone", 6)
    clock.at = last + 2 * HOUR_S
    stored(tmp_path, before, clock).write()

    boot = clock.at + 3 * HOUR_S
    after, _ = reboot(tmp_path, boot)

    assert after.quiet_snapshot()["restored_gap_s"] == 3 * HOUR_S
    assert after.quiet_since(["phone"], boot) == {}
    assert after.quiet_since(["phone"], boot + 6 * HOUR_S - 1) == {}
    assert after.quiet_since(["phone"], boot + 6 * HOUR_S) == {"phone": last}
    assert after.presence.established("phone") is True


def test_quiet_since_stays_the_real_last_query_across_restarts(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    last = regular(before, "phone", 6)
    clock.at = last + HOUR_S
    stored(tmp_path, before, clock).write()

    first_boot = clock.at + 2 * HOUR_S
    middle, presence = reboot(tmp_path, first_boot)
    clock.at = first_boot + HOUR_S
    PresenceFile(middle, presence._path, clock=clock).write()

    second_boot = clock.at + 3 * HOUR_S
    after, _ = reboot(tmp_path, second_boot)

    naive = last + 8 * HOUR_S
    assert after.quiet_since(["phone"], naive) == {}
    assert after.quiet_since(["phone"], last + 13 * HOUR_S - 1) == {}
    assert after.quiet_since(["phone"], last + 13 * HOUR_S) == {"phone": last}
    row = after.export_presence(second_boot)["devices"]["phone"]
    assert row["seen_at"] == last
    assert row["last_at"] == last + 5 * HOUR_S


def test_a_file_without_seen_at_falls_back_to_last_at(tmp_path, tuned) -> None:
    hour = int((START - 9 * HOUR_S) // HOUR_S)
    payload = {
        "version": 1,
        "saved_at": START,
        "devices": {
            "phone": {"last_at": START - 9 * HOUR_S, "hours": [hour - 3, hour - 2, hour - 1]}
        },
    }
    (tmp_path / "presence.json").write_text(json.dumps(payload), encoding="utf-8")

    after, _ = reboot(tmp_path, START + 60)

    assert after.quiet_since(["phone"], START + 60) == {"phone": START - 9 * HOUR_S}


@respx.mock
async def test_the_api_reports_the_real_last_query_after_a_restore(monkeypatch) -> None:
    from test_doh import client, mock_upstream

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": "hunter2"})
        now = time.time()
        real_last = now - 10 * HOUR_S
        payload = {
            "version": 1,
            "saved_at": now - HOUR_S,
            "devices": {
                "phone": {"last_at": real_last, "seen_at": real_last, "hours": []},
                "laptop": {"last_at": now - 8.5 * HOUR_S, "hours": []},
            },
        }
        watch_ = QuietWatch(8 * HOUR_S, 1, clock=Clock(now - 60))
        assert watch_.restore(payload, now) == 2
        app.state.metrics.presence = watch_

        listed = (await http.get("/api/devices")).json()["devices"]

    by_id = {d["id"]: d for d in listed}
    assert by_id["phone"]["quiet_since"] == real_last
    assert by_id["laptop"]["quiet_since"] is None


def test_a_rhythm_survives_a_restart_and_keeps_counting(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    last = regular(before, "phone", 3)
    clock.at = last + 60
    stored(tmp_path, before, clock).write()

    boot = clock.at + 10
    after, _ = reboot(tmp_path, boot)
    assert after.presence.established("phone") is False
    after.record(record("phone", boot + HOUR_S))

    assert after.presence.established("phone") is True


def test_a_missing_file_keeps_the_grace_window(tmp_path, tuned) -> None:
    after, presence = reboot(tmp_path, START)

    assert presence.restored == 0
    assert presence.snapshot()["load_error"] == "no presence file yet"
    assert after.presence.settled(START) is False
    assert after.presence.settles_at == START + 8 * HOUR_S


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "",
        "[]",
        json.dumps({"version": 99, "saved_at": START, "devices": {}}),
        json.dumps({"version": 1, "devices": {}}),
        json.dumps({"version": 1, "saved_at": START, "devices": []}),
        json.dumps({"version": 1, "saved_at": "soon", "devices": {}}),
    ],
)
def test_a_corrupt_file_falls_back_to_the_grace_window(tmp_path, tuned, content) -> None:
    (tmp_path / "presence.json").write_text(content, encoding="utf-8")

    after, presence = reboot(tmp_path, START)

    assert presence.restored == 0
    assert presence.snapshot()["load_error"]
    assert after.presence.settled(START) is False


def test_an_unreadable_path_falls_back_to_the_grace_window(tmp_path, tuned) -> None:
    (tmp_path / "presence.json").mkdir()

    after, presence = reboot(tmp_path, START)

    assert presence.restored == 0
    assert after.presence.settled(START) is False


def test_a_file_older_than_a_day_and_a_window_is_ignored(tmp_path, tuned) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    regular(before, "phone", 6)
    clock.at = START + 6 * HOUR_S
    stored(tmp_path, before, clock).write()

    too_late = clock.at + quiet.max_gap_s(8 * HOUR_S) + 1
    after, presence = reboot(tmp_path, too_late)

    assert presence.restored == 0
    assert "old" in presence.load_error
    assert after.presence.settled(too_late) is False

    _, kept = reboot(tmp_path, clock.at + quiet.max_gap_s(8 * HOUR_S))
    assert kept.restored == 1


def test_bad_rows_are_skipped_and_the_rest_restored(tmp_path, tuned) -> None:
    payload = {
        "version": 1,
        "saved_at": START,
        "devices": {
            "phone": {"last_at": START - 60, "hours": [int(START // HOUR_S)]},
            "broken": {"last_at": "x", "hours": []},
            "nohours": {"last_at": START - 60},
            "": {"last_at": START - 60, "hours": []},
            "odd": "nope",
        },
    }
    (tmp_path / "presence.json").write_text(json.dumps(payload), encoding="utf-8")

    after, presence = reboot(tmp_path, START)

    assert presence.restored == 1
    assert after.presence.settled(START) is True


def test_a_last_query_from_the_future_is_clamped_to_the_save(tmp_path, tuned) -> None:
    payload = {
        "version": 1,
        "saved_at": START,
        "devices": {"phone": {"last_at": START + 99 * HOUR_S, "hours": [1, 2, 3]}},
    }
    (tmp_path / "presence.json").write_text(json.dumps(payload), encoding="utf-8")

    after, _ = reboot(tmp_path, START)

    row = after.export_presence(START)["devices"]["phone"]
    assert row == {"last_at": START, "seen_at": START, "hours": [int(START // HOUR_S)]}


def test_a_forgotten_device_is_not_resurrected(tmp_path, tuned) -> None:
    async def run() -> None:
        clock = Clock(START)
        before = Metrics(clock=clock)
        regular(before, "phone", 6)
        regular(before, "tv", 6)
        presence = stored(tmp_path, before, clock)
        await presence.save()
        presence.start_background()
        try:
            before.forget_presence("phone")
            before.forget_device("tv")
            await asyncio.sleep(0.2)
            saved = json.loads((tmp_path / "presence.json").read_text(encoding="utf-8"))
            assert saved["devices"] == {}
        finally:
            await presence.aclose()

    asyncio.run(run())
    after, presence = reboot(tmp_path, START + 6 * HOUR_S)
    assert presence.restored == 0
    assert after.presence.forget("phone") is False
    assert after.presence.forget("tv") is False


def test_shutdown_writes_the_file(tmp_path, tuned) -> None:
    async def run() -> None:
        clock = Clock(START)
        before = Metrics(clock=clock)
        presence = stored(tmp_path, before, clock)
        presence.start_background()
        regular(before, "phone", 2)
        await presence.aclose()
        assert before.on_forget is None

    asyncio.run(run())
    saved = json.loads((tmp_path / "presence.json").read_text(encoding="utf-8"))
    assert set(saved["devices"]) == {"phone"}


def test_the_write_is_atomic_and_private(tmp_path, tuned, monkeypatch) -> None:
    clock = Clock(START)
    before = Metrics(clock=clock)
    regular(before, "phone", 2)
    presence = stored(tmp_path, before, clock)
    presence.write()
    good = (tmp_path / "presence.json").read_text(encoding="utf-8")

    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(tmp_path / "presence.json").st_mode) == 0o600

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(quiet.os, "replace", broken)
    regular(before, "tv", 2)
    written = asyncio.run(presence.save())

    assert written == 0
    assert "disk full" in presence.last_error
    assert (tmp_path / "presence.json").read_text(encoding="utf-8") == good
    assert [p.name for p in tmp_path.iterdir()] == ["presence.json"]


def test_restoring_respects_the_device_cap(tmp_path, tuned, monkeypatch) -> None:
    monkeypatch.setattr(quiet, "MAX_DEVICES", 2)
    devices = {
        name: {"last_at": START - offset, "hours": []}
        for name, offset in (("old", 300), ("newest", 10), ("middle", 100))
    }
    payload = {"version": 1, "saved_at": START, "devices": devices}
    (tmp_path / "presence.json").write_text(json.dumps(payload), encoding="utf-8")

    after, presence = reboot(tmp_path, START)

    assert presence.restored == 2
    assert set(after.export_presence(START)["devices"]) == {"newest", "middle"}


def test_without_a_path_nothing_is_read_or_written(tmp_path, tuned) -> None:
    metrics = Metrics(clock=Clock(START))
    presence = PresenceFile(metrics, "", clock=Clock(START))

    assert presence.load() == 0
    assert asyncio.run(presence.save()) == 0
    assert presence.snapshot()["enabled"] is False
    assert metrics.presence.settled(START) is False


def test_a_watch_built_directly_still_restores() -> None:
    w = QuietWatch(HOUR_S, 1, clock=Clock(START))
    w.note("phone", START - 2 * HOUR_S)
    payload = w.export(START)

    fresh = QuietWatch(HOUR_S, 1, clock=Clock(START + 60))
    assert fresh.restore(payload, START + 60) == 1
    assert fresh.quiet(["phone"], START + 60) == {"phone": START - 2 * HOUR_S}
