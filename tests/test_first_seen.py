from __future__ import annotations

import time

import respx
from test_doh import client, mock_upstream

from hydra_dns import firstseen

PASSWORD = "hunter2"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


def watcher(**kwargs) -> firstseen.FirstSeen:
    kwargs.setdefault("warmup_s", 0)
    return firstseen.FirstSeen(**kwargs)


def test_the_first_sighting_counts_and_the_second_does_not() -> None:
    seen = watcher()
    now = time.time()
    assert seen.note("laptop", "example.com", "mock", now) is True
    assert seen.note("laptop", "example.com", "mock", now + 1) is False
    assert seen.counted == 1


def test_each_device_has_its_own_memory() -> None:
    seen = watcher()
    now = time.time()
    assert seen.note("laptop", "example.com", "mock", now) is True
    assert seen.note("phone", "example.com", "mock", now) is True
    assert seen.counted == 2


def test_names_this_host_invented_are_not_discoveries() -> None:
    seen = watcher()
    now = time.time()
    assert seen.note("laptop", "printer.local", "local zone", now) is False
    assert seen.note("laptop", "nas.lan", "rewrite", now) is False
    assert seen.note("laptop", "ads.example", "device rule → 0.0.0.0", now) is False
    assert seen.note("laptop", "example.com", "cache", now) is True
    assert seen.counted == 1


def test_a_device_says_nothing_while_it_is_still_being_learned() -> None:
    seen = firstseen.FirstSeen(warmup_s=3600)
    now = time.time()
    assert seen.note("laptop", "one.example", "mock", now) is False
    assert seen.note("laptop", "two.example", "mock", now + 60) is False
    assert seen.note("laptop", "three.example", "mock", now + 3601) is True
    assert seen.counted == 1


def test_what_it_holds_per_device_is_capped() -> None:
    seen = watcher(per_device=10)
    now = time.time()
    for index in range(50):
        seen.note("laptop", f"n{index}.example", "mock", now)

    assert seen.snapshot()["tracked"] == 10
    assert seen.note("laptop", "n0.example", "mock", now) is True


def test_the_number_of_devices_it_watches_is_capped() -> None:
    seen = watcher(devices=3)
    now = time.time()
    for index in range(10):
        seen.note(f"device{index}", "example.com", "mock", now)

    assert seen.snapshot()["devices"] == 3


def test_the_recent_list_is_capped_and_newest_first() -> None:
    seen = watcher(recent=3)
    now = time.time()
    for index in range(10):
        seen.note("laptop", f"n{index}.example", "mock", now + index)

    rows = seen.recent()
    assert len(rows) == 3
    assert rows[0]["name"] == "n9.example"
    assert rows[-1]["name"] == "n7.example"


def test_the_recent_list_can_be_read_for_one_device() -> None:
    seen = watcher()
    now = time.time()
    seen.note("laptop", "one.example", "mock", now)
    seen.note("phone", "two.example", "mock", now)

    assert [r["name"] for r in seen.recent(device="phone")] == ["two.example"]


def test_an_empty_name_is_not_a_discovery() -> None:
    seen = watcher()
    assert seen.note("laptop", "", "mock", time.time()) is False


@respx.mock
async def test_the_resolver_reports_what_it_has_just_met() -> None:
    import dns.message
    import dns.rdatatype

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.metrics.first_seen.clear()
        app.state.metrics.first_seen._warmup_s = 0

        for name in ("example.com", "example.com", "printer.local"):
            query = dns.message.make_query(name, dns.rdatatype.A).to_wire()
            await app.state.resolver.resolve(query, "laptop")

        body = (await http.get("/api/resolver/first-seen")).json()

    names = [row["name"] for row in body["names"]]
    assert names == ["example.com"]
    assert body["watching"] is True
    assert body["tracking"]["counted"] == 1


@respx.mock
async def test_the_report_can_be_narrowed_to_one_device() -> None:
    import dns.message
    import dns.rdatatype

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.metrics.first_seen.clear()
        app.state.metrics.first_seen._warmup_s = 0

        query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
        await app.state.resolver.resolve(query, "laptop")
        await app.state.resolver.resolve(query, "phone")

        body = (await http.get("/api/resolver/first-seen?device=phone")).json()

    assert [row["device"] for row in body["names"]] == ["phone"]


@respx.mock
async def test_a_stranger_cannot_read_it() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.get("/api/resolver/first-seen")).status_code == 401


def test_each_new_name_carries_its_suspicion() -> None:
    seen = watcher(threshold=50)
    now = time.time()
    seen.note("laptop", "fonts.gstatic.com", "mock", now)
    seen.note("laptop", "xjw4k2qzp9v.com", "mock", now + 1)

    odd, plain = seen.recent()
    assert odd["name"] == "xjw4k2qzp9v.com"
    assert odd["suspicious"] is True
    assert odd["score"] >= 50
    assert "entropy" in odd["reasons"]
    assert plain == {
        "at": now,
        "device": "laptop",
        "name": "fonts.gstatic.com",
        "score": 0,
        "reasons": [],
        "suspicious": False,
    }
    assert seen.suspicious == 1
    assert seen.snapshot()["suspicious"] == 1
    assert seen.snapshot()["threshold"] == 50


def test_the_recent_list_can_be_narrowed_to_suspicious_names() -> None:
    seen = watcher()
    now = time.time()
    seen.note("laptop", "example.com", "mock", now)
    seen.note("laptop", "a1b2c3d4e5f6g7h8.top", "mock", now + 1)
    seen.note("phone", "kqgdpbvnhwfcz.xyz", "mock", now + 2)

    assert [r["name"] for r in seen.recent(suspicious=True)] == [
        "kqgdpbvnhwfcz.xyz",
        "a1b2c3d4e5f6g7h8.top",
    ]
    assert [r["name"] for r in seen.recent(device="laptop", suspicious=True)] == [
        "a1b2c3d4e5f6g7h8.top"
    ]


def test_the_threshold_decides_what_counts_as_suspicious() -> None:
    strict = watcher(threshold=100)
    now = time.time()
    strict.note("laptop", "xjw4k2qzp9v.com", "mock", now)
    strict.note("laptop", "a1b2c3d4e5f6g7h8.top", "mock", now)

    assert strict.suspicious == 1
    assert [r["name"] for r in strict.recent(suspicious=True)] == ["a1b2c3d4e5f6g7h8.top"]


def test_a_name_seen_during_warm_up_is_never_scored(monkeypatch) -> None:
    calls: list[str] = []
    original = firstseen.suspicion.score
    monkeypatch.setattr(
        firstseen.suspicion, "score", lambda name: calls.append(name) or original(name)
    )
    seen = firstseen.FirstSeen(warmup_s=3600)
    now = time.time()
    seen.note("laptop", "xjw4k2qzp9v.com", "mock", now)
    seen.note("laptop", "xjw4k2qzp9v.com", "mock", now + 4000)
    seen.note("laptop", "fresh.example", "mock", now + 4000)
    seen.note("laptop", "fresh.example", "mock", now + 4001)

    assert calls == ["fresh.example"]
    assert seen.suspicious == 0


def test_clearing_forgets_the_suspicious_count() -> None:
    seen = watcher()
    seen.note("laptop", "xjw4k2qzp9v.com", "mock", time.time())
    seen.clear()
    assert seen.suspicious == 0


@respx.mock
async def test_the_endpoint_scores_names_and_filters_to_suspicious_ones() -> None:
    import dns.message
    import dns.rdatatype

    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.metrics.first_seen.clear()
        app.state.metrics.first_seen._warmup_s = 0

        for name in ("example.com", "xjw4k2qzp9v.com"):
            query = dns.message.make_query(name, dns.rdatatype.A).to_wire()
            await app.state.resolver.resolve(query, "laptop")

        everything = (await http.get("/api/resolver/first-seen")).json()
        narrowed = (await http.get("/api/resolver/first-seen?suspicious=true")).json()

    rows = {row["name"]: row for row in everything["names"]}
    assert rows["example.com"]["score"] == 0
    assert rows["example.com"]["suspicious"] is False
    assert rows["xjw4k2qzp9v.com"]["suspicious"] is True
    assert "consonants" in rows["xjw4k2qzp9v.com"]["reasons"]
    assert [row["name"] for row in narrowed["names"]] == ["xjw4k2qzp9v.com"]
    assert everything["tracking"]["suspicious"] == 1
    assert everything["tracking"]["threshold"] == app.state.metrics.first_seen.threshold


@respx.mock
async def test_the_domain_sheet_carries_the_score() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        odd = (await http.get("/api/domain?name=xjw4k2qzp9v.com")).json()
        plain = (await http.get("/api/domain?name=fonts.gstatic.com")).json()

    assert odd["suspicion"]["score"] >= odd["suspicion"]["threshold"]
    assert "entropy" in odd["suspicion"]["reasons"]
    assert plain["suspicion"]["score"] == 0
    assert plain["suspicion"]["reasons"] == []
