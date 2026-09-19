from __future__ import annotations

import datetime as dt
from typing import Any

NOMINAL = "nominal"
ATTENTION = "attention"


MIN_ELAPSED_DAYS = 1 / 24

REGISTRY_STALE_S = 900.0
BLOCKLIST_STALE_REFRESHES = 3
ERROR_RATE = 0.10
ERROR_RATE_MIN_QUERIES = 200
SIGN_IN_FAILURES = 5
QUIET_SUFFIX = " has stopped querying Hydra; its Private DNS or profile may be off"
DRIFT_MARK = "; ads can get through on "


def elapsed_days(now: dt.datetime | None = None) -> float:
    moment = now or dt.datetime.now(dt.UTC)
    into_day = (
        moment.hour * 3600 + moment.minute * 60 + moment.second + moment.microsecond / 1_000_000
    ) / 86_400
    return (moment.day - 1) + into_day


def days_of_headroom(
    remaining: int, used_this_month: int, now: dt.datetime | None = None
) -> float | None:
    elapsed = elapsed_days(now)
    if used_this_month <= 0 or elapsed < MIN_ELAPSED_DAYS:
        return None
    per_day = used_this_month / elapsed
    if per_day <= 0:
        return None
    return remaining / per_day


def reasons(
    *,
    accounts: list[dict[str, Any]],
    quota: dict[str, Any],
    pool_state: dict[str, Any],
    warn_days: float,
    now: dt.datetime | None = None,
    certificate: dict[str, Any] | None = None,
    warn_failures: int = 3,
    cert_warn_days: float = 14,
    devices: list[str] | None = None,
    registry: dict[str, Any] | None = None,
    resolver: dict[str, Any] | None = None,
    sign_in_failures: int = 0,
    silent_listeners: list[str] | None = None,
    blocklists: list[dict[str, Any]] | None = None,
    blocklist_refresh_s: float = 86_400,
    quiet: list[str] | None = None,
    drift: tuple[dict[str, list[str]], list[str]] | None = None,
) -> list[str]:
    found: list[str] = []

    if not accounts:
        found.append("no AdGuard account is configured")
        return found

    if devices:
        found.append(
            f"{_join(sorted(devices))} has spent its hourly budget and is being answered "
            "from cache only"
        )

    if pool_state.get("spent"):
        fallback = (pool_state.get("fallback") or {}).get("active")
        found.append(
            "no account can answer; queries are going to the fallback resolver unfiltered"
            if fallback
            else "no account can answer and there is no fallback"
        )

    if silent_listeners:
        names = list(silent_listeners)
        found.append(
            f"{_join(names)} {'is' if len(names) == 1 else 'are'} enabled but not listening; "
            f"devices pointed at {'it' if len(names) == 1 else 'them'} cannot resolve"
        )

    found.extend(f"{name}{QUIET_SUFFIX}" for name in quiet or [])

    serving = [a for a in accounts if a.get("enabled", True)]

    found.extend(drift_reasons(*(drift or ({}, []))))

    failed = [a["label"] for a in serving if not (a.get("auth") or {}).get("authenticated")]
    if failed:
        found.append(f"{_join(failed)} cannot sign in to AdGuard")

    spent = [a["label"] for a in serving if (a.get("upstream") or {}).get("exhausted")]
    if spent and len(spent) < len(serving):
        found.append(f"{_join(spent)} has spent its monthly allowance")

    shaky = [
        a["label"]
        for a in serving
        if (a.get("auth") or {}).get("authenticated")
        and int((a.get("auth") or {}).get("consecutive_failures", 0)) >= warn_failures
    ]
    if shaky:
        found.append(f"{_join(shaky)} keeps failing to renew its AdGuard token")

    days_left = (certificate or {}).get("days_left")
    if days_left is not None:
        if days_left <= 0:
            found.append("the TLS certificate has expired; DoT and the API are refusing clients")
        elif days_left < cert_warn_days:
            found.append(f"the TLS certificate expires in {days_left:.0f} days")

    if registry and registry.get("configured") and not registry.get("using_seed"):
        synced_at = registry.get("last_sync_at") or 0
        stale_for = (now or dt.datetime.now(dt.UTC)).timestamp() - synced_at
        if synced_at and stale_for > REGISTRY_STALE_S:
            found.append(
                f"the device registry has not been readable for {stale_for / 60:.0f} minutes, "
                "so devices still resolve but changes to them will not apply"
            )

    found.extend(_blocklist_reasons(blocklists or [], blocklist_refresh_s, now))

    if resolver:
        answered = int(resolver.get("queries", 0))
        failed = int(resolver.get("errors", 0))
        if answered >= ERROR_RATE_MIN_QUERIES and failed / answered >= ERROR_RATE:
            found.append(
                f"{failed / answered:.0%} of queries are failing, so devices are losing "
                "name resolution rather than being filtered"
            )

    if sign_in_failures >= SIGN_IN_FAILURES:
        found.append(
            f"{sign_in_failures} failed dashboard sign-ins recently, which is someone "
            "trying passwords rather than mistyping one"
        )

    headroom = days_of_headroom(
        int(quota.get("requests_remaining", 0)),
        int(quota.get("requests_used", 0)),
        now,
    )
    if headroom is not None and headroom < warn_days:
        found.append(f"about {headroom:.1f} days of allowance left at the current rate")

    return found


def is_quiet(reason: str) -> bool:
    return reason.endswith(QUIET_SUFFIX)


def is_drift(reason: str) -> bool:
    return DRIFT_MARK in reason


def once_per_episode(reason: str) -> bool:
    return is_quiet(reason) or is_drift(reason)


def drift_reasons(outliers: dict[str, list[str]], split: list[str]) -> list[str]:
    grouped: dict[tuple[str, ...], list[str]] = {}
    for label in sorted(outliers):
        grouped.setdefault(tuple(outliers[label]), []).append(label)

    found = []
    for sections, labels in grouped.items():
        one = len(labels) == 1
        found.append(
            f"{_join(labels)} {'filters' if one else 'filter'} differently from the other "
            f"accounts ({', '.join(sections)}){DRIFT_MARK}{'its' if one else 'their'} share "
            "of queries"
        )
    if split:
        found.append(
            f"the accounts filter differently from each other ({', '.join(split)}){DRIFT_MARK}"
            "part of the queries"
        )
    return found


def _blocklist_reasons(
    lists: list[dict[str, Any]], refresh_s: float, now: dt.datetime | None
) -> list[str]:
    moment = (now or dt.datetime.now(dt.UTC)).timestamp()
    stale_after = BLOCKLIST_STALE_REFRESHES * max(60.0, float(refresh_s))
    found: list[str] = []
    for row in lists:
        if not row.get("enabled"):
            continue
        name = str(row.get("name") or row.get("id") or "a blocklist")
        error = str(row.get("last_error") or "")
        because = f": {error}" if error else ""
        fetched_at = row.get("fetched_at")
        if not row.get("loaded"):
            if row.get("tried") or error:
                found.append(f"blocklist {name} has never loaded, so it blocks nothing{because}")
        elif fetched_at and moment - float(fetched_at) > stale_after:
            hours = (moment - float(fetched_at)) / 3600
            found.append(
                f"blocklist {name} has not refreshed for {hours:.0f} hours and is enforcing "
                f"an old copy{because}"
            )
        elif not fetched_at and error:
            found.append(
                f"blocklist {name} has no successful fetch on record and is enforcing "
                f"the copy on disk{because}"
            )
    return found


def _join(labels: list[str]) -> str:
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]
