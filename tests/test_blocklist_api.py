from __future__ import annotations

import datetime as dt
import time

import httpx
import pytest
import respx
from test_blocklist_resolver import with_list
from test_blocklists import LIST_URL, PUBLIC_IP, FakeStore, row
from test_doh import client, mock_upstream

from hydra_dns import blocklists, watch
from hydra_dns.store import Device

PASSWORD = "hunter2"

BLOCKLIST_KEYS = {
    "id",
    "name",
    "url",
    "tags",
    "allow",
    "enabled",
    "created_at",
    "names",
    "skipped",
    "hits",
    "fetched_at",
    "last_error",
    "loaded",
    "reaches",
    "overlap",
    "unique",
}


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    async def resolve(host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    monkeypatch.setattr(blocklists, "addresses_of", resolve)


@pytest.fixture
def writable(monkeypatch):
    from hydra_dns.api import blocklists as routes

    monkeypatch.setattr(routes, "_guard_writable", lambda store: None)

    def attach(app, fake: FakeStore) -> FakeStore:
        app.state.blocklists._store = fake
        return fake

    return attach


async def signed_in(http) -> None:
    assert (await http.post("/api/login", json={"password": PASSWORD})).status_code == 200


@respx.mock
async def test_the_list_endpoint_has_the_contract_shape_and_is_read_only_without_a_registry():
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        app.state.devices._devices = [Device(id="tv", name="TV", tags=("kids",))]
        with_list(app.state.devices, tags=("kids",), lists=app.state.blocklists)
        body = (await http.get("/api/blocklists")).json()
        refused = await http.put(
            "/api/blocklists/ads", json={"name": "Ads", "url": LIST_URL, "tags": []}
        )

    assert set(body) == {
        "lists",
        "editable",
        "tags",
        "max_lists",
        "max_names",
        "total_names",
        "refresh_seconds",
    }
    assert body["editable"] is False
    assert body["tags"] == ["kids"]
    assert body["total_names"] == 2
    [listing] = body["lists"]
    assert set(listing) == BLOCKLIST_KEYS
    assert listing["reaches"] == ["tv"]
    assert listing["loaded"] is True
    assert refused.status_code == 503


@respx.mock
async def test_a_list_can_be_created_refreshed_excepted_and_removed(writable) -> None:
    from hydra_dns.main import app

    mock_upstream()
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text="||ads.example.com^\n"))
    async with client() as http:
        await signed_in(http)
        fake = writable(app, FakeStore([Device(id="tv", name="TV")]))

        created = await http.put(
            "/api/blocklists/ads",
            json={"name": "Ads", "url": LIST_URL, "tags": [], "allow": [], "enabled": True},
        )
        refreshed = await http.post("/api/blocklists/ads/refresh")
        allowed = await http.post(
            "/api/blocklists/ads/allow", json={"domain": "ok.ads.example.com", "action": "add"}
        )
        removed = await http.delete("/api/blocklists/ads")
        entries = app.state.audit.recent(limit=10)

    assert created.status_code == 200
    assert set(created.json()) == BLOCKLIST_KEYS
    assert refreshed.status_code == 200
    assert refreshed.json()["names"] == 1
    assert refreshed.json()["fetched_at"] is not None
    assert allowed.json()["allow"] == ["ok.ads.example.com"]
    assert fake.rows == []
    assert removed.json() == {"removed": True, "id": "ads"}
    detail = next(e for e in entries if e["action"].startswith("put:/api/blocklists"))
    assert detail["detail"]["url"] == LIST_URL


@respx.mock
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/blocklists/nope/refresh", None),
        ("post", "/api/blocklists/nope/allow", {"domain": "a.test", "action": "add"}),
        ("delete", "/api/blocklists/nope", None),
    ],
)
async def test_an_unknown_list_is_a_404(writable, method, path, body) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        writable(app, FakeStore())
        kwargs = {"json": body} if body is not None else {}
        response = await getattr(http, method)(path, **kwargs)

    assert response.status_code == 404


@respx.mock
async def test_a_bad_list_is_a_400_store_error(writable) -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        writable(app, FakeStore())
        response = await http.put(
            "/api/blocklists/ads", json={"name": "Ads", "url": "https://", "tags": []}
        )

    assert response.status_code in (400, 422)


@respx.mock
async def test_the_list_endpoint_carries_no_etag() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        response = await http.get("/api/blocklists")

    assert "etag" not in response.headers


@respx.mock
async def test_a_backup_carries_the_list_metadata_and_restores_it(writable) -> None:
    from hydra_dns.main import app

    mock_upstream()
    respx.get(LIST_URL).mock(return_value=httpx.Response(200, text="a.test\n"))
    async with client() as http:
        await signed_in(http)
        fake = writable(app, FakeStore(rows=[row(tags=["kids"], allow=["ok.test"])]))
        await app.state.blocklists.sync()
        backup = (await http.get("/api/backup", params={"accounts": "false"})).json()

        fake.rows = []
        await app.state.blocklists.sync()
        first = (await http.post("/api/restore", json=backup)).json()
        again = (await http.post("/api/restore", json=backup)).json()
        old = {key: value for key, value in backup.items() if key != "blocklists"}
        legacy = await http.post("/api/restore", json=old)

    assert backup["version"] == 1
    assert backup["blocklists"] == [
        {
            "id": "ads",
            "name": "Ads",
            "url": LIST_URL,
            "tags": ["kids"],
            "allow": ["ok.test"],
            "enabled": True,
        }
    ]
    assert first["restored"]["blocklists"] == 1
    assert again["restored"]["blocklists"] == 0
    assert [r["id"] for r in fake.rows] == ["ads"]
    assert fake.rows[0]["tags"] == ["kids"]
    assert legacy.status_code == 200


@respx.mock
async def test_the_scrape_carries_the_list_series() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        with_list(app.state.devices, lists=app.state.blocklists)
        app.state.blocklists.hit("ads")
        app.state.metrics_text = type(app.state.metrics_text)()
        text = (await http.get("/metrics")).text

    assert 'hydra_blocklist_hits_total{list="ads"} 1' in text
    assert 'hydra_blocklist_names{list="ads"} 2' in text


@respx.mock
async def test_health_reports_the_lists_for_resolver_internals() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        await signed_in(http)
        with_list(app.state.devices, lists=app.state.blocklists)
        app.state.blocklists.hit("ads")
        body = (await http.get("/api/health")).json()

    summary = body["blocklists"]
    assert summary["lists"] == 1
    assert summary["loaded"] == 1
    assert summary["names"] == 2
    assert summary["hits"] == 1


NOW = dt.datetime(2026, 9, 25, 12, 0, tzinfo=dt.UTC)


def reasons_for(lists: list[dict], refresh_s: float = 86_400) -> list[str]:
    return watch.reasons(
        accounts=[{"label": "one", "auth": {"authenticated": True}, "upstream": {}}],
        quota={},
        pool_state={},
        warn_days=5,
        now=NOW,
        blocklists=lists,
        blocklist_refresh_s=refresh_s,
    )


def health_row(**changes) -> dict:
    base = {
        "id": "ads",
        "name": "Ads",
        "enabled": True,
        "loaded": True,
        "tried": True,
        "fetched_at": NOW.timestamp() - 3600,
        "last_error": "",
    }
    return {**base, **changes}


def test_a_healthy_list_is_not_a_reason() -> None:
    assert reasons_for([health_row()]) == []


def test_a_list_that_has_never_loaded_is_a_reason() -> None:
    [reason] = reasons_for([health_row(loaded=False, fetched_at=None, last_error="HTTP 404")])

    assert "Ads has never loaded" in reason
    assert "HTTP 404" in reason


def test_a_list_not_yet_tried_is_not_a_reason_yet() -> None:
    assert reasons_for([health_row(loaded=False, tried=False, fetched_at=None)]) == []


def test_a_list_older_than_three_refreshes_is_a_reason() -> None:
    stale = health_row(fetched_at=NOW.timestamp() - 4 * 86_400, last_error="HTTP 503")

    [reason] = reasons_for([stale])

    assert "has not refreshed for 96 hours" in reason
    assert "HTTP 503" in reason


def test_a_copy_from_disk_that_cannot_refresh_is_a_reason() -> None:
    [reason] = reasons_for([health_row(fetched_at=None, last_error="could not resolve")])

    assert "no successful fetch on record" in reason


def test_a_disabled_list_is_never_a_reason() -> None:
    assert reasons_for([health_row(enabled=False, loaded=False, last_error="x")]) == []


@respx.mock
async def test_a_failing_list_reaches_the_status_monitor() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client() as http:
        with_list(app.state.devices, lists=app.state.blocklists)
        state = app.state.blocklists.get("ads")
        state.fetched_at = time.time() - 10 * 86_400
        state.last_error = "HTTP 500"
        status = (await http.get("/api/status")).json()
        await signed_in(http)
        attention = (await http.get("/api/health")).json()["attention"]

    assert status["state"] == watch.ATTENTION
    assert any("blocklist Ads has not refreshed" in reason for reason in attention)
