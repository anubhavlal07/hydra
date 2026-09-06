from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

import dns.message
import dns.rdatatype
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import certs, drift, quiet, rules, suspicion, watch
from ..accounts_store import AccountStoreError
from ..adguard import policy
from ..adguard.aggregate import (
    decode_cursor,
    merge_counted,
    merge_query_log,
    merge_time_series,
    quota_rollup,
)
from ..adguard.auth import AuthError, TokenManager
from ..config import AccountConfig, settings
from ..policy_store import TRACKED, settings_diff, settings_of
from ..policy_store import diff as diff_rules_against
from ..resolver.metrics import RECENT_QUERIES
from ..resolver.service import answer_to_json, build_query
from .access import ReadDep, WriteDep, actor_of, heavy_guard

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"], dependencies=[ReadDep])

WINDOWS = {
    "1h": 3_600,
    "6h": 21_600,
    "24h": 86_400,
    "7d": 604_800,
    "30d": 2_592_000,
}


def _range_ms(window: str) -> tuple[int, int]:
    seconds = WINDOWS.get(window)
    if seconds is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_window", "message": f"window must be one of {list(WINDOWS)}"},
        )
    now_ms = int(time.time() * 1000)
    return now_ms - seconds * 1000, now_ms


def _state(request: Request) -> Any:
    return request.app.state


@router.get("/overview")
async def overview(request: Request) -> dict:
    state = _state(request)
    manager, pool, metrics, cache = state.manager, state.pool, state.metrics, state.cache

    resolver = metrics.summary()
    accounts = manager.snapshot()
    rollup = quota_rollup(accounts, resolver["cache_hit_rate"])

    certificate = certs.snapshot(settings.dot_cert_file) if settings.dot_enabled else None
    healthy = sum(1 for a in accounts if a["upstream"] and a["upstream"]["available"])
    authed = sum(1 for a in accounts if a["auth"]["authenticated"] and a.get("enabled", True))

    return {
        "service": settings.service_name,
        "resolver_url": f"{settings.public_api_base}/dns-query",
        "strategy": settings.strategy,
        "warnings": settings.warnings(),
        "quota": rollup,
        "resolver": resolver,
        "cache": cache.snapshot(),
        "accounts": {
            "total": len(accounts),
            "enabled": sum(1 for a in accounts if a.get("enabled", True)),
            "authenticated": authed,
            "serving": healthy,
            "discovery_done": manager.discovery_done,
            "last_refresh_at": manager.last_refresh_at or None,
        },
        "upstreams": pool.snapshot(),
        "pool": pool.state(),
        "attention": watch.reasons(
            accounts=accounts,
            quota=rollup,
            pool_state=pool.state(),
            warn_days=settings.quota_warn_days,
            certificate=certificate,
            warn_failures=settings.auth_warn_failures,
            cert_warn_days=settings.cert_warn_days,
            blocklists=state.blocklists.health() if getattr(state, "blocklists", None) else None,
            blocklist_refresh_s=settings.blocklist_refresh_s,
            quiet=quiet.quiet_names(state),
            drift=drift.findings(state),
        ),
        "certificate": certificate,
        "prefetch": getattr(state, "prefetch", None).snapshot()
        if getattr(state, "prefetch", None)
        else None,
        "devices": len(_state(request).devices.devices),
        "saved": _savings(state, metrics, rollup["requests_limit"]),
    }


def _savings(state: Any, metrics: Any, allowance: int) -> dict | None:
    savings = getattr(metrics, "savings", None)
    if savings is None:
        return None
    store = getattr(state, "metrics_store", None)
    partial = bool(store is not None and store.configured and not savings.restored)
    return savings.snapshot(allowance, partial=partial)


@router.get("/accounts")
async def accounts(request: Request) -> dict:
    state = _state(request)
    return {"accounts": state.manager.snapshot(), "store": state.account_store.snapshot()}


class AccountBody(BaseModel):
    username: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    label: str | None = Field(default=None, max_length=24)


@router.post("/accounts", status_code=201, dependencies=[WriteDep])
async def add_account(request: Request, body: AccountBody) -> dict:
    state = _state(request)
    store = state.account_store

    username = body.username.strip().lower()
    if any(a.username.lower() == username for a in store.accounts):
        raise HTTPException(
            status_code=409,
            detail={"code": "duplicate", "message": f"{username} is already in the pool"},
        )

    probe = AccountConfig(label="probe", username=username, password=body.password)
    try:
        await TokenManager("new account", probe.username, probe.password, state.http).token()
    except AuthError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_credentials",
                "message": f"AdGuard refused that sign-in: {exc}",
            },
        ) from exc

    try:
        config = store.add(username, body.password, body.label)
    except AccountStoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "rejected", "message": str(exc)}
        ) from exc

    account = await state.manager.add_account(config)
    adoption = state.manager.policy_adoption.get(account.label) or {}
    request.state.audit_detail = {
        "label": account.label,
        "policy_synced": bool(adoption.get("synced")),
        "policy_left": adoption.get("left") or [],
        "policy_error": str(adoption.get("error") or "")[:200],
    }
    drift.recheck_soon(state)
    return {
        "account": account.snapshot(),
        "store": store.snapshot(),
        "serving": bool(account.doh_url),
        "policy_sync": adoption,
    }


class AccountEnabledBody(BaseModel):
    enabled: bool


@router.patch("/accounts/{label}", dependencies=[WriteDep])
async def set_account_enabled(request: Request, label: str, body: AccountEnabledBody) -> dict:
    state = _state(request)
    request.state.audit_detail = {"enabled": body.enabled}
    try:
        updated = state.account_store.set_enabled(label, body.enabled)
    except AccountStoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "rejected", "message": str(exc)}
        ) from exc

    await state.manager.set_enabled(label, updated.enabled)
    if updated.enabled:
        drift.recheck_soon(state)
    return {
        "label": updated.label,
        "enabled": updated.enabled,
        "store": state.account_store.snapshot(),
        "accounts": state.manager.snapshot(),
    }


@router.delete("/accounts/{label}", dependencies=[WriteDep])
async def remove_account(request: Request, label: str) -> dict:
    state = _state(request)
    try:
        removed = state.account_store.remove(label)
    except AccountStoreError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "rejected", "message": str(exc)}
        ) from exc

    await state.manager.remove_account(label)
    return {
        "removed": removed.label,
        "store": state.account_store.snapshot(),
        "accounts": state.manager.snapshot(),
    }


@router.get("/resolver")
async def resolver_metrics(
    request: Request,
    minutes: int = Query(60, ge=5, le=1440),
) -> dict:
    state = _state(request)
    metrics = state.metrics
    return {
        "summary": metrics.summary(),
        "timeline": metrics.timeline(minutes),
        "devices": metrics.devices(),
        "top_domains": metrics.top_domains(20),
        "top_blocked": metrics.top_domains(20, blocked_only=True),
        "qtypes": metrics.qtypes(),
        "upstreams": state.pool.snapshot(),
        "cache": state.cache.snapshot(),
    }


def _device_roster(state: Any) -> list[dict]:
    seen = state.metrics.device_totals()
    idle = {"queries": 0, "blocked": 0, "last_seen": None, "month_queries": 0}

    rows = [
        {"id": d.id, "name": d.name, "registered": True, **(seen.pop(d.id, None) or idle)}
        for d in state.devices.devices
    ]
    rows += [
        {"id": name, "name": name, "registered": False, **totals} for name, totals in seen.items()
    ]
    return sorted(rows, key=lambda r: (-r["queries"], r["name"]))


@router.get("/resolver/recent")
async def recent_queries(
    request: Request,
    limit: int = Query(100, ge=1, le=600),
    device: str | None = None,
    result: str | None = Query(None, pattern="^(blocked|allowed|error)$"),
    q: str | None = Query(None, max_length=253),
) -> dict:
    state = _state(request)
    return {
        "queries": state.metrics.recent(limit, device, result, settings.hidden_from_logs, q),
        "devices": _device_roster(state),
        "hidden_domains": settings.log_hidden_domains,
        "capacity": RECENT_QUERIES,
        "source": "hydra",
    }


@router.get("/resolver/first-seen")
async def first_seen(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    device: str | None = None,
    suspicious: bool = False,
) -> dict:
    state = _state(request)
    watcher = getattr(state.metrics, "first_seen", None)
    if watcher is None:
        return {"names": [], "watching": False, "tracking": {}, "devices": _device_roster(state)}

    return {
        "names": watcher.recent(limit, device, suspicious),
        "watching": True,
        "tracking": watcher.snapshot(),
        "devices": _device_roster(state),
    }


@router.get("/resolver/history")
async def query_history(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    before: int | None = None,
    device: str | None = None,
    result: str | None = Query(None, pattern="^(blocked|allowed|error)$"),
    q: str | None = Query(None, max_length=253),
) -> dict:
    state = _state(request)
    store = state.metrics_store

    if not store.configured:
        return {"rows": [], "next_before": None, "available": False, "source": "postgres"}

    try:
        rows = await store.history(
            limit=limit, before=before, device=device, search=q, result=result
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "store_unreachable", "message": str(exc)[:200]},
        ) from exc

    visible = [r for r in rows if not settings.hidden_from_logs(str(r.get("name") or ""))]
    return {
        "rows": visible,
        "next_before": rows[-1]["id"] if len(rows) >= limit else None,
        "available": True,
        "hidden_domains": settings.log_hidden_domains,
        "source": "postgres",
    }


@router.get("/stats/timeline")
async def stats_timeline(request: Request, window: str = "24h") -> dict:
    start_ms, end_ms = _range_ms(window)
    manager = _state(request).manager
    results, errors = await manager.cached_fan_out(
        f"time:{window}", lambda a: a.stats_time(start_ms, end_ms)
    )
    series = merge_time_series(results.values())
    return {
        "window": window,
        "series": series,
        "totals": {
            "queries": sum(p["queries"] for p in series),
            "blocked": sum(p["blocked"] for p in series),
        },
        "per_account": {
            label: sum(int((p.get("value") or {}).get("queries", 0)) for p in points)
            for label, points in results.items()
        },
        "errors": errors,
        "source": "adguard",
    }


@router.get("/stats/top")
async def stats_top(
    request: Request,
    kind: str = Query("domains", pattern="^(domains|companies|countries|categories)$"),
    window: str = "24h",
    limit: int = Query(25, ge=1, le=100),
) -> dict:
    start_ms, end_ms = _range_ms(window)
    manager = _state(request).manager

    per_account = max(limit * 2, 50)

    if kind == "domains":
        results, errors = await manager.cached_fan_out(
            f"domains:{window}:{per_account}",
            lambda a: a.stats_domains(start_ms, end_ms, per_account),
        )
        rows = merge_counted(results.values(), lambda e: e.get("domain", ""), "domain", limit)
    elif kind == "companies":
        results, errors = await manager.cached_fan_out(
            f"companies:{window}:{per_account}",
            lambda a: a.stats_companies(start_ms, end_ms, per_account),
        )
        rows = merge_counted(
            results.values(), lambda e: e.get("company_name", ""), "company", limit
        )
    elif kind == "countries":
        results, errors = await manager.cached_fan_out(
            f"countries:{window}:{per_account}",
            lambda a: a.stats_countries(start_ms, end_ms, per_account),
        )
        rows = merge_counted(results.values(), lambda e: e.get("country", ""), "country", limit)
    else:
        results, errors = await manager.cached_fan_out(
            f"categories:{window}", lambda a: a.stats_categories(start_ms, end_ms)
        )
        rows = merge_counted(
            results.values(), lambda e: str(e.get("category_type", "")), "category", limit
        )

    return {"kind": kind, "window": window, "rows": rows, "errors": errors, "source": "adguard"}


@router.get("/query-log")
async def query_log(
    request: Request,
    window: str = "24h",
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    q: str | None = Query(None, max_length=253),
) -> dict:
    start_ms, end_ms = _range_ms(window)
    manager = _state(request).manager
    cursors = decode_cursor(cursor)

    async def fetch(account) -> tuple[list[dict], str | None]:
        return await account.query_log(start_ms, end_ms, limit, cursors.get(account.label))

    results, errors = await manager.fan_out(fetch)
    visible = {
        label: (
            [
                i
                for i in items
                if not settings.hidden_from_logs(str(i.get("domain") or ""))
                and (not q or q.strip().lower() in str(i.get("domain") or "").lower())
            ],
            next_page,
        )
        for label, (items, next_page) in results.items()
    }
    items, next_cursor = merge_query_log(visible, limit)

    return {
        "window": window,
        "items": items,
        "next_cursor": next_cursor,
        "errors": errors,
        "hidden_domains": settings.log_hidden_domains,
        "source": "adguard",
    }


EXPORT_COLUMNS = ("at", "device", "name", "type", "rcode", "cached", "blocked", "upstream")


def _csv_row(values: list[object]) -> str:
    cells = []
    for value in values:
        text = "" if value is None else str(value)
        if any(ch in text for ch in (",", '"', "\n", "\r")):
            text = '"' + text.replace('"', '""') + '"'
        cells.append(text)
    return ",".join(cells) + "\n"


def _export_row(row: dict) -> list[object]:
    return [row.get(column) for column in EXPORT_COLUMNS]


async def _live_pages(state: Any, device: str | None, result: str | None, q: str | None):
    yield state.metrics.recent(600, device, result, settings.hidden_from_logs, q)


async def _history_pages(state: Any, device: str | None, result: str | None, q: str | None):
    store = state.metrics_store
    before: int | None = None
    sent = 0
    page = max(50, min(500, settings.export_page_rows))

    while sent < settings.export_max_rows:
        rows = await store.history(
            limit=min(page, settings.export_max_rows - sent),
            before=before,
            device=device,
            search=q,
            result=result,
        )
        if not rows:
            return
        visible = [r for r in rows if not settings.hidden_from_logs(str(r.get("name") or ""))]
        sent += len(rows)
        before = rows[-1]["id"]
        yield visible
        await asyncio.sleep(0)


@router.get("/query-log/export")
async def export_query_log(
    request: Request,
    source: str = Query("history", pattern="^(live|history)$"),
    format: str = Query("ndjson", pattern="^(ndjson|csv)$"),
    device: str | None = None,
    result: str | None = Query(None, pattern="^(blocked|allowed|error)$"),
    q: str | None = Query(None, max_length=253),
) -> StreamingResponse:
    state = _state(request)

    if source == "history" and not state.metrics_store.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "history_unavailable",
                "message": "query history needs HYDRA_METRICS_PERSIST and the Postgres registry",
            },
        )

    pages = _history_pages if source == "history" else _live_pages
    request.state.audit_detail = {"source": source, "format": format, "device": device}

    async def stream():
        async with heavy_guard(request):
            if format == "csv":
                yield _csv_row(list(EXPORT_COLUMNS))
            async for rows in pages(state, device, result, q):
                if format == "csv":
                    yield "".join(_csv_row(_export_row(row)) for row in rows)
                else:
                    yield "".join(json.dumps(row, default=str) + "\n" for row in rows)
                await asyncio.sleep(0)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    extension = "csv" if format == "csv" else "ndjson"
    return StreamingResponse(
        stream(),
        media_type="text/csv" if format == "csv" else "application/x-ndjson",
        headers={
            "content-disposition": f'attachment; filename="hydra-queries-{stamp}.{extension}"',
            "cache-control": "no-store",
        },
    )


@router.delete("/query-log", dependencies=[WriteDep])
async def purge_query_log(request: Request, device: str | None = None) -> dict:
    state = _state(request)
    if not state.metrics_store.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "history_unavailable",
                "message": "query history needs HYDRA_METRICS_PERSIST and the Postgres registry",
            },
        )
    if not device:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "no_target",
                "message": "name the device whose history should be forgotten",
            },
        )

    try:
        removed = await state.metrics_store.purge(device=device)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail={"code": "store_unreachable", "message": str(exc)[:200]}
        ) from exc

    state.metrics.forget_device(device)
    request.state.audit_detail = {"device": device, "removed": removed}
    log.info("purged %s persisted query row(s) for %s", removed, device)
    return {"removed": removed, "device": device}


@router.get("/adguard/devices")
async def adguard_devices(request: Request) -> dict:
    results, errors = await _state(request).manager.cached_fan_out("devices", lambda a: a.devices())
    rows = [
        {
            "account": label,
            "id": device.get("id"),
            "name": device.get("name"),
            "type": device.get("device_type"),
            "dns_server_id": device.get("dns_server_id"),
        }
        for label, devices in results.items()
        for device in devices
    ]
    return {"devices": rows, "errors": errors, "source": "adguard"}


class PolicyBody(BaseModel):
    rules: list[str] | None = Field(default=None, max_length=policy.MAX_RULES)
    rules_enabled: bool | None = None
    protection: bool | None = None
    filter_lists: list[str] | None = Field(default=None, max_length=200)
    filter_lists_enabled: bool | None = None
    safe_browsing: bool | None = None
    parental_control: bool | None = None
    youtube_safe_search: bool | None = None
    engines_safe_search: bool | None = None


class RuleBody(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    action: str = Field(default="block", pattern="^(block|allow|clear)$")


@router.get("/policy")
async def get_policy(request: Request) -> dict:
    manager = _state(request).manager
    results, errors = await manager.read_policy()
    if not results:
        return {
            "accounts": [],
            "policy": None,
            "catalogue": [],
            "errors": errors,
            "source": "adguard",
        }

    catalogue, catalogue_errors = await manager.cached_fan_out(
        "filter_lists", lambda a: a.filter_lists()
    )
    names: dict[str, str] = {}
    for entry in next(iter(catalogue.values()), []) or []:
        filter_id = str(entry.get("filter_id") or entry.get("id") or "")
        if filter_id:
            names[filter_id] = str(entry.get("name") or filter_id)

    summary = policy.summarise(results)
    summary["filter_list_names"] = names
    return {
        "accounts": sorted(results),
        "policy": summary,
        "rules_text": "\n".join(rule["rule"] for rule in summary["rules"]["rules"]),
        "rules_max": policy.MAX_RULES,
        "errors": {**errors, **catalogue_errors},
        "source": "adguard",
    }


@router.put("/policy", dependencies=[WriteDep])
async def put_policy(request: Request, body: PolicyBody) -> dict:
    changes: dict[str, Any] = {}
    if body.rules is not None:
        changes["rules"] = policy.parse_rules("\n".join(body.rules))
    if body.rules_enabled is not None:
        changes["rules_enabled"] = body.rules_enabled
    if body.protection is not None:
        changes["protection"] = body.protection
    if body.filter_lists is not None:
        changes["filter_lists"] = sorted({f.strip() for f in body.filter_lists if f.strip()})
    if body.filter_lists_enabled is not None:
        changes["filter_lists_enabled"] = body.filter_lists_enabled
    if body.safe_browsing is not None:
        changes["safe_browsing"] = body.safe_browsing
    for key in ("parental_control", "youtube_safe_search", "engines_safe_search"):
        if getattr(body, key) is not None:
            changes[key] = getattr(body, key)

    if not changes:
        raise HTTPException(
            status_code=400,
            detail={"code": "nothing_to_do", "message": "no policy fields were provided"},
        )

    return await _write_policy(request, changes)


@router.post("/policy/rule", dependencies=[WriteDep])
async def change_rule(request: Request, body: RuleBody) -> dict:
    try:
        block = policy.block_rule(body.domain)
        allow = policy.allow_rule(body.domain)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "bad_domain", "message": str(exc)}
        ) from exc

    manager = _state(request).manager
    results, read_errors = await manager.read_policy()
    if not results:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "no_accounts",
                "message": "could not read the current rules from any account: "
                + "; ".join(f"{k}: {v}" for k, v in read_errors.items()),
            },
        )

    current = [
        entry["rule"]
        for entry in policy.diff_rules({label: policy.rules_of(s) for label, s in results.items()})[
            "rules"
        ]
    ]

    if body.action == "block":
        rules = policy.with_rule(current, block)
    elif body.action == "allow":
        rules = policy.with_rule(current, allow)
    else:
        rules = policy.without_rule(policy.without_rule(current, block), allow)

    payload = await _write_policy(request, {"rules": rules, "rules_enabled": True})
    payload["domain"] = body.domain
    payload["action"] = body.action
    payload["rules"] = rules
    return payload


async def _write_policy(request: Request, changes: dict[str, Any], note: str = "") -> dict:
    state = _state(request)
    results, errors = await state.manager.apply_policy(**changes)

    if not results:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "policy_write_failed",
                "message": "no account accepted the change: "
                + "; ".join(f"{k}: {v}" for k, v in errors.items()),
            },
        )

    cleared = state.cache.clear()
    log.info(
        "policy updated on %s of %s account(s); dropped %s cached answers",
        len(results),
        len(results) + len(errors),
        cleared,
    )

    versions = getattr(state, "policy_versions", None)
    if versions is not None:
        await versions.record(
            actor_of(request),
            note or ", ".join(sorted(changes)),
            [str(rule) for rule in (changes.get("rules") or [])],
            {k: v for k, v in changes.items() if k != "rules"},
        )

    request.state.audit_detail = {
        "fields": sorted(changes),
        "applied_to": sorted(results),
        "rule_count": len(changes.get("rules") or []),
    }
    await drift.recheck(state)
    return {
        "applied_to": sorted(results),
        "errors": errors,
        "cache_cleared": cleared,
        "in_sync": not errors,
    }


@router.post("/policy/sync", dependencies=[WriteDep])
async def sync_policy(request: Request) -> dict:
    manager = _state(request).manager
    serving = {a.label for a in manager.accounts if a.enabled}
    results, read_errors = await manager.read_policy()
    read = {label: s for label, s in results.items() if label in serving}
    if not read:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "no_accounts",
                "message": "could not read the current policy from any account: "
                + "; ".join(f"{k}: {v}" for k, v in read_errors.items()),
            },
        )

    odd, _split = policy.outliers(read)
    changes, left = policy.agreed(read, unanimous=False)
    payload = await _write_policy(
        request,
        changes,
        note="synced " + (", ".join(odd) if odd else "every account") + " to the pool",
    )
    request.state.audit_detail = {
        **getattr(request.state, "audit_detail", {}),
        "repaired": odd,
        "left": left,
    }
    payload["repaired"] = odd
    payload["left"] = left
    payload["read_errors"] = {k: v for k, v in read_errors.items() if k in serving}
    return payload


async def _current_policy(request: Request) -> tuple[list[str], dict, dict]:
    manager = _state(request).manager
    results, errors = await manager.read_policy()
    if not results:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "no_accounts",
                "message": "could not read the current policy from any account: "
                + "; ".join(f"{k}: {v}" for k, v in errors.items()),
            },
        )
    summary = policy.summarise(results)
    rules = [entry["rule"] for entry in summary["rules"]["rules"]]
    return rules, settings_of(summary), errors


@router.post("/policy/preview")
async def preview_policy(request: Request, body: PolicyBody) -> dict:
    current_rules, current_settings, errors = await _current_policy(request)

    proposed_rules = (
        policy.parse_rules("\n".join(body.rules)) if body.rules is not None else current_rules
    )
    proposed_settings = {
        key: value
        for key, value in (
            ("protection", body.protection),
            ("safe_browsing", body.safe_browsing),
            ("parental_control", body.parental_control),
            ("youtube_safe_search", body.youtube_safe_search),
            ("engines_safe_search", body.engines_safe_search),
            (
                "filter_lists",
                sorted({f.strip() for f in body.filter_lists if f.strip()})
                if body.filter_lists is not None
                else None,
            ),
        )
        if value is not None
    }

    return {
        "rules": diff_rules_against(current_rules, proposed_rules),
        "settings": settings_diff(current_settings, proposed_settings),
        "errors": errors,
    }


@router.get("/policy/versions")
async def policy_versions(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict:
    versions = getattr(_state(request), "policy_versions", None)
    if versions is None or not versions.configured:
        return {"versions": [], "available": False}

    try:
        rows = await versions.list(limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail={"code": "store_unreachable", "message": str(exc)[:200]}
        ) from exc

    current_rules, current_settings, _ = await _current_policy(request)
    for row in rows:
        row["diff"] = diff_rules_against(current_rules, [str(r) for r in (row.get("rules") or [])])
        row["settings_diff"] = settings_diff(current_settings, row.get("settings") or {})

    return {"versions": rows, "available": True, "current_rules": len(current_rules)}


@router.post("/policy/versions/{version_id}/restore", dependencies=[WriteDep])
async def restore_policy_version(request: Request, version_id: int) -> dict:
    versions = getattr(_state(request), "policy_versions", None)
    if versions is None or not versions.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "versions_unavailable",
                "message": "policy history needs the Postgres registry configured",
            },
        )

    found = await versions.get(version_id)
    if found is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "no such policy version"}
        )

    changes: dict[str, Any] = {"rules": [str(r) for r in (found.get("rules") or [])]}
    for key, value in (found.get("settings") or {}).items():
        if key in TRACKED:
            changes[key] = value

    payload = await _write_policy(request, changes, note=f"restored version {version_id}")
    payload["restored"] = version_id
    return payload


class LookupBody(BaseModel):
    name: str = Field(min_length=1, max_length=253)
    type: str = Field(default="A", max_length=10)
    dnssec: bool = False


@router.post("/lookup", dependencies=[WriteDep])
async def lookup(request: Request, body: LookupBody) -> dict:
    qtype = body.type.upper()
    try:
        dns.rdatatype.from_text(qtype)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail={"code": "bad_type", "message": f"unknown record type {qtype}"}
        ) from exc

    state = _state(request)
    started = time.perf_counter()
    result = await state.resolver.resolve(build_query(body.name, qtype, body.dnssec), "dashboard")
    return {
        "answer": answer_to_json(result.wire),
        "cached": result.cached,
        "blocked": result.blocked,
        "upstream": result.upstream,
        "rcode": result.rcode,
        "ttl": result.ttl,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _peek(cache: Any, name: str, qtype: str) -> dict:
    key = cache.key_for(dns.message.make_query(name, dns.rdatatype.from_text(qtype)))
    return cache.peek(key) if key is not None else {"state": "absent", "ttl": 0}


def _askers(state: Any, name: str) -> list[dict]:
    if settings.hidden_from_logs(name):
        return []
    watcher = getattr(state.metrics, "first_seen", None)
    rows = []
    for row in state.metrics.askers(name):
        known = state.devices.get(row["device"])
        first = watcher.first_at(row["device"], name) if watcher is not None else None
        rows.append(
            {
                **row,
                "name": known.name if known is not None else row["device"],
                "first_at": round(first, 3) if first is not None else None,
            }
        )
    return rows


@router.get("/domain")
async def domain(request: Request, name: str = Query(..., min_length=1, max_length=253)) -> dict:
    wanted = name.strip().strip(".").lower()
    if not rules.DOMAIN.match(wanted):
        raise HTTPException(
            status_code=400, detail={"code": "bad_name", "message": f"not a domain: {name!r}"}
        )

    state = _state(request)
    store = state.devices
    decide = state.resolver.decide
    devices = []
    for device in store.devices[: max(0, settings.bulk_max_devices)]:
        decision = decide(wanted, "A", device.id, known=device)
        devices.append(
            {
                "id": device.id,
                "name": device.name,
                "tags": list(device.tags),
                "decision": decision.as_dict() if decision is not None else None,
                "blocked": decision.blocked if decision is not None else False,
            }
        )

    lists = getattr(state, "blocklists", None)
    apps = getattr(state, "apps", None)
    return {
        "name": wanted,
        "devices": devices,
        "askers": _askers(state, wanted),
        "cache": {"A": _peek(state.cache, wanted, "A"), "AAAA": _peek(state.cache, wanted, "AAAA")},
        "lists": lists.naming(wanted) if lists is not None else [],
        "apps": apps.naming(wanted) if apps is not None else [],
        "suspicion": {
            **suspicion.score(wanted).as_dict(),
            "threshold": settings.suspicious_score,
        },
        "editable": bool(store.writable),
    }


@router.post("/cache/clear", dependencies=[WriteDep])
async def clear_cache(request: Request) -> dict:
    state = _state(request)
    cleared = state.cache.clear()
    state.manager.invalidate_stats_cache()
    return {"cleared": cleared}


BACKUP_VERSION = 1


@router.get("/backup", dependencies=[WriteDep])
async def backup(request: Request, accounts: bool = Query(default=True)) -> dict:
    state = _state(request)
    devices = state.devices

    payload: dict = {
        "version": BACKUP_VERSION,
        "generated_at": time.time(),
        "service": settings.service_name,
        "devices": [
            {
                "id": d.id,
                "name": d.name,
                "rules": list(d.rules),
                "safe_search": d.safe_search,
                "safe_search_skip": list(d.safe_search_skip),
                "block_mode": d.block_mode,
                "block_address": d.block_address,
            }
            for d in devices.devices
        ],
        "rewrites": devices.rewrites,
        "blocklists": [
            {key: value for key, value in s.meta.as_row().items() if key != "created_at"}
            for s in (state.blocklists.lists if getattr(state, "blocklists", None) else [])
        ],
        "apps": [
            {key: value for key, value in b.as_row().items() if key != "created_at"}
            for b in (state.apps.blocks if getattr(state, "apps", None) else [])
        ],
        "accounts": [],
        "accounts_included": accounts,
    }

    if accounts:
        payload["accounts"] = [
            {
                "label": a.label,
                "username": a.username,
                "password": a.password,
                "enabled": a.enabled,
            }
            for a in state.account_store.accounts
        ]

    return payload


class RestoreBody(BaseModel):
    version: int = Field(default=BACKUP_VERSION)
    devices: list[dict] = Field(default_factory=list)
    rewrites: dict[str, str] = Field(default_factory=dict)
    blocklists: list[dict] = Field(default_factory=list)
    apps: list[dict] = Field(default_factory=list)
    accounts: list[dict] = Field(default_factory=list)


@router.post("/restore", dependencies=[WriteDep])
async def restore(request: Request, body: RestoreBody) -> dict:
    if body.version != BACKUP_VERSION:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "bad_version",
                "message": f"this backup is version {body.version}, this build reads "
                f"version {BACKUP_VERSION}",
            },
        )

    state = _state(request)
    devices, store = state.devices, state.account_store
    added = {"devices": 0, "rewrites": 0, "accounts": 0}
    if body.blocklists:
        added["blocklists"] = 0
    if body.apps:
        added["apps"] = 0
    skipped: list[str] = []

    for row in body.devices:
        device_id = str(row.get("id") or "").strip().lower()
        name = str(row.get("name") or "").strip()
        if not device_id or not name:
            continue
        try:
            if devices.get(device_id) is None:
                await devices.add(name, device_id)
                added["devices"] += 1
            rules = [str(r) for r in row.get("rules") or []]
            if rules:
                await devices.set_rules(device_id, rules)
            await devices.set_policy(
                device_id,
                safe_search=bool(row.get("safe_search")),
                block_mode=str(row.get("block_mode") or "zero"),
                block_address=str(row.get("block_address") or ""),
                safe_search_skip=[str(s) for s in row.get("safe_search_skip") or []],
            )
        except Exception as exc:
            skipped.append(f"device {device_id}: {exc}")

    for name, address in (body.rewrites or {}).items():
        try:
            await devices.set_rewrite(str(name), str(address))
            added["rewrites"] += 1
        except Exception as exc:
            skipped.append(f"rewrite {name}: {exc}")

    lists = getattr(state, "blocklists", None)
    for row in body.blocklists if lists is not None else []:
        list_id = str(row.get("id") or "").strip().lower()
        if not list_id:
            continue
        try:
            existed = lists.get(list_id) is not None
            await lists.put(
                list_id,
                name=str(row.get("name") or list_id),
                url=str(row.get("url") or ""),
                tags=[str(t) for t in row.get("tags") or []],
                allow=[str(a) for a in row.get("allow") or []],
                enabled=row.get("enabled") is not False,
            )
            if not existed:
                added["blocklists"] += 1
        except Exception as exc:
            skipped.append(f"blocklist {list_id}: {exc}")

    apps = getattr(state, "apps", None)
    for row in body.apps if apps is not None else []:
        app_id = str(row.get("id") or "").strip().lower()
        if not app_id:
            continue
        try:
            existed = apps.block(app_id) is not None
            await apps.put(
                app_id,
                tags=[str(t) for t in row.get("tags") or []],
                days=[int(d) for d in row.get("days") or [] if str(d).lstrip("-").isdigit()],
                starts_at=str(row.get("starts_at") or ""),
                ends_at=str(row.get("ends_at") or ""),
                enabled=row.get("enabled") is not False,
            )
            if not existed:
                added["apps"] += 1
        except Exception as exc:
            skipped.append(f"app {app_id}: {exc}")

    for row in body.accounts:
        username = str(row.get("username") or "").strip()
        password = str(row.get("password") or "")
        if not username or not password:
            continue
        if any(a.username.lower() == username.lower() for a in store.accounts):
            continue
        try:
            await TokenManager("restored account", username, password, state.http).token()
        except AuthError as exc:
            skipped.append(f"account {username}: AdGuard refused that sign-in: {exc}")
            continue
        try:
            config = store.add(username, password, str(row.get("label") or "") or None)
            await state.manager.add_account(config)
            added["accounts"] += 1
            adoption = state.manager.policy_adoption.get(config.label) or {}
            if adoption.get("error"):
                skipped.append(
                    f"account {config.label}: added, but its policy was not synced: "
                    f"{adoption['error']}"
                )
            if row.get("enabled") is False:
                with contextlib.suppress(AccountStoreError):
                    store.set_enabled(config.label, False)
                    await state.manager.set_enabled(config.label, False)
        except Exception as exc:
            skipped.append(f"account {username}: {exc}")

    return {"restored": added, "skipped": skipped}
