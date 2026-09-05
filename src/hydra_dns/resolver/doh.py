from __future__ import annotations

import base64
import json
import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Request, Response

from ..config import settings
from .service import MalformedQuery, Resolver, answer_to_json, build_query

log = logging.getLogger(__name__)

router = APIRouter(tags=["resolver"])

DNS_MESSAGE = "application/dns-message"
DNS_JSON = "application/dns-json"
MAX_QUERY_BYTES = 4096


def header_safe(value: str) -> str:
    return value.replace("→", "->").encode("latin-1", "replace").decode("latin-1")


class RateLimiter:
    def __init__(self, rate_per_s: int, burst_multiplier: int = 4) -> None:
        self._rate = float(rate_per_s)
        self._capacity = float(rate_per_s * burst_multiplier)
        self._buckets: dict[str, tuple[float, float]] = defaultdict(
            lambda: (self._capacity, time.monotonic())
        )
        self._last_sweep = time.monotonic()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        tokens, last = self._buckets[client]
        tokens = min(self._capacity, tokens + (now - last) * self._rate)
        if tokens < 1.0:
            self._buckets[client] = (tokens, now)
            return False
        self._buckets[client] = (tokens - 1.0, now)

        if now - self._last_sweep > 300:
            self._sweep(now)
        return True

    def _sweep(self, now: float) -> None:
        idle_for = self._capacity / self._rate if self._rate else 60.0
        self._buckets = defaultdict(
            lambda: (self._capacity, time.monotonic()),
            {k: v for k, v in self._buckets.items() if now - v[1] < idle_for * 2},
        )
        self._last_sweep = now


_limiter = RateLimiter(settings.rate_limit_qps)


def client_address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _resolver(request: Request) -> Resolver:
    return request.app.state.resolver


def _authorise(request: Request, token: str | None) -> tuple[str | None, Response | None]:
    if token is None:
        if settings.allow_anonymous:
            return "anonymous", None
        return None, Response(status_code=403)

    device = request.app.state.devices.by_token(token)
    if device is None:
        return None, Response(status_code=403)
    return device.id, None


async def _handle(request: Request, token: str | None) -> Response:
    device, refusal = _authorise(request, token)
    if refusal is not None:
        request.app.state.metrics.record_refused()
        return refusal

    if not _limiter.allow(client_address(request)):
        request.app.state.metrics.record_refused()
        return Response(status_code=429, headers={"retry-after": "1"})

    wire, json_mode, error = await _extract_query(request)
    if error is not None:
        return error

    resolver = _resolver(request)
    try:
        result = await resolver.resolve(wire, device or "anonymous")
    except MalformedQuery as exc:
        return Response(status_code=400, content=f"malformed DNS query: {exc}".encode())

    if json_mode:
        payload = answer_to_json(result.wire)
        payload["Hydra"] = {
            "cached": result.cached,
            "upstream": result.upstream,
            "blocked": result.blocked,
            "latency_ms": round(result.latency_ms, 1),
        }
        return Response(
            content=json.dumps(payload),
            media_type=DNS_JSON,
            headers={"cache-control": "no-store"},
        )

    return Response(
        content=result.wire,
        media_type=DNS_MESSAGE,
        headers={
            "cache-control": f"max-age={result.ttl}" if result.ttl else "no-store",
            "x-hydra-upstream": header_safe(result.upstream),
            "x-hydra-cache": "HIT" if result.cached else "MISS",
        },
    )


async def _extract_query(request: Request) -> tuple[bytes, bool, Response | None]:
    if request.method == "POST":
        body = await request.body()
        if not body:
            return b"", False, Response(status_code=400, content=b"empty request body")
        if len(body) > MAX_QUERY_BYTES:
            return b"", False, Response(status_code=413, content=b"query too large")
        return body, False, None

    params = request.query_params

    name = params.get("name")
    if name:
        qtype = (params.get("type") or "A").upper()
        want_dnssec = params.get("do", "").lower() in {"1", "true"}
        try:
            return build_query(name, qtype, want_dnssec), True, None
        except Exception as exc:
            return b"", True, Response(status_code=400, content=f"bad query: {exc}".encode())

    encoded = params.get("dns")
    if not encoded:
        return b"", False, Response(status_code=400, content=b"expected a dns or name parameter")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        wire = base64.urlsafe_b64decode(padded)
    except Exception:
        return b"", False, Response(status_code=400, content=b"dns parameter is not base64url")
    if len(wire) > MAX_QUERY_BYTES:
        return b"", False, Response(status_code=413, content=b"query too large")
    return wire, False, None


@router.get("/dns-query")
async def get_query(request: Request) -> Response:
    return await _handle(request, None)


@router.post("/dns-query")
async def post_query(request: Request) -> Response:
    return await _handle(request, None)


@router.get("/dns-query/{token}")
async def get_query_token(request: Request, token: str) -> Response:
    return await _handle(request, token)


@router.post("/dns-query/{token}")
async def post_query_token(request: Request, token: str) -> Response:
    return await _handle(request, token)
