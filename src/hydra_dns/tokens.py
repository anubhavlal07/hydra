from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

import httpx

from .config import settings

log = logging.getLogger(__name__)

FN_LIST = "hydra_tokens_list"
FN_ADD = "hydra_token_add"
FN_REMOVE = "hydra_token_remove"
FN_TOUCH = "hydra_token_touch"

PREFIX = "hyd_"
SECRET_BYTES = 20
SCOPES = ("read", "metrics")
MAX_TOKENS = 25
TOUCH_EVERY_S = 300.0


class TokenError(RuntimeError):
    pass


@dataclass(frozen=True)
class MachineToken:
    id: str
    name: str
    scope: str
    digest: str
    created_at: float = 0.0
    last_used_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "scope": self.scope,
            "created_at": self.created_at or None,
            "last_used_at": self.last_used_at or None,
        }


def mint() -> tuple[str, str, str]:
    raw = secrets.token_hex(SECRET_BYTES)
    plaintext = f"{PREFIX}{raw}"
    return plaintext, raw[:8], digest_of(plaintext)


def digest_of(plaintext: str) -> str:
    return hashlib.sha256(plaintext.strip().encode()).hexdigest()


def bearer(header: str) -> str:
    value = (header or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


class TokenStore:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._tokens: dict[str, MachineToken] = {}
        self._touched: dict[str, float] = {}
        self._pending_touch: set[str] = set()
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.last_sync_at = 0.0
        self.last_error = ""
        self._synced_once = False

    @property
    def configured(self) -> bool:
        return bool(settings.store_url and settings.store_key and settings.store_secret)

    @property
    def writable(self) -> bool:
        return self.configured and self._synced_once

    @property
    def tokens(self) -> list[MachineToken]:
        return sorted(self._tokens.values(), key=lambda t: t.created_at)

    def verify(self, plaintext: str) -> MachineToken | None:
        if not plaintext.startswith(PREFIX):
            return None
        presented = digest_of(plaintext)
        for token in self._tokens.values():
            if hmac.compare_digest(token.digest, presented):
                return token
        return None

    def note_use(self, token: MachineToken, now: float | None = None) -> bool:
        moment = now if now is not None else time.monotonic()
        last = self._touched.get(token.id)
        if last is not None and moment - last < TOUCH_EVERY_S:
            return False
        self._touched[token.id] = moment
        self._pending_touch.add(token.id)
        return True

    async def flush_touches(self) -> int:
        pending, self._pending_touch = self._pending_touch, set()
        for token_id in pending:
            if token_id in self._tokens:
                await self.touch(token_id)
        return len(pending)

    async def _rpc(self, function: str, payload: dict) -> object:
        if not self.configured:
            raise TokenError("token storage is not configured (HYDRA_STORE_URL / _KEY / _SECRET)")
        try:
            response = await self._client.post(
                f"{settings.store_url}/rest/v1/rpc/{function}",
                json={"p_secret": settings.store_secret, **payload},
                headers={
                    "apikey": settings.store_key,
                    "authorization": f"Bearer {settings.store_key}",
                    "content-type": "application/json",
                },
                timeout=15.0,
            )
        except httpx.HTTPError as exc:
            raise TokenError(f"could not reach token storage: {exc}") from exc

        if response.status_code >= 400:
            raise TokenError(f"token storage returned HTTP {response.status_code}")
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise TokenError("token storage returned a non-JSON response") from exc

    @staticmethod
    def _rows(rows: object) -> dict[str, MachineToken]:
        if not isinstance(rows, list):
            return {}
        found: dict[str, MachineToken] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            token_id = str(row.get("id") or "")
            digest = str(row.get("digest") or "")
            if not token_id or len(digest) != 64:
                continue
            found[token_id] = MachineToken(
                id=token_id,
                name=str(row.get("name") or token_id),
                scope=str(row.get("scope") or "read"),
                digest=digest,
                created_at=float(row.get("created_at") or 0),
                last_used_at=float(row.get("last_used_at") or 0),
            )
        return found

    async def sync(self) -> list[MachineToken]:
        if not self.configured:
            return []
        try:
            rows = await self._rpc(FN_LIST, {})
        except TokenError as exc:
            self.last_error = str(exc)
            log.warning("token sync failed, keeping %s in hand: %s", len(self._tokens), exc)
            return self.tokens

        self._tokens = self._rows(rows)
        self.last_error = ""
        self.last_sync_at = time.time()
        self._synced_once = True
        return self.tokens

    async def create(self, name: str, scope: str = "read") -> tuple[MachineToken, str]:
        name = name.strip()
        if not name:
            raise TokenError("a token needs a name")
        if len(name) > 60:
            raise TokenError("token names are limited to 60 characters")
        if scope not in SCOPES:
            raise TokenError(f"scope must be one of {', '.join(SCOPES)}")

        async with self._lock:
            if len(self._tokens) >= MAX_TOKENS:
                raise TokenError(f"there are already {MAX_TOKENS} tokens; revoke one first")
            plaintext, token_id, digest = mint()
            await self._rpc(
                FN_ADD,
                {"p_id": token_id, "p_name": name, "p_scope": scope, "p_digest": digest},
            )
            await self.sync()

        created = self._tokens.get(token_id)
        if created is None:
            created = MachineToken(id=token_id, name=name, scope=scope, digest=digest)
        return created, plaintext

    async def revoke(self, token_id: str) -> bool:
        async with self._lock:
            removed = await self._rpc(FN_REMOVE, {"p_id": token_id})
            await self.sync()
        self._touched.pop(token_id, None)
        self._pending_touch.discard(token_id)
        return bool(removed)

    async def touch(self, token_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._rpc(FN_TOUCH, {"p_id": token_id})

    def start_background(self) -> None:
        if self.configured:
            self._task = asyncio.create_task(self._loop(), name="hydra-tokens")

    async def _loop(self) -> None:
        await self.sync()
        while True:
            await asyncio.sleep(max(60, settings.store_refresh_s))
            try:
                await self.flush_touches()
                await self.sync()
            except Exception:
                log.exception("token refresh failed")

    async def aclose(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task
        self._task = None

    def snapshot(self) -> dict:
        return {
            "configured": self.configured,
            "writable": self.writable,
            "count": len(self._tokens),
            "last_sync_at": self.last_sync_at or None,
            "last_error": self.last_error,
            "max": MAX_TOKENS,
            "scopes": list(SCOPES),
        }
