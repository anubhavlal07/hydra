from __future__ import annotations

import dataclasses
import time

import respx
from test_doh import client, mock_upstream

from hydra_dns import alerts as alerts_module
from hydra_dns import quiet, watch
from hydra_dns.alerts import AlertDispatcher
from hydra_dns.config import settings
from hydra_dns.quiet import HOUR_S, QuietWatch
from hydra_dns.resolver import metrics as metrics_module
from hydra_dns.resolver.metrics import Metrics, QueryRecord

PASSWORD = "hunter2"
START = 1_800_000_000.0
ACCOUNTS = [{"label": "one", "auth": {"authenticated": True}, "upstream": {"available": True}}]
QUOTA = {"requests_remaining": 500_000, "requests_used": 10}
POOL = {"spent": False, "fallback": {"active": False}}


class Clock:
    def __init__(self, at: float = START) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def watcher(quiet_hours: float = 8, established: int = 4, at: float = START) -> QuietWatch:
    return QuietWatch(quiet_hours * HOUR_S, established, clock=Clock(at))


def regular(watch_: QuietWatch, device: str, hours: int, start: float = START) -> float:
    last = start
    for hour in range(hours):
        last = start + hour * HOUR_S + 60
        watch_.note(device, last)
    return last


def test_a_regular_device_that_stops_is_quiet() -> None:
    w = watcher()
    last = regular(w, "phone", 6)

    assert w.quiet(["phone"], last + 7 * HOUR_S) == {}
    assert w.quiet(["phone"], last + 8 * HOUR_S) == {"phone": last}


def test_querying_again_ends_the_quiet() -> None:
    w = watcher()
    last = regular(w, "phone", 6)
    later = last + 9 * HOUR_S
    assert "phone" in w.quiet(["phone"], later)

    w.note("phone", later)
    assert w.quiet(["phone"], later + 60) == {}


def test_a_device_that_never_settled_into_a_rhythm_is_never_quiet() -> None:
    w = watcher(established=4)
    last = regular(w, "laptop", 3)

    assert w.established("laptop") is False
    assert w.quiet(["laptop"], last + 48 * HOUR_S) == {}


def test_a_burst_inside_one_hour_does_not_count_as_a_rhythm() -> None:
    w = watcher(established=2)
    for second in range(0, 3000, 10):
        w.note("tv", START + second)

    assert w.established("tv") is False


def test_old_hours_fall_out_of_the_rhythm() -> None:
    w = watcher(established=4)
    regular(w, "phone", 4)
    assert w.established("phone") is True

    w.note("phone", START + 40 * HOUR_S)
    assert w.established("phone") is False


def test_nothing_is_quiet_until_the_process_has_been_up_a_whole_window() -> None:
    clock = Clock(START)
    w = QuietWatch(8 * HOUR_S, 1, clock=clock)
    w.note("phone", START - 20 * HOUR_S)

    assert w.quiet(["phone"], START + 7 * HOUR_S) == {}
    assert w.quiet(["phone"], START + 8 * HOUR_S) == {"phone": START - 20 * HOUR_S}


def test_only_the_devices_asked_about_are_reported() -> None:
    w = watcher()
    last = regular(w, "phone", 6)

    assert w.quiet(["laptop"], last + 9 * HOUR_S) == {}


def test_forgetting_a_device_drops_its_rhythm() -> None:
    w = watcher()
    last = regular(w, "phone", 6)

    assert w.forget("phone") is True
    assert w.quiet(["phone"], last + 9 * HOUR_S) == {}
    assert w.forget("phone") is False


def test_the_number_of_devices_it_remembers_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(quiet, "MAX_DEVICES", 2)
    w = watcher()
    w.note("a", START)
    w.note("b", START + 10)
    w.note("c", START + 20)

    assert w.snapshot(START)["tracked"] == 2
    assert w.forget("a") is False


def test_a_zero_window_turns_it_off() -> None:
    assert quiet.build(dataclasses.replace(settings, quiet_hours=0)) is None
    built = quiet.build(dataclasses.replace(settings, quiet_hours=0.5, quiet_established_hours=1))
    assert built is not None
    assert built.quiet_s == 1800


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


def fed_metrics(monkeypatch, clock: Clock) -> Metrics:
    monkeypatch.setattr(
        metrics_module,
        "settings",
        dataclasses.replace(settings, quiet_hours=1, quiet_established_hours=2),
    )
    m = Metrics(clock=clock)
    m.record(record("phone", START + 60))
    m.record(record("phone", START + HOUR_S + 60))
    return m


def test_the_resolver_metrics_feed_it_and_forget_it(monkeypatch) -> None:
    clock = Clock(START)
    m = fed_metrics(monkeypatch, clock)
    later = START + 3 * HOUR_S

    assert m.quiet_since(["phone"], later) == {"phone": START + HOUR_S + 60}
    m.forget_device("phone")
    assert m.quiet_since(["phone"], later) == {}


def test_forget_presence_leaves_the_counters(monkeypatch) -> None:
    m = fed_metrics(monkeypatch, Clock(START))

    assert m.forget_presence("phone") is True
    assert m.quiet_since(["phone"], START + 3 * HOUR_S) == {}
    assert m.device_totals()["phone"]["queries"] == 2


class Registry:
    def __init__(self, devices, paused=()):
        from hydra_dns.pauses import Pauses
        from hydra_dns.store import Device

        self.devices = [Device(id=i, name=n) for i, n in devices]
        self.pauses = Pauses()
        for device in paused:
            self.pauses.hold(device, 60)


class State:
    def __init__(self, metrics, registry):
        self.metrics = metrics
        self.devices = registry


def test_a_paused_device_is_not_reported(monkeypatch) -> None:
    m = fed_metrics(monkeypatch, Clock(START))
    later = START + 3 * HOUR_S

    live = State(m, Registry([("phone", "Pixel")]))
    assert quiet.quiet_names(live, later) == ["Pixel"]

    held = State(m, Registry([("phone", "Pixel")], paused=["phone"]))
    assert quiet.quiet_names(held, later) == []


def test_a_device_no_longer_registered_is_not_reported(monkeypatch) -> None:
    m = fed_metrics(monkeypatch, Clock(START))
    gone = State(m, Registry([("laptop", "Work Laptop")]))

    assert quiet.quiet_devices(gone, START + 3 * HOUR_S) == {}


def reasons(**extra) -> list[str]:
    return watch.reasons(accounts=ACCOUNTS, quota=QUOTA, pool_state=POOL, warn_days=5, **extra)


def test_each_quiet_device_is_its_own_reason() -> None:
    found = reasons(quiet=["Pixel", "Living room TV"])

    assert found == [f"Pixel{watch.QUIET_SUFFIX}", f"Living room TV{watch.QUIET_SUFFIX}"]
    assert all(watch.is_quiet(r) for r in found)
    assert reasons() == []


def test_a_quiet_episode_alerts_once_and_recovers_once(monkeypatch) -> None:
    monkeypatch.setattr(alerts_module, "settings", dataclasses.replace(settings, alert_repeat_s=60))
    current: list[str] = []
    sender = AlertDispatcher(None, lambda: list(current), once=watch.is_quiet)
    quiet_reason = f"Pixel{watch.QUIET_SUFFIX}"
    other = "the pool is spent"

    current[:] = [quiet_reason, other]
    first = sender.evaluate(current, now=START)
    assert first["opened"] == sorted([quiet_reason, other])

    later = sender.evaluate(current, now=START + 7200)
    assert later["opened"] == []
    assert later["repeated"] == [other]

    current[:] = [other]
    back = sender.evaluate(current, now=START + 7300)
    assert back["resolved"] == [quiet_reason]

    current[:] = [quiet_reason, other]
    again = sender.evaluate(current, now=START + 9000)
    assert again["opened"] == [quiet_reason]


def primed(app, hours: int = 2) -> float:
    w = QuietWatch(HOUR_S, 2, clock=Clock(time.time() - 10 * HOUR_S))
    last = time.time() - 3 * HOUR_S
    for hour in range(hours):
        w.note("phone", last - (hours - 1 - hour) * HOUR_S)
    app.state.metrics.presence = w
    return last


@respx.mock
async def test_the_api_reports_a_quiet_device_everywhere() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        last = primed(app)

        listed = (await http.get("/api/devices")).json()["devices"]
        overview = (await http.get("/api/overview")).json()
        health = (await http.get("/api/health")).json()
        status = (await http.get("/api/status")).json()

    by_id = {d["id"]: d for d in listed}
    assert by_id["phone"]["quiet_since"] == last
    assert by_id["laptop"]["quiet_since"] is None
    reason = f"Pixel{watch.QUIET_SUFFIX}"
    assert reason in overview["attention"]
    assert reason in health["attention"]
    assert health["quiet"]["established"] == 1
    assert status["state"] == watch.ATTENTION


@respx.mock
async def test_a_query_clears_it_through_the_real_resolver() -> None:
    import dns.message
    import dns.rdatatype

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        primed(app)
        query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
        await app.state.resolver.resolve(query, "phone")

        listed = (await http.get("/api/devices")).json()["devices"]
        health = (await http.get("/api/health")).json()

    assert {d["id"]: d for d in listed}["phone"]["quiet_since"] is None
    assert not any(watch.is_quiet(r) for r in health["attention"])


class WritableRegistry(Registry):
    configured = True
    writable = True

    def get(self, device_id):
        return next((d for d in self.devices if d.id == device_id), None)

    async def remove(self, device_id):
        self.devices = [d for d in self.devices if d.id != device_id]
        return True


@respx.mock
async def test_deleting_a_device_forgets_its_rhythm() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        primed(app)
        real = app.state.devices
        app.state.devices = WritableRegistry([("phone", "Pixel")])
        try:
            removed = await http.delete("/api/devices/phone")
        finally:
            app.state.devices = real

    assert removed.status_code == 200
    assert app.state.metrics.presence.forget("phone") is False
