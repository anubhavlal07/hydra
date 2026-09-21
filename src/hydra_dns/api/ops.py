from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..config import settings
from ..push import PushError
from ..tokens import SCOPES, TokenError
from .access import ReadDep, WriteDep

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ops"], dependencies=[ReadDep])


def _state(request: Request) -> Any:
    return request.app.state


@router.get("/audit")
async def audit_entries(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    before: int | None = None,
    source: str = Query("live", pattern="^(live|history)$"),
    actor: str | None = Query(None, max_length=80),
    action: str | None = Query(None, max_length=80),
    outcome: str | None = Query(None, pattern="^(ok|denied|failed)$"),
    q: str | None = Query(None, max_length=120),
) -> dict:
    trail = getattr(_state(request), "audit", None)
    if trail is None:
        return {"entries": [], "available": False, "source": source}

    if source == "live":
        return {
            "entries": trail.recent(limit, actor, action, outcome, q),
            "actions": trail.actions(),
            "actors": trail.actors(),
            "available": True,
            "next_before": None,
            "source": "live",
            "store": trail.snapshot(),
        }

    if not trail.configured:
        return {
            "entries": [],
            "actions": [],
            "actors": [],
            "available": False,
            "next_before": None,
            "source": "history",
            "store": trail.snapshot(),
        }

    try:
        rows = await trail.history(limit=limit, before=before, actor=actor, action=action, search=q)
        vocabulary = await trail.vocabulary()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail={"code": "store_unreachable", "message": str(exc)[:200]}
        ) from exc

    if outcome:
        rows = [r for r in rows if r.get("outcome") == outcome]

    return {
        "entries": rows,
        "actions": vocabulary["actions"],
        "actors": vocabulary["actors"],
        "available": True,
        "next_before": rows[-1]["id"] if len(rows) >= limit else None,
        "source": "history",
        "store": trail.snapshot(),
    }


@router.get("/tokens")
async def list_tokens(request: Request) -> dict:
    store = getattr(_state(request), "tokens", None)
    if store is None:
        return {"tokens": [], "store": {"configured": False}}
    return {
        "tokens": [token.as_dict() for token in store.tokens],
        "store": store.snapshot(),
        "scopes": list(SCOPES),
    }


class TokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scope: str = Field(default="read", pattern="^(read|metrics)$")


@router.post("/tokens", status_code=201, dependencies=[WriteDep])
async def create_token(request: Request, body: TokenBody) -> dict:
    store = getattr(_state(request), "tokens", None)
    if store is None or not store.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "tokens_unavailable",
                "message": "machine tokens need the Postgres registry configured",
            },
        )

    try:
        token, plaintext = await store.create(body.name, body.scope)
    except TokenError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "rejected", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {"name": token.name, "scope": token.scope, "id": token.id}
    log.info("machine token created: %s (%s)", token.id, token.scope)
    return {"token": token.as_dict(), "secret": plaintext}


@router.delete("/tokens/{token_id}", dependencies=[WriteDep])
async def revoke_token(request: Request, token_id: str) -> dict:
    store = getattr(_state(request), "tokens", None)
    if store is None or not store.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "tokens_unavailable",
                "message": "machine tokens need the Postgres registry configured",
            },
        )

    try:
        removed = await store.revoke(token_id)
    except TokenError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "rejected", "message": str(exc)}
        ) from exc

    log.info("machine token revoked: %s", token_id)
    return {"revoked": removed, "id": token_id}


@router.get("/alerts")
async def alert_state(request: Request) -> dict:
    dispatcher = getattr(_state(request), "alerts", None)
    if dispatcher is None:
        return {"alerts": {"configured": False}}
    weekly = getattr(_state(request), "digest", None)
    return {
        "alerts": dispatcher.snapshot(),
        "webhook_set": bool(settings.alert_webhook),
        "digest": weekly.snapshot() if weekly is not None else {"configured": False},
    }


@router.get("/digest/devices")
async def digest_devices(request: Request) -> dict:
    weekly = getattr(_state(request), "digest", None)
    if weekly is None:
        return {"available": False, "devices": []}
    return {"available": True, **weekly.report()}


@router.post("/alerts/test", dependencies=[WriteDep])
async def test_alert(request: Request) -> dict:
    dispatcher = getattr(_state(request), "alerts", None)
    if dispatcher is None or not dispatcher.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "no_webhook",
                "message": "set HYDRA_ALERT_WEBHOOK before sending a test alert",
            },
        )

    result = await dispatcher.test()
    request.state.audit_detail = {"delivered": result.get("sent")}
    return result


@router.post("/alerts/digest", dependencies=[WriteDep])
async def send_digest(request: Request) -> dict:
    weekly = getattr(_state(request), "digest", None)
    if weekly is None:
        raise HTTPException(
            status_code=503,
            detail={"code": "no_digest", "message": "the digest is not running"},
        )

    result = await weekly.send()
    request.state.audit_detail = {"delivered": result["delivered"]}
    return result


@router.get("/push")
async def push_state(request: Request) -> dict:
    sender = getattr(_state(request), "push", None)
    if sender is None:
        return {"configured": False, "public_key": "", "devices": []}
    return {
        **sender.snapshot(),
        "public_key": settings.vapid_public_key,
        "devices": [
            {
                "endpoint": s["endpoint"],
                "label": s.get("label") or "",
                "created_at": s.get("created_at"),
                "last_ok_at": s.get("last_ok_at"),
                "failures": s.get("failures", 0),
            }
            for s in sender.subscriptions
        ],
    }


class PushSubscribe(BaseModel):
    endpoint: str = Field(min_length=12, max_length=800)
    p256dh: str = Field(min_length=16, max_length=200)
    auth: str = Field(min_length=8, max_length=100)
    label: str = Field(default="", max_length=60)


@router.post("/push/subscribe", status_code=201, dependencies=[WriteDep])
async def push_subscribe(request: Request, body: PushSubscribe) -> dict:
    sender = getattr(_state(request), "push", None)
    if sender is None or not sender.configured:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "push_unavailable",
                "message": "push needs HYDRA_VAPID_PUBLIC_KEY, HYDRA_VAPID_PRIVATE_KEY "
                "and the Postgres registry",
            },
        )

    try:
        await sender.add(body.endpoint, body.p256dh, body.auth, body.label)
    except PushError as exc:
        raise HTTPException(
            status_code=400, detail={"code": "rejected", "message": str(exc)}
        ) from exc

    request.state.audit_detail = {"label": body.label, "devices": len(sender.subscriptions)}
    return {"subscribed": True, "devices": len(sender.subscriptions)}


class PushUnsubscribe(BaseModel):
    endpoint: str = Field(min_length=12, max_length=800)


@router.post("/push/unsubscribe", dependencies=[WriteDep])
async def push_unsubscribe(request: Request, body: PushUnsubscribe) -> dict:
    sender = getattr(_state(request), "push", None)
    if sender is None:
        raise HTTPException(
            status_code=503, detail={"code": "push_unavailable", "message": "push is not set up"}
        )
    removed = await sender.remove(body.endpoint)
    return {"removed": removed, "devices": len(sender.subscriptions)}


@router.post("/push/test", dependencies=[WriteDep])
async def push_test(request: Request) -> dict:
    sender = getattr(_state(request), "push", None)
    if sender is None or not sender.configured:
        raise HTTPException(
            status_code=503,
            detail={"code": "push_unavailable", "message": "push is not configured"},
        )

    result = await sender.send(
        {
            "title": f"{settings.service_name} test",
            "body": "Push is working. Real alerts will name the reason.",
            "tag": "hydra-test",
        },
        urgency="low",
    )
    request.state.audit_detail = result
    return result
