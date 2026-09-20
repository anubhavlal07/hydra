from __future__ import annotations

import dataclasses

import httpx
import pytest
import respx

from hydra_dns import alerts as alerts_module
from hydra_dns import tokens as tokens_module
from hydra_dns.alerts import AlertDispatcher
from hydra_dns.config import settings
from hydra_dns.tokens import PREFIX, TokenStore, bearer, digest_of, mint

WEBHOOK = "https://hooks.test/hydra"


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(
        alerts_module, "settings", dataclasses.replace(settings, alert_webhook=WEBHOOK)
    )
    return WEBHOOK


def dispatcher(reasons: list[str], client: httpx.AsyncClient | None = None) -> AlertDispatcher:
    return AlertDispatcher(client, lambda: list(reasons))


def test_a_new_reason_opens_once() -> None:
    sender = dispatcher([])
    first = sender.evaluate(["the pool is spent"])
    second = sender.evaluate(["the pool is spent"])

    assert first["opened"] == ["the pool is spent"]
    assert second["opened"] == []
    assert second["repeated"] == []


def test_a_reason_that_goes_away_resolves() -> None:
    sender = dispatcher([])
    sender.evaluate(["the pool is spent"])
    changes = sender.evaluate([])

    assert changes["resolved"] == ["the pool is spent"]
    assert sender.snapshot()["active"] == 0


def test_a_standing_reason_repeats_only_after_the_interval(monkeypatch) -> None:
    monkeypatch.setattr(
        alerts_module, "settings", dataclasses.replace(settings, alert_repeat_s=3600)
    )
    sender = dispatcher([])
    sender.evaluate(["certificate expires in 3 days"], now=0)
    sender._sent_at["certificate expires in 3 days"] = 0

    assert sender.evaluate(["certificate expires in 3 days"], now=60)["repeated"] == []
    assert sender.evaluate(["certificate expires in 3 days"], now=7200)["repeated"] == [
        "certificate expires in 3 days"
    ]


def test_a_reason_that_resolves_forgets_it_was_sent() -> None:
    sender = dispatcher([])
    sender.evaluate(["a"], now=0)
    sender._sent_at["a"] = 0
    sender.evaluate([], now=10)

    assert sender.evaluate(["a"], now=20)["opened"] == ["a"]


def test_the_tracked_set_is_bounded() -> None:
    sender = dispatcher([])
    sender.evaluate([f"reason {i}" for i in range(500)])
    assert sender.snapshot()["active"] == alerts_module.MAX_TRACKED


def test_nothing_is_posted_without_a_webhook() -> None:
    sender = dispatcher(["something"])
    assert sender.configured is False


@respx.mock
async def test_an_opened_reason_is_delivered(wired) -> None:
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        sender = dispatcher(["the pool is spent"], client)
        await sender.dispatch()

    assert route.called
    body = route.calls[0].request.read().decode()
    assert "the pool is spent" in body
    assert '"event":"attention"' in body.replace(" ", "")


@respx.mock
async def test_a_resolved_reason_is_delivered(wired) -> None:
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200))
    reasons = ["the pool is spent"]
    async with httpx.AsyncClient() as client:
        sender = dispatcher(reasons, client)
        await sender.dispatch()
        reasons.clear()
        await sender.dispatch()

    assert route.call_count == 2
    assert "resolved" in route.calls[1].request.read().decode()


@respx.mock
async def test_a_quiet_service_sends_nothing(wired) -> None:
    route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await dispatcher([], client).dispatch()
    assert not route.called


@respx.mock
async def test_a_webhook_that_refuses_is_counted_not_raised(wired) -> None:
    respx.post(WEBHOOK).mock(return_value=httpx.Response(500, text="nope"))
    async with httpx.AsyncClient() as client:
        sender = dispatcher(["something"], client)
        await sender.dispatch()

    assert sender.snapshot()["failures"] == 1
    assert sender.snapshot()["last_error"]


@respx.mock
async def test_a_webhook_that_is_unreachable_is_counted_not_raised(wired) -> None:
    respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("no route"))
    async with httpx.AsyncClient() as client:
        sender = dispatcher(["something"], client)
        await sender.dispatch()

    assert sender.snapshot()["failures"] == 1


def test_a_minted_token_is_recognisable_and_hashed() -> None:
    plaintext, token_id, digest = mint()
    assert plaintext.startswith(PREFIX)
    assert len(token_id) == 8
    assert len(digest) == 64
    assert digest == digest_of(plaintext)
    assert plaintext not in digest


def test_two_tokens_never_collide() -> None:
    assert len({mint()[0] for _ in range(200)}) == 200


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer hyd_abc", "hyd_abc"),
        ("bearer hyd_abc", "hyd_abc"),
        ("  Bearer   hyd_abc  ", "hyd_abc"),
        ("Basic hyd_abc", ""),
        ("", ""),
    ],
)
def test_a_bearer_header_is_read_forgivingly(header: str, expected: str) -> None:
    assert bearer(header) == expected


def test_a_store_verifies_only_what_it_holds() -> None:
    store = TokenStore(client=None)
    plaintext, token_id, digest = mint()
    store._tokens = {
        token_id: tokens_module.MachineToken(id=token_id, name="ci", scope="read", digest=digest)
    }

    assert store.verify(plaintext).name == "ci"
    assert store.verify(mint()[0]) is None
    assert store.verify("not-a-token") is None


def test_a_token_is_touched_at_most_once_an_interval() -> None:
    store = TokenStore(client=None)
    token = tokens_module.MachineToken(id="abcdef12", name="ci", scope="read", digest="0" * 64)

    assert store.note_use(token, now=0) is True
    assert store.note_use(token, now=10) is False
    assert store.note_use(token, now=tokens_module.TOUCH_EVERY_S + 10) is True


def test_an_unconfigured_store_reports_itself() -> None:
    store = TokenStore(client=None)
    snapshot = store.snapshot()
    assert snapshot["configured"] is False
    assert snapshot["count"] == 0


@pytest.fixture
def wired_tokens(monkeypatch):
    from hydra_dns.main import app
    from hydra_dns.tokens import MachineToken

    def install(scope: str) -> str:
        plaintext, token_id, digest = mint()
        store = app.state.tokens
        store._tokens = {token_id: MachineToken(id=token_id, name="ci", scope=scope, digest=digest)}
        return plaintext

    return install


@respx.mock
async def test_a_metrics_token_reads_metrics_without_a_session(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        secret = wired_tokens("metrics")
        refused = await http.get("/metrics")
        allowed = await http.get("/metrics", headers={"authorization": f"Bearer {secret}"})

    assert refused.status_code == 401
    assert allowed.status_code == 200
    assert "hydra_queries_total" in allowed.text


@respx.mock
async def test_a_metrics_token_cannot_read_the_dashboard(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        secret = wired_tokens("metrics")
        response = await http.get("/api/overview", headers={"authorization": f"Bearer {secret}"})

    assert response.status_code == 401


@respx.mock
async def test_a_read_token_reads_the_dashboard(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        secret = wired_tokens("read")
        response = await http.get("/api/overview", headers={"authorization": f"Bearer {secret}"})

    assert response.status_code == 200


@respx.mock
async def test_a_read_token_cannot_change_anything(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        secret = wired_tokens("read")
        response = await http.post(
            "/api/cache/clear", headers={"authorization": f"Bearer {secret}"}
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "read_only"


@respx.mock
async def test_a_token_use_is_noted_without_a_write_per_request(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        secret = wired_tokens("read")
        for _ in range(5):
            await http.get("/api/overview", headers={"authorization": f"Bearer {secret}"})
        pending = len(http._transport.app.state.tokens._pending_touch)

    assert pending == 1


@respx.mock
async def test_a_revoked_token_stops_working(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        secret = wired_tokens("read")
        before = await http.get("/api/overview", headers={"authorization": f"Bearer {secret}"})
        http._transport.app.state.tokens._tokens = {}
        after = await http.get("/api/overview", headers={"authorization": f"Bearer {secret}"})

    assert before.status_code == 200
    assert after.status_code == 401


@respx.mock
async def test_a_made_up_token_is_refused(wired_tokens) -> None:
    from test_doh import client, mock_upstream

    mock_upstream()
    async with client() as http:
        wired_tokens("read")
        response = await http.get("/api/overview", headers={"authorization": "Bearer hyd_deadbeef"})

    assert response.status_code == 401
