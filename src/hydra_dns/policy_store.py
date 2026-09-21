from __future__ import annotations

import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

FN_ADD = "hydra_policy_version_add"
FN_LIST = "hydra_policy_versions_list"
FN_GET = "hydra_policy_version_get"

TRACKED = (
    "protection",
    "safe_browsing",
    "parental_control",
    "youtube_safe_search",
    "engines_safe_search",
    "filter_lists",
    "filter_lists_enabled",
    "rules_enabled",
)


class PolicyVersions:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self.last_error = ""

    @property
    def configured(self) -> bool:
        return bool(settings.store_url and settings.store_key and settings.store_secret)

    async def _rpc(self, function: str, payload: dict) -> object:
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
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
        if not response.content:
            return None
        return response.json()

    async def record(self, actor: str, note: str, rules: list[str], settings_map: dict) -> None:
        if not self.configured:
            return
        try:
            await self._rpc(
                FN_ADD,
                {
                    "p_actor": actor,
                    "p_note": note[:200],
                    "p_rules": list(rules),
                    "p_settings": settings_map,
                    "p_keep": settings.policy_versions_kept,
                },
            )
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)[:200]
            log.warning("could not record a policy version: %s", exc)

    async def list(self, limit: int = 50) -> list[dict]:
        if not self.configured:
            return []
        rows = await self._rpc(FN_LIST, {"p_limit": limit})
        return rows if isinstance(rows, list) else []

    async def get(self, version_id: int) -> dict | None:
        if not self.configured:
            return None
        rows = await self._rpc(FN_GET, {"p_id": version_id})
        if isinstance(rows, list) and rows:
            return rows[0]
        return None

    def snapshot(self) -> dict:
        return {
            "configured": self.configured,
            "kept": settings.policy_versions_kept,
            "last_error": self.last_error,
        }


def settings_of(summary: dict) -> dict:
    found: dict[str, object] = {}
    for key in TRACKED:
        entry = summary.get(key)
        if isinstance(entry, dict) and "value" in entry:
            found[key] = entry["value"]
        elif entry is not None:
            found[key] = entry
    return found


def diff(current: list[str], proposed: list[str]) -> dict:
    before = list(dict.fromkeys(current))
    after = list(dict.fromkeys(proposed))
    removed = [rule for rule in before if rule not in set(after)]
    added = [rule for rule in after if rule not in set(before)]
    return {
        "added": added,
        "removed": removed,
        "unchanged": len(before) - len(removed),
        "before": len(before),
        "after": len(after),
        "changed": bool(added or removed),
    }


def settings_diff(current: dict, proposed: dict) -> list[dict]:
    rows = []
    for key, value in proposed.items():
        was = current.get(key)
        if was != value:
            rows.append({"field": key, "from": was, "to": value})
    return rows
