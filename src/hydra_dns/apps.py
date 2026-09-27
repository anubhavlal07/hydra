from __future__ import annotations

import asyncio
import contextlib
import functools
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import blocklists as lists
from . import rules as ruleset
from . import schedule
from .config import settings
from .store import MAX_TAGS, Device, StoreError, normalise_tags, targets, window_fields

log = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")
GROUP_PATTERN = re.compile(r"^[a-z0-9_]{1,40}$")
BUNDLED = Path(__file__).with_name("app_catalog.json")
MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_APPS = 1000
MIN_APPS = 20
MAX_NAME = 60
MAX_ICON = 32_768
ACTIVE_FOR_S = 20.0
MAX_REACH_CACHE = 1024
MAX_SEEN_DEVICES = 256
RETRY_S = 900.0
MIN_TICK_S = 60.0
OTHER_GROUP = "other"
BLOCKING_REWRITES = ("nxdomain", "refused")


@dataclass(frozen=True)
class Rules:
    names: frozenset[str]
    exact: frozenset[str]
    allow: frozenset[str]
    skipped: int


def _modifiers(raw: str) -> tuple[bool, list[str]]:
    allow: list[str] = []
    for part in raw.split(","):
        key, _, value = part.strip().partition("=")
        key = key.strip().lower()
        if key == "important" and not value:
            continue
        if key == "dnsrewrite" and value.strip().lower().startswith(BLOCKING_REWRITES):
            continue
        if key == "denyallow" and value:
            for host in value.split("|"):
                found = lists.normalise(host)
                if found is None:
                    return False, []
                allow.append(found)
            continue
        return False, []
    return True, allow


def read_rules(lines: object) -> Rules:
    names: set[str] = set()
    exact: set[str] = set()
    allow: set[str] = set()
    skipped = 0

    for raw in lines if isinstance(lines, list) else []:
        line = str(raw or "").strip()
        pattern, _, options = line.partition("$")
        usable, excepted = _modifiers(options) if options else (True, [])

        if pattern.startswith("||"):
            target, host = names, pattern[2:]
        elif pattern.startswith("|"):
            target, host = exact, pattern[1:]
        else:
            skipped += 1
            continue

        found = lists.normalise(host.removesuffix("^"))
        if not usable or found is None:
            skipped += 1
            continue
        target.add(found)
        allow.update(excepted)

    return Rules(
        names=frozenset(names),
        exact=frozenset(exact),
        allow=frozenset(allow),
        skipped=skipped,
    )


@dataclass(frozen=True)
class App:
    id: str
    name: str
    group: str
    icon: str
    names: frozenset[str]
    exact: frozenset[str]
    allow: frozenset[str]
    skipped: int

    def covers(self, name: str) -> bool:
        if name not in self.exact and ruleset.listed(self.names, name) is None:
            return False
        return ruleset.listed(self.allow, name) is None

    @property
    def domains(self) -> int:
        return len(self.names) + len(self.exact)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "group": self.group,
            "icon": self.icon,
            "domains": self.domains,
        }


def _app_from(entry: object) -> App | None:
    if not isinstance(entry, dict):
        return None
    app_id = str(entry.get("id") or "")
    if not ID_PATTERN.match(app_id):
        return None
    name = str(entry.get("name") or "").strip()[:MAX_NAME] or app_id
    group = str(entry.get("group") or "")
    icon = str(entry.get("icon_svg") or "")
    if not icon.lstrip().startswith("<svg") or len(icon) > MAX_ICON:
        icon = ""
    found = read_rules(entry.get("rules"))
    if not found.names and not found.exact:
        return None
    return App(
        id=app_id,
        name=name,
        group=group if GROUP_PATTERN.match(group) else OTHER_GROUP,
        icon=icon,
        names=found.names,
        exact=found.exact,
        allow=found.allow,
        skipped=found.skipped,
    )


@dataclass(frozen=True)
class Catalog:
    apps: dict[str, App]
    groups: tuple[str, ...]
    source: str
    fingerprint: str
    suffixes: dict[str, tuple[str, ...]]
    exacts: dict[str, tuple[str, ...]]

    def owners(self, name: str) -> tuple[str, ...]:
        found = self.exacts.get(name, ())
        at = 0
        while True:
            hit = self.suffixes.get(name[at:] if at else name)
            if hit:
                found = found + hit
            dot = name.find(".", at)
            if dot < 0:
                return found
            at = dot + 1


EMPTY = Catalog(apps={}, groups=(), source="none", fingerprint="", suffixes={}, exacts={})


def _index(apps: dict[str, App], field: str) -> dict[str, tuple[str, ...]]:
    index: dict[str, tuple[str, ...]] = {}
    for app in apps.values():
        for name in getattr(app, field):
            index[name] = (*index.get(name, ()), app.id)
    return index


def parse_catalog(raw: str | bytes, source: str) -> Catalog:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"the catalog is not JSON: {exc}") from exc
    services = payload.get("blocked_services") if isinstance(payload, dict) else None
    if not isinstance(services, list):
        raise ValueError("the catalog has no blocked_services array")

    found: dict[str, App] = {}
    for entry in services[:MAX_APPS]:
        app = _app_from(entry)
        if app is not None and app.id not in found:
            found[app.id] = app
    if len(found) < MIN_APPS:
        raise ValueError(f"the catalog holds {len(found)} usable apps, fewer than {MIN_APPS}")

    apps = dict(sorted(found.items(), key=lambda item: (item[1].name.casefold(), item[0])))
    listed = [str(g.get("id") or "") for g in payload.get("groups") or [] if isinstance(g, dict)]
    used = {app.group for app in apps.values()}
    groups = [g for g in dict.fromkeys(listed) if g in used]
    groups.extend(sorted(used - set(groups)))

    data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
    return Catalog(
        apps=apps,
        groups=tuple(groups),
        source=source,
        fingerprint=hashlib.sha256(data).hexdigest()[:16],
        suffixes=_index(apps, "names"),
        exacts=_index(apps, "exact"),
    )


@functools.cache
def bundled() -> Catalog:
    try:
        return parse_catalog(BUNDLED.read_bytes(), "bundled")
    except (OSError, ValueError) as exc:
        log.error("the bundled app catalog at %s is unusable: %s", BUNDLED, exc)
        return EMPTY


class UnknownApp(StoreError):
    pass


@dataclass(frozen=True)
class AppBlock:
    id: str
    tags: tuple[str, ...] = ()
    days: tuple[int, ...] = ()
    starts_at: str = ""
    ends_at: str = ""
    enabled: bool = True
    created_at: str | None = None

    @property
    def window(self) -> schedule.Window:
        return schedule.Window(days=self.days, starts_at=self.starts_at, ends_at=self.ends_at)

    @property
    def scheduled(self) -> bool:
        return bool(self.days or self.starts_at)

    def applies_to(self, device: Device | None) -> bool:
        return targets(self.tags, device)

    def as_row(self) -> dict:
        return {
            "id": self.id,
            "tags": list(self.tags),
            "days": list(self.days),
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }


def block_from_row(row: object) -> AppBlock | None:
    if not isinstance(row, dict):
        return None
    app_id = str(row.get("id") or "")
    if not ID_PATTERN.match(app_id):
        return None
    try:
        starts_at, ends_at = window_fields(
            str(row.get("starts_at") or ""), str(row.get("ends_at") or "")
        )
    except StoreError:
        starts_at, ends_at = "", ""
    created = row.get("created_at")
    return AppBlock(
        id=app_id,
        tags=normalise_tags(row.get("tags")),
        days=schedule.normalise_days(row.get("days")),
        starts_at=starts_at,
        ends_at=ends_at,
        enabled=bool(row.get("enabled", True)),
        created_at=str(created) if created else None,
    )


def validate(
    app_id: str, tags: list[str], days: list[int], starts_at: str, ends_at: str
) -> tuple[str, tuple[str, ...], tuple[int, ...], str, str]:
    app_id = (app_id or "").strip().lower()
    if not ID_PATTERN.match(app_id):
        raise StoreError("an app id is lowercase letters, digits, dashes or underscores")
    if len(tags) > MAX_TAGS:
        raise StoreError(f"an app block targets at most {MAX_TAGS} tags")
    starts_at, ends_at = window_fields(starts_at, ends_at)
    return app_id, normalise_tags(tags), schedule.normalise_days(days), starts_at, ends_at


class Apps:
    def __init__(
        self,
        client: httpx.AsyncClient | None,
        store: object | None,
        catalog: Catalog | None = None,
    ) -> None:
        self._client = client
        self._store = store
        self._dir = Path(settings.app_dir) if settings.app_dir else None
        self.catalog = catalog if catalog is not None else bundled()
        self.catalog_fetched_at: float | None = None
        self.catalog_error = ""
        self._etag = ""
        self._last_modified = ""
        self._catalog_due_at = 0.0
        self._blocks: dict[str, AppBlock] = {}
        self._active: frozenset[str] | None = None
        self._active_checked = 0.0
        self._reach: dict[str, frozenset[str]] = {}
        self._roster_seen: object = None
        self._hits: dict[str, int] = {}
        self._seen: dict[str, dict[str, float]] = {}
        self._task: asyncio.Task | None = None
        self.last_sync_at = 0.0
        self.last_error = ""

    @property
    def blocks(self) -> list[AppBlock]:
        return list(self._blocks.values())

    def block(self, app_id: str) -> AppBlock | None:
        return self._blocks.get(app_id)

    @property
    def editable(self) -> bool:
        return bool(getattr(self._store, "writable", False))

    def _forget_reach(self) -> None:
        self._active = None
        self._active_checked = 0.0
        self._reach = {}

    def active(self) -> frozenset[str]:
        if not self._blocks:
            return frozenset()
        stamp = time.monotonic()
        if self._active is not None and stamp - self._active_checked < ACTIVE_FOR_S:
            return self._active

        moment = schedule.now()
        known = self.catalog.apps
        active = frozenset(
            b.id
            for b in self._blocks.values()
            if b.enabled and b.id in known and b.window.covers(moment)
        )
        self._active_checked = stamp
        if active != self._active:
            self._active = active
            self._reach = {}
        return active

    def _device(self, device_id: str) -> Device | None:
        getter = getattr(self._store, "get", None)
        return getter(device_id) if getter is not None else None

    def _reach_for(self, device_id: str, active: frozenset[str]) -> frozenset[str]:
        roster = getattr(self._store, "roster", None)
        if roster is not self._roster_seen or len(self._reach) > MAX_REACH_CACHE:
            self._reach = {}
            self._roster_seen = roster
        found = self._reach.get(device_id)
        if found is None:
            device = self._device(device_id)
            found = frozenset(i for i in active if self._blocks[i].applies_to(device))
            self._reach[device_id] = found
        return found

    def match(self, device_id: str, name: str) -> App | None:
        if not self._blocks:
            return None
        owners = self.catalog.owners(name)
        if not owners:
            return None
        active = self.active()
        if not active:
            return None
        reach = self._reach_for(device_id, active)
        for app_id in owners:
            if app_id in reach:
                app = self.catalog.apps[app_id]
                if app.covers(name):
                    return app
        return None

    def hit(self, app_id: str | None) -> None:
        if app_id in self._blocks:
            self._hits[app_id] = self._hits.get(app_id, 0) + 1

    def hits_of(self, app_id: str) -> int:
        return self._hits.get(app_id, 0)

    def note(self, device_id: str, name: str, at: float) -> None:
        for app_id in self.catalog.owners(name):
            if not self.catalog.apps[app_id].covers(name):
                continue
            devices = self._seen.setdefault(app_id, {})
            devices.pop(device_id, None)
            devices[device_id] = at
            if len(devices) > MAX_SEEN_DEVICES:
                devices.pop(next(iter(devices)))

    def _excepted_by_device(self, device_id: str, name: str) -> bool:
        rules_for = getattr(self._store, "rules_for", None)
        return rules_for is not None and rules_for(device_id).allows(name)

    def everywhere(self, name: str) -> bool:
        for app_id in self.catalog.owners(name):
            block = self._blocks.get(app_id)
            if block is None or not block.enabled or block.tags or block.scheduled:
                continue
            if not self.catalog.apps[app_id].covers(name):
                continue
            roster = getattr(self._store, "roster", None) or []
            return not any(self._excepted_by_device(d.id, name) for d in roster)
        return False

    def naming(self, name: str) -> list[dict]:
        found: list[dict] = []
        for app_id in dict.fromkeys(self.catalog.owners(name)):
            app = self.catalog.apps[app_id]
            if not app.covers(name):
                continue
            block = self._blocks.get(app_id)
            found.append(
                {
                    "id": app.id,
                    "name": app.name,
                    "group": app.group,
                    "blocked": block is not None,
                    "enabled": bool(block and block.enabled),
                    "tags": list(block.tags) if block else [],
                    "when": self.when(block) if block else "",
                }
            )
        return found

    @staticmethod
    def when(block: AppBlock) -> str:
        return block.window.describe() if block.scheduled else "always"

    def reaches(self, block: AppBlock) -> list[str]:
        roster = getattr(self._store, "roster", None) or []
        return [d.id for d in roster if block.applies_to(d)]

    def _seen_rows(self, app_id: str) -> list[dict]:
        devices = self._seen.get(app_id) or {}
        return [
            {"id": device_id, "name": getattr(self._device(device_id), "name", device_id), "at": at}
            for device_id, at in sorted(devices.items(), key=lambda item: -item[1])
        ]

    def as_dict(self, block: AppBlock) -> dict:
        app = self.catalog.apps.get(block.id)
        active = self.active()
        return {
            **block.as_row(),
            "name": app.name if app else block.id,
            "group": app.group if app else OTHER_GROUP,
            "known": app is not None,
            "when": self.when(block),
            "active_now": block.id in active,
            "hits": self.hits_of(block.id),
            "reaches": self.reaches(block),
            "seen": self._seen_rows(block.id),
        }

    def seen(self) -> list[dict]:
        rows = [
            {"id": app_id, "devices": len(devices), "last_at": max(devices.values())}
            for app_id, devices in self._seen.items()
            if devices and app_id in self.catalog.apps
        ]
        rows.sort(key=lambda row: (-row["last_at"], row["id"]))
        return rows

    def catalog_payload(self) -> dict:
        catalog = self.catalog
        return {
            "fingerprint": catalog.fingerprint,
            "source": catalog.source,
            "fetched_at": self.catalog_fetched_at,
            "groups": list(catalog.groups),
            "apps": [app.as_dict() for app in catalog.apps.values()],
        }

    def summary(self) -> dict:
        return {
            "apps": len(self.catalog.apps),
            "blocked": len(self._blocks),
            "active": len(self.active()),
            "hits": sum(self._hits.values()),
            "catalog_source": self.catalog.source,
            "catalog_fetched_at": self.catalog_fetched_at,
            "catalog_error": self.catalog_error,
            "on_disk": self._dir is not None,
            "last_sync_at": self.last_sync_at or None,
            "last_error": self.last_error,
        }

    def _paths(self) -> tuple[Path, Path, Path] | None:
        if self._dir is None:
            return None
        return (
            self._dir / "blocks.json",
            self._dir / "catalog.json",
            self._dir / "catalog-meta.json",
        )

    def _save_blocks(self) -> None:
        paths = self._paths()
        if paths is None:
            return
        payload = {"written_at": time.time(), "blocks": [b.as_row() for b in self.blocks]}
        try:
            lists._atomic_write(paths[0], json.dumps(payload))
        except OSError as exc:
            log.warning("could not write app blocks to %s: %s", paths[0], exc)

    def _save_meta(self) -> None:
        paths = self._paths()
        if paths is None:
            return
        meta = {
            "etag": self._etag,
            "last_modified": self._last_modified,
            "fetched_at": self.catalog_fetched_at,
            "fingerprint": self.catalog.fingerprint,
            "last_error": self.catalog_error,
        }
        try:
            lists._atomic_write(paths[2], json.dumps(meta))
        except OSError as exc:
            log.warning("could not write the app catalog record to %s: %s", paths[2], exc)

    def _save_catalog(self, body: bytes) -> None:
        paths = self._paths()
        if paths is None:
            return
        try:
            lists._atomic_write(paths[1], body.decode("utf-8", errors="replace"))
        except OSError as exc:
            log.warning("could not write the app catalog to %s: %s", paths[1], exc)
        self._save_meta()

    def _read_disk(self) -> tuple[list[AppBlock] | None, Catalog | None, dict]:
        paths = self._paths()
        if paths is None or not self._dir or not self._dir.is_dir():
            return None, None, {}
        blocks_path, catalog_path, meta_path = paths

        blocks: list[AppBlock] | None = None
        try:
            payload = json.loads(blocks_path.read_text(encoding="utf-8"))
            rows = payload.get("blocks") if isinstance(payload, dict) else None
            if isinstance(rows, list):
                blocks = [b for b in (block_from_row(r) for r in rows) if b is not None]
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("app blocks at %s are unreadable, ignoring them: %s", blocks_path, exc)

        catalog: Catalog | None = None
        meta: dict = {}
        try:
            catalog = parse_catalog(catalog_path.read_bytes(), "downloaded")
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = loaded if isinstance(loaded, dict) else {}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning(
                "the app catalog at %s is unusable, keeping the bundled one: %s", catalog_path, exc
            )
        return blocks, catalog, meta

    async def load(self) -> int:
        blocks, catalog, meta = await asyncio.to_thread(self._read_disk)
        if catalog is not None:
            self.catalog = catalog
            self._etag = str(meta.get("etag") or "")
            self._last_modified = str(meta.get("last_modified") or "")
            fetched = meta.get("fetched_at")
            self.catalog_fetched_at = float(fetched) if isinstance(fetched, (int, float)) else None
        if blocks is not None:
            self._blocks = {b.id: b for b in blocks}
            self._forget_reach()
            log.info("loaded %s app block(s) from %s", len(self._blocks), self._dir)
        return len(self._blocks)

    def _adopt(self, blocks: list[AppBlock]) -> None:
        self._blocks = {b.id: b for b in blocks}
        self._hits = {k: v for k, v in self._hits.items() if k in self._blocks}
        self._forget_reach()
        self._save_blocks()

    async def sync(self) -> bool:
        if not getattr(self._store, "configured", False):
            return False
        try:
            rows = await self._store.app_block_rows()  # type: ignore[union-attr]
        except StoreError as exc:
            self.last_error = str(exc)
            log.warning(
                "app block registry read failed, keeping the %s in hand: %s", len(self._blocks), exc
            )
            return False
        if not isinstance(rows, list):
            self.last_error = "the registry returned something other than a list"
            return False
        self._adopt([b for b in (block_from_row(r) for r in rows) if b is not None])
        self.last_error = ""
        self.last_sync_at = time.time()
        return True

    def _require_app(self, app_id: str) -> App:
        app = self.catalog.apps.get(app_id)
        if app is None:
            raise UnknownApp(f"no app with id {app_id!r} in the catalog")
        return app

    async def put(
        self,
        app_id: str,
        *,
        tags: list[str],
        days: list[int],
        starts_at: str,
        ends_at: str,
        enabled: bool,
    ) -> AppBlock:
        app_id, wanted_tags, wanted_days, starts_at, ends_at = validate(
            app_id, tags, days, starts_at, ends_at
        )
        self._require_app(app_id)
        async with self._store.lock:  # type: ignore[union-attr]
            await self._store.write_app_block(  # type: ignore[union-attr]
                {
                    "id": app_id,
                    "tags": list(wanted_tags),
                    "days": list(wanted_days),
                    "starts_at": starts_at,
                    "ends_at": ends_at,
                    "enabled": bool(enabled),
                }
            )
            await self.sync()
        found = self._blocks.get(app_id)
        if found is None:
            raise StoreError(f"app block {app_id!r} vanished while it was written")
        return found

    async def remove(self, app_id: str) -> bool:
        async with self._store.lock:  # type: ignore[union-attr]
            if app_id not in self._blocks:
                raise UnknownApp(f"{app_id!r} is not blocked")
            await self._store.delete_app_block(app_id)  # type: ignore[union-attr]
            await self.sync()
        return app_id not in self._blocks

    async def refresh_catalog(self) -> bool:
        url = settings.app_catalog_url
        if not url or self._client is None:
            return False
        try:
            outcome = await asyncio.wait_for(
                lists.download(
                    self._client,
                    url,
                    etag=self._etag if self.catalog.source == "downloaded" else "",
                    last_modified=self._last_modified
                    if self.catalog.source == "downloaded"
                    else "",
                    cap=MAX_CATALOG_BYTES,
                ),
                lists.FETCH_TIMEOUT_S,
            )
        except TimeoutError:
            return self._catalog_failed(
                f"the download took longer than {lists.FETCH_TIMEOUT_S:.0f} seconds"
            )
        except lists.FetchError as exc:
            return self._catalog_failed(str(exc))
        except httpx.HTTPError as exc:
            return self._catalog_failed(
                f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            )

        if outcome is None:
            self.catalog_fetched_at = time.time()
            self.catalog_error = ""
            await asyncio.to_thread(self._save_meta)
            return False

        body, etag, last_modified = outcome
        try:
            catalog = await asyncio.to_thread(parse_catalog, body, "downloaded")
        except ValueError as exc:
            return self._catalog_failed(str(exc))

        changed = catalog.fingerprint != self.catalog.fingerprint
        self.catalog = catalog
        self._etag = etag
        self._last_modified = last_modified
        self.catalog_fetched_at = time.time()
        self.catalog_error = ""
        self._forget_reach()
        log.info("app catalog holds %s app(s) from %s", len(catalog.apps), url)
        await asyncio.to_thread(self._save_catalog, body)
        return changed

    def _catalog_failed(self, reason: str) -> bool:
        self.catalog_error = reason
        log.warning("the app catalog kept its previous copy: %s", reason)
        self._save_meta()
        return False

    async def tick(self) -> None:
        await self.sync()
        if time.time() < self._catalog_due_at:
            return
        await self.refresh_catalog()
        wait = RETRY_S if self.catalog_error else max(MIN_TICK_S, settings.app_catalog_refresh_s)
        self._catalog_due_at = time.time() + wait

    def start_background(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="hydra-apps")

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("app block refresh failed")
            await asyncio.sleep(max(MIN_TICK_S, float(settings.store_refresh_s)))

    async def aclose(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
