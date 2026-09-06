from __future__ import annotations

import dataclasses

import dns.message
import dns.rdatatype
import pytest
import respx
from test_doh import TOKEN, client, mock_upstream

from hydra_dns.api.session import GLOBAL_MAX_ATTEMPTS, MAX_ATTEMPTS, LoginLimiter

PASSWORD = "hunter2"


@pytest.fixture(autouse=True)
def fresh_limiter(monkeypatch):
    from hydra_dns.api import session

    monkeypatch.setattr(session, "_limiter", LoginLimiter())


def test_a_few_failures_are_tolerated() -> None:
    limiter = LoginLimiter()
    for _ in range(MAX_ATTEMPTS - 1):
        limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 0


def test_too_many_failures_lock_the_address_out() -> None:
    limiter = LoginLimiter()
    for _ in range(MAX_ATTEMPTS):
        limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") > 0


def test_a_lockout_is_per_address() -> None:
    limiter = LoginLimiter()
    for _ in range(MAX_ATTEMPTS):
        limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("5.6.7.8") == 0


def test_a_successful_sign_in_clears_the_count() -> None:
    limiter = LoginLimiter()
    for _ in range(MAX_ATTEMPTS - 1):
        limiter.record_failure("1.2.3.4")
    limiter.record_success("1.2.3.4")
    for _ in range(MAX_ATTEMPTS - 1):
        limiter.record_failure("1.2.3.4")
    assert limiter.retry_after("1.2.3.4") == 0


def test_a_global_cap_catches_guesses_spread_over_many_addresses() -> None:
    limiter = LoginLimiter()
    for i in range(GLOBAL_MAX_ATTEMPTS):
        limiter.record_failure(f"10.0.0.{i % 255}")
    assert limiter.retry_after("192.168.1.1") > 0


@respx.mock
async def test_repeated_wrong_passwords_stop_being_answered() -> None:
    mock_upstream()
    async with client() as http:
        codes = [
            (await http.post("/api/login", json={"password": f"guess{i}"})).status_code
            for i in range(MAX_ATTEMPTS + 2)
        ]

    assert codes[0] == 401, "the first attempt is answered normally"
    assert 429 in codes, "guessing must stop being free at some point"
    assert codes[-1] == 429


@respx.mock
async def test_a_lockout_says_when_to_come_back() -> None:
    mock_upstream()
    async with client() as http:
        for i in range(MAX_ATTEMPTS + 1):
            response = await http.post("/api/login", json={"password": f"g{i}"})

    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0


@respx.mock
async def test_the_right_password_still_works_before_a_lockout() -> None:
    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": "wrong"})
        response = await http.post("/api/login", json={"password": PASSWORD})
    assert response.status_code == 200


@respx.mock
async def test_health_tells_a_stranger_almost_nothing() -> None:
    mock_upstream()
    async with client() as http:
        body = (await http.get("/api/health")).json()

    assert set(body) == {"status", "uptime_s"}
    flat = str(body)
    assert "dot" not in flat
    assert "accounts" not in flat


@respx.mock
async def test_health_is_still_usable_as_a_monitor() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.get("/api/health")
    assert response.status_code == 200
    assert '"status":"ok"' in response.text.replace(" ", "")


@respx.mock
async def test_health_is_detailed_once_signed_in() -> None:
    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        body = (await http.get("/api/health")).json()

    assert "dot" in body
    assert "resolver" in body
    assert "accounts" in body


@respx.mock
async def test_the_openapi_schema_is_not_published() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.get("/openapi.json")).status_code == 404
        assert (await http.get("/docs")).status_code == 404


@respx.mock
async def test_the_root_says_only_what_it_must() -> None:
    mock_upstream()
    async with client() as http:
        body = (await http.get("/")).json()
    assert set(body) == {"service"}


@respx.mock
async def test_security_headers_are_set() -> None:
    mock_upstream()
    async with client() as http:
        headers = (await http.get("/api/health")).headers

    assert "max-age=" in headers["strict-transport-security"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "default-src 'none'" in headers["content-security-policy"]


@respx.mock
async def test_api_answers_are_never_stored_by_an_intermediary() -> None:
    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": PASSWORD})
        assert (await http.get("/api/devices")).headers["cache-control"] == "no-store"


@respx.mock
async def test_dns_answers_keep_their_own_cache_control() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.post(
            f"/dns-query/{TOKEN}",
            content=dns.message.make_query("example.com", dns.rdatatype.A).to_wire(),
        )
    assert response.headers["cache-control"] == "max-age=300"


@respx.mock
async def test_the_resolver_is_still_closed_to_strangers() -> None:
    mock_upstream()
    async with client() as http:
        assert (await http.get("/dns-query", params={"name": "example.com"})).status_code == 403
        assert (
            await http.get("/dns-query/nope", params={"name": "example.com"})
        ).status_code == 403


def test_every_route_method_survives_a_preflight() -> None:
    from hydra_dns.main import CORS_METHODS, app

    used = {
        method
        for route in app.routes
        for method in getattr(route, "methods", set()) or set()
        if method != "HEAD"
    }
    assert used <= set(CORS_METHODS), (
        f"not allowed through CORS: {sorted(used - set(CORS_METHODS))}"
    )


@respx.mock
async def test_a_preflight_for_a_policy_write_is_answered() -> None:
    from hydra_dns.config import settings

    mock_upstream()
    async with client() as http:
        response = await http.request(
            "OPTIONS",
            "/api/policy",
            headers={
                "origin": settings.cors_origins[0],
                "access-control-request-method": "PUT",
                "access-control-request-headers": "content-type",
            },
        )
    assert response.status_code == 200, response.text
    assert "PUT" in response.headers["access-control-allow-methods"]


@respx.mock
async def test_health_says_degraded_when_no_account_can_answer() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        for upstream in app.state.pool.upstreams:
            upstream.quota_limit = 300_000
            upstream.quota_used = 300_000
        body = (await http.get("/api/health")).json()

    assert body["status"] == "degraded"
    assert '"status":"ok"' not in str(body).replace(" ", "")


@respx.mock
async def test_degraded_health_still_leaks_nothing() -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns.main import app

        for upstream in app.state.pool.upstreams:
            upstream.quota_limit = 300_000
            upstream.quota_used = 300_000
        body = (await http.get("/api/health")).json()

    assert set(body) == {"status", "uptime_s"}


@respx.mock
async def test_health_is_degraded_when_an_enabled_listener_is_not_running(monkeypatch) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns import main

        monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, dot_enabled=True))
        main.app.state.dot = None
        body = (await http.get("/api/health")).json()

    assert body["status"] == "degraded"
    assert '"status":"ok"' not in str(body).replace(" ", "")


@respx.mock
async def test_a_silent_listener_still_leaks_nothing(monkeypatch) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns import main

        monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, doq_enabled=True))
        main.app.state.doq = None
        body = (await http.get("/api/health")).json()

    assert set(body) == {"status", "uptime_s"}
    assert "doq" not in str(body)


@respx.mock
async def test_a_silent_listener_reaches_status_and_the_dashboard(monkeypatch) -> None:
    mock_upstream()
    async with client() as http:
        from hydra_dns import main

        monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, dot_enabled=True))
        main.app.state.dot = None
        state = (await http.get("/api/status")).json()

        await http.post("/api/login", json={"password": PASSWORD})
        detail = (await http.get("/api/health")).json()

    assert state == {"state": "attention"}
    assert any("not listening" in reason for reason in detail["attention"])


PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/status"),
    ("POST", "/api/login"),
    ("POST", "/api/logout"),
    ("GET", "/api/session"),
    ("GET", "/setup/{token}"),
    ("GET", "/setup/{token}/hydra.mobileconfig"),
}


def _declared_routes():
    from hydra_dns.api import blocklists, dashboard, ops, session, setup

    for router in (
        session.router,
        dashboard.router,
        ops.router,
        setup.router,
        blocklists.router,
    ):
        for route in router.routes:
            path = getattr(route, "path", "")
            if not path.startswith(("/api", "/setup")):
                continue
            for method in sorted((getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"}):
                yield method, path, route


def _guards() -> set:
    from hydra_dns.api.access import require_metrics, require_read, require_write
    from hydra_dns.api.session import require_session

    return {require_read, require_write, require_metrics, require_session}


def test_every_api_route_is_behind_a_guard() -> None:
    guards = _guards()
    seen = list(_declared_routes())
    assert len(seen) > 30, "no routes were discovered, so this test is proving nothing"

    open_to_all = [
        f"{method} {path}"
        for method, path, route in seen
        if (method, path) not in PUBLIC
        and not any(
            getattr(dependency, "dependency", None) in guards
            for dependency in getattr(route, "dependencies", [])
        )
    ]

    assert not open_to_all, f"reachable without signing in: {', '.join(open_to_all)}"


@respx.mock
async def test_a_guarded_route_really_answers_401() -> None:
    mock_upstream()
    async with client() as http:
        for target in (
            "/api/rule-groups",
            "/api/devices",
            "/api/overview",
            "/api/audit",
            "/api/blocklists",
            "/api/domain?name=example.com",
        ):
            assert (await http.get(target)).status_code == 401, target


@respx.mock
async def test_the_scoped_rule_writes_need_a_session() -> None:
    mock_upstream()
    entry = {"domain": "ads.example", "action": "block"}
    async with client() as http:
        for target in (
            "/api/devices/laptop/rules/entry",
            "/api/rule-groups/bedtime/rules/entry",
        ):
            assert (await http.post(target, json=entry)).status_code == 401, target


def test_the_domain_sheet_routes_are_guarded_and_never_conditional() -> None:
    from hydra_dns.api.access import require_read, require_write
    from hydra_dns.main import CONDITIONAL

    wanted = {
        ("GET", "/api/domain"): require_read,
        ("POST", "/api/devices/{device_id}/rules/entry"): require_write,
        ("POST", "/api/rule-groups/{group_id}/rules/entry"): require_write,
    }
    found = {
        (method, path): {getattr(d, "dependency", None) for d in route.dependencies}
        for method, path, route in _declared_routes()
        if (method, path) in wanted
    }
    assert set(found) == set(wanted)
    for key, guard in wanted.items():
        assert guard in found[key], key
    assert "/api/domain" not in CONDITIONAL
