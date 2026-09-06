from __future__ import annotations

import asyncio
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from ..config import settings
from ..tokens import bearer
from .session import require_session

ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class Actor:
    kind: str
    name: str
    scope: str = ""

    @property
    def label(self) -> str:
        return self.name if self.kind == "session" else f"token:{self.name}"


def _token_actor(request: Request, wanted: str) -> Actor | None:
    store = getattr(request.app.state, "tokens", None)
    if store is None:
        return None

    presented = bearer(request.headers.get("authorization", ""))
    if not presented:
        return None

    found = store.verify(presented)
    if found is None:
        return None
    if wanted == "read" and found.scope != "read":
        return None

    store.note_use(found)
    return Actor(kind="token", name=found.name, scope=found.scope)


def _session_actor(request: Request) -> Actor | None:
    try:
        require_session(request)
    except HTTPException:
        return None
    return Actor(kind="session", name="dashboard", scope="admin")


def require_read(request: Request) -> Actor:
    actor = _session_actor(request) or _token_actor(request, "read")
    if actor is None:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "sign in"})
    request.state.actor = actor
    return actor


def require_write(request: Request) -> Actor:
    actor = _session_actor(request)
    if actor is None:
        if _token_actor(request, "read") is not None:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "read_only",
                    "message": "machine tokens are read-only; this needs a signed-in session",
                },
            )
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "sign in"})
    request.state.actor = actor
    return actor


def require_metrics(request: Request) -> Actor:
    actor = _session_actor(request) or _token_actor(request, "metrics")
    if actor is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "a session or a machine token is needed"},
            headers={"www-authenticate": "Bearer"},
        )
    request.state.actor = actor
    return actor


def actor_of(request: Request) -> str:
    actor = getattr(request.state, "actor", None)
    if isinstance(actor, Actor):
        return actor.label
    return ANONYMOUS


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else "unknown"


def heavy_guard(request: Request) -> asyncio.Semaphore:
    guard = getattr(request.app.state, "heavy", None)
    if guard is None:
        guard = asyncio.Semaphore(max(1, settings.heavy_concurrency))
        request.app.state.heavy = guard
    return guard


ReadDep = Depends(require_read)
WriteDep = Depends(require_write)
MetricsDep = Depends(require_metrics)
