from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Iterable
from typing import Any

from ..watch import days_of_headroom

QUERIES = "queries"
BLOCKED = "blocked"


def _counts(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, dict):
        return 0, 0, 0
    return (
        int(value.get("queries", 0) or 0),
        int(value.get("blocked", 0) or 0),
        int(value.get("companies", 0) or 0),
    )


def merge_time_series(series_per_account: Iterable[list[dict]]) -> list[dict]:
    merged: dict[int, dict[str, int]] = {}
    for series in series_per_account:
        for point in series or []:
            at = int(point.get("time_millis", 0) or 0)
            if not at:
                continue
            queries, blocked, _ = _counts(point.get("value"))
            bucket = merged.setdefault(at, {QUERIES: 0, BLOCKED: 0})
            bucket[QUERIES] += queries
            bucket[BLOCKED] += blocked

    return [
        {"at": at, "queries": v[QUERIES], "blocked": v[BLOCKED]} for at, v in sorted(merged.items())
    ]


def merge_counted(
    lists_per_account: Iterable[list[dict]],
    key_of: Callable[[dict], str],
    key_name: str,
    limit: int = 25,
) -> list[dict]:
    merged: dict[str, dict[str, Any]] = {}
    for entries in lists_per_account:
        for entry in entries or []:
            key = key_of(entry)
            if not key:
                continue
            queries, blocked, companies = _counts(entry.get("value"))
            if "queries" in entry and not isinstance(entry.get("value"), dict):
                queries = int(entry.get("queries", 0) or 0)
            row = merged.setdefault(
                key, {key_name: key, "queries": 0, "blocked": 0, "companies": 0}
            )
            row["queries"] += queries
            row["blocked"] += blocked
            row["companies"] = max(row["companies"], companies)

    ranked = sorted(merged.values(), key=lambda r: r["queries"], reverse=True)
    return ranked[:limit]


def merge_query_log(
    pages: dict[str, tuple[list[dict], str | None]], limit: int
) -> tuple[list[dict], str | None]:
    combined: list[dict] = []
    cursors: dict[str, str] = {}

    for label, (items, next_cursor) in pages.items():
        for item in items or []:
            entry = dict(item)
            entry["account"] = label
            combined.append(entry)
        if next_cursor:
            cursors[label] = next_cursor

    combined.sort(key=lambda i: int(i.get("time_millis", 0) or 0), reverse=True)
    return combined[:limit], encode_cursor(cursors)


def encode_cursor(cursors: dict[str, str]) -> str | None:
    if not cursors:
        return None
    raw = json.dumps(cursors, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, str]:
    if not cursor:
        return {}
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, binascii.Error):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(k): str(v) for k, v in decoded.items()}


def quota_rollup(accounts: Iterable[dict], cache_hit_rate: float = 0.0) -> dict:
    rows = list(accounts)
    serving = [a for a in rows if a.get("enabled", True)]
    known = [a for a in serving if a.get("requests_limit", -1) > 0]
    total_limit = sum(int(a["requests_limit"]) for a in known)
    total_used = sum(max(0, int(a.get("requests_used", 0))) for a in known)
    remaining = max(0, total_limit - total_used)

    multiplier = 1.0 / (1.0 - cache_hit_rate) if cache_hit_rate < 0.99 else 100.0
    headroom = days_of_headroom(remaining, total_used)

    return {
        "accounts_reporting": len(known),
        "accounts_disabled": len(rows) - len(serving),
        "requests_limit": total_limit,
        "requests_used": total_used,
        "requests_remaining": remaining,
        "used_fraction": round(total_used / total_limit, 4) if total_limit else 0.0,
        "cache_hit_rate": round(cache_hit_rate, 4),
        "cache_multiplier": round(multiplier, 2),
        "effective_remaining": int(remaining * multiplier),
        "effective_limit": int(total_limit * multiplier),
        "headroom_days": headroom if headroom is None else round(headroom, 2),
    }
