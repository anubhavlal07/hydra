from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..blocklist_presets import catalog
from ..blocklists import MAX_ALLOW, MAX_NAME, MAX_URL, UnknownList
from ..config import settings
from ..store import MAX_TAGS, StoreError
from .access import ReadDep, WriteDep
from .setup import _guard_writable

log = logging.getLogger(__name__)

router = APIRouter(tags=["blocklists"])


def _lists(request: Request):
    return request.app.state.blocklists


def _failure(exc: StoreError) -> HTTPException:
    if isinstance(exc, UnknownList):
        return HTTPException(status_code=404, detail={"code": "no_blocklist", "message": str(exc)})
    return HTTPException(status_code=400, detail={"code": "store_error", "message": str(exc)})


class BlocklistBody(BaseModel):
    name: str = Field(min_length=1, max_length=MAX_NAME)
    url: str = Field(min_length=9, max_length=MAX_URL)
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    allow: list[str] = Field(default_factory=list, max_length=MAX_ALLOW)
    enabled: bool = True


class AllowBody(BaseModel):
    domain: str = Field(min_length=1, max_length=253)
    action: str = Field(pattern="^(add|remove)$")


@router.get("/api/blocklists", dependencies=[ReadDep])
async def list_blocklists(request: Request) -> dict:
    lists = _lists(request)
    store = request.app.state.devices
    return {
        "lists": [lists.as_dict(state) for state in lists.lists],
        "editable": store.writable,
        "tags": store.tags(),
        "max_lists": settings.blocklist_max,
        "max_names": settings.blocklist_max_names,
        "total_names": lists.total_names,
        "refresh_seconds": settings.blocklist_refresh_s,
    }


@router.get("/api/blocklists/presets", dependencies=[ReadDep])
async def blocklist_presets(request: Request) -> dict:
    lists = _lists(request)
    held = lists.lists
    return {
        "presets": catalog(
            held_urls={state.meta.url for state in held},
            total_names=lists.total_names,
            max_names=settings.blocklist_max_names,
            max_bytes=settings.blocklist_max_bytes,
            full=len(held) >= settings.blocklist_max,
        ),
        "headroom": max(0, settings.blocklist_max_names - lists.total_names),
        "max_bytes": settings.blocklist_max_bytes,
    }


@router.put("/api/blocklists/{list_id}", dependencies=[WriteDep])
async def set_blocklist(request: Request, list_id: str, body: BlocklistBody) -> dict:
    _guard_writable(request.app.state.devices)
    lists = _lists(request)
    try:
        state = await lists.put(
            list_id,
            name=body.name,
            url=body.url,
            tags=body.tags,
            allow=body.allow,
            enabled=body.enabled,
        )
    except StoreError as exc:
        raise _failure(exc) from exc

    request.state.audit_detail = {
        "name": state.meta.name,
        "url": state.meta.url,
        "tags": list(state.meta.tags),
        "allow": len(state.meta.allow),
        "enabled": state.meta.enabled,
    }
    log.info("blocklist written: %s (%s)", state.meta.id, state.meta.url)
    return lists.as_dict(state)


@router.post("/api/blocklists/{list_id}/refresh", dependencies=[WriteDep])
async def refresh_blocklist(request: Request, list_id: str) -> dict:
    lists = _lists(request)
    try:
        state = await lists.refresh(list_id)
    except StoreError as exc:
        raise _failure(exc) from exc

    request.state.audit_detail = {
        "names": len(state.names),
        "last_error": state.last_error,
    }
    return lists.as_dict(state)


@router.post("/api/blocklists/{list_id}/allow", dependencies=[WriteDep])
async def blocklist_allow(request: Request, list_id: str, body: AllowBody) -> dict:
    _guard_writable(request.app.state.devices)
    lists = _lists(request)
    try:
        state = await lists.set_allow(list_id, body.domain, body.action)
    except StoreError as exc:
        raise _failure(exc) from exc

    request.state.audit_detail = {"domain": body.domain.strip().lower(), "action": body.action}
    return lists.as_dict(state)


@router.delete("/api/blocklists/{list_id}", dependencies=[WriteDep])
async def remove_blocklist(request: Request, list_id: str) -> dict:
    _guard_writable(request.app.state.devices)
    lists = _lists(request)
    try:
        removed = await lists.remove(list_id)
    except StoreError as exc:
        raise _failure(exc) from exc

    log.info("blocklist removed: %s", list_id)
    return {"removed": removed, "id": list_id}
