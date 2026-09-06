from __future__ import annotations

import dataclasses
import time

import dns.message
import dns.rdatatype
import httpx
import pytest
import respx
from test_doh import client

PASSWORD = "hunter2"
ADGUARD_LOG = "https://api.adguard-dns.io/oapi/v1/query_log"


def record(device: str, name: str, *, blocked: bool = False, rcode: str = "NOERROR"):
    from hydra_dns.resolver.metrics import QueryRecord

    return QueryRecord(
        at=time.time(),
        device=device,
        name=name,
        qtype="A",
        rcode=rcode,
        cached=False,
        blocked=blocked,
        upstream="mock",
        latency_ms=9.0,
    )


async def signed_in(http: httpx.AsyncClient) -> None:
    await http.post("/api/login", json={"password": PASSWORD})


async def recent(http: httpx.AsyncClient, **params) -> dict:
    response = await http.get("/api/resolver/recent", params=params)
    assert response.status_code == 200
    return response.json()


@respx.mock
async def test_every_registered_device_is_listed_even_with_no_traffic() -> None:
    async with client() as http:
        await signed_in(http)
        body = await recent(http)

    assert {d["id"]: d["name"] for d in body["devices"]} == {
        "laptop": "Work Laptop",
        "phone": "Pixel",
    }
    assert all(d["registered"] and d["queries"] == 0 for d in body["devices"])


@respx.mock
async def test_the_busiest_device_is_offered_first() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("phone", "one.example"))
        app.state.metrics.record(record("phone", "two.example"))
        app.state.metrics.record(record("laptop", "three.example"))

        body = await recent(http)

    assert [d["id"] for d in body["devices"]] == ["phone", "laptop"]


@respx.mock
async def test_filtering_to_one_device_still_lists_the_others() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "one.example"))
        app.state.metrics.record(record("phone", "two.example"))

        filtered = await recent(http, device="laptop")

    assert [q["name"] for q in filtered["queries"]] == ["one.example"]
    assert {d["id"] for d in filtered["devices"]} == {"laptop", "phone"}


@respx.mock
async def test_a_device_with_traffic_but_no_registration_is_marked() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("anonymous", "one.example"))
        body = await recent(http)

    unknown = [d for d in body["devices"] if not d["registered"]]
    assert [d["id"] for d in unknown] == ["anonymous"]
    assert unknown[0]["queries"] == 1


@respx.mock
@pytest.mark.parametrize(
    "result,expected",
    [
        ("blocked", ["ads.example"]),
        ("allowed", ["good.example"]),
        ("error", ["gone.example"]),
        (None, ["gone.example", "ads.example", "good.example"]),
    ],
)
async def test_the_result_filter_splits_blocked_allowed_and_errors(result, expected) -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "good.example"))
        app.state.metrics.record(record("laptop", "ads.example", blocked=True))
        app.state.metrics.record(record("laptop", "gone.example", rcode="NXDOMAIN"))

        body = await recent(http, **({"result": result} if result else {}))

    assert [q["name"] for q in body["queries"]] == expected


@respx.mock
async def test_an_unknown_result_filter_is_rejected() -> None:
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/resolver/recent", params={"result": "maybe"})
    assert response.status_code == 422


@respx.mock
async def test_the_dashboards_own_domain_is_hidden_from_the_live_log() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "cdn.api.dns.example.test"))
        app.state.metrics.record(record("laptop", "api.dns.example.test"))
        app.state.metrics.record(record("laptop", "notapi.dns.example.test"))
        app.state.metrics.record(record("laptop", "good.example"))

        body = await recent(http)

    assert [q["name"] for q in body["queries"]] == ["good.example", "notapi.dns.example.test"]
    assert body["hidden_domains"] == ["api.dns.example.test"]


@respx.mock
async def test_hiding_a_domain_leaves_the_totals_alone() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "api.dns.example.test"))

        body = await recent(http)
        overview = await http.get("/api/overview")

    assert body["queries"] == []
    assert overview.json()["resolver"]["queries"] == 1
    assert [d for d in body["devices"] if d["id"] == "laptop"][0]["queries"] == 1


@respx.mock
async def test_the_hidden_list_is_configurable(monkeypatch) -> None:
    from hydra_dns.api import dashboard
    from hydra_dns.main import app

    monkeypatch.setattr(
        dashboard,
        "settings",
        dataclasses.replace(dashboard.settings, log_hidden_domains=["example"]),
    )

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "good.example"))
        app.state.metrics.record(record("laptop", "api.dns.example.test"))

        body = await recent(http)

    assert [q["name"] for q in body["queries"]] == ["api.dns.example.test"]
    assert body["hidden_domains"] == ["example"]


@respx.mock
async def test_hidden_domains_never_reach_the_adguard_log() -> None:
    respx.post("https://api.adguard-dns.io/oapi/v1/oauth_token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "t", "refresh_token": "r", "expires_in": 3600}
        )
    )
    respx.get(ADGUARD_LOG).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"domain": "api.dns.example.test", "time_millis": 3},
                    {"domain": "good.example", "time_millis": 2},
                    {"domain": "cdn.api.dns.example.test", "time_millis": 1},
                ],
                "pages": [],
            },
        )
    )

    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/query-log", params={"window": "24h"})

    body = response.json()
    assert response.status_code == 200
    assert [i["domain"] for i in body["items"]] == ["good.example", "good.example"]
    assert body["hidden_domains"] == ["api.dns.example.test"]


@respx.mock
async def test_a_total_upstream_failure_shows_up_in_the_log() -> None:
    from hydra_dns.main import app
    from hydra_dns.resolver.upstream import UpstreamError

    async with client() as http:
        await signed_in(http)

        async def dead(_wire):
            raise UpstreamError("every account refused")

        app.state.pool.forward = dead
        wire = dns.message.make_query("broken.example", dns.rdatatype.A).to_wire()
        await app.state.resolver.resolve(wire, "laptop")

        body = await recent(http)
        errors = await recent(http, result="error")
        overview = (await http.get("/api/overview")).json()

    assert [(q["name"], q["rcode"]) for q in body["queries"]] == [("broken.example", "SERVFAIL")]
    assert [q["name"] for q in errors["queries"]] == ["broken.example"]
    assert overview["resolver"]["errors"] == 1
    assert overview["resolver"]["queries"] == 1


@respx.mock
async def test_the_live_log_can_be_searched_by_domain() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "ads.doubleclick.net"))
        app.state.metrics.record(record("laptop", "github.com"))
        app.state.metrics.record(record("laptop", "static.doubleclick.net"))

        hit = await recent(http, q="doubleclick")
        miss = await recent(http, q="nothing-like-this")

    assert sorted(q["name"] for q in hit["queries"]) == [
        "ads.doubleclick.net",
        "static.doubleclick.net",
    ]
    assert miss["queries"] == []


@respx.mock
async def test_search_and_result_narrow_together() -> None:
    from hydra_dns.main import app

    async with client() as http:
        await signed_in(http)
        app.state.metrics.record(record("laptop", "ads.example", blocked=True))
        app.state.metrics.record(record("laptop", "ads.example"))

        body = await recent(http, q="ads", result="blocked")

    assert len(body["queries"]) == 1
    assert body["queries"][0]["blocked"] is True


@respx.mock
async def test_history_says_so_when_there_is_no_store() -> None:
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/resolver/history")

    body = response.json()
    assert response.status_code == 200
    assert body["available"] is False
    assert body["rows"] == []
