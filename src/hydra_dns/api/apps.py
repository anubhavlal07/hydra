from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import schedule
from ..apps import UnknownApp
from ..store import MAX_TAGS, StoreError
from .access import ReadDep, WriteDep
from .setup import _guard_writable

log = logging.getLogger(__name__)

router = APIRouter(tags=["apps"])


def _apps(request: Request):
    return request.app.state.apps


def _failure(exc: StoreError) -> HTTPException:
    if isinstance(exc, UnknownApp):
        return HTTPException(status_code=404, detail={"code": "no_app", "message": str(exc)})
    return HTTPException(status_code=400, detail={"code": "store_error", "message": str(exc)})


class AppBlockBody(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    days: list[int] = Field(default_factory=list, max_length=7)
    starts_at: str = Field(default="", max_length=5)
    ends_at: str = Field(default="", max_length=5)
    enabled: bool = True


@router.get("/api/apps", dependencies=[ReadDep])
async def list_apps(request: Request) -> dict:
    apps = _apps(request)
    store = request.app.state.devices
    return {
        "blocks": [apps.as_dict(block) for block in apps.blocks],
        "seen": apps.seen(),
        "editable": store.writable,
        "tags": store.tags(),
        "timezone": str(schedule.zone()),
        "catalog": {
            "fingerprint": apps.catalog.fingerprint,
            "source": apps.catalog.source,
            "apps": len(apps.catalog.apps),
            "fetched_at": apps.catalog_fetched_at,
            "error": apps.catalog_error,
        },
    }


@router.get("/api/apps/catalog", dependencies=[ReadDep])
async def app_catalog(request: Request) -> dict:
    return _apps(request).catalog_payload()


@router.put("/api/apps/{app_id}", dependencies=[WriteDep])
async def block_app(request: Request, app_id: str, body: AppBlockBody) -> dict:
    _guard_writable(request.app.state.devices)
    apps = _apps(request)
    try:
        block = await apps.put(
            app_id,
            tags=body.tags,
            days=body.days,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            enabled=body.enabled,
        )
    except StoreError as exc:
        raise _failure(exc) from exc

    request.state.audit_detail = {
        "tags": list(block.tags),
        "when": apps.when(block),
        "enabled": block.enabled,
    }
    log.info("app block written: %s (%s)", block.id, apps.when(block))
    return apps.as_dict(block)


@router.delete("/api/apps/{app_id}", dependencies=[WriteDep])
async def unblock_app(request: Request, app_id: str) -> dict:
    _guard_writable(request.app.state.devices)
    apps = _apps(request)
    try:
        removed = await apps.remove(app_id)
    except StoreError as exc:
        raise _failure(exc) from exc

    log.info("app block removed: %s", app_id)
    return {"removed": removed, "id": app_id}
