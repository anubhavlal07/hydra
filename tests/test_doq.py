from __future__ import annotations

import asyncio
import dataclasses
import ssl

import dns.message
import dns.rcode
import dns.rdatatype
import httpx
import pytest
import respx
from aioquic.asyncio import connect
from aioquic.quic.configuration import QuicConfiguration
from conftest import make_answer
from test_dot import BASE_DOMAIN, write_wildcard_cert

from hydra_dns.config import DeviceConfig, device_token
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.doq import ALPN, DoqServer
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamPool
from hydra_dns.store import DeviceStore

UPSTREAM = "https://upstream.test/dns-query"
SEED = [DeviceConfig(id="laptop", name="Laptop"), DeviceConfig(id="phone", name="Phone")]


def free_udp_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class DoqHarness:
    def __init__(self, server: DoqServer, port: int, cert_file: str) -> None:
        self.server = server
        self.port = port
        self.cert_file = cert_file

    def _configuration(self, hostname: str) -> QuicConfiguration:
        configuration = QuicConfiguration(is_client=True, alpn_protocols=ALPN)
        configuration.load_verify_locations(cafile=self.cert_file)
        configuration.server_name = hostname
        configuration.verify_mode = ssl.CERT_REQUIRED
        configuration.idle_timeout = 10.0
        return configuration

    async def ask(self, hostname: str, name: str = "example.com", timeout: float = 10.0):
        async with connect(
            "127.0.0.1",
            self.port,
            configuration=self._configuration(hostname),
        ) as protocol:
            await protocol.wait_connected()
            reader, writer = await protocol.create_stream()

            query = dns.message.make_query(name, dns.rdatatype.A)
            query.id = 0
            wire = query.to_wire()
            writer.write(len(wire).to_bytes(2, "big") + wire)
            writer.write_eof()

            body = await asyncio.wait_for(reader.read(), timeout=timeout)
            if len(body) < 2:
                return None
            length = int.from_bytes(body[:2], "big")
            return dns.message.from_wire(body[2 : 2 + length])


@pytest.fixture
async def doq(tmp_path, monkeypatch):
    from hydra_dns import store as store_module
    from hydra_dns.resolver import doq as doq_module

    cert_file, key_file = write_wildcard_cert(tmp_path)
    port = free_udp_port()

    tweaked = dataclasses.replace(
        doq_module.settings,
        doq_enabled=True,
        doq_host="127.0.0.1",
        doq_port=port,
        dot_base_domain=BASE_DOMAIN,
        dot_cert_file=cert_file,
        dot_key_file=key_file,
        store_url="",
    )
    monkeypatch.setattr(doq_module, "settings", tweaked)
    monkeypatch.setattr(store_module, "settings", tweaked)

    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="mock", doh_url=UPSTREAM)])

    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    metrics = Metrics()
    resolver = Resolver(cache, pool, metrics)
    store = DeviceStore(client, SEED)

    server = DoqServer(resolver, store, metrics)
    await server.start()

    yield DoqHarness(server, port, cert_file)

    await server.aclose()
    await client.aclose()


def hostname_for(device_id: str) -> str:
    return f"{device_token(device_id)}.{BASE_DOMAIN}"


@respx.mock
async def test_a_known_token_resolves_over_quic(doq) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    answer = await doq.ask(hostname_for("laptop"))

    assert answer is not None
    assert answer.rcode() == dns.rcode.NOERROR
    assert str(answer.answer[0][0]) == "93.184.216.34"
    assert doq.server.stats.queries == 1


@respx.mock
async def test_the_device_is_taken_from_the_quic_sni(doq) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    await doq.ask(hostname_for("phone"))

    devices = {row["device"] for row in doq.server.metrics.devices()}
    assert devices == {"phone"}


@respx.mock
async def test_an_unknown_token_is_refused(doq) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    assert await doq.ask(f"aaaaaaaaaaaaaaaa.{BASE_DOMAIN}", timeout=5.0) is None

    assert doq.server.stats.refused_unknown_sni == 1
    assert doq.server.stats.queries == 0
    assert doq.server.stats.connections == 0


@respx.mock
async def test_a_name_outside_the_base_domain_is_refused(doq) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    assert await doq.ask(BASE_DOMAIN, timeout=5.0) is None

    assert doq.server.stats.refused_no_sni == 1
    assert doq.server.stats.queries == 0


@respx.mock
async def test_two_queries_on_one_connection_both_answer(doq) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    async with connect(
        "127.0.0.1",
        doq.port,
        configuration=doq._configuration(hostname_for("laptop")),
    ) as protocol:
        await protocol.wait_connected()

        answers = []
        for name in ("one.test", "two.test"):
            reader, writer = await protocol.create_stream()
            query = dns.message.make_query(name, dns.rdatatype.A)
            query.id = 0
            wire = query.to_wire()
            writer.write(len(wire).to_bytes(2, "big") + wire)
            writer.write_eof()
            body = await asyncio.wait_for(reader.read(), timeout=10)
            length = int.from_bytes(body[:2], "big")
            answers.append(dns.message.from_wire(body[2 : 2 + length]))

    assert all(a.rcode() == dns.rcode.NOERROR for a in answers)
    assert doq.server.stats.queries == 2
    assert doq.server.stats.connections == 1


def test_the_snapshot_reports_the_listener(doq) -> None:
    snapshot = doq.server.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["base_domain"] == BASE_DOMAIN
    assert snapshot["port"] == doq.port
