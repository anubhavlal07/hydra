from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import time
from xml.sax.saxutils import escape

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from .. import pauses, quiet, safesearch, schedule
from ..config import settings
from ..store import MAX_TEMPORARY_MINUTES, MIN_TEMPORARY_MINUTES, Device, StoreError
from .access import ReadDep, WriteDep

log = logging.getLogger(__name__)

router = APIRouter(tags=["setup"])

APPLE_CONFIG_TYPE = "application/x-apple-aspen-config"
BUNDLE_ID = "io.github.hydra-dns"


def _store(request: Request):
    return request.app.state.devices


def _csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in (",", '"', "\n", "\r")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _split_csv_line(line: str) -> list[str]:
    cells: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(line):
        char = line[index]
        if quoted:
            if char == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    current.append('"')
                    index += 1
                else:
                    quoted = False
            else:
                current.append(char)
        elif char == '"':
            quoted = True
        elif char == ",":
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def _parse_device_csv(text: str) -> list[dict]:
    lines = [line for line in text.replace("\r\n", "\n").split("\n") if line.strip()]
    if len(lines) < 2:
        return []

    header = [cell.strip().lower() for cell in _split_csv_line(lines[0])]
    if "name" not in header:
        return []

    rows: list[dict] = []
    for line in lines[1:]:
        cells = _split_csv_line(line)
        record = dict(zip(header, cells, strict=False))
        name = (record.get("name") or "").strip()
        if not name:
            continue
        rows.append(
            {
                "id": (record.get("id") or "").strip().lower(),
                "name": name[:60],
                "tags": [t for t in (record.get("tags") or "").replace(";", " ").split() if t],
                "rules": [r for r in (record.get("rules") or "").replace(";", " ").split() if r],
            }
        )
    return rows


def _resolver_url(token: str) -> str:
    return f"{settings.public_api_base}/dns-query/{token}"


def _setup_url(token: str) -> str:
    return f"{settings.public_api_base}/setup/{token}"


def _stable_uuid(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}".upper()


def _dot_hostname(token: str) -> str:
    base = settings.dot_base_domain
    return f"{token}.{base}" if base else ""


def _device_payload(device: Device, store: object | None = None) -> dict:
    token = device.token
    url = _resolver_url(token)
    dot_host = _dot_hostname(token)
    held = getattr(store, "pauses", None)
    return device.as_dict() | {
        "paused_until": (held.until(device.id) or None) if held else None,
        "safe_search_covers": safesearch.covers(),
        "token": token,
        "doh_url": url,
        "dot_hostname": dot_host,
        "json_url": f"{url}?name=example.com&type=A",
        "mobileconfig_url": f"{_setup_url(token)}/hydra.mobileconfig",
        "setup_url": _setup_url(token),
        "clients": {
            "firefox": {
                "path": "Settings > Privacy & Security > DNS over HTTPS > Max Protection, Custom",
                "value": url,
            },
            "chrome": {
                "path": "Settings > Privacy and security > Security > Use secure DNS > Custom",
                "value": url,
            },
            "apple": {
                "path": "Open the profile link in Safari on the device, then "
                "Settings > Profile Downloaded > Install",
                "value": f"{settings.public_api_base}/setup/{token}/hydra.mobileconfig",
            },
            "android": {
                "path": "Settings > Network & internet > Private DNS > Private DNS provider "
                "hostname. Paste the hostname exactly -- no tls:// and no port",
                "value": dot_host,
            },
            "windows": {
                "path": "Windows 11 native DoH needs a fixed server IP, which neither host "
                "provides. Use the browser setting above, or a local forwarder",
                "value": url,
            },
            "dnsproxy": {
                "path": "For plain DNS on a router or anything with no encrypted-DNS support, "
                "run AdGuard dnsproxy on the LAN and forward to Hydra over DoT",
                "value": f"dnsproxy -l 0.0.0.0 -p 53 -u tls://{dot_host}"
                if dot_host
                else f"dnsproxy -l 127.0.0.1 -p 53 -u {url}",
            },
        },
    }


@router.get("/api/devices", dependencies=[ReadDep])
async def list_devices(request: Request) -> dict:
    store = _store(request)
    seen = {row["device"]: row for row in request.app.state.metrics.devices()}
    silent = quiet.quiet_devices(request.app.state)

    devices = []
    for device in store.devices:
        payload = _device_payload(device, store)
        payload["activity"] = seen.get(device.id)
        payload["quiet_since"] = silent.get(device.id)
        devices.append(payload)

    return {
        "devices": devices,
        "anonymous_allowed": settings.allow_anonymous,
        "base_url": settings.public_api_base,
        "store": store.snapshot(),
        "tags": store.tags(),
    }


@router.get("/api/native-dns", dependencies=[ReadDep])
async def native_dns(request: Request) -> dict:
    manager = request.app.state.manager
    now = time.monotonic()
    pool_by_label = {u.label: u for u in request.app.state.pool.upstreams}

    rows = []
    for account in manager.accounts:
        addresses = account.dns_addresses or {}
        dot_url = addresses.get("dns_over_tls_url") or ""
        hostname = dot_url.replace("tls://", "").strip("/")
        upstream = pool_by_label.get(account.label)

        rows.append(
            {
                "account": account.label,
                "device_id": account.device_id or None,
                "private_dns_hostname": hostname or None,
                "dot_url": dot_url or None,
                "doq_url": addresses.get("dns_over_quic_url") or None,
                "doh_url": addresses.get("dns_over_https_url") or None,
                "adguard_app_link": addresses.get("adguard_dns_over_tls_url") or None,
                "ip_addresses": [
                    ip.get("ip_address") for ip in (addresses.get("ip_addresses") or [])
                ],
                "requests_used": account.requests_used,
                "requests_limit": account.requests_limit,
                "requests_remaining": (
                    max(0, account.requests_limit - account.requests_used)
                    if account.requests_limit > 0
                    else -1
                ),
                "available": bool(upstream and upstream.available(now)),
            }
        )

    rows.sort(key=lambda r: r["requests_remaining"], reverse=True)
    return {"accounts": rows, "recommended": rows[0]["account"] if rows else None}


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    id: str | None = Field(default=None, max_length=40)


class DeviceRename(BaseModel):
    name: str = Field(min_length=1, max_length=60)


def _guard_writable(store) -> None:
    if not store.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "store_unconfigured",
                "message": "Device storage is not configured, so devices can only be changed "
                "through HYDRA_DEVICES.",
            },
        )
    if not store.writable:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "store_unavailable",
                "message": "Device storage has not been reachable since startup, so the list "
                "shown is the fallback from HYDRA_DEVICES. "
                + (store.last_error or "Try again shortly."),
            },
        )


@router.post("/api/devices", dependencies=[WriteDep], status_code=201)
async def create_device(request: Request, body: DeviceCreate) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        device = await store.add(body.name, body.id)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    log.info("device added: %s (%s)", device.id, device.name)
    return _device_payload(device, store)


@router.patch("/api/devices/{device_id}", dependencies=[WriteDep])
async def rename_device(request: Request, device_id: str, body: DeviceRename) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        device = await store.rename(device_id, body.name)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc
    return _device_payload(device, store)


class DevicePolicy(BaseModel):
    safe_search: bool = False
    safe_search_skip: list[str] | None = Field(default=None, max_length=10)
    block_mode: str = Field(default="zero", pattern="^(zero|nxdomain|address)$")
    block_address: str = Field(default="", max_length=253)


@router.put("/api/devices/{device_id}/policy", dependencies=[WriteDep])
async def set_device_policy(request: Request, device_id: str, body: DevicePolicy) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        device = await store.set_policy(
            device_id,
            body.safe_search,
            body.block_mode,
            body.block_address,
            body.safe_search_skip,
        )
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    log.info(
        "device policy written: %s (safe search %s, skipping %s, block %s)",
        device.id,
        "on" if device.safe_search else "off",
        ", ".join(device.safe_search_skip) or "nothing",
        device.block_mode,
    )
    return _device_payload(device, store)


class DevicePause(BaseModel):
    minutes: int = Field(default=15, ge=1, le=pauses.MAX_MINUTES)


def _known_device(store, device_id: str) -> Device:
    device = store.get(device_id)
    if device is None:
        raise HTTPException(
            status_code=404, detail={"code": "no_device", "message": f"no device {device_id!r}"}
        )
    return device


@router.post("/api/devices/{device_id}/pause", dependencies=[WriteDep])
async def pause_device(request: Request, device_id: str, body: DevicePause) -> dict:
    store = _store(request)
    device = _known_device(store, device_id)
    until = store.pauses.hold(device.id, body.minutes)
    log.info("filtering paused for %s for %s minutes", device.id, body.minutes)
    return _device_payload(device, store) | {"paused_until": until}


@router.delete("/api/devices/{device_id}/pause", dependencies=[WriteDep])
async def resume_device(request: Request, device_id: str) -> dict:
    store = _store(request)
    device = _known_device(store, device_id)
    if store.pauses.release(device.id):
        log.info("filtering resumed for %s", device.id)
    return _device_payload(device, store)


class Rewrite(BaseModel):
    name: str = Field(min_length=1, max_length=253)
    address: str = Field(min_length=2, max_length=45)


@router.get("/api/rewrites", dependencies=[ReadDep])
async def list_rewrites(request: Request) -> dict:
    store = _store(request)
    return {
        "rewrites": [{"name": n, "address": a} for n, a in sorted(store.rewrites.items())],
        "editable": store.writable,
    }


@router.put("/api/rewrites", dependencies=[WriteDep])
async def set_rewrite(request: Request, body: Rewrite) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        found = await store.set_rewrite(body.name, body.address)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    log.info("rewrite set: %s -> %s", body.name, body.address)
    return {"rewrites": [{"name": n, "address": a} for n, a in sorted(found.items())]}


@router.delete("/api/rewrites/{name}", dependencies=[WriteDep])
async def remove_rewrite(request: Request, name: str) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        found = await store.remove_rewrite(name)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    return {"rewrites": [{"name": n, "address": a} for n, a in sorted(found.items())]}


class DeviceRules(BaseModel):
    rules: list[str] = Field(default_factory=list, max_length=200)


@router.put("/api/devices/{device_id}/rules", dependencies=[WriteDep])
async def set_device_rules(request: Request, device_id: str, body: DeviceRules) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        device = await store.set_rules(device_id, body.rules)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    log.info("device rules written: %s (%s rule(s))", device.id, len(device.rules))
    return _device_payload(device, store)


class RuleEntry(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    action: str = Field(pattern="^(block|allow|clear)$")


class GroupRuleEntry(RuleEntry):
    model_config = ConfigDict(extra="forbid")


class DeviceRuleEntry(RuleEntry):
    minutes: int | None = Field(default=None, ge=MIN_TEMPORARY_MINUTES, le=MAX_TEMPORARY_MINUTES)
    temporary: bool = False


@router.post("/api/devices/{device_id}/rules/entry", dependencies=[WriteDep])
async def device_rule_entry(request: Request, device_id: str, body: DeviceRuleEntry) -> dict:
    store = _store(request)
    _guard_writable(store)
    _known_device(store, device_id)
    try:
        device = await store.rule_entry(
            device_id, body.domain, body.action, body.minutes, body.temporary
        )
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    detail: dict = {
        "domain": body.domain,
        "action": body.action,
        "rules": len(device.rules),
    }
    if body.minutes is not None and body.action != "clear":
        detail["minutes"] = body.minutes
    if body.temporary:
        detail["temporary"] = True
    request.state.audit_detail = detail
    log.info(
        "device rule %s: %s on %s%s",
        body.action,
        body.domain,
        device.id,
        f" for {body.minutes} minute(s)" if "minutes" in detail else "",
    )
    return {"id": device.id, "rules": device.as_dict()["rules"]}


class RuleGroupBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    rules: list[str] = Field(default_factory=list, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=10)
    days: list[int] = Field(default_factory=list, max_length=7)
    starts_at: str = Field(default="", max_length=5)
    ends_at: str = Field(default="", max_length=5)
    enabled: bool = True


@router.get("/api/rule-groups", dependencies=[ReadDep])
async def list_rule_groups(request: Request) -> dict:
    store = _store(request)
    return {
        "groups": [group.as_dict() for group in store.groups],
        "active": sorted(store.active_groups()),
        "timezone": str(schedule.zone()),
        "editable": store.writable,
        "tags": store.tags(),
    }


@router.put("/api/rule-groups/{group_id}", dependencies=[WriteDep])
async def set_rule_group(request: Request, group_id: str, body: RuleGroupBody) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        group = await store.set_group(
            group_id,
            name=body.name,
            rules=body.rules,
            tags=body.tags,
            days=body.days,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            enabled=body.enabled,
        )
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {
        "rules": len(group.rules),
        "tags": list(group.tags),
        "when": group.window.describe(),
        "enabled": group.enabled,
    }
    log.info(
        "rule group written: %s (%s rule(s), %s)",
        group.id,
        len(group.rules),
        group.window.describe(),
    )
    return group.as_dict()


@router.post("/api/rule-groups/{group_id}/rules/entry", dependencies=[WriteDep])
async def group_rule_entry(request: Request, group_id: str, body: GroupRuleEntry) -> dict:
    store = _store(request)
    _guard_writable(store)
    if store.group(group_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "no_group", "message": f"no rule group {group_id!r}"},
        )
    try:
        group = await store.group_rule_entry(group_id, body.domain, body.action)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {
        "domain": body.domain,
        "action": body.action,
        "rules": len(group.rules),
    }
    log.info("schedule rule %s: %s on %s", body.action, body.domain, group.id)
    return group.as_dict()


@router.delete("/api/rule-groups/{group_id}", dependencies=[WriteDep])
async def remove_rule_group(request: Request, group_id: str) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        removed = await store.remove_group(group_id)
    except StoreError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    log.info("rule group removed: %s", group_id)
    return {"removed": removed, "id": group_id}


class DeviceBudget(BaseModel):
    hourly_budget: int = Field(default=0, ge=0, le=1_000_000)


@router.put("/api/devices/{device_id}/budget", dependencies=[WriteDep])
async def set_device_budget(request: Request, device_id: str, body: DeviceBudget) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        device = await store.set_budget(device_id, body.hourly_budget)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {"hourly_budget": device.hourly_budget}
    log.info("device budget written: %s (%s/hour)", device.id, device.hourly_budget or "default")
    return _device_payload(device, store)


class DeviceTags(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=10)


@router.put("/api/devices/{device_id}/tags", dependencies=[WriteDep])
async def set_device_tags(request: Request, device_id: str, body: DeviceTags) -> dict:
    store = _store(request)
    _guard_writable(store)
    try:
        device = await store.set_tags(device_id, body.tags)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {"tags": list(device.tags)}
    return _device_payload(device, store)


class BulkBody(BaseModel):
    devices: list[str] = Field(min_length=1, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=10)
    rules: list[str] | None = Field(default=None, max_length=200)
    safe_search: bool | None = None
    safe_search_skip: list[str] | None = Field(default=None, max_length=10)
    block_mode: str | None = Field(default=None, pattern="^(zero|nxdomain|address)$")
    block_address: str | None = Field(default=None, max_length=253)


@router.post("/api/devices/bulk", dependencies=[WriteDep])
async def bulk_devices(request: Request, body: BulkBody) -> dict:
    store = _store(request)
    _guard_writable(store)

    if len(body.devices) > settings.bulk_max_devices:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "too_many",
                "message": f"a bulk change covers at most {settings.bulk_max_devices} devices",
            },
        )

    nothing_asked = all(
        field is None for field in (body.tags, body.rules, body.safe_search, body.block_mode)
    )
    if nothing_asked:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "nothing_to_do",
                "message": "a bulk change needs tags, rules or a policy",
            },
        )

    policy_change = None
    if body.safe_search is not None or body.block_mode is not None:
        policy_change = {
            "safe_search": bool(body.safe_search),
            "safe_search_skip": body.safe_search_skip,
            "block_mode": body.block_mode or "zero",
            "block_address": body.block_address or "",
        }

    try:
        result = await store.bulk_apply(
            body.devices, tags=body.tags, rules=body.rules, policy=policy_change
        )
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {
        "devices": len(body.devices),
        "applied": len(result["applied"]),
        "failed": len(result["failed"]),
    }
    log.info("bulk change applied to %s device(s)", len(result["applied"]))
    return {**result, "tags": store.tags()}


@router.get("/api/devices/export", dependencies=[ReadDep])
async def export_devices(request: Request) -> Response:
    store = _store(request)
    lines = ["id,name,tags,rules,safe_search,block_mode,block_address"]
    for device in store.devices:
        lines.append(
            ",".join(
                _csv_cell(value)
                for value in (
                    device.id,
                    device.name,
                    " ".join(device.tags),
                    " ".join(device.rules),
                    "yes" if device.safe_search else "no",
                    device.block_mode,
                    device.block_address,
                )
            )
        )

    return Response(
        content="\n".join(lines) + "\n",
        media_type="text/csv",
        headers={
            "content-disposition": 'attachment; filename="hydra-devices.csv"',
            "cache-control": "no-store",
        },
    )


class DeviceImport(BaseModel):
    csv: str = Field(min_length=1, max_length=200_000)
    dry_run: bool = False


@router.post("/api/devices/import", dependencies=[WriteDep])
async def import_devices(request: Request, body: DeviceImport) -> dict:
    store = _store(request)
    _guard_writable(store)

    rows = _parse_device_csv(body.csv)
    if not rows:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "empty",
                "message": "no usable rows; the first line must name the columns, "
                "and a row needs at least a name",
            },
        )
    if len(rows) > settings.bulk_max_devices:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "too_many",
                "message": f"an import covers at most {settings.bulk_max_devices} devices",
            },
        )

    planned = [row for row in rows if store.get(row["id"]) is None] if rows else []
    if body.dry_run:
        return {
            "dry_run": True,
            "would_add": [row["id"] for row in planned],
            "would_update": [row["id"] for row in rows if store.get(row["id"]) is not None],
            "rows": len(rows),
        }

    added: list[str] = []
    updated: list[str] = []
    skipped: dict[str, str] = {}

    for row in rows:
        try:
            if store.get(row["id"]) is None:
                created = await store.add(row["name"], row["id"] or None)
                added.append(created.id)
                row["id"] = created.id
            else:
                updated.append(row["id"])
            if row["tags"]:
                await store.set_tags(row["id"], row["tags"])
            if row["rules"]:
                await store.set_rules(row["id"], row["rules"])
        except StoreError as exc:
            skipped[row["id"] or row["name"]] = str(exc)
        await asyncio.sleep(0)

    request.state.audit_detail = {
        "added": len(added),
        "updated": len(updated),
        "skipped": len(skipped),
    }
    log.info(
        "device import: %s added, %s updated, %s skipped",
        len(added),
        len(updated),
        len(skipped),
    )
    return {"added": added, "updated": updated, "skipped": skipped, "dry_run": False}


@router.delete("/api/devices/{device_id}", dependencies=[WriteDep])
async def delete_device(request: Request, device_id: str) -> dict:
    store = _store(request)
    _guard_writable(store)

    device = store.get(device_id)
    if device is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "no such device"}
        )
    if device.source == "env":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "seed_device",
                "message": f"{device_id!r} is showing because the registry could not be read, "
                "so it came from HYDRA_DEVICES. Removing it has to happen there.",
            },
        )

    try:
        removed = await store.remove(device_id)
    except StoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "store_error", "message": str(exc)}
        ) from exc

    request.app.state.metrics.forget_presence(device_id)
    log.info("device removed: %s", device_id)
    return {"removed": removed, "id": device_id}


SETUP_PAGE_POLICY = (
    "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; "
    "base-uri 'none'; form-action 'none'"
)

SETUP_PAGE_STYLE = """
:root { color-scheme: light dark; --bg: #f6f6f4; --fg: #1b1b1d; --muted: #6b6b70;
  --card: #ffffff; --line: #e2e2de; --accent: #2f6fde; --accent-fg: #ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #0e0e10; --fg: #ececee; --muted: #9a9aa2; --card: #17171a; --line: #2a2a2f; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 32rem; margin: 0 auto; padding: 24px 16px 40px; }
h1 { font-size: 1.5rem; margin: 0 0 20px; overflow-wrap: anywhere; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px; margin-bottom: 12px; }
h2 { font-size: .8rem; letter-spacing: .06em; text-transform: uppercase; color: var(--muted);
  margin: 0 0 10px; font-weight: 600; }
p { margin: 10px 0 0; color: var(--muted); font-size: .875rem; }
code { display: block; font: 14px/1.5 ui-monospace, Menlo, Consolas, monospace;
  overflow-wrap: anywhere; user-select: all; -webkit-user-select: all; padding: 10px 12px;
  border: 1px solid var(--line); border-radius: 8px; }
a.install { display: flex; align-items: center; justify-content: center; min-height: 48px;
  border-radius: 10px; background: var(--accent); color: var(--accent-fg); font-weight: 600;
  text-decoration: none; }
"""


def _setup_page(device: Device) -> str:
    token = device.token
    name = html.escape(device.name)
    service = html.escape(settings.service_name)
    profile = html.escape(f"/setup/{token}/hydra.mobileconfig", quote=True)
    dot_host = _dot_hostname(token)
    android = (
        f"""
<section>
<h2>Android — Private DNS</h2>
<code>{html.escape(dot_host)}</code>
<p>Settings → Network &amp; internet → Private DNS → Private DNS provider hostname</p>
</section>"""
        if dot_host
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<meta name="color-scheme" content="light dark">
<title>{service} · {name}</title>
<style>{SETUP_PAGE_STYLE}</style>
</head>
<body>
<main>
<h1>{name}</h1>
<section>
<h2>iPhone, iPad, Mac</h2>
<a class="install" href="{profile}">Install profile</a>
<p>Settings → Profile Downloaded → Install</p>
</section>{android}
<section>
<h2>Browsers and apps — DoH</h2>
<code>{html.escape(_resolver_url(token))}</code>
</section>
</main>
</body>
</html>
"""


@router.get("/setup/{token}")
async def setup_page(request: Request, token: str) -> Response:
    device = _store(request).by_token(token)
    if device is None:
        raise HTTPException(status_code=404, detail="no such device")

    return Response(
        content=_setup_page(device),
        media_type="text/html; charset=utf-8",
        headers={
            "cache-control": "no-store, private",
            "content-security-policy": SETUP_PAGE_POLICY,
            "x-robots-tag": "noindex, nofollow",
        },
    )


@router.get("/setup/{token}/hydra.mobileconfig")
async def mobileconfig(request: Request, token: str) -> Response:
    device = _store(request).by_token(token)
    if device is None:
        raise HTTPException(status_code=404, detail="no such device")

    url = escape(_resolver_url(token))
    name = escape(f"{settings.service_name} ({device.name})")
    outer_uuid = _stable_uuid(BUNDLE_ID, device.id, "outer")
    inner_uuid = _stable_uuid(BUNDLE_ID, device.id, "dns")

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>PayloadType</key>
      <string>com.apple.dnsSettings.managed</string>
      <key>PayloadIdentifier</key>
      <string>{BUNDLE_ID}.dns.{escape(device.id)}</string>
      <key>PayloadUUID</key>
      <string>{inner_uuid}</string>
      <key>PayloadVersion</key>
      <integer>1</integer>
      <key>PayloadDisplayName</key>
      <string>{name}</string>
      <key>DNSSettings</key>
      <dict>
        <key>DNSProtocol</key>
        <string>HTTPS</string>
        <key>ServerURL</key>
        <string>{url}</string>
      </dict>
    </dict>
  </array>
  <key>PayloadDisplayName</key>
  <string>{name}</string>
  <key>PayloadIdentifier</key>
  <string>{BUNDLE_ID}.{escape(device.id)}</string>
  <key>PayloadType</key>
  <string>Configuration</string>
  <key>PayloadUUID</key>
  <string>{outer_uuid}</string>
  <key>PayloadVersion</key>
  <integer>1</integer>
  <key>PayloadDescription</key>
  <string>Routes DNS on this device through {escape(settings.service_name)}.</string>
  <key>PayloadRemovalDisallowed</key>
  <false/>
</dict>
</plist>
"""
    return Response(
        content=plist,
        media_type=APPLE_CONFIG_TYPE,
        headers={
            "content-disposition": f'attachment; filename="hydra-{device.id}.mobileconfig"',
            "cache-control": "no-store, private",
        },
    )
