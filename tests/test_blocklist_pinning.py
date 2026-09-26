from __future__ import annotations

import dataclasses
import ssl

import httpcore
import httpx
import pytest
from test_blocklists import PUBLIC_IP, FakeStore, row

from hydra_dns import blocklists
from hydra_dns.blocklists import Blocklists, PinnedNetwork
from hydra_dns.config import settings

LIST_HOST = "lists.example.test"
METADATA_IP = "169.254.169.254"


class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self, network: FakeNetwork, address: str) -> None:
        self._network = network
        self._address = address
        self._pending = network.replies.get(address, b"")

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        chunk, self._pending = self._pending[:max_bytes], self._pending[max_bytes:]
        return chunk

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._network.written[self._address] = self._network.written.get(self._address, b"") + (
            buffer
        )

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self._network.tls.append((self._address, server_hostname, ssl_context))
        return self

    def get_extra_info(self, info: str) -> object:
        return None


class FakeNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.dialed: list[tuple[str, int]] = []
        self.budgets: list[float | None] = []
        self.refused: set[str] = set()
        self.replies: dict[str, bytes] = {}
        self.written: dict[str, bytes] = {}
        self.tls: list[tuple[str, str | None, ssl.SSLContext]] = []

    def answer(self, address: str, status: str, body: bytes = b"", **headers: str) -> None:
        lines = [f"HTTP/1.1 {status}", f"Content-Length: {len(body)}", "Connection: close"]
        lines += [f"{name.replace('_', '-')}: {value}" for name, value in headers.items()]
        self.replies[address] = ("\r\n".join(lines) + "\r\n\r\n").encode() + body

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: object = None,
    ) -> httpcore.AsyncNetworkStream:
        self.dialed.append((host, port))
        self.budgets.append(timeout)
        if host in self.refused:
            raise httpcore.ConnectError(f"{host} refused")
        return FakeStream(self, host)

    async def sleep(self, seconds: float) -> None:
        return None


@pytest.fixture
def network(monkeypatch, tmp_path) -> FakeNetwork:
    fake = FakeNetwork()
    monkeypatch.setattr(blocklists, "NETWORK", fake)
    monkeypatch.setattr(
        blocklists, "settings", dataclasses.replace(settings, blocklist_dir=str(tmp_path))
    )
    return fake


def rebinding(monkeypatch, first: dict[str, list[str]], later: list[str]) -> list[str]:
    lookups: list[str] = []

    async def resolve(host: str, port: int) -> list[str]:
        lookups.append(host)
        if lookups.count(host) == 1:
            return first[host]
        return later

    monkeypatch.setattr(blocklists, "addresses_of", resolve)
    return lookups


async def fetched(store: FakeStore) -> Blocklists:
    lists = Blocklists(httpx.AsyncClient(headers={"user-agent": "hydra-test"}), store)
    await lists.tick()
    return lists


async def test_a_rebinding_host_is_still_dialled_at_the_address_that_was_checked(
    network, monkeypatch
) -> None:
    lookups = rebinding(monkeypatch, {LIST_HOST: [PUBLIC_IP]}, [METADATA_IP])
    network.answer(PUBLIC_IP, "200 OK", b"ads.example.com\n", ETag='"1"')

    lists = await fetched(FakeStore(rows=[row()]))

    assert network.dialed == [(PUBLIC_IP, 443)]
    assert lookups == [LIST_HOST]
    assert lists.get("ads").names == {"ads.example.com"}
    assert lists.get("ads").last_error == ""


async def test_tls_and_the_host_header_stay_on_the_hostname(network, monkeypatch) -> None:
    rebinding(monkeypatch, {LIST_HOST: [PUBLIC_IP]}, [METADATA_IP])
    network.answer(PUBLIC_IP, "200 OK", b"ads.example.com\n")

    await fetched(FakeStore(rows=[row()]))

    [(address, server_hostname, context)] = network.tls
    assert address == PUBLIC_IP
    assert server_hostname == LIST_HOST
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    request = network.written[PUBLIC_IP].decode()
    assert request.startswith("GET /ads.txt HTTP/1.1\r\n")
    assert f"\r\nHost: {LIST_HOST}\r\n" in request
    assert "\r\nuser-agent: hydra-test\r\n" in request.lower()


async def test_an_ipv6_only_host_is_dialled_at_its_checked_address(network, monkeypatch) -> None:
    address = "2606:4700:4700::1111"
    rebinding(monkeypatch, {LIST_HOST: [address]}, ["fd00::1"])
    network.answer(address, "200 OK", b"ads.example.com\n")

    lists = await fetched(FakeStore(rows=[row()]))

    assert network.dialed == [(address, 443)]
    assert network.tls[0][1] == LIST_HOST
    assert lists.get("ads").loaded


async def test_the_next_checked_address_is_tried_quickly_when_the_first_fails(
    network, monkeypatch
) -> None:
    second = "93.184.216.35"
    rebinding(monkeypatch, {LIST_HOST: [PUBLIC_IP, second]}, [METADATA_IP])
    network.refused.add(PUBLIC_IP)
    network.answer(second, "200 OK", b"ads.example.com\n")

    lists = await fetched(FakeStore(rows=[row()]))

    assert network.dialed == [(PUBLIC_IP, 443), (second, 443)]
    assert network.budgets == [blocklists.CONNECT_ATTEMPT_S, blocklists.FETCH_TIMEOUT_S]
    assert lists.get("ads").loaded


async def test_every_redirect_hop_is_resolved_checked_and_pinned_again(
    network, monkeypatch
) -> None:
    cdn_ip = "151.101.1.1"
    lookups = rebinding(
        monkeypatch, {LIST_HOST: [PUBLIC_IP], "cdn.example.test": [cdn_ip]}, [METADATA_IP]
    )
    network.answer(PUBLIC_IP, "302 Found", location="https://cdn.example.test/a.txt")
    network.answer(cdn_ip, "200 OK", b"cdn.example.com\n")

    lists = await fetched(FakeStore(rows=[row()]))

    assert lookups == [LIST_HOST, "cdn.example.test"]
    assert network.dialed == [(PUBLIC_IP, 443), (cdn_ip, 443)]
    assert [hostname for _, hostname, _ in network.tls] == [LIST_HOST, "cdn.example.test"]
    assert "\r\nHost: cdn.example.test\r\n" in network.written[cdn_ip].decode()
    assert lists.get("ads").names == {"cdn.example.com"}


async def test_a_redirect_to_a_host_resolving_privately_is_never_dialled(
    network, monkeypatch
) -> None:
    rebinding(
        monkeypatch,
        {LIST_HOST: [PUBLIC_IP], "internal.example.test": [METADATA_IP]},
        [METADATA_IP],
    )
    network.answer(PUBLIC_IP, "301 Moved Permanently", location="https://internal.example.test/x")

    lists = await fetched(FakeStore(rows=[row()]))

    assert network.dialed == [(PUBLIC_IP, 443)]
    assert not lists.get("ads").loaded
    assert "not a public address" in lists.get("ads").last_error


async def test_a_host_other_than_the_checked_one_is_not_dialled(network) -> None:
    pinned = PinnedNetwork(LIST_HOST, [PUBLIC_IP])

    with pytest.raises(httpcore.ConnectError, match="was not checked"):
        await pinned.connect_tcp("elsewhere.example.test", 443)

    assert network.dialed == []
