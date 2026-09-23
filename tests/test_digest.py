from __future__ import annotations

import dataclasses
import datetime as dt
import json
import time

import respx
from test_doh import client, mock_upstream

from hydra_dns import digest, firstseen, schedule
from hydra_dns.config import settings
from hydra_dns.decision import ALLOWED, BLOCKLIST, DEVICE_RULE, SCHEDULE, Decision
from hydra_dns.main import app
from hydra_dns.resolver.metrics import BLOCKED_PER_DEVICE, Metrics, QueryRecord

PASSWORD = "hunter2"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


class FakeMetrics:
    def __init__(self, queries=0, cached=0, blocked=0, rows=None) -> None:
        self._summary = {"queries": queries, "cached": cached, "blocked": blocked}
        self._rows = rows or []

    def summary(self) -> dict:
        return dict(self._summary)

    def devices(self) -> list[dict]:
        return list(self._rows)

    def advance(self, **counts) -> None:
        for key, value in counts.items():
            self._summary[key] += value


class FakePush:
    def __init__(self, configured=True, sent=1) -> None:
        self.configured = configured
        self.payloads: list[dict] = []
        self._sent = sent

    async def send(self, payload: dict, urgency: str = "normal") -> dict:
        self.payloads.append(payload)
        return {"sent": self._sent, "failed": 0}


def test_the_next_one_lands_on_the_chosen_day_and_hour() -> None:
    at = digest.next_at(2, 7, time.time())
    moment = dt.datetime.fromtimestamp(at, schedule.zone())
    assert moment.weekday() == 2
    assert (moment.hour, moment.minute) == (7, 0)
    assert at > time.time()


def test_a_moment_that_has_just_gone_waits_a_whole_week() -> None:
    now = time.time()
    first = digest.next_at(0, 9, now)
    again = digest.next_at(0, 9, first)
    assert round(again - first) == 7 * 86_400


def test_it_counts_what_happened_since_the_last_one() -> None:
    metrics = FakeMetrics(queries=1000, cached=400, blocked=100)
    weekly = digest.Digest(metrics, FakePush())

    metrics.advance(queries=500, cached=250, blocked=50)
    payload = weekly.compose()

    assert "500 queries" in payload["reasons"][0]
    assert "10% blocked, 50% from cache" in payload["reasons"][1]


def test_a_quiet_week_says_nothing_absurd() -> None:
    weekly = digest.Digest(FakeMetrics(), FakePush())
    payload = weekly.compose()
    assert "0 queries" in payload["reasons"][0]
    assert "0% blocked, 0% from cache" in payload["reasons"][1]


def test_the_busiest_device_is_named_when_there_is_one() -> None:
    rows = [{"device": "tv", "queries": 10}, {"device": "phone", "queries": 90}]
    metrics = FakeMetrics(rows=rows)
    weekly = digest.Digest(metrics, FakePush())
    assert "busiest device: phone" in weekly.compose()["reasons"]


def test_the_pooled_allowance_rides_along_when_it_is_known() -> None:
    weekly = digest.Digest(
        FakeMetrics(),
        FakePush(),
        lambda: {"requests_remaining": 471_600, "headroom_days": 11.4},
    )
    lines = weekly.compose()["reasons"]
    assert "471.6K of pooled allowance left" in lines
    assert "11 days of headroom at this rate" in lines


async def test_sending_resets_the_window_and_books_the_next_one() -> None:
    metrics = FakeMetrics(queries=100)
    push = FakePush()
    weekly = digest.Digest(metrics, push)
    booked = weekly._due_at

    metrics.advance(queries=900)
    first = await weekly.send()

    metrics.advance(queries=5)
    second = weekly.compose()

    assert "900 queries" in first["payload"]["reasons"][0]
    assert "5 queries" in second["reasons"][0]
    assert weekly.sent == 1
    assert weekly._due_at >= booked
    assert push.payloads[0]["tag"] == "hydra-digest"


async def test_nothing_subscribed_is_recorded_rather_than_raised() -> None:
    weekly = digest.Digest(FakeMetrics(), FakePush(sent=0))
    result = await weekly.send()
    assert result["delivered"] == 0
    assert weekly.failures == 1
    assert weekly.last_error


async def test_it_stays_quiet_until_it_is_switched_on() -> None:
    weekly = digest.Digest(FakeMetrics(), FakePush())
    assert settings.digest is False
    assert weekly.configured is False


async def test_it_needs_push_as_well_as_the_switch() -> None:
    weekly = digest.Digest(FakeMetrics(), FakePush(configured=False))
    assert weekly.configured is False


def test_a_due_moment_is_only_due_once_it_has_arrived() -> None:
    weekly = digest.Digest(FakeMetrics(), FakePush())
    assert weekly.due(weekly._due_at - 1) is False
    assert weekly.due(weekly._due_at) is True


@respx.mock
async def test_the_dashboard_can_see_it_and_send_one() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        state = (await http.get("/api/alerts")).json()
        sent = await http.post("/api/alerts/digest")
        trail = (await http.get("/api/audit")).json()

    assert state["digest"]["enabled"] is False
    assert state["digest"]["day"] == "Mon"
    assert state["digest"]["due_at"] > time.time()
    assert sent.status_code == 200
    assert "queries" in sent.json()["payload"]["body"]
    assert "alerts.digest" in [e["action"] for e in trail["entries"]]


@respx.mock
async def test_a_stranger_cannot_send_one() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.post("/api/alerts/digest")).status_code == 401


def _asked(metrics, device, name, at, blocked=False, upstream="account-one"):
    metrics.record(
        QueryRecord(
            at=at,
            device=device,
            name=name,
            qtype="A",
            rcode="NOERROR",
            cached=False,
            blocked=blocked,
            upstream=upstream,
            latency_ms=5.0,
        )
    )


def _watched_metrics() -> Metrics:
    metrics = Metrics()
    metrics.first_seen = firstseen.FirstSeen(warmup_s=0)
    return metrics


def test_the_digest_says_how_many_new_names_look_machine_generated() -> None:
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, FakePush())
    now = time.time()
    _asked(metrics, "phone", "example.com", now)
    _asked(metrics, "phone", "xjw4k2qzp9v.com", now)
    _asked(metrics, "phone", "a1b2c3d4e5f6g7h8.top", now)

    lines = weekly.compose()["reasons"]
    assert "3 domains seen for the first time" in lines
    assert "2 of them look machine-generated" in lines


def test_a_week_of_ordinary_new_names_says_nothing_about_suspicion() -> None:
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, FakePush())
    _asked(metrics, "phone", "example.com", time.time())

    assert not any("machine-generated" in line for line in weekly.compose()["reasons"])


class FakeResolver:
    def __init__(self, decisions: dict[str, Decision] | None = None) -> None:
        self._decisions = decisions or {}
        self.asked: list[tuple[str, str, str]] = []

    def decide(self, name: str, qtype: str, device: str) -> Decision | None:
        self.asked.append((name, qtype, device))
        return self._decisions.get(name)


class Named:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeDevices:
    def get(self, device: str):
        return Named("Pixel") if device == "phone" else None


class NoPool:
    configured = False

    def __getattr__(self, name: str):
        raise AssertionError(f"the digest touched the pool: {name}")


def test_each_device_reports_its_new_names_and_what_blocked_it() -> None:
    metrics = _watched_metrics()
    resolver = FakeResolver(
        {
            "ads.example": Decision(
                layer=BLOCKLIST, label="blocklist Ads", blocked=True, source="ads"
            ),
            "late.example": Decision(layer=SCHEDULE, label="schedule Bedtime", blocked=True),
            "mine.example": Decision(layer=DEVICE_RULE, label=DEVICE_RULE, blocked=True),
        }
    )
    weekly = digest.Digest(metrics, FakePush(), resolver=resolver, devices=FakeDevices())
    now = time.time() + 1

    _asked(metrics, "phone", "news.example", now)
    _asked(metrics, "phone", "shop.example", now + 1)
    for offset in range(3):
        _asked(metrics, "phone", "ads.example", now + 2 + offset, blocked=True)
    _asked(metrics, "phone", "tracker.example", now + 6, blocked=True)
    _asked(metrics, "phone", "late.example", now + 7, blocked=True)
    _asked(metrics, "phone", "mine.example", now + 8, blocked=True)
    _asked(metrics, "tv", "news.example", now + 9)

    report = weekly.report(now + 10)
    phone, tv = report["devices"]

    assert (phone["device"], phone["name"], phone["queries"], phone["blocked"]) == (
        "phone",
        "Pixel",
        8,
        6,
    )
    assert tv["name"] == "tv"
    assert phone["first_seen"]["count"] == 6
    assert phone["first_seen"]["names"][0]["name"] == "mine.example"
    assert {"news.example", "shop.example"} <= {n["name"] for n in phone["first_seen"]["names"]}

    layers = {row["name"]: row["label"] for row in phone["blocked_names"]}
    assert phone["blocked_names"][0] == {
        "name": "ads.example",
        "count": 3,
        "layer": BLOCKLIST,
        "label": "blocklist Ads",
        "blocked_now": True,
    }
    assert layers["tracker.example"] == "AdGuard"
    assert layers["late.example"] == "schedule Bedtime"
    assert layers["mine.example"] == "device rule"
    assert tv["blocked_names"] == []
    assert report["active"] == 2
    assert all(qtype == "A" for _, qtype, _ in resolver.asked)


def test_a_name_allowed_now_says_so_rather_than_blaming_adguard() -> None:
    metrics = _watched_metrics()
    resolver = FakeResolver({"ok.example": Decision(layer=ALLOWED, label="allowed by @@ device")})
    weekly = digest.Digest(metrics, FakePush(), resolver=resolver)
    _asked(metrics, "phone", "ok.example", time.time() + 1, blocked=True)

    (row,) = weekly.report()["devices"][0]["blocked_names"]
    assert row["label"] == "allowed by @@ device"
    assert row["blocked_now"] is False


def test_a_rebind_refusal_is_credited_to_the_guard_not_adguard() -> None:
    metrics = _watched_metrics()
    resolver = FakeResolver(
        {
            "nas.example": Decision(layer=ALLOWED, label="allowed by @@ device"),
            "ruled.example": Decision(layer=DEVICE_RULE, label=DEVICE_RULE, blocked=True),
        }
    )
    weekly = digest.Digest(metrics, FakePush(), resolver=resolver)
    now = time.time() + 1
    guard = "rebind guard → account-one"
    _asked(metrics, "phone", "router.example", now, blocked=True, upstream=guard)
    _asked(metrics, "phone", "nas.example", now + 1, blocked=True, upstream="rebind guard")
    _asked(metrics, "phone", "ruled.example", now + 2, blocked=True, upstream=guard)
    _asked(metrics, "phone", "moved.example", now + 3, blocked=True, upstream=guard)
    _asked(metrics, "phone", "moved.example", now + 4, blocked=True)

    rows = {row["name"]: row for row in weekly.report()["devices"][0]["blocked_names"]}
    assert rows["router.example"] == {
        "name": "router.example",
        "count": 1,
        "layer": "rebind guard",
        "label": "rebind guard",
        "blocked_now": True,
    }
    assert rows["nas.example"]["layer"] == "rebind guard"
    assert rows["ruled.example"]["label"] == DEVICE_RULE
    assert rows["moved.example"]["label"] == "AdGuard"


def test_names_older_than_the_window_are_not_new_this_week() -> None:
    metrics = _watched_metrics()
    _asked(metrics, "phone", "old.example", time.time() - 60)
    weekly = digest.Digest(metrics, FakePush(), resolver=FakeResolver())
    _asked(metrics, "phone", "old.example", time.time() + 1)
    _asked(metrics, "phone", "fresh.example", time.time() + 2)

    row = weekly.report()["devices"][0]
    assert [n["name"] for n in row["first_seen"]["names"]] == ["fresh.example"]
    assert row["queries"] == 2


def test_the_report_is_bounded_by_devices_and_by_names(monkeypatch) -> None:
    monkeypatch.setattr(digest, "settings", dataclasses.replace(settings, digest_devices=5))
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, FakePush(), resolver=FakeResolver())
    now = time.time() + 1
    for device in range(80):
        for index in range(30 + device):
            _asked(metrics, f"device-{device}", f"n{index}.example", now + index, blocked=True)

    report = weekly.report()
    assert report["limit"] == 5
    assert report["active"] == 80
    assert [row["device"] for row in report["devices"]] == [
        f"device-{n}" for n in (79, 78, 77, 76, 75)
    ]
    for row in report["devices"]:
        assert len(row["first_seen"]["names"]) == digest.NAMES_PER_DEVICE
        assert len(row["blocked_names"]) == digest.NAMES_PER_DEVICE
        assert row["first_seen"]["count"] > digest.NAMES_PER_DEVICE


def test_the_per_device_blocked_tally_has_a_ceiling() -> None:
    metrics = Metrics()
    now = time.time()
    for index in range(BLOCKED_PER_DEVICE * 5):
        _asked(metrics, "phone", f"b{index}.example", now, blocked=True)
    assert len(metrics._devices["phone"].blocked_domains) <= BLOCKED_PER_DEVICE * 2


def test_the_rebind_tally_stays_inside_the_blocked_tally() -> None:
    metrics = Metrics()
    now = time.time()
    for index in range(BLOCKED_PER_DEVICE * 5):
        _asked(metrics, "phone", f"b{index}.example", now, blocked=True, upstream="rebind guard")
    entry = metrics._devices["phone"]
    assert len(entry.rebound) <= BLOCKED_PER_DEVICE * 2
    assert entry.rebound <= entry.blocked_domains.keys()
    metrics.forget_device_blocked()
    assert metrics.device_rebound("phone") == frozenset()


def test_names_hidden_from_the_log_stay_out_of_the_report(monkeypatch) -> None:
    monkeypatch.setattr(
        digest, "settings", dataclasses.replace(settings, log_hidden_domains=["secret.example"])
    )
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, FakePush(), resolver=FakeResolver())
    _asked(metrics, "phone", "a.secret.example", time.time() + 1, blocked=True)
    _asked(metrics, "phone", "open.example", time.time() + 2)

    row = weekly.report()["devices"][0]
    assert [n["name"] for n in row["first_seen"]["names"]] == ["open.example"]
    assert row["blocked_names"] == []


async def test_sending_starts_a_fresh_per_device_window() -> None:
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, FakePush(), resolver=FakeResolver())
    _asked(metrics, "phone", "ads.example", time.time() + 1, blocked=True)
    assert weekly.report()["devices"]

    await weekly.send(time.time() + 2)
    assert weekly.report()["devices"] == []

    _asked(metrics, "phone", "other.example", time.time() + 3)
    (row,) = weekly.report()["devices"]
    assert row["queries"] == 1
    assert row["blocked_names"] == []


def test_the_push_carries_a_summary_and_a_link_not_the_report(monkeypatch) -> None:
    monkeypatch.setattr(digest, "settings", dataclasses.replace(settings, digest_devices=50))
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, FakePush(), resolver=FakeResolver())
    now = time.time() + 1
    for device in range(50):
        for index in range(40):
            _asked(metrics, "d" * 60 + str(device), f"n{index}.example", now + index)

    payload = weekly.compose()

    assert len(json.dumps(payload).encode()) <= digest.PUSH_MAX_BYTES
    assert payload["url"] == "/#tools/digest"
    assert any(line.startswith("new names by device:") for line in payload["reasons"])
    assert "n1.example" not in json.dumps(payload)


def test_an_oversized_payload_is_trimmed_under_the_push_limit() -> None:
    fitted = digest.fit(
        {
            "title": "t" * 900,
            "body": "b" * 5000,
            "tag": "x",
            "url": "/",
            "reasons": ["r" * 900] * 40,
        }
    )
    assert len(json.dumps(fitted).encode()) <= digest.PUSH_MAX_BYTES
    assert fitted["reasons"]


async def test_the_report_asks_nothing_of_the_pool_or_adguard() -> None:
    metrics = _watched_metrics()
    weekly = digest.Digest(metrics, NoPool(), quota=None, resolver=FakeResolver())
    _asked(metrics, "phone", "ads.example", time.time() + 1, blocked=True)
    assert weekly.report()["devices"][0]["blocked_names"][0]["label"] == "AdGuard"
    assert weekly.compose()["reasons"]


@respx.mock
async def test_the_dashboard_reads_the_per_device_report_without_an_upstream_call() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        _asked(app.state.metrics, "phone", "ads.example", time.time() + 1, blocked=True)
        before = respx.calls.call_count
        response = await http.get("/api/digest/devices")
        after = respx.calls.call_count

    body = response.json()
    assert response.status_code == 200
    assert body["available"] is True
    assert after == before
    row = next(r for r in body["devices"] if r["device"] == "phone")
    assert row["name"] == "Pixel"
    assert row["blocked_names"][0]["name"] == "ads.example"


@respx.mock
async def test_a_stranger_cannot_read_the_per_device_report() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.get("/api/digest/devices")).status_code == 401


def test_the_per_device_report_is_read_guarded_and_never_conditional() -> None:
    from hydra_dns.api import ops
    from hydra_dns.api.access import require_read
    from hydra_dns.main import CONDITIONAL

    (route,) = [r for r in ops.router.routes if getattr(r, "path", "") == "/api/digest/devices"]
    assert route.methods == {"GET"}
    assert require_read in {getattr(d, "dependency", None) for d in route.dependencies}
    assert "/api/digest/devices" not in CONDITIONAL
