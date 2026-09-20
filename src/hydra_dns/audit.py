from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field

import httpx

from .config import settings

log = logging.getLogger(__name__)

FN_WRITE = "hydra_audit_write"
FN_PAGE = "hydra_audit_page"
FN_VOCABULARY = "hydra_audit_vocabulary"

IN_MEMORY_ENTRIES = 250
MAX_PER_FLUSH = 100

REDACTED = "***"
SECRET_KEYS = frozenset({"password", "secret", "token", "accounts", "authorization"})


@dataclass(frozen=True)
class AuditEntry:
    at: float
    actor: str
    address: str
    action: str
    target: str
    outcome: str
    status: int
    detail: dict = field(default_factory=dict)
    request_id: str = ""

    def as_dict(self) -> dict:
        return {
            "at": round(self.at, 3),
            "actor": self.actor,
            "address": self.address,
            "action": self.action,
            "target": self.target,
            "outcome": self.outcome,
            "status": self.status,
            "detail": self.detail,
            "request_id": self.request_id,
        }


def redact(value: object, depth: int = 0) -> object:
    if depth > 4:
        return REDACTED
    if isinstance(value, dict):
        return {
            key: (REDACTED if key.lower() in SECRET_KEYS else redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


class AuditLog:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._entries: deque[AuditEntry] = deque(maxlen=IN_MEMORY_ENTRIES)
        self._pending: deque[AuditEntry] = deque(maxlen=1000)
        self._task: asyncio.Task | None = None
        self.last_flush_at = 0.0
        self.last_error = ""
        self.writes = 0

    @property
    def configured(self) -> bool:
        return bool(
            settings.store_url
            and settings.store_key
            and settings.store_secret
            and settings.audit_persist
        )

    def record(
        self,
        *,
        actor: str,
        address: str,
        action: str,
        target: str = "",
        outcome: str = "ok",
        status: int = 200,
        detail: dict | None = None,
        request_id: str = "",
    ) -> AuditEntry:
        entry = AuditEntry(
            at=time.time(),
            actor=actor,
            address=address,
            action=action,
            target=target,
            outcome=outcome,
            status=status,
            detail=redact(detail or {}),
            request_id=request_id,
        )
        self._entries.appendleft(entry)
        if self.configured:
            self._pending.append(entry)
        log.info(
            "audit %s %s by %s (%s)",
            action,
            target or "-",
            actor,
            outcome,
            extra={"audit_action": action, "audit_actor": actor, "audit_outcome": outcome},
        )
        return entry

    def recent(
        self,
        limit: int = 100,
        actor: str | None = None,
        action: str | None = None,
        outcome: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        rows = list(self._entries)
        if actor:
            rows = [r for r in rows if r.actor == actor]
        if action:
            rows = [r for r in rows if r.action == action]
        if outcome:
            rows = [r for r in rows if r.outcome == outcome]
        if search:
            needle = search.strip().lower()
            rows = [r for r in rows if needle in r.target.lower() or needle in r.action.lower()]
        return [r.as_dict() for r in rows[:limit]]

    def actions(self) -> list[str]:
        return sorted({entry.action for entry in self._entries})

    def actors(self) -> list[str]:
        return sorted({entry.actor for entry in self._entries})

    async def _rpc(self, function: str, payload: dict) -> object:
        response = await self._client.post(
            f"{settings.store_url}/rest/v1/rpc/{function}",
            json={"p_secret": settings.store_secret, **payload},
            headers={
                "apikey": settings.store_key,
                "authorization": f"Bearer {settings.store_key}",
                "content-type": "application/json",
            },
            timeout=20.0,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
        if not response.content:
            return None
        return response.json()

    async def history(
        self,
        limit: int = 100,
        before: int | None = None,
        actor: str | None = None,
        action: str | None = None,
        search: str | None = None,
    ) -> list[dict]:
        if not self.configured:
            return []
        rows = await self._rpc(
            FN_PAGE,
            {
                "p_limit": limit,
                "p_before": before,
                "p_actor": actor,
                "p_action": action,
                "p_search": search,
            },
        )
        return rows if isinstance(rows, list) else []

    async def vocabulary(self) -> dict[str, list[str]]:
        if not self.configured:
            return {"actions": self.actions(), "actors": self.actors()}

        rows = await self._rpc(FN_VOCABULARY, {})
        actions: list[str] = []
        actors: list[str] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            value = str(row.get("value") or "")
            if not value:
                continue
            if row.get("kind") == "action":
                actions.append(value)
            elif row.get("kind") == "actor":
                actors.append(value)
        return {"actions": sorted(set(actions)), "actors": sorted(set(actors))}

    async def flush(self) -> bool:
        if not self.configured or not self._pending:
            return False

        batch = [self._pending[i] for i in range(min(MAX_PER_FLUSH, len(self._pending)))]
        try:
            await self._rpc(
                FN_WRITE,
                {
                    "p_entries": [e.as_dict() for e in batch],
                    "p_keep_days": settings.audit_retention_days,
                },
            )
        except Exception as exc:
            self.last_error = str(exc)[:200]
            log.warning("audit flush failed, holding %s entries: %s", len(self._pending), exc)
            return False

        for _ in range(len(batch)):
            with contextlib.suppress(IndexError):
                self._pending.popleft()
        self.last_error = ""
        self.last_flush_at = time.time()
        self.writes += len(batch)
        return True

    def start_background(self) -> None:
        if self.configured:
            self._task = asyncio.create_task(self._loop(), name="hydra-audit")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(settings.audit_flush_s)
            try:
                await self.flush()
            except Exception:
                log.exception("audit flush loop failed")

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        if self.configured:
            with contextlib.suppress(Exception):
                await self.flush()

    def snapshot(self) -> dict:
        return {
            "persisted": self.configured,
            "in_memory": len(self._entries),
            "pending": len(self._pending),
            "writes": self.writes,
            "last_flush_at": self.last_flush_at or None,
            "last_error": self.last_error,
            "retention_days": settings.audit_retention_days,
        }


ACTIONS = {
    ("POST", "/api/login"): "auth.login",
    ("POST", "/api/logout"): "auth.logout",
    ("POST", "/api/accounts"): "account.add",
    ("PATCH", "/api/accounts/{label}"): "account.toggle",
    ("DELETE", "/api/accounts/{label}"): "account.remove",
    ("PUT", "/api/policy"): "policy.update",
    ("POST", "/api/policy/rule"): "policy.rule",
    ("POST", "/api/policy/sync"): "policy.sync",
    ("POST", "/api/policy/versions/{version_id}/restore"): "policy.restore",
    ("POST", "/api/cache/clear"): "cache.clear",
    ("GET", "/api/backup"): "backup.download",
    ("POST", "/api/restore"): "backup.restore",
    ("POST", "/api/devices"): "device.add",
    ("PATCH", "/api/devices/{device_id}"): "device.rename",
    ("DELETE", "/api/devices/{device_id}"): "device.remove",
    ("POST", "/api/devices/{device_id}/pause"): "device.pause",
    ("DELETE", "/api/devices/{device_id}/pause"): "device.resume",
    ("PUT", "/api/devices/{device_id}/policy"): "device.policy",
    ("PUT", "/api/devices/{device_id}/rules"): "device.rules",
    ("POST", "/api/devices/{device_id}/rules/entry"): "device.rule",
    ("POST", "/api/rule-groups/{group_id}/rules/entry"): "schedule.rule",
    ("PUT", "/api/devices/{device_id}/tags"): "device.tags",
    ("POST", "/api/devices/bulk"): "device.bulk",
    ("POST", "/api/devices/import"): "device.import",
    ("PUT", "/api/rewrites"): "rewrite.set",
    ("DELETE", "/api/rewrites/{name}"): "rewrite.remove",
    ("PUT", "/api/apps/{app_id}"): "app.block",
    ("DELETE", "/api/apps/{app_id}"): "app.unblock",
    ("POST", "/api/tokens"): "token.create",
    ("DELETE", "/api/tokens/{token_id}"): "token.revoke",
    ("DELETE", "/api/query-log"): "querylog.purge",
    ("GET", "/api/query-log/export"): "querylog.export",
    ("POST", "/api/lookup"): "tools.lookup",
    ("POST", "/api/alerts/test"): "alert.test",
    ("POST", "/api/alerts/digest"): "alerts.digest",
}

ALWAYS_AUDITED = frozenset(
    {"backup.download", "querylog.export", "auth.login", "auth.logout", "tools.lookup"}
)


NOT_AN_ACTION = frozenset({404, 405, 400, 422})

READS_WEARING_A_POST = frozenset({"/api/policy/preview"})


def action_for(method: str, route_path: str) -> str:
    known = ACTIONS.get((method.upper(), route_path))
    if known:
        return known
    if route_path in READS_WEARING_A_POST:
        return ""
    if not route_path.startswith("/api/"):
        return ""
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        return f"{method.lower()}:{route_path}"
    return ""


def worth_recording(action: str, status: int) -> bool:
    if not action:
        return False
    if action in ALWAYS_AUDITED:
        return True
    return status not in NOT_AN_ACTION


def outcome_for(status: int) -> str:
    if status < 400:
        return "ok"
    return "denied" if status in (401, 403, 429) else "failed"
