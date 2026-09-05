from __future__ import annotations

import base64
import re
from contextlib import asynccontextmanager

import dns.message
import dns.rcode
import dns.rdatatype
import httpx
import pytest
import respx
from conftest import make_answer

from hydra_dns.config import TOKEN_LENGTH, device_token, settings
from hydra_dns.main import app
from hydra_dns.resolver.upstream import Upstream

UPSTREAM = "https://upstream.test/dns-query"
FALLBACK_URL = "https://fallback.test/dns-query"
TOKEN = settings.seed_devices[0].token


@asynccontextmanager
async def client():
    async with app.router.lifespan_context(app):
        await app.state.manager.aclose()
        app.state.pool.set_upstreams([Upstream(label="mock", doh_url=UPSTREAM)])
        app.state.cache.clear()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
            yield http


def mock_upstream(wire: bytes | None = None, **kwargs) -> None:
    if wire is None:
        _, wire = make_answer()
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=wire, **kwargs))


def encode(wire: bytes) -> str:
    return base64.urlsafe_b64encode(wire).decode().rstrip("=")


@respx.mock
async def test_no_token_is_refused_when_anonymous_access_is_off() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.post(
            "/dns-query",
            content=dns.message.make_query("example.com", dns.rdatatype.A).to_wire(),
            headers={"content-type": "application/dns-message"},
        )
    assert response.status_code == 403


@respx.mock
async def test_a_wrong_token_is_refused_identically() -> None:
    mock_upstream()
    async with client() as http:
        wrong = await http.post("/dns-query/definitely-not-a-real-token", content=b"x")
        none = await http.post("/dns-query", content=b"x")
    assert wrong.status_code == none.status_code == 403
    assert wrong.content == none.content


@respx.mock
async def test_a_valid_token_resolves() -> None:
    mock_upstream()
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    async with client() as http:
        response = await http.post(
            f"/dns-query/{TOKEN}",
            content=query.to_wire(),
            headers={"content-type": "application/dns-message"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/dns-message"
    answer = dns.message.from_wire(response.content)
    assert answer.rcode() == dns.rcode.NOERROR
    assert "93.184.216.34" in answer.answer[0].to_text()


@respx.mock
async def test_get_with_base64url_wire_format() -> None:
    mock_upstream()
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    async with client() as http:
        response = await http.get(f"/dns-query/{TOKEN}", params={"dns": encode(query.to_wire())})

    assert response.status_code == 200
    assert dns.message.from_wire(response.content).rcode() == dns.rcode.NOERROR


@respx.mock
async def test_padded_base64_is_accepted_even_though_the_rfc_forbids_it() -> None:
    mock_upstream()
    wire = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
    padded = base64.urlsafe_b64encode(wire).decode()
    assert padded.endswith("=")
    async with client() as http:
        response = await http.get(f"/dns-query/{TOKEN}", params={"dns": padded})
    assert response.status_code == 200


@respx.mock
async def test_json_form_returns_the_google_shape() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.get(
            f"/dns-query/{TOKEN}", params={"name": "example.com", "type": "A"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["StatusName"] == "NOERROR"
    assert body["Answer"][0]["data"] == "93.184.216.34"
    assert body["Hydra"]["upstream"] == "mock"


@respx.mock
async def test_the_query_id_is_echoed_back() -> None:
    mock_upstream()
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    query.id = 4242
    async with client() as http:
        response = await http.post(f"/dns-query/{TOKEN}", content=query.to_wire())
    assert dns.message.from_wire(response.content).id == 4242


@respx.mock
async def test_a_repeat_query_never_reaches_the_upstream() -> None:
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()

    async with client() as http:
        first = await http.post(f"/dns-query/{TOKEN}", content=query)
        second = await http.post(f"/dns-query/{TOKEN}", content=query)

    assert route.call_count == 1
    assert first.headers["x-hydra-cache"] == "MISS"
    assert second.headers["x-hydra-cache"] == "HIT"


@respx.mock
async def test_cache_control_carries_the_ttl() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.post(
            f"/dns-query/{TOKEN}",
            content=dns.message.make_query("example.com", dns.rdatatype.A).to_wire(),
        )
    assert response.headers["cache-control"] == "max-age=300"


@respx.mock
async def test_any_queries_are_refused_without_being_forwarded() -> None:
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    query = dns.message.make_query("example.com", dns.rdatatype.ANY)

    async with client() as http:
        response = await http.post(f"/dns-query/{TOKEN}", content=query.to_wire())

    assert route.call_count == 0
    assert dns.message.from_wire(response.content).rcode() == dns.rcode.REFUSED


@respx.mock
async def test_an_upstream_outage_becomes_servfail_not_a_500() -> None:
    respx.post(UPSTREAM).mock(side_effect=httpx.ConnectError("upstream down"))
    async with client() as http:
        from hydra_dns.main import app

        app.state.pool.set_fallback("")
        response = await http.post(
            f"/dns-query/{TOKEN}",
            content=dns.message.make_query("example.com", dns.rdatatype.A).to_wire(),
        )

    assert response.status_code == 200
    assert dns.message.from_wire(response.content).rcode() == dns.rcode.SERVFAIL


@respx.mock
async def test_servfail_is_not_cached() -> None:
    route = respx.post(UPSTREAM).mock(side_effect=httpx.ConnectError("down"))
    query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()

    async with client() as http:
        from hydra_dns.main import app

        app.state.pool.set_fallback("")
        await http.post(f"/dns-query/{TOKEN}", content=query)
        route.side_effect = None
        route.return_value = httpx.Response(200, content=make_answer()[1])
        second = await http.post(f"/dns-query/{TOKEN}", content=query)

    assert dns.message.from_wire(second.content).rcode() == dns.rcode.NOERROR


@respx.mock
async def test_garbage_bodies_are_rejected_at_the_edge() -> None:
    mock_upstream()
    async with client() as http:
        empty = await http.post(f"/dns-query/{TOKEN}", content=b"")
        junk = await http.post(f"/dns-query/{TOKEN}", content=b"not a dns message at all")
        missing = await http.get(f"/dns-query/{TOKEN}")

    assert empty.status_code == 400
    assert junk.status_code == 400
    assert missing.status_code == 400


@respx.mock
async def test_oversized_queries_are_rejected() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.post(f"/dns-query/{TOKEN}", content=b"x" * 5000)
    assert response.status_code == 413


@respx.mock
async def test_health_is_cheap_and_unauthenticated() -> None:
    async with client() as http:
        response = await http.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@respx.mock
async def test_dashboard_routes_require_a_session() -> None:
    async with client() as http:
        for path in ("/api/overview", "/api/accounts", "/api/devices", "/api/resolver"):
            assert (await http.get(path)).status_code == 401, path


@respx.mock
async def test_login_then_read_the_dashboard() -> None:
    async with client() as http:
        assert (await http.post("/api/login", json={"password": "wrong"})).status_code == 401
        assert (await http.post("/api/login", json={"password": "hunter2"})).status_code == 200
        overview = await http.get("/api/overview")

    assert overview.status_code == 200
    assert overview.json()["strategy"] == settings.strategy


@respx.mock
async def test_mobileconfig_is_served_without_a_session() -> None:
    async with client() as http:
        response = await http.get(f"/setup/{TOKEN}/hydra.mobileconfig")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-apple-aspen-config")
    body = response.text
    assert "com.apple.dnsSettings.managed" in body
    assert f"/dns-query/{TOKEN}" in body
    assert "<key>DNSProtocol</key>" in body


@respx.mock
async def test_mobileconfig_uuid_is_stable_across_downloads() -> None:
    async with client() as http:
        first = (await http.get(f"/setup/{TOKEN}/hydra.mobileconfig")).text
        second = (await http.get(f"/setup/{TOKEN}/hydra.mobileconfig")).text
    assert first == second


@respx.mock
async def test_unknown_device_profile_is_a_404() -> None:
    async with client() as http:
        response = await http.get("/setup/nope/hydra.mobileconfig")
    assert response.status_code == 404


@respx.mock
async def test_the_setup_page_is_served_without_a_session() -> None:
    async with client() as http:
        response = await http.get(f"/setup/{TOKEN}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "script-src" not in response.headers["content-security-policy"]
    assert "noindex" in response.headers["x-robots-tag"]
    body = response.text
    assert f'href="/setup/{TOKEN}/hydra.mobileconfig"' in body
    assert f"/dns-query/{TOKEN}" in body
    assert "<script" not in body


def test_the_setup_page_escapes_the_device_name() -> None:
    from hydra_dns.api import setup as setup_api
    from hydra_dns.store import Device

    device = Device(id="kid", name="<b>Kid's</b> phone")
    page = setup_api._setup_page(device)

    assert "<b>Kid" not in page
    assert "&lt;b&gt;Kid&#x27;s&lt;/b&gt; phone" in page


@respx.mock
async def test_unknown_device_setup_page_is_a_404() -> None:
    async with client() as http:
        response = await http.get("/setup/nope")
    assert response.status_code == 404


def test_the_device_list_carries_the_setup_link() -> None:
    from hydra_dns.api import setup as setup_api
    from hydra_dns.store import Device

    device = Device(id="laptop", name="Laptop")
    payload = setup_api._device_payload(device)

    assert payload["setup_url"] == f"{settings.public_api_base}/setup/{device.token}"
    assert payload["mobileconfig_url"] == f"{payload['setup_url']}/hydra.mobileconfig"


@pytest.mark.parametrize("device_id", ["laptop", "phone"])
def test_device_tokens_are_distinct_and_derived(device_id: str) -> None:
    device = next(d for d in settings.seed_devices if d.id == device_id)
    assert len(device.token) == TOKEN_LENGTH
    assert device_token(device_id) == device.token


def test_a_token_is_usable_as_a_dns_label() -> None:
    label = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
    for device in settings.seed_devices:
        assert label.match(device.token), device.token


def test_tokens_differ_between_devices() -> None:
    tokens = {d.token for d in settings.seed_devices}
    assert len(tokens) == len(settings.seed_devices)


@respx.mock
async def test_a_device_keeps_resolving_when_every_account_is_spent() -> None:
    respx.post(UPSTREAM).mock(side_effect=httpx.ConnectError("upstream down"))
    respx.post(FALLBACK_URL).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    async with client() as http:
        from hydra_dns.main import app

        app.state.pool.set_fallback(FALLBACK_URL)
        response = await http.post(
            f"/dns-query/{TOKEN}",
            content=dns.message.make_query("example.com", dns.rdatatype.A).to_wire(),
        )

    assert response.status_code == 200
    answer = dns.message.from_wire(response.content)
    assert answer.rcode() == dns.rcode.NOERROR
    assert answer.answer


@respx.mock
async def test_the_fallback_answer_is_not_cached_over_the_real_one() -> None:
    respx.post(UPSTREAM).mock(side_effect=httpx.ConnectError("down"))
    respx.post(FALLBACK_URL).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()

    async with client() as http:
        from hydra_dns.main import app

        app.state.pool.set_fallback(FALLBACK_URL)
        await http.post(f"/dns-query/{TOKEN}", content=query)
        assert app.state.cache.snapshot()["entries"] == 0
