from __future__ import annotations

import dns.message
import dns.rdatatype
import pytest
import respx
from test_doh import TOKEN, client, mock_upstream

from hydra_dns.resolver.doh import header_safe


@pytest.mark.parametrize(
    "label,expected",
    [
        ("mock", "mock"),
        ("stale (upstream)", "stale (upstream)"),
        ("device rule → 0.0.0.0", "device rule -> 0.0.0.0"),
        ("local zone → 127.0.0.1", "local zone -> 127.0.0.1"),
        ("café", "café"),
        ("em — dash", "em ? dash"),
    ],
)
def test_every_label_survives_the_trip_into_a_header(label, expected) -> None:
    safe = header_safe(label)
    assert safe == expected
    safe.encode("latin-1")


async def _over_doh(http, name: str, qtype: str = "A"):
    query = dns.message.make_query(name, dns.rdatatype.from_text(qtype))
    return await http.post(
        f"/dns-query/{TOKEN}",
        content=query.to_wire(),
        headers={"content-type": "application/dns-message"},
    )


@respx.mock
async def test_a_local_zone_answer_is_a_dns_answer_not_a_500() -> None:
    mock_upstream()
    async with client() as http:
        response = await _over_doh(http, "localhost")

    assert response.status_code == 200
    assert response.headers["x-hydra-upstream"] == "local zone -> 127.0.0.1"
    assert dns.message.from_wire(response.content).answer[0][0].to_text() == "127.0.0.1"


@respx.mock
async def test_a_device_rule_block_is_a_dns_answer_not_a_500() -> None:
    from hydra_dns.main import app
    from hydra_dns.store import Device

    mock_upstream()
    async with client() as http:
        app.state.devices._devices = [
            Device(id="laptop", name="Work Laptop", rules=("ads.example",))
        ]
        app.state.devices._compiled = {}
        response = await _over_doh(http, "img.ads.example")

    assert response.status_code == 200
    assert response.headers["x-hydra-upstream"] == "device rule -> 0.0.0.0"
    assert dns.message.from_wire(response.content).answer[0][0].to_text() == "0.0.0.0"


@respx.mock
async def test_an_account_label_that_is_not_latin_1_still_answers() -> None:
    from test_doh import UPSTREAM

    from hydra_dns.main import app
    from hydra_dns.resolver.upstream import Upstream

    mock_upstream()
    async with client() as http:
        app.state.pool.set_upstreams([Upstream(label="café — one", doh_url=UPSTREAM)])
        response = await _over_doh(http, "example.com")

    assert response.status_code == 200
    assert response.headers["x-hydra-upstream"] == "café ? one"
