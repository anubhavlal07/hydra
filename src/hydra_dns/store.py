from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

import httpx

from . import localzones, pauses, safesearch, schedule
from . import rules as ruleset
from .config import DeviceConfig, device_token, settings
from .decision import (
    ALLOWED,
    DEVICE_ALLOW_DECISION,
    DEVICE_RULE,
    DEVICE_RULE_DECISION,
    SCHEDULE,
    Decision,
)

log = logging.getLogger(__name__)

FN_LIST = "hydra_devices_list"
FN_ADD = "hydra_device_add"
FN_REMOVE = "hydra_device_remove"
FN_SEED = "hydra_devices_seed"
FN_RULES = "hydra_device_rules_set"
FN_POLICY = "hydra_device_policy_set"
FN_TAGS = "hydra_device_tags_set"
FN_BUDGET = "hydra_device_budget_set"
FN_GROUPS = "hydra_rule_groups_list"
FN_GROUP_SET = "hydra_rule_group_set"
FN_GROUP_REMOVE = "hydra_rule_group_remove"
FN_BLOCKLISTS = "hydra_blocklists_list"
FN_BLOCKLIST_SET = "hydra_blocklist_set"
FN_BLOCKLIST_REMOVE = "hydra_blocklist_remove"
FN_APP_BLOCKS = "hydra_app_blocks_list"
FN_APP_BLOCK_SET = "hydra_app_block_set"
FN_APP_BLOCK_REMOVE = "hydra_app_block_remove"
FN_REWRITES = "hydra_rewrites_list"
FN_REWRITE_SET = "hydra_rewrite_set"
FN_REWRITE_REMOVE = "hydra_rewrite_remove"

ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?$")


class StoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    source: str = "store"
    rules: tuple[str, ...] = ()
    safe_search: bool = False
    block_mode: str = "zero"
    block_address: str = ""
    tags: tuple[str, ...] = ()
    hourly_budget: int = 0
    safe_search_skip: tuple[str, ...] = ()

    @property
    def token(self) -> str:
        return device_token(self.id)

    @property
    def dot_hostname(self) -> str:
        base = settings.dot_base_domain
        return f"{self.token}.{base}" if base else ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "source": self.source,
            "rules": ruleset.prune(self.rules, time.time()),
            "safe_search": self.safe_search,
            "safe_search_skip": list(self.safe_search_skip),
            "block_mode": self.block_mode,
            "block_address": self.block_address,
            "tags": list(self.tags),
            "hourly_budget": self.hourly_budget,
            "dot_hostname": self.dot_hostname,
        }

    def as_row(self) -> dict:
        return {
            key: value
            for key, value in self.as_dict().items()
            if key not in ("source", "dot_hostname")
        }


def targets(tags: tuple[str, ...], device: Device | None) -> bool:
    if not tags:
        return True
    return device is not None and bool(set(tags) & set(device.tags))


@dataclass(frozen=True)
class RuleGroup:
    id: str
    name: str
    rules: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    days: tuple[int, ...] = ()
    starts_at: str = ""
    ends_at: str = ""
    enabled: bool = True

    @property
    def window(self) -> schedule.Window:
        return schedule.Window(days=self.days, starts_at=self.starts_at, ends_at=self.ends_at)

    def applies_to(self, device: Device) -> bool:
        return targets(self.tags, device)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "rules": list(self.rules),
            "tags": list(self.tags),
            "days": list(self.days),
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "enabled": self.enabled,
            "when": self.window.describe(),
        }


def _positive(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _device_from_row(row: object, source: str) -> Device | None:
    if not isinstance(row, dict):
        return None
    device_id = str(row.get("id") or "")
    if not ID_PATTERN.match(device_id):
        return None
    raw = row.get("rules")
    labels = row.get("tags")
    return Device(
        id=device_id,
        name=str(row.get("name") or device_id),
        source=source,
        rules=tuple(str(r) for r in raw) if isinstance(raw, list) else (),
        safe_search=bool(row.get("safe_search")),
        block_mode=str(row.get("block_mode") or "zero"),
        block_address=str(row.get("block_address") or ""),
        tags=normalise_tags(labels) if isinstance(labels, list) else (),
        hourly_budget=_positive(row.get("hourly_budget")),
        safe_search_skip=_skip_from(row.get("safe_search_skip")),
    )


def _skip_from(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    covered = set(safesearch.covers())
    return tuple(str(v) for v in value if str(v) in covered)


def _checked_skip(values: list[str]) -> tuple[str, ...]:
    try:
        return safesearch.normalise_skip(values)
    except ValueError as exc:
        raise StoreError(str(exc)) from exc


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def slugify(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:32]
    if not base:
        base = "device"
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise StoreError(f"could not find a free id for {name!r}")


MAX_TAGS = 10
MAX_GROUP_RULES = 200
MAX_DEVICE_RULES = 200
MAX_TEMPORARY_RULES = 20
MIN_TEMPORARY_MINUTES = 5
MAX_TEMPORARY_MINUTES = 7 * 24 * 60
GROUP_ID_PATTERN = re.compile(r"^[a-z0-9_-]{1,40}$")
ACTIVE_FOR_S = 20.0
MAX_HOURLY_BUDGET = 1_000_000
TAG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9 _-]{0,23}$")


def normalise_tags(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    seen: list[str] = []
    for raw in values:
        tag = re.sub(r"\s+", " ", str(raw or "").strip().lower())
        if tag and TAG_PATTERN.match(tag) and tag not in seen:
            seen.append(tag)
    return tuple(seen[:MAX_TAGS])


def _rows_to_groups(rows: list) -> list[RuleGroup]:
    out: list[RuleGroup] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        group_id = str(row.get("id") or "")
        if not GROUP_ID_PATTERN.match(group_id):
            log.warning("ignoring rule group with unusable id %r", group_id)
            continue
        raw = row.get("rules")
        labels = row.get("tags")
        out.append(
            RuleGroup(
                id=group_id,
                name=str(row.get("name") or group_id),
                rules=tuple(str(r) for r in raw) if isinstance(raw, list) else (),
                tags=normalise_tags(labels) if isinstance(labels, list) else (),
                days=schedule.normalise_days(row.get("days")),
                starts_at=str(row.get("starts_at") or ""),
                ends_at=str(row.get("ends_at") or ""),
                enabled=bool(row.get("enabled", True)),
            )
        )
    return out


def window_fields(starts_at: str, ends_at: str) -> tuple[str, str]:
    starts_at = (starts_at or "").strip()
    ends_at = (ends_at or "").strip()
    if bool(starts_at) != bool(ends_at):
        raise StoreError("a time window needs both a start and an end, or neither")
    if starts_at and ends_at:
        if schedule.minutes(starts_at) is None or schedule.minutes(ends_at) is None:
            raise StoreError("a time window is two 24-hour clock times, like 22:00 and 07:00")
        if starts_at == ends_at:
            raise StoreError("a window that starts and ends at the same minute covers nothing")
    return starts_at, ends_at


def _group_row(
    group_id: str,
    name: str,
    rules: list[str],
    tags: list[str],
    days: list[int],
    starts_at: str,
    ends_at: str,
    enabled: bool,
) -> dict:
    group_id = (group_id or "").strip().lower()
    if not GROUP_ID_PATTERN.match(group_id):
        raise StoreError(
            "a rule group id is lowercase letters, digits, dashes or underscores, "
            "up to 40 characters"
        )

    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise StoreError("a rule group needs a name of 1 to 60 characters")

    if len(rules) > MAX_GROUP_RULES:
        raise StoreError(f"a rule group holds at most {MAX_GROUP_RULES} rules")
    bad = ruleset.invalid(rules)
    if bad:
        raise StoreError(f"not a domain: {', '.join(bad[:3])}")
    if ruleset.temporary(rules):
        raise StoreError("a temporary entry belongs to one device, not to a rule group")

    wanted_tags = normalise_tags(tags)
    if len(tags) > MAX_TAGS:
        raise StoreError(f"a rule group targets at most {MAX_TAGS} tags")

    starts_at, ends_at = window_fields(starts_at, ends_at)

    return {
        "p_id": group_id,
        "p_name": name,
        "p_rules": [str(r) for r in rules],
        "p_tags": list(wanted_tags),
        "p_days": list(schedule.normalise_days(days)),
        "p_starts_at": starts_at,
        "p_ends_at": ends_at,
        "p_enabled": bool(enabled),
    }


def _check_device_rules(lines: list[str]) -> None:
    bad = ruleset.invalid(lines)
    if bad:
        raise StoreError(f"not a domain: {', '.join(bad[:3])}")
    if len(ruleset.temporary(lines)) > MAX_TEMPORARY_RULES:
        raise StoreError(f"a device holds at most {MAX_TEMPORARY_RULES} temporary entries")


def expiry_for(minutes: int | None, now: float) -> float | None:
    if minutes is None:
        return None
    if not MIN_TEMPORARY_MINUTES <= minutes <= MAX_TEMPORARY_MINUTES:
        raise StoreError(
            f"a temporary entry lasts {MIN_TEMPORARY_MINUTES} minutes to "
            f"{MAX_TEMPORARY_MINUTES // 1440} days"
        )
    return float(int(now) + minutes * 60)


def _entry_domain(domain: str, action: str) -> str:
    if action not in ruleset.ENTRY_ACTIONS:
        raise StoreError("action is block, allow or clear")
    target = ruleset.normalise(domain)
    if target is None:
        raise StoreError(f"not a domain: {domain!r}")
    return target


class DeviceStore:
    def __init__(self, client: httpx.AsyncClient, seed: list[DeviceConfig]) -> None:
        self._client = client
        self._seed = [Device(id=d.id, name=d.name, source="env") for d in seed]
        self._devices: list[Device] = list(self._seed)
        self._lock = asyncio.Lock()
        self._compiled: dict[str, ruleset.RuleSet] = {}
        self._groups: list[RuleGroup] = []
        self._group_rules: dict[str, ruleset.RuleSet] = {}
        self._effective: dict[str, ruleset.RuleSet] = {}
        self._active: frozenset[str] | None = None
        self._active_checked: float = 0.0
        self._rewrites: dict[str, str] = dict(settings.rewrites)
        self._ptrs: dict[str, tuple[str, ...]] = localzones.ptr_index(self._rewrites)
        self.pauses = pauses.Pauses()
        self.last_sync_at: float = 0.0
        self.last_error: str = ""
        self._synced_once = False

    @property
    def configured(self) -> bool:
        return bool(settings.store_url and settings.store_key and settings.store_secret)

    @property
    def writable(self) -> bool:
        return self.configured and self._synced_once

    @property
    def devices(self) -> list[Device]:
        return list(self._devices)

    @property
    def roster(self) -> list[Device]:
        return self._devices

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    async def blocklist_rows(self) -> object:
        return await self._rpc(FN_BLOCKLISTS, {})

    async def write_blocklist(self, row: dict) -> object:
        return await self._rpc(
            FN_BLOCKLIST_SET,
            {
                "p_id": row["id"],
                "p_name": row["name"],
                "p_url": row["url"],
                "p_tags": list(row.get("tags") or []),
                "p_allow": list(row.get("allow") or []),
                "p_enabled": bool(row.get("enabled", True)),
            },
        )

    async def delete_blocklist(self, list_id: str) -> object:
        return await self._rpc(FN_BLOCKLIST_REMOVE, {"p_id": list_id})

    async def app_block_rows(self) -> object:
        return await self._rpc(FN_APP_BLOCKS, {})

    async def write_app_block(self, row: dict) -> object:
        return await self._rpc(
            FN_APP_BLOCK_SET,
            {
                "p_id": row["id"],
                "p_tags": list(row.get("tags") or []),
                "p_days": list(row.get("days") or []),
                "p_starts_at": str(row.get("starts_at") or ""),
                "p_ends_at": str(row.get("ends_at") or ""),
                "p_enabled": bool(row.get("enabled", True)),
            },
        )

    async def delete_app_block(self, app_id: str) -> object:
        return await self._rpc(FN_APP_BLOCK_REMOVE, {"p_id": app_id})

    @property
    def rewrites(self) -> dict[str, str]:
        return dict(self._rewrites)

    def rewrite_for(self, name: str) -> str | None:
        return self._rewrites.get(name)

    def ptr_for(self, name: str) -> tuple[str, ...]:
        return self._ptrs.get(name, ())

    async def set_rewrite(self, name: str, address: str) -> dict[str, str]:
        name = name.strip().strip(".").lower()
        if not ruleset.DOMAIN.match(name):
            raise StoreError(f"not a hostname: {name!r}")
        try:
            ipaddress.ip_address(address.strip())
        except ValueError as exc:
            raise StoreError(f"not an IP address: {address!r}") from exc

        async with self._lock:
            await self._rpc(FN_REWRITE_SET, {"p_name": name, "p_address": address.strip()})
            await self.sync()
        return self.rewrites

    async def remove_rewrite(self, name: str) -> dict[str, str]:
        async with self._lock:
            await self._rpc(FN_REWRITE_REMOVE, {"p_name": name.strip().lower()})
            await self.sync()
        return self.rewrites

    async def _sync_rewrites(self) -> None:
        try:
            rows = await self._rpc(FN_REWRITES, {})
        except Exception as exc:
            log.warning("rewrite sync failed, keeping the %s in hand: %s", len(self._rewrites), exc)
            return

        if isinstance(rows, list):
            self._replace_rewrites(
                {
                    str(r.get("name") or "").lower(): str(r.get("address") or "")
                    for r in rows
                    if isinstance(r, dict) and r.get("name") and r.get("address")
                }
                or dict(settings.rewrites)
            )

    def _replace_rewrites(self, rewrites: dict[str, str]) -> None:
        self._rewrites = rewrites
        self._ptrs = localzones.ptr_index(rewrites)

    async def _sync_groups(self) -> None:
        try:
            rows = await self._rpc(FN_GROUPS, {})
        except Exception as exc:
            log.warning(
                "rule group sync failed, keeping the %s in hand: %s", len(self._groups), exc
            )
            return

        if isinstance(rows, list):
            self._adopt_groups(_rows_to_groups(rows))

    def get(self, device_id: str) -> Device | None:
        return next((d for d in self._devices if d.id == device_id), None)

    def by_token(self, token: str) -> Device | None:
        for device in self._devices:
            if hmac.compare_digest(device.token, token):
                return device
        return None

    async def _rpc(self, function: str, payload: dict) -> object:
        if not self.configured:
            raise StoreError("device storage is not configured (HYDRA_STORE_URL / _KEY / _SECRET)")
        url = f"{settings.store_url}/rest/v1/rpc/{function}"
        body = {"p_secret": settings.store_secret, **payload}
        try:
            response = await self._client.post(
                url,
                json=body,
                headers={
                    "apikey": settings.store_key,
                    "authorization": f"Bearer {settings.store_key}",
                    "content-type": "application/json",
                },
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise StoreError(f"could not reach device storage: {exc}") from exc

        if response.status_code >= 400:
            detail = response.text[:200]
            if "28000" in detail or "unauthorized" in detail:
                raise StoreError("device storage rejected the shared secret")
            raise StoreError(f"device storage returned HTTP {response.status_code}: {detail}")

        try:
            return response.json()
        except ValueError as exc:
            raise StoreError("device storage returned a non-JSON response") from exc

    @staticmethod
    def _rows_to_devices(rows: object) -> list[Device]:
        if not isinstance(rows, list):
            return []
        out: list[Device] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            device = _device_from_row(row, "store")
            if device is None:
                log.warning("ignoring device row with unusable id %r", str(row.get("id") or ""))
                continue
            out.append(device)
        return out

    def _adopt(self, devices: list[Device]) -> None:
        if not devices:
            log.warning("device registry is empty; nothing can resolve until a device is added")
        self._replace_devices(devices)
        self._write_cache(devices)

    def _replace_devices(self, devices: list[Device]) -> None:
        self._devices = devices
        self._compiled = {d.id: ruleset.parse(d.rules) for d in devices}
        self._effective.clear()

    def _adopt_groups(self, groups: list[RuleGroup]) -> None:
        self._groups = groups
        self._group_rules = {g.id: ruleset.parse(g.rules) for g in groups}
        self._effective.clear()
        self._active = None
        self._active_checked = 0.0

    @property
    def groups(self) -> list[RuleGroup]:
        return list(self._groups)

    def active_groups(self) -> frozenset[str]:
        if not self._groups:
            return frozenset()

        stamp = time.monotonic()
        if self._active is not None and stamp - self._active_checked < ACTIVE_FOR_S:
            return self._active

        moment = schedule.now()
        active = frozenset(g.id for g in self._groups if g.enabled and g.window.covers(moment))
        self._active_checked = stamp
        if active != self._active:
            self._active = active
            self._effective.clear()
        return active

    def _own_rules(self, device_id: str) -> ruleset.RuleSet:
        compiled = self._compiled.get(device_id)
        if compiled is None:
            device = self.get(device_id)
            compiled = ruleset.parse(device.rules) if device is not None else ruleset.RuleSet()
            self._compiled[device_id] = compiled
        return compiled

    def _compose(self, device_id: str, active: frozenset[str]) -> ruleset.RuleSet:
        own = self._own_rules(device_id)
        device = self.get(device_id)
        if device is None or not active:
            return own

        block = list(own.block)
        allow = list(own.allow)
        for group in self._groups:
            if group.id not in active or not group.applies_to(device):
                continue
            extra = self._group_rules.get(group.id)
            if extra is None:
                continue
            block.extend(rule for rule in extra.block if rule not in block)
            allow.extend(rule for rule in extra.allow if rule not in allow)

        return replace(own, block=tuple(block), allow=tuple(allow))

    def rules_for(self, device_id: str) -> ruleset.RuleSet:
        active = self.active_groups()
        effective = self._effective.get(device_id)
        if effective is not None:
            lapses = effective.next_expiry
            if lapses != ruleset.NEVER and lapses <= time.time():
                self._compiled.pop(device_id, None)
                effective = None
        if effective is None:
            effective = self._compose(device_id, active)
            self._effective[device_id] = effective
        return effective

    def blocks(self, device_id: str, name: str) -> bool:
        return self.rules_for(device_id).blocks(name)

    def explain(self, device_id: str, name: str) -> Decision | None:
        effective = self.rules_for(device_id)
        if not effective:
            return None
        allowed = effective.allows(name)
        if not allowed and not effective.blocks(name):
            return None

        own = self._own_rules(device_id)
        brief = ruleset.deciding(
            own.temporary_allow if allowed else own.temporary_block,
            name,
        )
        if brief is not None:
            return Decision(
                layer=ALLOWED if allowed else DEVICE_RULE,
                label=DEVICE_ALLOW_DECISION.label if allowed else DEVICE_RULE,
                blocked=not allowed,
                rule=brief,
                expires_at=own.expires_at(brief, allowed),
            )

        device = self.get(device_id)
        active = self.active_groups()
        reaching = [
            group
            for group in self._groups
            if device is not None and group.id in active and group.applies_to(device)
        ]

        if allowed:
            rule = ruleset.deciding(own.allow, name)
            if rule is not None:
                return Decision(layer=ALLOWED, label=DEVICE_ALLOW_DECISION.label, rule=rule)
            for group in reaching:
                extra = self._group_rules.get(group.id)
                rule = ruleset.deciding(extra.allow, name) if extra is not None else None
                if rule is not None:
                    return Decision(
                        layer=ALLOWED,
                        label=f"allowed by @@ schedule {group.name}",
                        source=group.id,
                        rule=rule,
                    )
            return None

        rule = ruleset.deciding(own.block, name)
        if rule is not None:
            return Decision(layer=DEVICE_RULE, label=DEVICE_RULE, blocked=True, rule=rule)
        for group in reaching:
            extra = self._group_rules.get(group.id)
            rule = ruleset.deciding(extra.block, name) if extra is not None else None
            if rule is not None:
                return Decision(
                    layer=SCHEDULE,
                    label=f"schedule {group.name}",
                    blocked=True,
                    source=group.id,
                    rule=rule,
                )
        return DEVICE_RULE_DECISION

    async def set_policy(
        self,
        device_id: str,
        safe_search: bool,
        block_mode: str,
        block_address: str,
        safe_search_skip: list[str] | None = None,
    ) -> Device:
        block_mode = (block_mode or "zero").strip().lower()
        if block_mode not in ("zero", "nxdomain", "address"):
            raise StoreError(f"block mode must be zero, nxdomain or address, not {block_mode!r}")

        block_address = (block_address or "").strip()
        if block_mode == "address":
            if not block_address:
                raise StoreError("an address block needs somewhere to point")
            if not _is_address(block_address) and not ruleset.DOMAIN.match(block_address.lower()):
                raise StoreError(f"not an address or a hostname: {block_address!r}")
        else:
            block_address = ""

        params: dict = {
            "p_id": device_id,
            "p_safe_search": bool(safe_search),
            "p_block_mode": block_mode,
            "p_block_address": block_address,
        }
        if safe_search_skip is not None:
            params["p_safe_search_skip"] = list(_checked_skip(safe_search_skip))

        async with self._lock:
            if self.get(device_id) is None:
                raise StoreError(f"no device with id {device_id!r}")
            await self._rpc(FN_POLICY, params)
            await self.sync()

        found = self.get(device_id)
        if found is None:
            raise StoreError(f"device {device_id!r} vanished while its policy was written")
        return found

    async def set_tags(self, device_id: str, values: list[str]) -> Device:
        tags = normalise_tags(values)
        if len(values) > MAX_TAGS:
            raise StoreError(f"a device carries at most {MAX_TAGS} tags")
        rejected = [
            str(v) for v in values if str(v or "").strip() and str(v).strip().lower() not in tags
        ]
        if rejected:
            raise StoreError(
                "a tag is lowercase letters, digits, spaces, dashes or underscores, "
                f"up to 24 characters: {', '.join(rejected[:3])}"
            )

        async with self._lock:
            if self.get(device_id) is None:
                raise StoreError(f"no device with id {device_id!r}")
            await self._rpc(FN_TAGS, {"p_id": device_id, "p_tags": list(tags)})
            await self.sync()

        found = self.get(device_id)
        if found is None:
            raise StoreError(f"device {device_id!r} vanished while its tags were written")
        return found

    async def set_budget(self, device_id: str, hourly: int) -> Device:
        try:
            hourly = int(hourly)
        except (TypeError, ValueError):
            raise StoreError("an hourly ceiling is a whole number of queries") from None
        if hourly < 0:
            raise StoreError("an hourly ceiling cannot be negative")
        if hourly > MAX_HOURLY_BUDGET:
            raise StoreError(f"an hourly ceiling tops out at {MAX_HOURLY_BUDGET}")

        async with self._lock:
            if self.get(device_id) is None:
                raise StoreError(f"no device with id {device_id!r}")
            await self._rpc(FN_BUDGET, {"p_id": device_id, "p_hourly": hourly})
            await self.sync()

        found = self.get(device_id)
        if found is None:
            raise StoreError(f"device {device_id!r} vanished while its budget was written")
        return found

    async def set_group(
        self,
        group_id: str,
        *,
        name: str,
        rules: list[str],
        tags: list[str],
        days: list[int],
        starts_at: str,
        ends_at: str,
        enabled: bool,
    ) -> RuleGroup:
        row = _group_row(group_id, name, rules, tags, days, starts_at, ends_at, enabled)
        async with self._lock:
            await self._put_group(row)
        return self._written_group(row["p_id"])

    async def group_rule_entry(self, group_id: str, domain: str, action: str) -> RuleGroup:
        target = _entry_domain(domain, action)
        async with self._lock:
            group = self.group(group_id)
            if group is None:
                raise StoreError(f"no rule group with id {group_id!r}")
            rules = ruleset.edit(group.rules, target, action)
            row = _group_row(
                group.id,
                group.name,
                rules,
                list(group.tags),
                list(group.days),
                group.starts_at,
                group.ends_at,
                group.enabled,
            )
            await self._put_group(row)
        return self._written_group(group.id)

    async def _put_group(self, row: dict) -> None:
        await self._rpc(FN_GROUP_SET, row)
        await self._sync_groups()

    def _written_group(self, group_id: str) -> RuleGroup:
        found = self.group(group_id)
        if found is None:
            raise StoreError(f"rule group {group_id!r} vanished while it was written")
        return found

    async def remove_group(self, group_id: str) -> bool:
        async with self._lock:
            if self.group(group_id) is None:
                raise StoreError(f"no rule group with id {group_id!r}")
            await self._rpc(FN_GROUP_REMOVE, {"p_id": group_id})
            await self._sync_groups()
        return self.group(group_id) is None

    def group(self, group_id: str) -> RuleGroup | None:
        return next((g for g in self._groups if g.id == group_id), None)

    def tags(self) -> list[str]:
        return sorted({tag for device in self._devices for tag in device.tags})

    async def bulk_apply(
        self,
        device_ids: list[str],
        *,
        tags: list[str] | None = None,
        rules: list[str] | None = None,
        policy: dict | None = None,
    ) -> dict:
        if tags is None and rules is None and policy is None:
            raise StoreError("a bulk change needs tags, rules or a policy")
        if rules is not None:
            _check_device_rules(rules)
        skip = policy.get("safe_search_skip") if policy is not None else None
        if skip is not None:
            skip = list(_checked_skip(skip))

        wanted = normalise_tags(tags) if tags is not None else None
        applied: list[str] = []
        failed: dict[str, str] = {}

        async with self._lock:
            for device_id in device_ids:
                if self.get(device_id) is None:
                    failed[device_id] = "no such device"
                    continue
                try:
                    if wanted is not None:
                        await self._rpc(FN_TAGS, {"p_id": device_id, "p_tags": list(wanted)})
                    if rules is not None:
                        await self._rpc(FN_RULES, {"p_id": device_id, "p_rules": rules})
                    if policy is not None:
                        params = {
                            "p_id": device_id,
                            "p_safe_search": bool(policy.get("safe_search")),
                            "p_block_mode": str(policy.get("block_mode") or "zero"),
                            "p_block_address": str(policy.get("block_address") or ""),
                        }
                        if skip is not None:
                            params["p_safe_search_skip"] = skip
                        await self._rpc(FN_POLICY, params)
                    applied.append(device_id)
                except StoreError as exc:
                    failed[device_id] = str(exc)
                await asyncio.sleep(0)

            await self.sync()

        return {"applied": applied, "failed": failed}

    async def set_rules(self, device_id: str, lines: list[str]) -> Device:
        _check_device_rules(lines)

        async with self._lock:
            await self._put_rules(device_id, lines)
        return self._written_device(device_id)

    async def rule_entry(
        self,
        device_id: str,
        domain: str,
        action: str,
        minutes: int | None = None,
        only_temporary: bool = False,
    ) -> Device:
        target = _entry_domain(domain, action)
        if only_temporary and action != "clear":
            raise StoreError("only a clear can be limited to the temporary entry")
        now = time.time()
        expires_at = expiry_for(minutes, now) if action != "clear" else None
        async with self._lock:
            device = self.get(device_id)
            if device is None:
                raise StoreError(f"no device with id {device_id!r}")
            lines = ruleset.edit(
                ruleset.prune(device.rules, now), target, action, expires_at, only_temporary
            )
            if len(lines) > MAX_DEVICE_RULES:
                raise StoreError(f"a device holds at most {MAX_DEVICE_RULES} rules")
            if len(ruleset.temporary(lines)) > MAX_TEMPORARY_RULES:
                raise StoreError(f"a device holds at most {MAX_TEMPORARY_RULES} temporary entries")
            await self._put_rules(device_id, lines)
        return self._written_device(device_id)

    async def sweep_expired(self) -> int:
        if not self.writable:
            return 0
        now = time.time()
        if not any(ruleset.lapsed(d.rules, now) for d in self._devices):
            return 0

        swept = 0
        async with self._lock:
            for device in list(self._devices):
                kept = ruleset.prune(device.rules, now)
                if len(kept) == len(device.rules):
                    continue
                try:
                    await self._rpc(FN_RULES, {"p_id": device.id, "p_rules": kept})
                except StoreError as exc:
                    log.warning("could not sweep expired entries off %s: %s", device.id, exc)
                    continue
                swept += 1
            if swept:
                await self.sync()
        if swept:
            log.info("swept expired temporary entries off %s device(s)", swept)
        return swept

    async def _put_rules(self, device_id: str, lines: list[str]) -> None:
        if self.get(device_id) is None:
            raise StoreError(f"no device with id {device_id!r}")
        await self._rpc(FN_RULES, {"p_id": device_id, "p_rules": lines})
        await self.sync()

    def _written_device(self, device_id: str) -> Device:
        found = self.get(device_id)
        if found is None:
            raise StoreError(f"device {device_id!r} vanished while its rules were being written")
        return found

    @property
    def _cache_path(self) -> Path | None:
        return Path(settings.device_cache_file) if settings.device_cache_file else None

    def _write_cache(self, devices: list[Device]) -> None:
        path = self._cache_path
        if path is None:
            return
        payload = {
            "written_at": time.time(),
            "devices": [d.as_row() for d in devices],
            "rewrites": self._rewrites,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", dir=path.parent, prefix=".devices-", suffix=".tmp", delete=False
            ) as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
                temp = Path(handle.name)
            temp.replace(path)
        except OSError as exc:
            log.warning("could not write the device cache to %s: %s", path, exc)

    def _load_cache(self) -> list[Device]:
        path = self._cache_path
        if path is None or not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("devices") if isinstance(payload, dict) else None
            parsed = (_device_from_row(d, "cache") for d in (rows or []))
            devices = [device for device in parsed if device is not None]
            cached_rewrites = payload.get("rewrites")
            if isinstance(cached_rewrites, dict) and cached_rewrites:
                self._replace_rewrites({str(k): str(v) for k, v in cached_rewrites.items()})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("device cache at %s is unreadable, ignoring it: %s", path, exc)
            return []

        if devices:
            age = time.time() - float(payload.get("written_at") or 0)
            log.info(
                "loaded %s device(s) from the on-disk cache (written %.0f minutes ago)",
                len(devices),
                age / 60,
            )
        return devices

    def _fall_back(self) -> None:
        cached = self._load_cache()
        if cached:
            self._replace_devices(cached)
            return
        log.warning(
            "no usable device cache; falling back to the %s device(s) in HYDRA_DEVICES",
            len(self._seed),
        )
        self._replace_devices(list(self._seed))

    async def sync(self) -> list[Device]:
        if not self.configured:
            self.last_error = ""
            return self.devices
        try:
            rows = await self._rpc(FN_LIST, {})
        except StoreError as exc:
            self.last_error = str(exc)
            log.warning(
                "device registry sync failed, keeping %s cached device(s): %s",
                len(self._devices),
                exc,
            )
            return self.devices

        self._adopt(self._rows_to_devices(rows))
        await self._sync_groups()
        await self._sync_rewrites()
        self.last_error = ""
        self.last_sync_at = time.time()
        self._synced_once = True
        return self.devices

    async def bootstrap(self) -> None:
        if not self.configured:
            log.info("device storage not configured; using %s seed device(s)", len(self._seed))
            return

        function = FN_SEED if settings.store_seed else FN_LIST
        payload = (
            {"p_devices": [{"id": d.id, "name": d.name} for d in self._seed]}
            if settings.store_seed
            else {}
        )
        try:
            rows = await self._rpc(function, payload)
        except StoreError as exc:
            self.last_error = str(exc)
            log.warning("device registry bootstrap failed: %s", exc)
            self._fall_back()
            return

        self._adopt(self._rows_to_devices(rows))
        await self._sync_rewrites()
        self.last_error = ""
        self.last_sync_at = time.time()
        self._synced_once = True
        log.info(
            "device registry ready: %s device(s), %s rewrite(s)",
            len(self._devices),
            len(self._rewrites),
        )

    async def add(self, name: str, device_id: str | None = None) -> Device:
        name = name.strip()
        if not name:
            raise StoreError("a device needs a name")
        if len(name) > 60:
            raise StoreError("device names are limited to 60 characters")

        async with self._lock:
            taken = {d.id for d in self._devices}
            if device_id:
                device_id = device_id.strip().lower()
                if not ID_PATTERN.match(device_id):
                    raise StoreError(
                        "a device id becomes a DNS hostname label, so it must be lowercase "
                        "letters, digits and inner dashes only -- no underscores, and it "
                        "cannot start or end with a dash"
                    )
                if device_id in taken:
                    raise StoreError(f"device id {device_id!r} is already in use")
            else:
                device_id = slugify(name, taken)

            rows = await self._rpc(FN_ADD, {"p_id": device_id, "p_name": name})
            created = self._rows_to_devices(rows)
            await self.sync()
            return created[0] if created else Device(id=device_id, name=name)

    async def rename(self, device_id: str, name: str) -> Device:
        name = name.strip()
        if not name:
            raise StoreError("a device needs a name")
        async with self._lock:
            if self.get(device_id) is None:
                raise StoreError(f"no device with id {device_id!r}")
            rows = await self._rpc(FN_ADD, {"p_id": device_id, "p_name": name})
            created = self._rows_to_devices(rows)
            await self.sync()
            return created[0] if created else Device(id=device_id, name=name)

    async def remove(self, device_id: str) -> bool:
        async with self._lock:
            removed = await self._rpc(FN_REMOVE, {"p_id": device_id})
            await self.sync()
            return bool(removed)

    def start_background(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hydra-devices")

    async def _loop(self) -> None:
        await self.bootstrap()
        while True:
            await asyncio.sleep(settings.store_refresh_s)
            try:
                if self._synced_once:
                    await self.sync()
                else:
                    await self.bootstrap()
                await self.sweep_expired()
            except Exception:
                log.exception("device registry refresh failed")

    async def aclose(self) -> None:
        task = getattr(self, "_task", None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        self._task = None

    def snapshot(self) -> dict:
        sources = {d.source for d in self._devices}
        return {
            "configured": self.configured,
            "writable": self.writable,
            "count": len(self._devices),
            "last_sync_at": self.last_sync_at or None,
            "last_error": self.last_error,
            "using_seed": not self._synced_once,
            "source": "store" if self._synced_once else ("cache" if "cache" in sources else "env"),
            "cache_file": settings.device_cache_file or None,
        }
