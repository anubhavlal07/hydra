from __future__ import annotations

import asyncio
import datetime
import ssl
from pathlib import Path

import dns.message
import dns.rcode
import dns.rdatatype
import httpx
import pytest
import respx
from conftest import make_answer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from hydra_dns.config import DeviceConfig, device_token
from hydra_dns.resolver.cache import AnswerCache
from hydra_dns.resolver.dot import DotServer, token_from_sni
from hydra_dns.resolver.metrics import Metrics
from hydra_dns.resolver.service import Resolver
from hydra_dns.resolver.upstream import Upstream, UpstreamPool
from hydra_dns.store import DeviceStore

BASE_DOMAIN = "dot.test"
UPSTREAM = "https://upstream.test/dns-query"
SEED = [DeviceConfig(id="laptop", name="Laptop"), DeviceConfig(id="phone", name="Phone")]


def test_token_is_taken_from_the_label_in_front_of_the_base() -> None:
    assert token_from_sni("abc123.dot.test", BASE_DOMAIN) == "abc123"


def test_sni_matching_is_case_insensitive_and_tolerates_a_trailing_dot() -> None:
    assert token_from_sni("ABC123.DOT.TEST.", BASE_DOMAIN) == "abc123"


@pytest.mark.parametrize(
    "server_name",
    [
        None,
        "",
        "dot.test",
        "deep.abc123.dot.test",
        "abc123.evil.test",
        "abc123.dot.test.evil.com",
    ],
)
def test_names_that_carry_no_device_are_rejected(server_name) -> None:
    assert token_from_sni(server_name, BASE_DOMAIN) is None


def write_wildcard_cert(directory: Path) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"*.{BASE_DOMAIN}")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(f"*.{BASE_DOMAIN}"), x509.DNSName(BASE_DOMAIN)]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_file = directory / "cert.pem"
    key_file = directory / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_file), str(key_file)


class DotHarness:
    def __init__(self, server: DotServer, port: int, cert_file: str, store: DeviceStore) -> None:
        self.server = server
        self.port = port
        self.store = store
        self._client_context = ssl.create_default_context(cafile=cert_file)
        self._client_context.check_hostname = True

    async def connect(self, hostname: str):
        return await asyncio.open_connection(
            "127.0.0.1",
            self.port,
            ssl=self._client_context,
            server_hostname=hostname,
        )

    @staticmethod
    def frame(message: dns.message.Message) -> bytes:
        wire = message.to_wire()
        return len(wire).to_bytes(2, "big") + wire

    @staticmethod
    async def read_answer(reader: asyncio.StreamReader) -> dns.message.Message:
        header = await asyncio.wait_for(reader.readexactly(2), timeout=10)
        body = await asyncio.wait_for(reader.readexactly(int.from_bytes(header, "big")), timeout=10)
        return dns.message.from_wire(body)

    async def ask(self, hostname: str, name: str = "example.com") -> dns.message.Message:
        reader, writer = await self.connect(hostname)
        try:
            query = dns.message.make_query(name, dns.rdatatype.A)
            writer.write(self.frame(query))
            await writer.drain()
            return await self.read_answer(reader)
        finally:
            writer.close()


@pytest.fixture
async def dot(tmp_path, monkeypatch):
    import dataclasses

    from hydra_dns import store as store_module
    from hydra_dns.resolver import dot as dot_module

    cert_file, key_file = write_wildcard_cert(tmp_path)

    tweaked = dataclasses.replace(
        dot_module.settings,
        dot_enabled=True,
        dot_host="127.0.0.1",
        dot_port=0,
        dot_base_domain=BASE_DOMAIN,
        dot_cert_file=cert_file,
        dot_key_file=key_file,
        store_url="",
    )
    monkeypatch.setattr(dot_module, "settings", tweaked)
    monkeypatch.setattr(store_module, "settings", tweaked)

    client = httpx.AsyncClient()
    pool = UpstreamPool(strategy="round_robin", timeout_s=5.0)
    pool.attach_client(client)
    pool.set_upstreams([Upstream(label="mock", doh_url=UPSTREAM)])

    cache = AnswerCache(max_entries=100, min_ttl=30, max_ttl=3600)
    metrics = Metrics()
    resolver = Resolver(cache, pool, metrics)
    store = DeviceStore(client, SEED)

    server = DotServer(resolver, store, metrics)
    await server.start()
    port = server._server.sockets[0].getsockname()[1]

    yield DotHarness(server, port, cert_file, store)

    await server.aclose()
    await client.aclose()


def token_for(device_id: str) -> str:
    return device_token(device_id)


def hostname_for(device_id: str) -> str:
    return f"{token_for(device_id)}.{BASE_DOMAIN}"


@respx.mock
async def test_a_known_token_resolves(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    answer = await dot.ask(hostname_for("laptop"))

    assert answer.rcode() == dns.rcode.NOERROR
    assert "93.184.216.34" in answer.answer[0].to_text()
    assert dot.server.stats.queries == 1


@respx.mock
async def test_the_query_id_is_echoed_back(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    reader, writer = await dot.connect(hostname_for("laptop"))
    query = dns.message.make_query("example.com", dns.rdatatype.A)
    query.id = 31337
    writer.write(dot.frame(query))
    await writer.drain()
    answer = await dot.read_answer(reader)
    writer.close()

    assert answer.id == 31337


@respx.mock
async def test_the_connection_is_reused_across_queries(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    reader, writer = await dot.connect(hostname_for("laptop"))
    try:
        for _ in range(3):
            writer.write(dot.frame(dns.message.make_query("example.com", dns.rdatatype.A)))
            await writer.drain()
            assert (await dot.read_answer(reader)).rcode() == dns.rcode.NOERROR
    finally:
        writer.close()

    assert dot.server.stats.connections == 1
    assert dot.server.stats.queries == 3


@respx.mock
async def test_pipelined_queries_are_all_answered(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    reader, writer = await dot.connect(hostname_for("laptop"))
    try:
        sent = []
        for index in range(4):
            query = dns.message.make_query(f"host{index}.example.com", dns.rdatatype.A)
            query.id = 1000 + index
            sent.append(query.id)
            writer.write(dot.frame(query))
        await writer.drain()

        seen = {(await dot.read_answer(reader)).id for _ in sent}
    finally:
        writer.close()

    assert seen == set(sent)


@respx.mock
async def test_dot_and_doh_share_one_cache(dot) -> None:
    route = respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    await dot.ask(hostname_for("laptop"))
    await dot.ask(hostname_for("phone"))

    assert route.call_count == 1


@respx.mock
async def test_queries_are_attributed_to_the_right_device(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    await dot.ask(hostname_for("phone"))

    devices = {row["device"]: row for row in dot.server._metrics.devices()}
    assert "phone" in devices
    assert devices["phone"]["queries"] == 1


async def expect_no_answer(dot, hostname: str) -> None:
    reader, writer = await dot.connect(hostname)
    try:
        writer.write(dot.frame(dns.message.make_query("example.com", dns.rdatatype.A)))
        await writer.drain()
        with pytest.raises((asyncio.IncompleteReadError, TimeoutError, ConnectionError)):
            await asyncio.wait_for(reader.readexactly(2), timeout=5)
    finally:
        writer.close()


@respx.mock
async def test_an_unknown_token_gets_nothing(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    await expect_no_answer(dot, f"aaaaaaaaaaaaaaaa.{BASE_DOMAIN}")

    assert dot.server.stats.refused_unknown_sni == 1
    assert dot.server.stats.queries == 0


@respx.mock
async def test_the_bare_base_domain_gets_nothing(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    await expect_no_answer(dot, BASE_DOMAIN)
    assert dot.server.stats.refused_no_sni == 1


@respx.mock
async def test_a_removed_device_stops_resolving_over_dot(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    hostname = hostname_for("laptop")

    assert (await dot.ask(hostname)).rcode() == dns.rcode.NOERROR

    dot.store._devices = [d for d in dot.store.devices if d.id != "laptop"]

    await expect_no_answer(dot, hostname)


@respx.mock
async def test_a_bogus_length_prefix_drops_the_connection(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))

    reader, writer = await dot.connect(hostname_for("laptop"))
    try:
        writer.write((60000).to_bytes(2, "big") + b"\x00" * 10)
        await writer.drain()
        with pytest.raises((asyncio.IncompleteReadError, TimeoutError, ConnectionError)):
            await asyncio.wait_for(reader.readexactly(2), timeout=5)
    finally:
        writer.close()

    assert dot.server.stats.malformed == 1


@respx.mock
async def test_an_upstream_outage_still_produces_a_dns_answer(dot) -> None:
    respx.post(UPSTREAM).mock(side_effect=httpx.ConnectError("upstream down"))

    answer = await dot.ask(hostname_for("laptop"))

    assert answer.rcode() == dns.rcode.SERVFAIL


@respx.mock
async def test_snapshot_reports_what_the_listener_is_doing(dot) -> None:
    respx.post(UPSTREAM).mock(return_value=httpx.Response(200, content=make_answer()[1]))
    await dot.ask(hostname_for("laptop"))

    snapshot = dot.server.snapshot()
    assert snapshot["enabled"] is True
    assert snapshot["base_domain"] == BASE_DOMAIN
    assert snapshot["queries"] == 1
