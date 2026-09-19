from __future__ import annotations

import dataclasses

import dns.message
import dns.rdatatype
import respx
from test_doh import client, mock_upstream

from hydra_dns import designated
from hydra_dns.config import device_token, settings


def with_settings(**over):
    return dataclasses.replace(settings, **over)


def test_a_device_is_pointed_at_its_own_doh_url(monkeypatch) -> None:
    monkeypatch.setattr(designated, "settings", with_settings(dot_enabled=False, doq_enabled=False))
    records = designated.records_for("laptop")

    assert len(records) == 1
    assert f"/dns-query/{device_token('laptop')}{{?dns}}" in records[0]
    assert 'alpn="h2"' in records[0]


def test_dot_and_doq_are_offered_only_once_they_are_running(monkeypatch) -> None:
    monkeypatch.setattr(
        designated,
        "settings",
        with_settings(dot_enabled=True, doq_enabled=True, dot_base_domain="dot.example"),
    )
    token = device_token("laptop")

    designated.listening(dot=False, doq=False)
    try:
        assert len(designated.records_for("laptop")) == 1

        designated.listening(dot=True, doq=True)
        records = designated.records_for("laptop")
    finally:
        designated.listening(dot=False, doq=False)

    assert len(records) == 3
    assert f'2 {token}.dot.example. alpn="dot" port=853' in records[1]
    assert f'3 {token}.dot.example. alpn="doq" port=853' in records[2]


def test_a_listener_that_never_started_is_never_advertised(monkeypatch) -> None:
    monkeypatch.setattr(
        designated,
        "settings",
        with_settings(dot_enabled=True, doq_enabled=True, dot_base_domain="dot.example"),
    )

    designated.listening(dot=False, doq=False)
    records = designated.records_for("laptop")

    assert [r for r in records if "dot" in r or "doq" in r] == []
    assert designated.advertised() == {"dot": False, "doq": False}


def test_each_device_is_pointed_somewhere_of_its_own(monkeypatch) -> None:
    monkeypatch.setattr(
        designated,
        "settings",
        with_settings(dot_enabled=True, doq_enabled=False, dot_base_domain="dot.example"),
    )
    assert designated.records_for("laptop") != designated.records_for("phone")


def test_switching_it_off_offers_nothing(monkeypatch) -> None:
    monkeypatch.setattr(designated, "settings", with_settings(designated=False))
    assert designated.records_for("laptop") == []


def test_a_client_with_no_device_is_told_nothing(monkeypatch) -> None:
    monkeypatch.setattr(designated, "settings", with_settings(dot_enabled=False))
    assert designated.records_for("") == []


@respx.mock
async def test_the_discovery_query_is_answered_here() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        query = dns.message.make_query(designated.NAME, dns.rdatatype.SVCB)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    response = dns.message.from_wire(result.wire)
    assert result.rcode == "NOERROR"
    assert result.upstream == "designated"
    assert response.answer[0].rdtype == dns.rdatatype.SVCB
    assert device_token("laptop") in response.answer[0][0].to_text()
    assert respx.calls.call_count == 0


@respx.mock
async def test_nothing_to_offer_is_a_clean_empty_answer_not_a_failure(monkeypatch) -> None:
    from hydra_dns.main import app

    monkeypatch.setattr(designated, "settings", with_settings(designated=False))
    mock_upstream()
    async with client():
        query = dns.message.make_query(designated.NAME, dns.rdatatype.SVCB)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    response = dns.message.from_wire(result.wire)
    assert result.rcode == "NOERROR"
    assert not response.answer
    assert response.authority[0].rdtype == dns.rdatatype.SOA
    assert respx.calls.call_count == 0


@respx.mock
async def test_another_type_on_the_same_name_is_not_hijacked() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        query = dns.message.make_query(designated.NAME, dns.rdatatype.A)
        result = await app.state.resolver.resolve(query.to_wire(), "laptop")

    assert result.upstream == "mock"


@respx.mock
async def test_the_discovery_answer_is_not_counted_as_a_new_domain() -> None:
    from hydra_dns.main import app

    mock_upstream()
    async with client():
        app.state.metrics.first_seen.clear()
        app.state.metrics.first_seen._warmup_s = 0

        query = dns.message.make_query(designated.NAME, dns.rdatatype.SVCB)
        await app.state.resolver.resolve(query.to_wire(), "laptop")

        assert app.state.metrics.first_seen.counted == 0
