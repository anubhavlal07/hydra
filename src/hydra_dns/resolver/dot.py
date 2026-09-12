from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from dataclasses import dataclass, field

from ..certwatch import watcher
from ..config import settings
from .service import MalformedQuery, Resolver

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096
IDLE_TIMEOUT_S = 120.0
MAX_CONCURRENT_PER_CONNECTION = 32


@dataclass
class DotStats:
    connections: int = 0
    open_connections: int = 0
    queries: int = 0
    refused_unknown_sni: int = 0
    refused_no_sni: int = 0
    malformed: int = 0
    handshake_failures: int = 0
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        return {
            "connections": self.connections,
            "open_connections": self.open_connections,
            "queries": self.queries,
            "refused_unknown_sni": self.refused_unknown_sni,
            "refused_no_sni": self.refused_no_sni,
            "malformed": self.malformed,
            "handshake_failures": self.handshake_failures,
            "uptime_s": round(time.time() - self.started_at, 1),
        }


def token_from_sni(server_name: str | None, base_domain: str) -> str | None:
    if not server_name:
        return None
    name = server_name.strip().rstrip(".").lower()
    suffix = "." + base_domain.strip().rstrip(".").lower()
    if not name.endswith(suffix):
        return None
    label = name[: -len(suffix)]
    if not label or "." in label:
        return None
    return label


class DotServer:
    def __init__(self, resolver: Resolver, devices, metrics) -> None:
        self._resolver = resolver
        self._devices = devices
        self._metrics = metrics
        self._server: asyncio.AbstractServer | None = None
        self.stats = DotStats()
        self._base_domain = settings.dot_base_domain

    def _build_ssl_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(settings.dot_cert_file, settings.dot_key_file)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with contextlib.suppress(NotImplementedError):
            context.set_alpn_protocols(["dot"])
        context.sni_callback = _remember_sni
        return context

    async def start(self) -> None:
        context = self._build_ssl_context()
        watcher.register("dot", context)
        self._server = await asyncio.start_server(
            self._handle,
            host=settings.dot_host,
            port=settings.dot_port,
            ssl=context,
            ssl_handshake_timeout=15.0,
            backlog=128,
            reuse_address=True,
        )
        where = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        log.info(
            "DoT listening on %s for *.%s",
            where or f"{settings.dot_host}:{settings.dot_port}",
            self._base_domain,
        )

    async def aclose(self) -> None:
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(Exception):
            await self._server.wait_closed()
        self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.stats.connections += 1
        self.stats.open_connections += 1

        peer = writer.get_extra_info("peername")
        ssl_object = writer.get_extra_info("ssl_object")
        server_name = getattr(ssl_object, "hydra_sni", None)
        token = token_from_sni(server_name, self._base_domain)

        try:
            if token is None:
                self.stats.refused_no_sni += 1
                self._metrics.record_refused()
                log.debug("DoT connection from %s with unusable SNI %r", peer, server_name)
                return

            device = self._devices.by_token(token)
            if device is None:
                self.stats.refused_unknown_sni += 1
                self._metrics.record_refused()
                log.debug("DoT connection from %s presented an unknown token", peer)
                return

            await self._serve(reader, writer, device.id)
        except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
            pass
        except Exception:
            log.exception("DoT connection from %s failed", peer)
        finally:
            self.stats.open_connections -= 1
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, device_id: str
    ) -> None:
        write_lock = asyncio.Lock()
        in_flight: set[asyncio.Task] = set()

        try:
            while True:
                try:
                    header = await asyncio.wait_for(reader.readexactly(2), timeout=IDLE_TIMEOUT_S)
                except (TimeoutError, asyncio.IncompleteReadError):
                    break

                length = int.from_bytes(header, "big")
                if length == 0 or length > MAX_MESSAGE:
                    self.stats.malformed += 1
                    break

                try:
                    wire = await asyncio.wait_for(
                        reader.readexactly(length), timeout=IDLE_TIMEOUT_S
                    )
                except (TimeoutError, asyncio.IncompleteReadError):
                    break

                if len(in_flight) >= MAX_CONCURRENT_PER_CONNECTION:
                    await asyncio.wait(in_flight, return_when=asyncio.FIRST_COMPLETED)

                task = asyncio.create_task(self._answer(wire, device_id, writer, write_lock))
                in_flight.add(task)
                task.add_done_callback(in_flight.discard)
        finally:
            for task in list(in_flight):
                task.cancel()
            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)

    async def _answer(
        self,
        wire: bytes,
        device_id: str,
        writer: asyncio.StreamWriter,
        write_lock: asyncio.Lock,
    ) -> None:
        try:
            result = await self._resolver.resolve(wire, device_id)
        except MalformedQuery:
            self.stats.malformed += 1
            return
        except Exception:
            log.exception("DoT query failed for device %s", device_id)
            return

        self.stats.queries += 1
        payload = len(result.wire).to_bytes(2, "big") + result.wire

        async with write_lock:
            if writer.is_closing():
                return
            writer.write(payload)
            with contextlib.suppress(Exception):
                await writer.drain()

    def snapshot(self) -> dict:
        return {
            "enabled": True,
            "base_domain": self._base_domain,
            "port": settings.dot_port,
            **self.stats.snapshot(),
        }


def _remember_sni(ssl_object, server_name: str | None, _context) -> None:
    with contextlib.suppress(AttributeError):
        ssl_object.hydra_sni = server_name
