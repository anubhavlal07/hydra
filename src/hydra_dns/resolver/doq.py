from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field

from aioquic import tls
from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import ConnectionTerminated, QuicEvent, StreamDataReceived

from ..certwatch import watcher
from ..config import settings
from .dot import token_from_sni
from .service import MalformedQuery, Resolver

log = logging.getLogger(__name__)

ALPN = ["doq"]
MAX_MESSAGE = 4096
PREFIX = 2


@dataclass
class DoqStats:
    connections: int = 0
    open_connections: int = 0
    queries: int = 0
    refused_unknown_sni: int = 0
    refused_no_sni: int = 0
    malformed: int = 0
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        return {
            "connections": self.connections,
            "open_connections": self.open_connections,
            "queries": self.queries,
            "refused_unknown_sni": self.refused_unknown_sni,
            "refused_no_sni": self.refused_no_sni,
            "malformed": self.malformed,
            "uptime_s": round(time.time() - self.started_at, 1),
        }


def peek_server_name(buf) -> str | None:
    at = buf.tell()
    try:
        return tls.pull_client_hello(buf).server_name
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            buf.seek(at)


class DoqProtocol(QuicConnectionProtocol):
    server: DoqServer | None = None

    def __init__(self, quic, stream_handler=None) -> None:
        super().__init__(quic, stream_handler)
        self.server_name: str | None = None
        self.device_id: str | None = None
        self._buffers: dict[int, bytearray] = {}
        self._answering: set[asyncio.Task] = set()
        self._watch_handshake(quic)

    def _watch_handshake(self, quic) -> None:
        initialize = quic._initialize

        def wrapped(peer_cid: bytes) -> None:
            initialize(peer_cid)
            with contextlib.suppress(AttributeError):
                self._watch_hello(quic.tls)

        with contextlib.suppress(AttributeError):
            quic._initialize = wrapped

    def _watch_hello(self, context) -> None:
        handle = context._server_handle_hello

        def wrapped(input_buf, *args, **kwargs):
            self.server_name = peek_server_name(input_buf)
            return handle(input_buf, *args, **kwargs)

        context._server_handle_hello = wrapped

    def quic_event_received(self, event: QuicEvent) -> None:
        server = self.server
        if server is None:
            return

        if isinstance(event, ConnectionTerminated):
            server.stats.open_connections = max(0, server.stats.open_connections - 1)
            self._buffers.clear()
            for task in list(self._answering):
                task.cancel()
            return

        if not isinstance(event, StreamDataReceived):
            return

        if self.device_id is None and not self._authorise(server):
            self.close()
            self.transmit()
            return

        held = self._buffers.setdefault(event.stream_id, bytearray())
        held.extend(event.data)

        if len(held) < PREFIX:
            return
        length = int.from_bytes(held[:PREFIX], "big")
        if length == 0 or length > MAX_MESSAGE:
            server.stats.malformed += 1
            self._buffers.pop(event.stream_id, None)
            return
        if len(held) < PREFIX + length:
            return

        wire = bytes(held[PREFIX : PREFIX + length])
        self._buffers.pop(event.stream_id, None)
        task = asyncio.create_task(self._answer(server, event.stream_id, wire))
        self._answering.add(task)
        task.add_done_callback(self._answering.discard)

    def _authorise(self, server: DoqServer) -> bool:
        token = token_from_sni(self.server_name, server.base_domain)
        if token is None:
            server.stats.refused_no_sni += 1
            server.metrics.record_refused()
            return False

        device = server.devices.by_token(token)
        if device is None:
            server.stats.refused_unknown_sni += 1
            server.metrics.record_refused()
            return False

        self.device_id = device.id
        server.stats.connections += 1
        server.stats.open_connections += 1
        return True

    async def _answer(self, server: DoqServer, stream_id: int, wire: bytes) -> None:
        try:
            result = await server.resolver.resolve(wire, self.device_id or "anonymous")
        except MalformedQuery:
            server.stats.malformed += 1
            self._quic.send_stream_data(stream_id, b"", end_stream=True)
            self.transmit()
            return
        except Exception:
            log.exception("DoQ query failed for device %s", self.device_id)
            return

        server.stats.queries += 1
        payload = len(result.wire).to_bytes(PREFIX, "big") + result.wire
        with contextlib.suppress(Exception):
            self._quic.send_stream_data(stream_id, payload, end_stream=True)
            self.transmit()


class DoqServer:
    def __init__(self, resolver: Resolver, devices, metrics) -> None:
        self.resolver = resolver
        self.devices = devices
        self.metrics = metrics
        self.stats = DoqStats()
        self.base_domain = settings.dot_base_domain
        self._server = None

    def _configuration(self) -> QuicConfiguration:
        configuration = QuicConfiguration(
            is_client=False,
            alpn_protocols=ALPN,
            idle_timeout=settings.doq_idle_timeout_s,
        )
        configuration.load_cert_chain(settings.dot_cert_file, settings.dot_key_file)
        return configuration

    async def start(self) -> None:
        configuration = self._configuration()
        watcher.register("doq", configuration)

        owner = self

        class _Protocol(DoqProtocol):
            server = owner

        self._server = await serve(
            settings.doq_host,
            settings.doq_port,
            configuration=configuration,
            create_protocol=_Protocol,
        )
        log.info(
            "DoQ listening on %s:%s for *.%s",
            settings.doq_host,
            settings.doq_port,
            self.base_domain,
        )

    async def aclose(self) -> None:
        if self._server is None:
            return
        with contextlib.suppress(Exception):
            self._server.close()
        self._server = None

    def snapshot(self) -> dict:
        return {
            "enabled": True,
            "base_domain": self.base_domain,
            "port": settings.doq_port,
            **self.stats.snapshot(),
        }
