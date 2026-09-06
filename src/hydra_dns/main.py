from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import audit, certs, designated, drift, logs, quiet, runtime, telemetry, watch
from .accounts_store import AccountStore
from .adguard.aggregate import quota_rollup
from .adguard.manager import AccountManager
from .alerts import AlertDispatcher
from .api import apps as app_api
from .api import blocklists as blocklist_api
from .api import dashboard, ops, session, setup
from .api.access import MetricsDep, ReadDep, actor_of, client_ip
from .apps import Apps
from .audit import AuditLog
from .blocklists import Blocklists
from .certwatch import watcher
from .config import settings
from .digest import Digest
from .metrics_store import MetricsStore
from .policy_store import PolicyVersions
from .push import PushSender
from .resolver import doh
from .resolver.cache import AnswerCache
from .resolver.doq import DoqServer
from .resolver.dot import DotServer
from .resolver.metrics import Metrics
from .resolver.persistence import CachePersistence
from .resolver.prefetch import Prefetcher
from .resolver.service import Resolver
from .resolver.upstream import UpstreamPool
from .store import DeviceStore
from .tokens import TokenStore

logs.configure(os.environ.get("HYDRA_LOG_LEVEL", "INFO"), settings.log_json)
log = logging.getLogger("hydra")

STARTED_AT = time.time()


WARM_WAIT_S = 2.0
WARM_ATTEMPTS = 30


async def _warm_when_ready(prefetcher: Prefetcher, pool: UpstreamPool) -> None:
    for _ in range(WARM_ATTEMPTS):
        if pool.ready:
            await prefetcher.warm()
            return
        await asyncio.sleep(WARM_WAIT_S)


def _build_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(10.0, connect=5.0),
        limits=httpx.Limits(
            max_keepalive_connections=20, max_connections=60, keepalive_expiry=300.0
        ),
        headers={"user-agent": "hydra-dns/0.1 (+https://github.com/anubhavlal07/hydra)"},
        follow_redirects=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    problems = settings.validate()
    if problems:
        log.error("refusing to start, %s configuration problem(s):", len(problems))
        for problem in problems:
            log.error("  - %s", problem)
        sys.exit(1)

    for warning in settings.warnings():
        log.warning("%s", warning)

    client = _build_http_client()
    cache = AnswerCache(
        max_entries=settings.cache_max_entries,
        min_ttl=settings.cache_min_ttl_s,
        max_ttl=settings.cache_max_ttl_s,
        blocked_min_ttl=settings.cache_blocked_min_ttl_s,
        stale_window_s=settings.cache_stale_window_s,
    )
    cache_persistence = CachePersistence(cache)
    cache_persistence.load()
    metrics = Metrics()
    presence_file = quiet.PresenceFile(metrics, settings.presence_file)
    presence_file.load()
    pool = UpstreamPool(
        strategy=settings.strategy,
        timeout_s=settings.upstream_timeout_s,
        fallback_url=settings.fallback_doh_url,
        balance_floor=settings.balance_floor,
    )
    pool.attach_client(client)
    manager = AccountManager(pool, client)
    account_store = AccountStore(settings.accounts)
    account_store.load()
    manager.configure(account_store.accounts)
    devices = DeviceStore(client, settings.seed_devices)
    blocklists = Blocklists(client, devices)
    await blocklists.load()
    apps = Apps(client, devices)
    await apps.load()

    app.state.http = client
    app.state.devices = devices
    app.state.blocklists = blocklists
    app.state.apps = apps
    app.state.cache = cache
    app.state.metrics = metrics
    app.state.pool = pool
    app.state.manager = manager
    app.state.account_store = account_store
    app.state.resolver = Resolver(cache, pool, metrics, devices, blocklists=blocklists, apps=apps)
    app.state.cache_persistence = cache_persistence
    app.state.presence_file = presence_file
    prefetcher = Prefetcher(cache, pool, metrics, blocklists, app.state.resolver.rebind, apps=apps)
    app.state.prefetch = prefetcher
    metrics_store = MetricsStore(client, metrics)
    app.state.metrics_store = metrics_store
    await metrics_store.load()

    trail = AuditLog(client)
    app.state.audit = trail
    token_store = TokenStore(client)
    app.state.tokens = token_store
    app.state.policy_versions = PolicyVersions(client)
    push = PushSender(client)
    app.state.push = push
    alerts = AlertDispatcher(client, lambda: _attention_for(app), push, once=watch.once_per_episode)
    app.state.alerts = alerts
    policy_drift = drift.build(manager, settings)
    app.state.policy_drift = policy_drift
    weekly = Digest(
        metrics, push, lambda: _quota_for(app), resolver=app.state.resolver, devices=devices
    )
    app.state.digest = weekly
    app.state.heavy = asyncio.Semaphore(max(1, settings.heavy_concurrency))
    app.state.metrics_text = telemetry.RenderCache()

    log.info(
        "%s starting: %s account(s), %s device(s), strategy=%s",
        settings.service_name,
        len(account_store.accounts),
        len(settings.seed_devices),
        settings.strategy,
    )
    manager.start_background()
    devices.start_background()
    blocklists.start_background()
    apps.start_background()
    metrics_store.start_background()
    cache_persistence.start_background()
    presence_file.start_background()
    prefetcher.start()
    watcher.start_background()
    trail.start_background()
    token_store.start_background()
    push.start_background()
    alerts.start_background()
    policy_drift.start_background()
    weekly.start_background()

    unclean_since = runtime.claim()
    if unclean_since is not None:
        log.warning("the previous run did not shut down cleanly")
        app.state.unclean = asyncio.create_task(
            _report_unclean_restart(push, unclean_since), name="hydra-unclean"
        )
    app.state.warm = asyncio.create_task(_warm_when_ready(prefetcher, pool), name="hydra-warm")

    app.state.dot = None
    app.state.dot_error = settings.dot_problem()
    if settings.dot_enabled and not app.state.dot_error:
        dot = DotServer(app.state.resolver, devices, metrics)
        try:
            await dot.start()
            app.state.dot = dot
        except Exception as exc:
            app.state.dot_error = f"{type(exc).__name__}: {exc}"
            log.error("DoT listener failed to start: %s", app.state.dot_error)
    elif app.state.dot_error:
        log.error("DoT is enabled but cannot start: %s", app.state.dot_error)

    app.state.doq = None
    app.state.doq_error = settings.doq_problem()
    if settings.doq_enabled and not app.state.doq_error:
        doq = DoqServer(app.state.resolver, devices, metrics)
        try:
            await doq.start()
            app.state.doq = doq
        except Exception as exc:
            app.state.doq_error = f"{type(exc).__name__}: {exc}"
            log.error("DoQ listener failed to start: %s", app.state.doq_error)
    elif app.state.doq_error:
        log.error("DoQ is enabled but cannot start: %s", app.state.doq_error)

    designated.listening(app.state.dot is not None, app.state.doq is not None)

    try:
        yield
    finally:
        app.state.warm.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await app.state.warm
        await watcher.aclose()
        if app.state.doq is not None:
            await app.state.doq.aclose()
        if app.state.dot is not None:
            await app.state.dot.aclose()
        unclean = getattr(app.state, "unclean", None)
        if unclean is not None:
            unclean.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await unclean
        await alerts.aclose()
        await policy_drift.aclose()
        await weekly.aclose()
        await push.aclose()
        runtime.release()
        await token_store.aclose()
        await trail.aclose()
        await prefetcher.aclose()
        await cache_persistence.aclose()
        await presence_file.aclose()
        await metrics_store.aclose()
        await blocklists.aclose()
        await apps.aclose()
        await devices.aclose()
        await manager.aclose()
        await client.aclose()


app = FastAPI(
    title="Hydra DNS",
    version="0.1.0",
    description=(
        "A DNS-over-HTTPS resolver that spreads queries across several AdGuard DNS accounts, "
        "with one dashboard over all of them."
    ),
    lifespan=lifespan,
    docs_url="/docs" if os.environ.get("HYDRA_DOCS") else None,
    openapi_url="/openapi.json" if os.environ.get("HYDRA_DOCS") else None,
    redoc_url=None,
)

CORS_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=CORS_METHODS,
    allow_headers=["content-type", "authorization", "x-request-id", "if-none-match"],
    expose_headers=["etag", "x-request-id"],
    max_age=86_400,
)


DNS_PREFIX = "/dns-query"

CONDITIONAL = frozenset(
    {
        "/api/accounts",
        "/api/apps/catalog",
        "/api/devices",
        "/api/native-dns",
        "/api/rewrites",
        "/api/rule-groups",
        "/api/status",
    }
)


async def _conditional(request: Request, response: Response) -> Response:
    body = b"".join([chunk async for chunk in response.body_iterator])
    etag = f'"{hashlib.blake2b(body, digest_size=16).hexdigest()}"'

    headers = dict(response.headers)
    headers["etag"] = etag

    if request.headers.get("if-none-match") == etag:
        headers.pop("content-length", None)
        return Response(status_code=304, headers=headers)

    return Response(content=body, status_code=response.status_code, headers=headers)


def _harden(response, api: bool) -> None:
    response.headers.setdefault("strict-transport-security", "max-age=63072000; includeSubDomains")
    response.headers.setdefault("x-content-type-options", "nosniff")
    response.headers.setdefault("referrer-policy", "no-referrer")
    response.headers.setdefault("x-frame-options", "DENY")
    response.headers.setdefault(
        "content-security-policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )
    if api:
        response.headers.setdefault("cache-control", "no-store")


def _record(request: Request, response, request_id: str) -> None:
    trail = getattr(request.app.state, "audit", None)
    if trail is None:
        return

    route = request.scope.get("route")
    route_path = getattr(route, "path", "") or request.scope.get("path", "")
    action = audit.action_for(request.method, route_path)
    if not audit.worth_recording(action, response.status_code):
        return

    params = request.scope.get("path_params") or {}
    detail = dict(getattr(request.state, "audit_detail", {}) or {})
    query = dict(request.query_params)
    if query:
        detail.setdefault("query", query)

    trail.record(
        actor=actor_of(request),
        address=client_ip(request),
        action=action,
        target=str(next(iter(params.values()), "")) if params else "",
        outcome=audit.outcome_for(response.status_code),
        status=response.status_code,
        detail=detail,
        request_id=request_id,
    )


@app.middleware("http")
async def envelope(request: Request, call_next):
    path = request.scope.get("path", "")

    if path.startswith(DNS_PREFIX):
        response = await call_next(request)
        _harden(response, api=False)
        return response

    request_id = (request.headers.get("x-request-id") or "")[:64] or logs.new_request_id()
    reset = logs.REQUEST_ID.set(request_id)
    try:
        response = await call_next(request)
    finally:
        logs.REQUEST_ID.reset(reset)

    response.headers["x-request-id"] = request_id
    _harden(response, api=path.startswith("/api/"))

    if request.method == "GET" and response.status_code == 200 and path in CONDITIONAL:
        response = await _conditional(request, response)

    _record(request, response, request_id)
    return response


app.include_router(doh.router)
app.include_router(session.router)
app.include_router(dashboard.router)
app.include_router(ops.router)
app.include_router(setup.router)
app.include_router(blocklist_api.router)
app.include_router(app_api.router)


def _has_session(request) -> bool:
    from .api.session import require_session

    try:
        require_session(request)
    except Exception:
        return False
    return True


async def _report_unclean_restart(push: PushSender, since: float) -> None:
    await asyncio.sleep(20)
    gap = runtime.describe_gap(since)
    await push.send(
        {
            "title": f"{settings.service_name} restarted unexpectedly",
            "body": f"It was last running {gap} ago and did not shut down cleanly. "
            "A crash, an out-of-memory kill or the host rebooting all look like this.",
            "tag": "hydra-unclean",
        },
        urgency="high",
    )


def _certificate() -> dict | None:
    return certs.snapshot(settings.dot_cert_file) if settings.dot_enabled else None


def _quota_for(scoped: FastAPI) -> dict:
    state = scoped.state
    manager = getattr(state, "manager", None)
    metrics = getattr(state, "metrics", None)
    if manager is None:
        return {}
    summary = metrics.summary() if metrics else {}
    return quota_rollup(manager.snapshot(), summary.get("cache_hit_rate", 0.0))


LISTENERS = {"dot": "DoT", "doq": "DoQ"}


def _silent_listeners(scoped: FastAPI) -> list[str]:
    enabled = {"dot": settings.dot_enabled, "doq": settings.doq_enabled}
    return [
        label
        for name, label in LISTENERS.items()
        if enabled[name] and getattr(scoped.state, name, None) is None
    ]


def _attention_for(scoped: FastAPI) -> list[str]:
    state = scoped.state
    manager = getattr(state, "manager", None)
    pool = getattr(state, "pool", None)
    metrics = getattr(state, "metrics", None)
    if manager is None or pool is None:
        return []

    from .api.session import _limiter

    accounts = manager.snapshot()
    summary = metrics.summary() if metrics else {}
    resolver = getattr(state, "resolver", None)
    registry = getattr(state, "devices", None)
    return watch.reasons(
        accounts=accounts,
        quota=quota_rollup(accounts, summary.get("cache_hit_rate", 0.0)),
        pool_state=pool.state(),
        warn_days=settings.quota_warn_days,
        certificate=_certificate(),
        warn_failures=settings.auth_warn_failures,
        cert_warn_days=settings.cert_warn_days,
        devices=resolver.budget.breaching() if resolver is not None else [],
        registry=registry.snapshot() if registry is not None else None,
        resolver=summary or None,
        sign_in_failures=_limiter.recent_failures(),
        silent_listeners=_silent_listeners(scoped),
        blocklists=_blocklist_health(scoped),
        blocklist_refresh_s=settings.blocklist_refresh_s,
        quiet=quiet.quiet_names(state),
        drift=drift.findings(state),
    )


def _quiet_health(scoped: FastAPI) -> dict | None:
    metrics = getattr(scoped.state, "metrics", None)
    found = metrics.quiet_snapshot() if metrics is not None else None
    if found is None:
        return None
    stored = getattr(scoped.state, "presence_file", None)
    return {**found, "file": stored.snapshot() if stored is not None else None}


def _blocklist_health(scoped: FastAPI) -> list[dict] | None:
    lists = getattr(scoped.state, "blocklists", None)
    return lists.health() if lists is not None else None


def _attention(request: Request) -> list[str]:
    return _attention_for(request.app)


@app.get("/api/status", tags=["health"])
async def status(request: Request) -> dict:
    if settings.warnings() or not getattr(app.state, "manager", None):
        return {"state": watch.ATTENTION}
    found = _attention(request)
    return {"state": watch.ATTENTION if found else watch.NOMINAL}


@app.get("/api/pulse", tags=["dashboard"], dependencies=[ReadDep])
async def pulse(request: Request, minutes: int = Query(60, ge=5, le=1440)) -> dict:
    return {
        "overview": await dashboard.overview(request),
        "resolver": await dashboard.resolver_metrics(request, minutes),
        "health": await health(request),
    }


@app.get("/api/health", tags=["health"])
async def health(request: Request) -> dict:
    pool = getattr(app.state, "pool", None)
    manager = getattr(app.state, "manager", None)
    ready = bool(pool and pool.ready)
    spent = bool(pool and pool.spent)
    warnings = settings.warnings()
    detailed = _has_session(request)
    silent = _silent_listeners(app)

    if warnings:
        status = "unconfigured"
    elif ready and silent:
        status = "degraded"
    elif ready:
        status = "ok"
    elif spent:
        status = "degraded"
    else:
        status = "starting"

    public = {
        "status": status,
        "uptime_s": round(time.time() - STARTED_AT, 1),
    }
    if detailed:
        public = {**public, "instance": settings.instance_id}
    if not detailed:
        return public

    return {
        **public,
        "upstreams_ready": sum(1 for u in pool.upstreams if u.doh_url) if pool else 0,
        "accounts": len(manager.accounts) if manager else 0,
        "discovery_done": bool(manager and manager.discovery_done),
        "resolver": manager.health() if manager else None,
        "attention": _attention(request),
        "pool": pool.state() if pool else None,
        "metrics_store": getattr(app.state, "metrics_store", None).snapshot()
        if getattr(app.state, "metrics_store", None)
        else None,
        "devices": len(getattr(app.state, "devices", None).devices)
        if getattr(app.state, "devices", None)
        else 0,
        "dot": getattr(app.state, "dot", None).snapshot()
        if getattr(app.state, "dot", None)
        else {"enabled": False, "error": getattr(app.state, "dot_error", "")},
        "doq": getattr(app.state, "doq", None).snapshot()
        if getattr(app.state, "doq", None)
        else {"enabled": False, "error": getattr(app.state, "doq_error", "")},
        "certificate_watch": watcher.snapshot(),
        "policy_drift": getattr(app.state, "policy_drift", None).snapshot()
        if getattr(app.state, "policy_drift", None)
        else None,
        "quiet": _quiet_health(app),
        "blocklists": getattr(app.state, "blocklists", None).summary()
        if getattr(app.state, "blocklists", None)
        else None,
        "apps": getattr(app.state, "apps", None).summary()
        if getattr(app.state, "apps", None)
        else None,
        "cache_snapshot": getattr(app.state, "cache_persistence", None).snapshot()
        if getattr(app.state, "cache_persistence", None)
        else None,
        "budget": app.state.resolver.budget_snapshot()
        if getattr(app.state, "resolver", None)
        else None,
        "coalescing": app.state.resolver.flight.snapshot()
        if getattr(app.state, "resolver", None)
        else None,
        "rebind": app.state.resolver.rebind.snapshot()
        if getattr(app.state, "resolver", None)
        else None,
        "cname": app.state.resolver.uncloak.snapshot()
        if getattr(app.state, "resolver", None)
        else None,
        "audit": getattr(app.state, "audit", None).snapshot()
        if getattr(app.state, "audit", None)
        else None,
        "alerts": getattr(app.state, "alerts", None).snapshot()
        if getattr(app.state, "alerts", None)
        else None,
        "tokens": getattr(app.state, "tokens", None).snapshot()
        if getattr(app.state, "tokens", None)
        else None,
        "push": getattr(app.state, "push", None).snapshot()
        if getattr(app.state, "push", None)
        else None,
        "warnings": warnings,
    }


@app.get("/metrics", tags=["health"], dependencies=[MetricsDep])
async def prometheus(request: Request) -> Response:
    if not settings.metrics_endpoint:
        raise HTTPException(
            status_code=404, detail={"code": "disabled", "message": "the metrics endpoint is off"}
        )

    cache = getattr(app.state, "metrics_text", None)
    certificate = _certificate()
    days_left = (certificate or {}).get("days_left")

    def build() -> str:
        return telemetry.render(app.state, _attention(request), days_left)

    text = cache.get(build) if cache is not None else build()
    return Response(content=text, media_type=telemetry.CONTENT_TYPE)


@app.get("/", tags=["health"])
async def root() -> dict:
    return {"service": settings.service_name}
