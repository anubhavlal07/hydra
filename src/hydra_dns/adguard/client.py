from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..config import AccountConfig
from .auth import AuthError, TokenManager

log = logging.getLogger(__name__)

API_BASE = "https://api.adguard-dns.io"


class AdGuardError(RuntimeError):
    def __init__(self, label: str, detail: str, status: int = 0) -> None:
        super().__init__(f"{label}: {detail}")
        self.label = label
        self.detail = detail
        self.status = status


class AdGuardAccount:
    def __init__(self, config: AccountConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self.label = config.label
        self._client = client
        self._auth = TokenManager(config.label, config.username, config.password, client)

        self.enabled: bool = config.enabled
        self.doh_url: str = config.doh_url
        self.device_id: str = config.device_id
        self.device_name: str = ""
        self.dns_server_id: str = ""
        self.dns_addresses: dict[str, Any] = {}

        self.limits: dict[str, Any] = {}
        self.limits_fetched_at: float = 0.0
        self.last_error: str = ""
        self._discovery_lock = asyncio.Lock()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in (1, 2):
            try:
                token = await self._auth.token()
            except AuthError as exc:
                self.last_error = str(exc)
                raise AdGuardError(self.label, str(exc), 401) from exc

            try:
                response = await self._client.request(
                    method,
                    f"{API_BASE}{path}",
                    headers={"authorization": f"Bearer {token}", "accept": "application/json"},
                    timeout=25.0,
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                self.last_error = f"network error: {exc}"
                raise AdGuardError(self.label, self.last_error) from exc

            if response.status_code == 401 and attempt == 1:
                self._auth.invalidate()
                continue

            if response.status_code >= 400:
                detail = _error_detail(response)
                self.last_error = detail
                raise AdGuardError(self.label, detail, response.status_code)

            self.last_error = ""
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError as exc:
                raise AdGuardError(self.label, "response was not JSON") from exc

        raise AdGuardError(self.label, "authentication kept failing")

    async def discover_upstream(self) -> str:
        if self.doh_url:
            return self.doh_url

        async with self._discovery_lock:
            if self.doh_url:
                return self.doh_url

            payload = await self._request("GET", "/oapi/v2/devices", params={"limit": 100})
            devices = (payload or {}).get("items", []) or []

            chosen: dict[str, Any] | None = None
            if self.device_id:
                chosen = next((d for d in devices if d.get("id") == self.device_id), None)
                if chosen is None:
                    raise AdGuardError(
                        self.label,
                        f"device_id {self.device_id!r} is not on this account "
                        f"(it has {len(devices)}: {[d.get('id') for d in devices]})",
                    )
            else:
                chosen = next((d for d in devices if d.get("name") == _DEVICE_NAME), None)

            if chosen is None:
                try:
                    chosen = await self._create_device()
                except AdGuardError as exc:
                    if not devices:
                        raise
                    chosen = devices[0]
                    log.warning(
                        "%s: could not create a dedicated device (%s); borrowing %r instead, "
                        "so its AdGuard statistics will include Hydra traffic",
                        self.label,
                        exc.detail,
                        chosen.get("name"),
                    )

            url = (chosen.get("dns_addresses") or {}).get("dns_over_https_url", "")
            if not url:
                raise AdGuardError(
                    self.label,
                    f"device {chosen.get('id')!r} exposes no dns_over_https_url",
                )

            self.doh_url = url.rstrip("/")
            self.device_id = chosen.get("id", "")
            self.device_name = chosen.get("name", "")
            self.dns_server_id = chosen.get("dns_server_id", "")
            self.dns_addresses = chosen.get("dns_addresses") or {}
            log.info("%s: upstream is device %s (%s)", self.label, self.device_id, self.device_name)
            return self.doh_url

    async def _create_device(self) -> dict[str, Any]:
        servers = await self._request("GET", "/oapi/v2/dns_servers", params={"limit": 20})
        items = (servers or {}).get("items", []) or []
        if not items:
            raise AdGuardError(self.label, "account has no DNS server to attach a device to")
        server = next((s for s in items if s.get("default")), items[0])

        created = await self._request(
            "POST",
            "/oapi/v1/devices",
            json={
                "name": _DEVICE_NAME,
                "device_type": "LINUX",
                "dns_server_id": server.get("id"),
            },
        )
        log.info("%s: created device %s", self.label, (created or {}).get("id"))
        return created or {}

    async def fetch_limits(self) -> dict[str, Any]:
        payload = await self._request("GET", "/oapi/v1/account/limits")
        self.limits = payload or {}
        self.limits_fetched_at = time.time()
        return self.limits

    @property
    def requests_used(self) -> int:
        return int((self.limits.get("requests") or {}).get("used", -1))

    @property
    def requests_limit(self) -> int:
        return int((self.limits.get("requests") or {}).get("limit", -1))

    async def devices(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/oapi/v2/devices", params={"limit": 100})
        return (payload or {}).get("items", []) or []

    async def dns_servers(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/oapi/v2/dns_servers", params={"limit": 50})
        return (payload or {}).get("items", []) or []

    async def stats_time(self, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/oapi/v1/stats/time",
            params={"time_from_millis": start_ms, "time_to_millis": end_ms},
        )
        return (payload or {}).get("stats", []) or []

    async def stats_domains(self, start_ms: int, end_ms: int, limit: int = 50) -> list[dict]:
        payload = await self._request(
            "GET",
            "/oapi/v1/stats/domains",
            params={"time_from_millis": start_ms, "time_to_millis": end_ms, "limit": limit},
        )
        return (payload or {}).get("stats", []) or []

    async def stats_companies(self, start_ms: int, end_ms: int, limit: int = 50) -> list[dict]:
        payload = await self._request(
            "GET",
            "/oapi/v1/stats/companies",
            params={"time_from_millis": start_ms, "time_to_millis": end_ms, "limit": limit},
        )
        return (payload or {}).get("stats", []) or []

    async def stats_countries(self, start_ms: int, end_ms: int, limit: int = 30) -> list[dict]:
        payload = await self._request(
            "GET",
            "/oapi/v1/stats/countries",
            params={"time_from_millis": start_ms, "time_to_millis": end_ms, "limit": limit},
        )
        return (payload or {}).get("stats", []) or []

    async def stats_categories(self, start_ms: int, end_ms: int) -> list[dict]:
        payload = await self._request(
            "GET",
            "/oapi/v1/stats/categories",
            params={"time_from_millis": start_ms, "time_to_millis": end_ms},
        )
        return (payload or {}).get("stats", []) or []

    async def filter_lists(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/oapi/v1/filter_lists")
        if isinstance(payload, list):
            return payload
        return (payload or {}).get("filter_lists", []) or []

    async def _settings_server_id(self) -> str:
        if not self.dns_server_id:
            await self.discover_upstream()
        if self.dns_server_id:
            return self.dns_server_id

        servers = await self.dns_servers()
        if not servers:
            raise AdGuardError(self.label, "account has no DNS server to read settings from")
        chosen = next((s for s in servers if s.get("default")), servers[0])
        self.dns_server_id = str(chosen.get("id", ""))
        return self.dns_server_id

    async def server_settings(self) -> dict[str, Any]:
        server_id = await self._settings_server_id()
        return await self._request("GET", f"/oapi/v1/dns_servers/{server_id}/settings") or {}

    async def update_server_settings(self, body: dict[str, Any]) -> None:
        server_id = await self._settings_server_id()
        await self._request("PUT", f"/oapi/v1/dns_servers/{server_id}/settings", json=body)

    async def query_log(
        self, start_ms: int, end_ms: int, limit: int = 100, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        params: dict[str, Any] = {
            "time_from_millis": start_ms,
            "time_to_millis": end_ms,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor
        payload = await self._request("GET", "/oapi/v1/query_log", params=params) or {}

        items = payload.get("items", []) or []
        pages = payload.get("pages", []) or []
        next_cursor: str | None = None
        for index, page in enumerate(pages):
            if page.get("current") and index + 1 < len(pages):
                next_cursor = pages[index + 1].get("page_cursor")
                break
        return items, next_cursor

    def snapshot(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "username": self.config.redacted,
            "enabled": self.enabled,
            "auth": self._auth.snapshot(),
            "device_id": self.device_id or None,
            "device_name": self.device_name or None,
            "dns_server_id": self.dns_server_id or None,
            "upstream_configured": bool(self.doh_url),
            "dns_addresses": self.dns_addresses,
            "requests_used": self.requests_used,
            "requests_limit": self.requests_limit,
            "limits_fetched_at": self.limits_fetched_at or None,
            "limits": self.limits,
            "last_error": self.last_error,
        }

    def force_reauth(self) -> None:
        self._auth.invalidate()

    def forget_upstream(self) -> None:
        if not self.config.doh_url:
            self.doh_url = ""

    async def close(self) -> None:
        await self._auth.revoke()


_DEVICE_NAME = "Hydra DNS"


def _error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    code = body.get("error_code") or ""
    message = body.get("message") or body.get("error_description") or ""
    fields = body.get("fields") or []
    parts = [f"HTTP {response.status_code}"]
    if code:
        parts.append(str(code))
    if message:
        parts.append(str(message))
    for field in fields[:3]:
        parts.append(f"{field.get('field')}: {field.get('message')}")
    return " / ".join(parts)
