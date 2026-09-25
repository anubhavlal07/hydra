from __future__ import annotations

import datetime as dt

import httpx
import pytest

from hydra_dns.config import DeviceConfig
from hydra_dns.schedule import Window, normalise_days
from hydra_dns.store import Device, DeviceStore, RuleGroup, StoreError

MON, TUE, SAT, SUN = 0, 1, 5, 6


def at(weekday: int, hour: int, minute: int = 0) -> dt.datetime:
    base = dt.datetime(2024, 1, 1, hour, minute, tzinfo=dt.UTC)
    return base + dt.timedelta(days=weekday)


def test_an_empty_window_is_always_on() -> None:
    window = Window()
    assert window.all_day
    assert window.covers(at(MON, 3))
    assert window.covers(at(SUN, 23, 59))


def test_a_window_inside_one_day_holds_only_between_its_hours() -> None:
    window = Window(starts_at="09:00", ends_at="17:00")

    assert not window.covers(at(MON, 8, 59))
    assert window.covers(at(MON, 9, 0))
    assert window.covers(at(MON, 16, 59))
    assert not window.covers(at(MON, 17, 0))


def test_a_window_that_wraps_midnight_runs_into_the_next_morning() -> None:
    window = Window(starts_at="22:00", ends_at="07:00")

    assert window.covers(at(MON, 22, 0))
    assert window.covers(at(MON, 23, 59))
    assert window.covers(at(TUE, 6, 59))
    assert not window.covers(at(TUE, 7, 0))
    assert not window.covers(at(TUE, 12, 0))


def test_a_wrapping_window_only_spills_from_a_day_it_was_asked_for() -> None:
    window = Window(days=(SAT,), starts_at="22:00", ends_at="07:00")

    assert window.covers(at(SAT, 23, 0))
    assert window.covers(at(SUN, 6, 0))
    assert not window.covers(at(SUN, 23, 0))
    assert not window.covers(at(MON, 6, 0))


def test_days_narrow_an_all_day_window() -> None:
    window = Window(days=(MON, TUE))

    assert window.covers(at(MON, 12))
    assert window.covers(at(TUE, 12))
    assert not window.covers(at(SAT, 12))


def test_a_window_that_starts_and_ends_together_covers_nothing() -> None:
    assert not Window(starts_at="09:00", ends_at="09:00").covers(at(MON, 9))


def test_a_nonsense_clock_falls_back_to_all_day() -> None:
    window = Window(starts_at="25:00", ends_at="07:00")
    assert window.all_day
    assert window.covers(at(MON, 3))


def test_days_are_normalised_and_deduplicated() -> None:
    assert normalise_days([2, 0, 2, 9, -1, "1"]) == (0, 1, 2)
    assert normalise_days(None) == ()
    assert normalise_days(["x"]) == ()


def test_a_window_describes_itself_for_the_dashboard() -> None:
    assert Window().describe() == "every day"
    assert Window(days=(MON, TUE)).describe() == "Mon Tue"
    assert Window(starts_at="22:00", ends_at="07:00").describe() == "22:00–07:00, every day"


def build() -> DeviceStore:
    store = DeviceStore(httpx.AsyncClient(), [DeviceConfig(id="tv", name="TV")])
    store._adopt(
        [
            Device(id="tv", name="TV", source="env", tags=("kids",)),
            Device(id="laptop", name="Laptop", source="env", tags=("work",), rules=("ads.test",)),
        ]
    )
    return store


def test_a_group_reaches_every_device_carrying_its_tag() -> None:
    store = build()
    store._adopt_groups(
        [RuleGroup(id="bedtime", name="Bedtime", rules=("tiktok.test",), tags=("kids",))]
    )

    assert store.blocks("tv", "tiktok.test")
    assert not store.blocks("laptop", "tiktok.test")


def test_a_group_with_no_tags_reaches_every_device() -> None:
    store = build()
    store._adopt_groups([RuleGroup(id="all", name="Everywhere", rules=("tracker.test",))])

    assert store.blocks("tv", "tracker.test")
    assert store.blocks("laptop", "tracker.test")


def test_a_device_keeps_its_own_rules_alongside_a_group() -> None:
    store = build()
    store._adopt_groups([RuleGroup(id="all", name="Everywhere", rules=("tracker.test",))])

    assert store.blocks("laptop", "ads.test")
    assert store.blocks("laptop", "tracker.test")


def test_a_group_exception_lets_one_name_through() -> None:
    store = build()
    store._adopt_groups(
        [RuleGroup(id="all", name="Everywhere", rules=("test", "@@safe.test"), tags=())]
    )

    assert store.blocks("tv", "anything.test")
    assert not store.blocks("tv", "safe.test")


def test_a_disabled_group_does_nothing() -> None:
    store = build()
    store._adopt_groups(
        [RuleGroup(id="off", name="Off", rules=("tiktok.test",), tags=("kids",), enabled=False)]
    )

    assert not store.blocks("tv", "tiktok.test")


def test_a_group_outside_its_window_does_nothing(monkeypatch) -> None:
    store = build()
    store._adopt_groups(
        [
            RuleGroup(
                id="bedtime",
                name="Bedtime",
                rules=("tiktok.test",),
                tags=("kids",),
                starts_at="22:00",
                ends_at="07:00",
            )
        ]
    )

    monkeypatch.setattr("hydra_dns.schedule.now", lambda: at(MON, 12))
    assert not store.blocks("tv", "tiktok.test")

    store._active = None
    store._active_checked = 0.0
    monkeypatch.setattr("hydra_dns.schedule.now", lambda: at(MON, 23))
    assert store.blocks("tv", "tiktok.test")


def test_the_active_set_is_reported_for_the_dashboard(monkeypatch) -> None:
    store = build()
    store._adopt_groups(
        [
            RuleGroup(id="always", name="Always", rules=("a.test",)),
            RuleGroup(
                id="night", name="Night", rules=("b.test",), starts_at="22:00", ends_at="07:00"
            ),
        ]
    )

    monkeypatch.setattr("hydra_dns.schedule.now", lambda: at(MON, 12))
    assert store.active_groups() == frozenset({"always"})


def test_a_device_with_no_groups_is_unchanged() -> None:
    store = build()
    assert store.blocks("laptop", "ads.test")
    assert not store.blocks("laptop", "anything-else.test")
    assert store.active_groups() == frozenset()


async def test_a_group_id_has_to_be_usable() -> None:
    store = build()
    with pytest.raises(StoreError, match="lowercase letters"):
        await store.set_group(
            "Not Valid!",
            name="x",
            rules=[],
            tags=[],
            days=[],
            starts_at="",
            ends_at="",
            enabled=True,
        )


async def test_half_a_window_is_refused() -> None:
    store = build()
    with pytest.raises(StoreError, match="both a start and an end"):
        await store.set_group(
            "bedtime",
            name="Bedtime",
            rules=[],
            tags=[],
            days=[],
            starts_at="22:00",
            ends_at="",
            enabled=True,
        )


async def test_a_window_of_no_length_is_refused() -> None:
    store = build()
    with pytest.raises(StoreError, match="covers nothing"):
        await store.set_group(
            "bedtime",
            name="Bedtime",
            rules=[],
            tags=[],
            days=[],
            starts_at="09:00",
            ends_at="09:00",
            enabled=True,
        )


async def test_a_rule_that_is_not_a_domain_is_refused() -> None:
    store = build()
    with pytest.raises(StoreError, match="not a domain"):
        await store.set_group(
            "bedtime",
            name="Bedtime",
            rules=["not a domain at all"],
            tags=[],
            days=[],
            starts_at="",
            ends_at="",
            enabled=True,
        )


def _with_zone(monkeypatch, name: str) -> None:
    from types import SimpleNamespace

    from hydra_dns import schedule

    monkeypatch.setattr(schedule, "settings", SimpleNamespace(timezone=name))


def test_a_real_zone_resolves_on_any_host(monkeypatch) -> None:
    from hydra_dns import schedule

    _with_zone(monkeypatch, "Asia/Kolkata")
    assert str(schedule.zone()) == "Asia/Kolkata"


def test_a_zone_that_shifts_is_read_at_the_right_offset(monkeypatch) -> None:
    import datetime as dt

    from hydra_dns import schedule

    _with_zone(monkeypatch, "Asia/Kolkata")
    moment = dt.datetime(2024, 1, 1, 12, 0, tzinfo=schedule.zone())
    assert moment.utcoffset() == dt.timedelta(hours=5, minutes=30)


def test_a_zone_the_host_does_not_have_falls_back_and_says_so(monkeypatch) -> None:
    from hydra_dns import schedule

    _with_zone(monkeypatch, "Mars/Olympus_Mons")
    assert str(schedule.zone()) == "UTC"


def test_an_empty_zone_is_utc(monkeypatch) -> None:
    from hydra_dns import schedule

    _with_zone(monkeypatch, "")
    assert str(schedule.zone()) == "UTC"
