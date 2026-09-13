from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

MAX_RULES = 100

_READ_ONLY = {"user_rules_settings": ("rules_count",)}

_DOMAIN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+\.?$", re.I)


def normalise_rule(rule: str) -> str:
    return rule.strip().strip("\r\n").strip()


def parse_rules(text: str) -> list[str]:
    seen: set[str] = set()
    rules: list[str] = []
    for line in text.splitlines():
        rule = normalise_rule(line)
        if not rule or rule in seen:
            continue
        seen.add(rule)
        rules.append(rule)
    return rules


def rules_of(server_settings: dict[str, Any]) -> list[str]:
    raw = (server_settings.get("user_rules_settings") or {}).get("rules") or []
    return [r for r in (normalise_rule(item) for item in raw) if r]


def block_rule(domain: str) -> str:
    return f"||{_clean_domain(domain)}^"


def allow_rule(domain: str) -> str:
    return f"@@||{_clean_domain(domain)}^"


def _clean_domain(domain: str) -> str:
    name = normalise_rule(domain).lower().rstrip(".")
    if not _DOMAIN.match(name):
        raise ValueError(f"{domain!r} does not look like a domain name")
    return name


def rule_targets(rule: str) -> str:
    match = re.match(r"^(?:@@)?\|\|([a-z0-9.-]+)\^", normalise_rule(rule), re.I)
    return match.group(1).lower() if match else ""


def diff_rules(per_account: dict[str, list[str]]) -> dict[str, Any]:
    labels = sorted(per_account)
    order: list[str] = []
    holders: dict[str, list[str]] = {}
    for label in labels:
        for rule in per_account[label]:
            if rule not in holders:
                holders[rule] = []
                order.append(rule)
            holders[rule].append(label)

    return {
        "rules": [
            {
                "rule": rule,
                "accounts": holders[rule],
                "everywhere": len(holders[rule]) == len(labels),
            }
            for rule in order
        ],
        "in_sync": all(len(holders[rule]) == len(labels) for rule in order),
    }


def diff_setting(per_account: dict[str, Any]) -> dict[str, Any]:
    values = list(per_account.values())
    agreed = all(v == values[0] for v in values) if values else True
    return {
        "value": values[0] if agreed and values else None,
        "in_sync": agreed,
        "per_account": per_account,
    }


def enabled_filter_lists(server_settings: dict[str, Any]) -> list[str]:
    lists = (server_settings.get("filter_lists_settings") or {}).get("filter_list") or []
    return sorted(str(item.get("filter_id")) for item in lists if item.get("enabled"))


def filter_lists_switch(server_settings: dict[str, Any]) -> bool:
    return bool((server_settings.get("filter_lists_settings") or {}).get("enabled", True))


def rules_switch(server_settings: dict[str, Any]) -> bool:
    return bool((server_settings.get("user_rules_settings") or {}).get("enabled", True))


def parental(field: str) -> Callable[[dict[str, Any]], bool]:
    return lambda s: bool((s.get("parental_control_settings") or {}).get(field))


PARENTAL_FIELDS = {
    "parental_control": "enabled",
    "youtube_safe_search": "youtube_safe_search_enabled",
    "engines_safe_search": "engines_safe_search_enabled",
}


SETTINGS: tuple[tuple[str, str, Callable[[dict[str, Any]], Any]], ...] = (
    ("protection", "protection", lambda s: bool(s.get("protection_enabled"))),
    ("filter lists", "filter_lists", enabled_filter_lists),
    ("filter lists switch", "filter_lists_enabled", filter_lists_switch),
    ("user rules switch", "rules_enabled", rules_switch),
    (
        "safe browsing",
        "safe_browsing",
        lambda s: bool((s.get("safebrowsing_settings") or {}).get("enabled")),
    ),
    ("parental control", "parental_control", parental("enabled")),
    ("YouTube restricted mode", "youtube_safe_search", parental("youtube_safe_search_enabled")),
    ("search engine safe search", "engines_safe_search", parental("engines_safe_search_enabled")),
)

RULES = "user rules"


def _frozen(value: Any) -> Any:
    return tuple(value) if isinstance(value, list) else value


def _majority(per_account: dict[str, Any]) -> tuple[bool, Any]:
    if not per_account:
        return False, None
    counts: dict[Any, int] = {}
    for value in per_account.values():
        counts[_frozen(value)] = counts.get(_frozen(value), 0) + 1
    value, count = max(counts.items(), key=lambda item: item[1])
    return count * 2 > len(per_account), value


def _per_section(settings_by_account: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sections = {
        RULES: {label: sorted(set(rules_of(s))) for label, s in settings_by_account.items()},
    }
    for name, _key, read in SETTINGS:
        sections[name] = {label: read(s) for label, s in settings_by_account.items()}
    return sections


def outliers(
    settings_by_account: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[str]], list[str]]:
    odd: dict[str, list[str]] = {}
    split: list[str] = []
    for name, per_account in _per_section(settings_by_account).items():
        values = {_frozen(v) for v in per_account.values()}
        if len(values) <= 1:
            continue
        held, value = _majority(per_account)
        if not held:
            split.append(name)
            continue
        for label, mine in per_account.items():
            if _frozen(mine) != value:
                odd.setdefault(label, []).append(name)
    return {label: odd[label] for label in sorted(odd)}, split


def agreed(
    settings_by_account: dict[str, dict[str, Any]], *, unanimous: bool
) -> tuple[dict[str, Any], list[str]]:
    if not settings_by_account:
        return {}, []
    changes: dict[str, Any] = {
        "rules": [
            entry["rule"]
            for entry in diff_rules(
                {label: rules_of(s) for label, s in settings_by_account.items()}
            )["rules"]
        ]
    }
    left: list[str] = []
    for name, key, read in SETTINGS:
        per_account = {label: read(s) for label, s in settings_by_account.items()}
        values = {_frozen(v) for v in per_account.values()}
        if len(values) == 1:
            changes[key] = next(iter(per_account.values()))
            continue
        held, value = _majority(per_account)
        if held and not unanimous:
            changes[key] = list(value) if isinstance(value, tuple) else value
        else:
            left.append(name)
    return changes, left


def summarise(settings_by_account: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rules = diff_rules({label: rules_of(s) for label, s in settings_by_account.items()})
    sections = {
        key: diff_setting({label: read(s) for label, s in settings_by_account.items()})
        for _name, key, read in SETTINGS
    }

    unprotected = [
        label for label, value in sections["protection"]["per_account"].items() if not value
    ]

    drifting = [RULES] if not rules["in_sync"] else []
    drifting.extend(name for name, key, _read in SETTINGS if not sections[key]["in_sync"])
    odd, split = outliers(settings_by_account)

    return {
        "rules": rules,
        **sections,
        "unprotected_accounts": unprotected,
        "drifting": drifting,
        "outliers": odd,
        "split": split,
        "in_sync": not drifting,
    }


def apply_to_settings(
    current: dict[str, Any],
    *,
    rules: list[str] | None = None,
    rules_enabled: bool | None = None,
    protection: bool | None = None,
    filter_lists: list[str] | None = None,
    filter_lists_enabled: bool | None = None,
    safe_browsing: bool | None = None,
    parental_control: bool | None = None,
    youtube_safe_search: bool | None = None,
    engines_safe_search: bool | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = _deep_copy(current)

    for section, keys in _READ_ONLY.items():
        for key in keys:
            if isinstance(body.get(section), dict):
                body[section].pop(key, None)

    if rules is not None or rules_enabled is not None:
        user_rules = body.setdefault("user_rules_settings", {})
        if rules is not None:
            user_rules["rules"] = list(rules)
        if rules_enabled is not None:
            user_rules["enabled"] = rules_enabled

    if protection is not None:
        body["protection_enabled"] = protection

    if safe_browsing is not None:
        body.setdefault("safebrowsing_settings", {})["enabled"] = safe_browsing

    for key, value in (
        ("parental_control", parental_control),
        ("youtube_safe_search", youtube_safe_search),
        ("engines_safe_search", engines_safe_search),
    ):
        if value is not None:
            body.setdefault("parental_control_settings", {})[PARENTAL_FIELDS[key]] = value

    if filter_lists is not None:
        wanted = set(filter_lists)
        section = body.setdefault("filter_lists_settings", {})
        known = {str(item.get("filter_id")) for item in (section.get("filter_list") or [])}
        section["filter_list"] = [
            {"filter_id": filter_id, "enabled": filter_id in wanted}
            for filter_id in (
                str(item.get("filter_id")) for item in (section.get("filter_list") or [])
            )
        ]
        section["filter_list"].extend(
            {"filter_id": filter_id, "enabled": True} for filter_id in sorted(wanted - known)
        )
        section["enabled"] = True

    if filter_lists_enabled is not None:
        body.setdefault("filter_lists_settings", {})["enabled"] = filter_lists_enabled

    return body


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def with_rule(rules: Iterable[str], rule: str) -> list[str]:
    rule = normalise_rule(rule)
    domain = rule_targets(rule)
    kept = [
        existing
        for existing in (normalise_rule(r) for r in rules)
        if existing and existing != rule and not (domain and rule_targets(existing) == domain)
    ]
    return [*kept, rule]


def without_rule(rules: Iterable[str], rule: str) -> list[str]:
    rule = normalise_rule(rule)
    return [r for r in (normalise_rule(x) for x in rules) if r and r != rule]
