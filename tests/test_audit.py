from __future__ import annotations

import dns.message
import dns.rdatatype
import pytest
import respx
from test_doh import TOKEN, client, mock_upstream

from hydra_dns import audit
from hydra_dns.audit import AuditLog, action_for, outcome_for, redact

PASSWORD = "hunter2"


async def signed_in(http):
    await http.post("/api/login", json={"password": PASSWORD})


def test_a_password_never_reaches_the_trail() -> None:
    cleaned = redact({"username": "a@b.test", "password": "hunter2", "nested": {"token": "abc"}})
    assert cleaned["username"] == "a@b.test"
    assert cleaned["password"] == audit.REDACTED
    assert cleaned["nested"]["token"] == audit.REDACTED


def test_redaction_survives_lists_and_odd_types() -> None:
    cleaned = redact({"accounts": [{"password": "x"}], "count": 3, "when": None})
    assert cleaned["accounts"] == audit.REDACTED
    assert cleaned["count"] == 3
    assert cleaned["when"] is None


def test_a_long_string_is_trimmed() -> None:
    assert len(redact({"note": "x" * 5000})["note"]) == 500


def test_every_mutating_method_gets_an_action() -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert action_for(method, "/api/something/new")


def test_a_plain_read_is_not_audited() -> None:
    assert action_for("GET", "/api/overview") == ""


def test_the_reads_that_matter_are_audited() -> None:
    assert action_for("GET", "/api/backup") == "backup.download"
    assert action_for("GET", "/api/query-log/export") == "querylog.export"


def test_outcomes_separate_refusal_from_failure() -> None:
    assert outcome_for(200) == "ok"
    assert outcome_for(401) == "denied"
    assert outcome_for(403) == "denied"
    assert outcome_for(429) == "denied"
    assert outcome_for(500) == "failed"


def test_the_ring_holds_only_what_it_promises() -> None:
    trail = AuditLog(client=None)
    for index in range(audit.IN_MEMORY_ENTRIES + 50):
        trail.record(actor="dashboard", address="1.2.3.4", action="cache.clear", target=str(index))
    assert len(trail.recent(10_000)) == audit.IN_MEMORY_ENTRIES


def test_nothing_is_queued_for_postgres_when_it_is_off() -> None:
    trail = AuditLog(client=None)
    trail.record(actor="dashboard", address="1.2.3.4", action="cache.clear")
    assert trail.snapshot()["pending"] == 0


def test_entries_can_be_filtered() -> None:
    trail = AuditLog(client=None)
    trail.record(actor="dashboard", address="1.1.1.1", action="device.remove", target="laptop")
    trail.record(actor="token:ci", address="2.2.2.2", action="cache.clear", outcome="denied")

    assert len(trail.recent(actor="dashboard")) == 1
    assert len(trail.recent(action="cache.clear")) == 1
    assert len(trail.recent(outcome="denied")) == 1
    assert len(trail.recent(search="laptop")) == 1
    assert trail.actions() == ["cache.clear", "device.remove"]


@respx.mock
async def test_a_signed_in_change_is_recorded_against_the_session() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await http.post("/api/cache/clear")
        entries = http._transport.app.state.audit.recent(5)

    assert entries[0]["action"] == "cache.clear"
    assert entries[0]["actor"] == "dashboard"
    assert entries[0]["outcome"] == "ok"


@respx.mock
async def test_a_failed_sign_in_is_recorded() -> None:
    mock_upstream()
    async with client() as http:
        await http.post("/api/login", json={"password": "wrong"})
        entries = http._transport.app.state.audit.recent(5)

    assert entries[0]["action"] == "auth.login"
    assert entries[0]["outcome"] == "denied"
    assert entries[0]["status"] == 401


@respx.mock
async def test_a_refused_change_is_recorded_as_denied() -> None:
    mock_upstream()
    async with client() as http:
        await http.post("/api/cache/clear")
        entries = http._transport.app.state.audit.recent(5)

    assert entries[0]["action"] == "cache.clear"
    assert entries[0]["outcome"] == "denied"


@respx.mock
async def test_resolving_never_touches_the_trail() -> None:
    mock_upstream()
    query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
    async with client() as http:
        before = len(http._transport.app.state.audit.recent(10_000))
        for _ in range(5):
            await http.post(
                f"/dns-query/{TOKEN}",
                content=query,
                headers={"content-type": "application/dns-message"},
            )
        after = len(http._transport.app.state.audit.recent(10_000))

    assert before == after


@respx.mock
async def test_a_request_id_comes_back_on_the_api_but_not_on_dns() -> None:
    mock_upstream()
    query = dns.message.make_query("example.com", dns.rdatatype.A).to_wire()
    async with client() as http:
        api = await http.get("/api/health")
        dns_response = await http.post(
            f"/dns-query/{TOKEN}",
            content=query,
            headers={"content-type": "application/dns-message"},
        )

    assert api.headers.get("x-request-id")
    assert "x-request-id" not in dns_response.headers


@respx.mock
async def test_a_supplied_request_id_is_echoed() -> None:
    mock_upstream()
    async with client() as http:
        response = await http.get("/api/health", headers={"x-request-id": "abc123"})
    assert response.headers["x-request-id"] == "abc123"


@respx.mock
async def test_the_audit_endpoint_needs_a_session() -> None:
    mock_upstream()
    async with client() as http:
        anonymous = await http.get("/api/audit")
        await signed_in(http)
        allowed = await http.get("/api/audit")

    assert anonymous.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json()["source"] == "live"


@respx.mock
async def test_the_audit_endpoint_filters() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        await http.post("/api/cache/clear")
        matched = await http.get("/api/audit", params={"action": "cache.clear"})
        missed = await http.get("/api/audit", params={"action": "device.remove"})

    assert len(matched.json()["entries"]) >= 1
    assert missed.json()["entries"] == []


@pytest.mark.parametrize("field", ["actions", "actors", "store"])
@respx.mock
async def test_the_audit_endpoint_carries_its_filter_vocabulary(field: str) -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        payload = (await http.get("/api/audit")).json()
    assert field in payload


def test_scanner_noise_outside_the_api_is_not_an_action() -> None:
    assert action_for("POST", "/") == ""
    assert action_for("POST", "/wp-login.php") == ""
    assert action_for("DELETE", "/.env") == ""


def test_an_unmapped_api_mutation_is_still_named() -> None:
    assert action_for("POST", "/api/something-new") == "post:/api/something-new"


def test_a_request_that_reached_no_route_is_not_recorded() -> None:
    from hydra_dns.audit import worth_recording

    assert worth_recording("post:/api/nope", 404) is False
    assert worth_recording("post:/api/nope", 405) is False
    assert worth_recording("post:/api/nope", 422) is False


def test_a_real_refusal_is_still_recorded() -> None:
    from hydra_dns.audit import worth_recording

    assert worth_recording("device.remove", 403) is True
    assert worth_recording("device.remove", 401) is True
    assert worth_recording("device.remove", 500) is True
    assert worth_recording("device.remove", 200) is True


def test_a_sign_in_probe_is_recorded_whatever_it_returns() -> None:
    from hydra_dns.audit import worth_recording

    assert worth_recording("auth.login", 422) is True
    assert worth_recording("auth.login", 429) is True


def test_nothing_is_recorded_without_an_action() -> None:
    from hydra_dns.audit import worth_recording

    assert worth_recording("", 200) is False


@respx.mock
async def test_a_scanner_post_to_the_root_leaves_no_trace() -> None:
    mock_upstream()
    async with client() as http:
        trail = http._transport.app.state.audit
        before = len(trail.recent(10_000))
        for _ in range(5):
            await http.post("/", json={"probe": True})
        after = len(trail.recent(10_000))

    assert before == after


@respx.mock
async def test_a_post_to_a_route_that_does_not_exist_leaves_no_trace() -> None:
    mock_upstream()
    async with client() as http:
        await signed_in(http)
        trail = http._transport.app.state.audit
        before = len(trail.recent(10_000))
        await http.post("/api/definitely-not-a-route", json={})
        after = len(trail.recent(10_000))

    assert before == after


def test_a_preview_changes_nothing_so_it_is_not_an_action() -> None:
    assert action_for("POST", "/api/policy/preview") == ""


def test_the_write_beside_it_still_is() -> None:
    assert action_for("PUT", "/api/policy") == "policy.update"
    assert action_for("POST", "/api/policy/rule") == "policy.rule"
