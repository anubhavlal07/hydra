from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpcore
import httpx

from . import blocklist_overlap
from . import rules as ruleset
from .config import settings
from .store import MAX_TAGS, Device, StoreError, normalise_tags, targets

log = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z0-9_-]{1,40}$")
MAX_URL = 2048
MAX_NAME = 60
MAX_ALLOW = 500
MAX_HOSTNAME = 253
FETCH_TIMEOUT_S = 30.0
MAX_REDIRECTS = 5
CONNECT_ATTEMPT_S = 3.0
REDIRECTS = frozenset({301, 302, 303, 307, 308})
RETRY_S = 900.0
MIN_TICK_S = 60.0
STALE_AFTER_REFRESHES = 3
MAX_REACH_CACHE = 1024
ALLOW_PREFIX = "@@"
COMMENT_PREFIXES = ("!", "#")
INLINE_COMMENT = re.compile(r"\s#")
ADBLOCK = re.compile(r"^(@@)?\|\|([^\^|/$*]+)\^?$")
HOSTS_NOISE = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "local",
        "broadcasthost",
        "ip6-localhost",
        "ip6-loopback",
        "ip6-localnet",
        "ip6-mcastprefix",
        "ip6-allnodes",
        "ip6-allrouters",
        "ip6-allhosts",
    }
)


@dataclass(frozen=True)
class Parsed:
    names: frozenset[str]
    allow: frozenset[str]
    skipped: int


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return True


def normalise(raw: str) -> str | None:
    name = raw.strip().lower().removesuffix(".")
    if not name or len(name) > MAX_HOSTNAME or not ruleset.DOMAIN.match(name):
        return None
    if _is_address(name):
        return None
    return name


def _lines(source: str | bytes) -> Iterable[str]:
    if isinstance(source, bytes):
        return io.TextIOWrapper(io.BytesIO(source), encoding="utf-8-sig", errors="replace")
    return io.StringIO(source.removeprefix("﻿"))


def parse(source: str | bytes) -> Parsed:
    names: set[str] = set()
    allow: set[str] = set()
    skipped = 0

    for raw in _lines(source):
        line = raw.strip()
        if not line or line.startswith(COMMENT_PREFIXES):
            continue

        if line.startswith(("||", ALLOW_PREFIX)):
            match = ADBLOCK.match(line)
            found = normalise(match.group(2)) if match else None
            if found is None:
                skipped += 1
            elif match and match.group(1):
                allow.add(found)
            else:
                names.add(found)
            continue

        fields = INLINE_COMMENT.split(line, maxsplit=1)[0].split()
        if len(fields) == 1:
            found = normalise(fields[0])
            if found is None:
                skipped += 1
            else:
                names.add(found)
            continue

        if not fields or not _is_address(fields[0]):
            skipped += 1
            continue

        for token in fields[1:]:
            if token.lower() in HOSTS_NOISE:
                continue
            found = normalise(token)
            if found is None:
                skipped += 1
            else:
                names.add(found)

    return Parsed(names=frozenset(names), allow=frozenset(allow), skipped=skipped)


def normalise_allow(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    seen: list[str] = []
    for raw in values:
        name = normalise(str(raw or "").removeprefix(ALLOW_PREFIX).removeprefix("||").strip("^"))
        if name and name not in seen:
            seen.append(name)
    return tuple(seen[:MAX_ALLOW])


@dataclass(frozen=True)
class Blocklist:
    id: str
    name: str
    url: str
    tags: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    enabled: bool = True
    created_at: str | None = None

    def applies_to(self, device: Device | None) -> bool:
        return targets(self.tags, device)

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "tags": list(self.tags),
            "allow": list(self.allow),
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


def from_row(row: object) -> Blocklist | None:
    if not isinstance(row, dict):
        return None
    list_id = str(row.get("id") or "")
    url = str(row.get("url") or "")
    if not ID_PATTERN.match(list_id) or not url.startswith("https://") or len(url) > MAX_URL:
        return None
    created = row.get("created_at")
    return Blocklist(
        id=list_id,
        name=str(row.get("name") or list_id)[:MAX_NAME],
        url=url,
        tags=normalise_tags(row.get("tags")),
        allow=normalise_allow(row.get("allow")),
        enabled=bool(row.get("enabled", True)),
        created_at=str(created) if created else None,
    )


@dataclass(eq=False)
class ListState:
    meta: Blocklist
    names: frozenset[str] = frozenset()
    own_allow: frozenset[str] = frozenset()
    excepted: frozenset[str] = frozenset()
    skipped: int = 0
    etag: str = ""
    last_modified: str = ""
    fetched_at: float | None = None
    last_error: str = ""
    hits: int = 0
    tried: bool = False
    version: int = 0

    def __post_init__(self) -> None:
        self.adopt(self.meta)

    @property
    def loaded(self) -> bool:
        return bool(self.names)

    def adopt(self, meta: Blocklist) -> None:
        self.meta = meta
        self.excepted = self.own_allow | frozenset(meta.allow)

    def hold(self, parsed: Parsed) -> None:
        self.version += 1
        self.names = parsed.names
        self.own_allow = parsed.allow
        self.skipped = parsed.skipped
        self.excepted = parsed.allow | frozenset(self.meta.allow)

    def blocks(self, name: str) -> bool:
        return (
            ruleset.listed(self.names, name) is not None
            and ruleset.listed(self.excepted, name) is None
        )

    def record(self) -> dict:
        return {
            **self.meta.as_row(),
            "etag": self.etag,
            "last_modified": self.last_modified,
            "fetched_at": self.fetched_at,
            "names": len(self.names),
            "skipped": self.skipped,
            "last_error": self.last_error,
        }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        prefix=f".{path.stem}-",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
        newline="\n",
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        temp = Path(handle.name)
    os.replace(temp, path)


class Mirror:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory) if directory else None

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def _paths(self, list_id: str) -> tuple[Path, Path]:
        assert self.directory is not None
        return self.directory / f"{list_id}.txt", self.directory / f"{list_id}.json"

    def save(self, record: dict, names: frozenset[str], allow: frozenset[str]) -> None:
        if self.directory is None:
            return
        content, meta = self._paths(str(record["id"]))
        try:
            body = "\n".join([*sorted(names), *(f"{ALLOW_PREFIX}{n}" for n in sorted(allow))])
            _atomic_write(content, body + "\n")
            _atomic_write(meta, json.dumps(record))
        except OSError as exc:
            log.warning("could not write blocklist %s to %s: %s", record["id"], self.directory, exc)

    def save_record(self, record: dict) -> None:
        if self.directory is None:
            return
        _, meta = self._paths(str(record["id"]))
        try:
            _atomic_write(meta, json.dumps(record))
        except OSError as exc:
            log.warning("could not write blocklist %s to %s: %s", record["id"], self.directory, exc)

    def forget(self, list_id: str) -> None:
        if self.directory is None or not ID_PATTERN.match(list_id):
            return
        for path in self._paths(list_id):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not remove %s: %s", path, exc)

    def load(self) -> list[ListState]:
        if self.directory is None or not self.directory.is_dir():
            return []
        found: list[ListState] = []
        for meta_path in sorted(self.directory.glob("*.json")):
            state = self._load_one(meta_path)
            if state is not None:
                found.append(state)
        found.sort(key=lambda s: (s.meta.created_at or "", s.meta.id))
        return found

    def _load_one(self, meta_path: Path) -> ListState | None:
        try:
            record = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("blocklist record %s is unreadable, ignoring it: %s", meta_path, exc)
            return None
        meta = from_row(record)
        if meta is None or f"{meta.id}.json" != meta_path.name:
            log.warning("blocklist record %s does not describe a usable list", meta_path)
            return None

        state = ListState(meta=meta)
        state.etag = str(record.get("etag") or "")
        state.last_modified = str(record.get("last_modified") or "")
        state.last_error = str(record.get("last_error") or "")
        fetched = record.get("fetched_at")
        state.fetched_at = float(fetched) if isinstance(fetched, (int, float)) else None

        content = meta_path.with_suffix(".txt")
        try:
            text = content.read_text(encoding="utf-8")
        except FileNotFoundError:
            return state
        except OSError as exc:
            log.warning("blocklist content %s is unreadable: %s", content, exc)
            return state

        names: set[str] = set()
        allow: set[str] = set()
        for line in text.splitlines():
            if line.startswith(ALLOW_PREFIX):
                allow.add(line[len(ALLOW_PREFIX) :])
            elif line:
                names.add(line)
        state.hold(
            Parsed(
                names=frozenset(names),
                allow=frozenset(allow),
                skipped=int(record.get("skipped") or 0),
            )
        )
        return state


class FetchError(RuntimeError):
    pass


class UnknownList(StoreError):
    pass


def public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or not address.is_global
    )


async def addresses_of(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError(f"could not resolve {host}: {exc}") from exc
    return [str(info[4][0]) for info in infos]


async def guard(url: str) -> tuple[str, list[str]]:
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError) as exc:
        raise FetchError(f"not a usable URL: {exc}") from exc
    if target.scheme != "https":
        raise FetchError("only https:// lists are fetched")
    host = target.raw_host.decode("ascii")
    if not host:
        raise FetchError("the URL has no host")

    try:
        found = [ipaddress.ip_address(host)]
    except ValueError:
        found = []
        for raw in await addresses_of(host, target.port or 443):
            try:
                address = ipaddress.ip_address(raw.split("%", 1)[0])
            except ValueError:
                continue
            if address not in found:
                found.append(address)
    if not found:
        raise FetchError(f"{host} resolves to nothing")
    for address in found:
        if not public(address):
            raise FetchError(f"{host} resolves to {address}, which is not a public address")
    return host, [str(address) for address in found]


NETWORK: httpcore.AsyncNetworkBackend = httpcore.AnyIOBackend()


@functools.cache
def tls_context() -> ssl.SSLContext:
    return httpx.create_ssl_context()


class PinnedNetwork(httpcore.AsyncNetworkBackend):
    def __init__(self, host: str, addresses: list[str]) -> None:
        self._host = host
        self._addresses = addresses

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[object] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        if host != self._host:
            raise httpcore.ConnectError(f"{host} was not checked before connecting")
        failure: Exception = httpcore.ConnectError(f"{host} has no checked address")
        final = len(self._addresses) - 1
        for index, address in enumerate(self._addresses):
            budget = timeout
            if index < final:
                budget = CONNECT_ATTEMPT_S if timeout is None else min(timeout, CONNECT_ATTEMPT_S)
            try:
                return await NETWORK.connect_tcp(
                    address,
                    port,
                    timeout=budget,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                failure = exc
        raise failure

    async def sleep(self, seconds: float) -> None:
        await NETWORK.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    def __init__(self, host: str, addresses: list[str]) -> None:
        super().__init__(verify=tls_context(), trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=tls_context(),
            max_connections=1,
            max_keepalive_connections=0,
            network_backend=PinnedNetwork(host, addresses),
        )


def pinned_client(host: str, addresses: list[str], headers: httpx.Headers) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=PinnedTransport(host, addresses), headers=headers, trust_env=False
    )


async def download(
    shared: httpx.AsyncClient, url: str, *, etag: str, last_modified: str, cap: int
) -> tuple[bytes, str, str] | None:
    headers: dict[str, str] = {}
    if etag:
        headers["if-none-match"] = etag
    if last_modified:
        headers["if-modified-since"] = last_modified

    for _ in range(MAX_REDIRECTS + 1):
        host, addresses = await guard(url)
        async with (
            pinned_client(host, addresses, shared.headers) as client,
            client.stream(
                "GET", url, headers=headers, timeout=FETCH_TIMEOUT_S, follow_redirects=False
            ) as response,
        ):
            if response.status_code in REDIRECTS:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(f"HTTP {response.status_code} without a location")
                url = str(response.url.join(location))
                if not url.startswith("https://"):
                    raise FetchError(f"redirected to {url[:80]}, which is not https")
                continue
            if response.status_code == 304 and headers:
                return None
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code}")
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > cap:
                raise FetchError(f"the list is {int(declared):,} bytes, over {cap:,}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > cap:
                    raise FetchError(f"the list is over {cap:,} bytes")
                chunks.append(chunk)
            return (
                b"".join(chunks),
                response.headers.get("etag", ""),
                response.headers.get("last-modified", ""),
            )
    raise FetchError(f"more than {MAX_REDIRECTS} redirects")


def validate(
    list_id: str, name: str, url: str, tags: list[str], allow: list[str]
) -> tuple[str, str, str, tuple[str, ...], tuple[str, ...]]:
    list_id = (list_id or "").strip().lower()
    if not ID_PATTERN.match(list_id):
        raise StoreError(
            "a blocklist id is lowercase letters, digits, dashes or underscores, "
            "up to 40 characters"
        )
    name = (name or "").strip()
    if not 1 <= len(name) <= MAX_NAME:
        raise StoreError(f"a blocklist needs a name of 1 to {MAX_NAME} characters")
    url = (url or "").strip()
    if not url.startswith("https://") or len(url) > MAX_URL:
        raise StoreError(f"a blocklist URL starts with https:// and fits in {MAX_URL} characters")
    try:
        host = httpx.URL(url).host
    except (httpx.InvalidURL, ValueError) as exc:
        raise StoreError(f"not a usable URL: {exc}") from exc
    if not host:
        raise StoreError("a blocklist URL needs a host")

    if len(tags) > MAX_TAGS:
        raise StoreError(f"a blocklist targets at most {MAX_TAGS} tags")
    wanted_tags = normalise_tags(tags)

    if len(allow) > MAX_ALLOW:
        raise StoreError(f"a blocklist holds at most {MAX_ALLOW} exceptions")
    bad = [str(a) for a in allow if normalise_allow([a]) == ()]
    if bad:
        raise StoreError(f"not a domain: {', '.join(bad[:3])}")
    return list_id, name, url, wanted_tags, normalise_allow(allow)


class Blocklists:
    def __init__(self, client: httpx.AsyncClient | None, store: object | None) -> None:
        self._client = client
        self._store = store
        self._mirror = Mirror(settings.blocklist_dir)
        self._states: dict[str, ListState] = {}
        self._active: tuple[ListState, ...] = ()
        self._reach: dict[str, tuple[ListState, ...]] = {}
        self._roster_seen: object = None
        self._fetching = asyncio.Lock()
        self._kicked: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None
        self._overlap: dict[str, dict] = {}
        self._overlap_of: tuple = ()
        self._overlap_task: asyncio.Task | None = None
        self.last_sync_at: float = 0.0
        self.last_error: str = ""

    @property
    def lists(self) -> list[ListState]:
        return list(self._states.values())

    def get(self, list_id: str) -> ListState | None:
        return self._states.get(list_id)

    @property
    def editable(self) -> bool:
        return bool(getattr(self._store, "writable", False))

    @property
    def total_names(self) -> int:
        return sum(len(s.names) for s in self._states.values())

    def _rebuild(self) -> None:
        self._active = tuple(s for s in self._states.values() if s.meta.enabled and s.loaded)
        self._reach = {}
        self._measure_overlap()

    def _overlap_wanted(self) -> tuple:
        return tuple(
            (s.meta.id, s.version, s.meta.enabled)
            for s in list(self._states.values())[: max(0, settings.blocklist_max)]
            if s.loaded
        )

    def _measure_overlap(self) -> None:
        if self._overlap_task is not None and not self._overlap_task.done():
            return
        if self._overlap_wanted() == self._overlap_of:
            return
        try:
            self._overlap_task = asyncio.get_running_loop().create_task(
                self._overlap_loop(), name="hydra-blocklist-overlap"
            )
        except RuntimeError:
            return

    async def _overlap_loop(self) -> None:
        while (wanted := self._overlap_wanted()) != self._overlap_of:
            held = {list_id: self._states[list_id].names for list_id, _, _ in wanted}
            enabled = frozenset(list_id for list_id, _, on in wanted if on)
            try:
                found = await asyncio.to_thread(blocklist_overlap.measure, held, enabled)
            except Exception:
                log.exception("blocklist overlap could not be measured")
                return
            self._overlap = found
            self._overlap_of = wanted

    def _device(self, device_id: str) -> Device | None:
        getter = getattr(self._store, "get", None)
        return getter(device_id) if getter is not None else None

    def _reach_for(self, device_id: str) -> tuple[ListState, ...]:
        roster = getattr(self._store, "roster", None)
        if roster is not self._roster_seen or len(self._reach) > MAX_REACH_CACHE:
            self._reach = {}
            self._roster_seen = roster
        found = self._reach.get(device_id)
        if found is None:
            device = self._device(device_id)
            found = tuple(s for s in self._active if s.meta.applies_to(device))
            self._reach[device_id] = found
        return found

    def _excepted_by_device(self, device_id: str, name: str) -> bool:
        rules_for = getattr(self._store, "rules_for", None)
        return rules_for is not None and rules_for(device_id).allows(name)

    def match(self, device_id: str, name: str) -> Blocklist | None:
        if not self._active:
            return None
        reach = self._reach_for(device_id)
        if not reach or self._excepted_by_device(device_id, name):
            return None
        for state in reach:
            if state.blocks(name):
                return state.meta
        return None

    def cloaking(self, device_id: str, asked: str, target: str) -> Blocklist | None:
        if not self._active:
            return None
        reach = self._reach_for(device_id)
        if not reach or self._excepted_by_device(device_id, target):
            return None
        for state in reach:
            if state.blocks(target) and ruleset.listed(state.excepted, asked) is None:
                return state.meta
        return None

    def hit(self, list_id: str) -> None:
        state = self._states.get(list_id)
        if state is not None:
            state.hits += 1

    def everywhere(self, name: str) -> bool:
        for state in self._active:
            if state.meta.tags or not state.blocks(name):
                continue
            roster = getattr(self._store, "roster", None) or []
            return not any(self._excepted_by_device(d.id, name) for d in roster)
        return False

    def naming(self, name: str) -> list[dict]:
        return [
            {
                "id": state.meta.id,
                "name": state.meta.name,
                "allowed": ruleset.listed(state.excepted, name) is not None,
                "enabled": state.meta.enabled,
            }
            for state in self._states.values()
            if ruleset.listed(state.names, name) is not None
        ]

    def reaches(self, state: ListState) -> list[str]:
        roster = getattr(self._store, "roster", None) or []
        return [d.id for d in roster if state.meta.applies_to(d)]

    async def load(self) -> int:
        found = await asyncio.to_thread(self._mirror.load)
        total = 0
        for state in found[: max(0, settings.blocklist_max)]:
            if total + len(state.names) > settings.blocklist_max_names:
                state.last_error = self._over_cap(len(state.names), total)
                state.hold(Parsed(names=frozenset(), allow=frozenset(), skipped=state.skipped))
            total += len(state.names)
            self._states[state.meta.id] = state
        self._rebuild()
        if found:
            log.info(
                "loaded %s blocklist(s) and %s name(s) from %s",
                len(self._states),
                total,
                self._mirror.directory,
            )
        return len(self._states)

    def _adopt(self, metas: list[Blocklist]) -> list[str]:
        cap = max(0, settings.blocklist_max)
        if len(metas) > cap:
            log.warning("the registry holds %s blocklists; the first %s apply", len(metas), cap)
            metas = metas[:cap]

        wanted = {m.id for m in metas}
        for list_id in [i for i in self._states if i not in wanted]:
            self._states.pop(list_id, None)
            self._mirror.forget(list_id)

        due: list[str] = []
        ordered: dict[str, ListState] = {}
        for meta in metas:
            state = self._states.get(meta.id)
            if state is None:
                state = ListState(meta=meta)
                due.append(meta.id)
                self._mirror.save_record(state.record())
            else:
                before = state.meta
                if before.url != meta.url:
                    state.etag = ""
                    state.last_modified = ""
                    state.last_error = ""
                    due.append(meta.id)
                elif meta.enabled and not state.loaded:
                    due.append(meta.id)
                state.adopt(meta)
                if before != meta:
                    self._mirror.save_record(state.record())
            ordered[meta.id] = state
        self._states = ordered
        self._rebuild()
        return [i for i in due if self._states[i].meta.enabled]

    async def sync(self) -> list[str]:
        if not getattr(self._store, "configured", False):
            return []
        try:
            rows = await self._store.blocklist_rows()  # type: ignore[union-attr]
        except StoreError as exc:
            self.last_error = str(exc)
            log.warning(
                "blocklist registry read failed, keeping the %s in hand: %s", len(self._states), exc
            )
            return []
        if not isinstance(rows, list):
            self.last_error = "the registry returned something other than a list"
            return []
        metas = [meta for meta in (from_row(r) for r in rows) if meta is not None]
        self.last_error = ""
        self.last_sync_at = time.time()
        return self._adopt(metas)

    def _require(self, list_id: str) -> ListState:
        state = self._states.get(list_id)
        if state is None:
            raise UnknownList(f"no blocklist with id {list_id!r}")
        return state

    async def put(
        self,
        list_id: str,
        *,
        name: str,
        url: str,
        tags: list[str],
        allow: list[str],
        enabled: bool,
    ) -> ListState:
        list_id, name, url, wanted_tags, wanted_allow = validate(list_id, name, url, tags, allow)
        async with self._store.lock:  # type: ignore[union-attr]
            if list_id not in self._states and len(self._states) >= settings.blocklist_max:
                raise StoreError(f"Hydra holds at most {settings.blocklist_max} blocklists")
            await self._store.write_blocklist(  # type: ignore[union-attr]
                {
                    "id": list_id,
                    "name": name,
                    "url": url,
                    "tags": list(wanted_tags),
                    "allow": list(wanted_allow),
                    "enabled": bool(enabled),
                }
            )
            due = await self.sync()
        state = self._require(list_id)
        for fetch_id in due:
            self.kick(fetch_id)
        return state

    async def set_allow(self, list_id: str, domain: str, action: str) -> ListState:
        name = normalise(str(domain or "").removeprefix(ALLOW_PREFIX))
        if name is None:
            raise StoreError(f"not a domain: {domain!r}")
        if action not in ("add", "remove"):
            raise StoreError("action is add or remove")

        async with self._store.lock:  # type: ignore[union-attr]
            meta = self._require(list_id).meta
            current = list(meta.allow)
            if action == "add" and name not in current:
                if len(current) >= MAX_ALLOW:
                    raise StoreError(f"a blocklist holds at most {MAX_ALLOW} exceptions")
                current.append(name)
            elif action == "remove":
                current = [a for a in current if a != name]
            await self._store.write_blocklist(  # type: ignore[union-attr]
                {**meta.as_row(), "allow": current}
            )
            await self.sync()
        return self._require(list_id)

    async def remove(self, list_id: str) -> bool:
        async with self._store.lock:  # type: ignore[union-attr]
            self._require(list_id)
            await self._store.delete_blocklist(list_id)  # type: ignore[union-attr]
            await self.sync()
        return list_id not in self._states

    def kick(self, list_id: str) -> None:
        task = asyncio.create_task(self._quietly(list_id), name=f"hydra-blocklist-{list_id}")
        self._kicked.add(task)
        task.add_done_callback(self._kicked.discard)

    async def _quietly(self, list_id: str) -> None:
        try:
            await self.refresh(list_id)
        except Exception:
            log.exception("blocklist %s could not be refreshed", list_id)

    async def refresh(self, list_id: str) -> ListState:
        state = self._require(list_id)
        async with self._fetching:
            if self._states.get(list_id) is state:
                await self._fetch(state)
        return state

    def _over_cap(self, adding: int, others: int) -> str:
        return (
            f"{adding:,} names would take Hydra's lists to {adding + others:,}, past "
            f"HYDRA_BLOCKLIST_MAX_NAMES ({settings.blocklist_max_names:,}); "
            "the previous copy is kept"
        )

    async def _fetch(self, state: ListState) -> None:
        state.tried = True
        try:
            outcome = await asyncio.wait_for(self._download(state), FETCH_TIMEOUT_S)
        except TimeoutError:
            self._failed(state, f"the download took longer than {FETCH_TIMEOUT_S:.0f} seconds")
            return
        except FetchError as exc:
            self._failed(state, str(exc))
            return
        except httpx.HTTPError as exc:
            self._failed(state, f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
            return

        if outcome is None:
            state.fetched_at = time.time()
            state.last_error = ""
            self._mirror.save_record(state.record())
            return

        body, etag, last_modified = outcome
        parsed = await asyncio.to_thread(parse, body)
        del body

        if self._states.get(state.meta.id) is not state:
            return
        if not parsed.names:
            self._failed(state, f"the list parsed to no names ({parsed.skipped:,} lines skipped)")
            return
        others = sum(len(s.names) for s in self._states.values() if s is not state)
        if others + len(parsed.names) > settings.blocklist_max_names:
            self._failed(state, self._over_cap(len(parsed.names), others))
            return

        state.hold(parsed)
        state.etag = etag
        state.last_modified = last_modified
        state.fetched_at = time.time()
        state.last_error = ""
        self._rebuild()
        log.info(
            "blocklist %s holds %s name(s), %s line(s) skipped",
            state.meta.id,
            len(state.names),
            state.skipped,
        )
        await asyncio.to_thread(self._mirror.save, state.record(), state.names, state.own_allow)

    def _failed(self, state: ListState, reason: str) -> None:
        state.last_error = reason
        log.warning("blocklist %s kept its previous copy: %s", state.meta.id, reason)
        self._mirror.save_record(state.record())

    async def _download(self, state: ListState) -> tuple[bytes, str, str] | None:
        if self._client is None:
            raise FetchError("no HTTP client")
        return await download(
            self._client,
            state.meta.url,
            etag=state.etag if state.loaded else "",
            last_modified=state.last_modified if state.loaded else "",
            cap=settings.blocklist_max_bytes,
        )

    def _due(self, state: ListState, now: float) -> bool:
        if not state.meta.enabled:
            return False
        if not state.loaded or state.last_error or state.fetched_at is None:
            return True
        return now - state.fetched_at >= settings.blocklist_refresh_s - MIN_TICK_S

    async def tick(self) -> int:
        await self.sync()
        fetched = 0
        now = time.time()
        for state in list(self._states.values()):
            if self._states.get(state.meta.id) is not state or not self._due(state, now):
                continue
            async with self._fetching:
                await self._fetch(state)
            fetched += 1
        return fetched

    def _next_wait(self) -> float:
        refresh = max(MIN_TICK_S, float(settings.blocklist_refresh_s))
        troubled = any(
            s.meta.enabled and (s.last_error or not s.loaded) for s in self._states.values()
        )
        return min(refresh, RETRY_S) if troubled else refresh

    def start_background(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hydra-blocklists")

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("blocklist refresh failed")
            await asyncio.sleep(self._next_wait())

    async def aclose(self) -> None:
        tasks = [t for t in (self._task, self._overlap_task, *self._kicked) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._task = None
        self._overlap_task = None
        self._kicked.clear()

    def as_dict(self, state: ListState) -> dict:
        return {
            **state.meta.as_row(),
            "names": len(state.names),
            "skipped": state.skipped,
            "hits": state.hits,
            "fetched_at": state.fetched_at,
            "last_error": state.last_error,
            "loaded": state.loaded,
            "reaches": self.reaches(state),
            **self._overlap_for(state),
        }

    def _overlap_for(self, state: ListState) -> dict:
        found = self._overlap.get(state.meta.id)
        if found is None or not state.loaded:
            return {"overlap": [], "unique": None}
        return {
            "overlap": [entry for entry in found["overlap"] if entry["id"] in self._states],
            "unique": found["unique"],
        }

    def summary(self) -> dict:
        states = list(self._states.values())
        return {
            "lists": len(states),
            "enabled": sum(1 for s in states if s.meta.enabled),
            "loaded": sum(1 for s in states if s.loaded),
            "failing": sum(1 for s in states if s.meta.enabled and s.last_error),
            "names": self.total_names,
            "hits": sum(s.hits for s in states),
            "max_lists": settings.blocklist_max,
            "max_names": settings.blocklist_max_names,
            "refresh_seconds": settings.blocklist_refresh_s,
            "on_disk": self._mirror.enabled,
            "last_sync_at": self.last_sync_at or None,
            "last_error": self.last_error,
        }

    def health(self) -> list[dict]:
        return [
            {
                "id": s.meta.id,
                "name": s.meta.name,
                "enabled": s.meta.enabled,
                "loaded": s.loaded,
                "tried": s.tried,
                "fetched_at": s.fetched_at,
                "last_error": s.last_error,
            }
            for s in self._states.values()
        ]
