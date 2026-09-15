from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

ALLOW_PREFIX = "@@"
COMMENT_PREFIXES = ("!", "#")
ENTRY_ACTIONS = ("block", "allow", "clear")
DOMAIN = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*$")
EXPIRES = "$expires="
EXPIRES_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
NEVER = float("inf")


@dataclass(frozen=True)
class RuleSet:
    block: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    temporary_block: tuple[str, ...] = ()
    temporary_allow: tuple[str, ...] = ()
    expiry: tuple[tuple[str, float], ...] = ()
    next_expiry: float = NEVER

    def __bool__(self) -> bool:
        return bool(self.block or self.allow or self.temporary_block or self.temporary_allow)

    def blocks(self, name: str) -> bool:
        if not self.block and not self.temporary_block:
            return False
        target = name.strip(".").lower()
        if self.temporary_allow and _covers(self.temporary_allow, target):
            return False
        if self.temporary_block and _covers(self.temporary_block, target):
            return True
        return _covers(self.allow, target) is False and _covers(self.block, target) is True

    def allows(self, name: str) -> bool:
        if not self.allow and not self.temporary_allow:
            return False
        target = name.strip(".").lower()
        if self.temporary_allow and _covers(self.temporary_allow, target):
            return True
        if self.temporary_block and _covers(self.temporary_block, target):
            return False
        return _covers(self.allow, target)

    def expires_at(self, rule: str, allowed: bool) -> float | None:
        key = render(rule, allowed)
        return next((at for line, at in self.expiry if line == key), None)


def listed(names: frozenset[str] | set[str], name: str) -> str | None:
    if not names:
        return None
    start = 0
    while True:
        candidate = name[start:] if start else name
        if candidate in names:
            return candidate
        dot = name.find(".", start)
        if dot < 0:
            return None
        start = dot + 1


def covers(patterns: tuple[str, ...], name: str) -> bool:
    return _covers(patterns, name.strip(".").lower())


def deciding(patterns: tuple[str, ...], name: str) -> str | None:
    target = name.strip(".").lower()
    exact = None
    for pattern in patterns:
        if target == pattern:
            exact = pattern
        elif target.endswith(f".{pattern}"):
            return pattern
    return exact


def _covers(patterns: tuple[str, ...], name: str) -> bool:
    return any(name == p or name.endswith(f".{p}") for p in patterns)


@dataclass(frozen=True, slots=True)
class Line:
    allow: bool
    domain: str
    expires_at: float | None = None

    @property
    def temporary(self) -> bool:
        return self.expires_at is not None


def stamp(expires_at: float) -> str:
    return datetime.fromtimestamp(int(expires_at), UTC).strftime(EXPIRES_FORMAT)


def _moment(value: str) -> float | None:
    try:
        parsed = datetime.strptime(value.strip().upper(), EXPIRES_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC).timestamp()


def read_line(raw: object) -> Line | None:
    line = str(raw or "").strip().lower()
    if not line or line.startswith(COMMENT_PREFIXES):
        return None

    expires_at = None
    head, marker, tail = line.partition("$")
    if marker:
        modifier = f"{marker}{tail}"
        if not modifier.startswith(EXPIRES):
            return None
        expires_at = _moment(modifier[len(EXPIRES) :])
        if expires_at is None:
            return None
        line = head.strip()

    allow = line.startswith(ALLOW_PREFIX)
    if allow:
        line = line[len(ALLOW_PREFIX) :].strip()

    line = line.removeprefix("||").removeprefix("*.").strip("^").strip(".")
    if not line or not DOMAIN.match(line):
        return None
    return Line(allow=allow, domain=line, expires_at=expires_at)


def render(domain: str, allow: bool, expires_at: float | None = None) -> str:
    line = f"{ALLOW_PREFIX}{domain}" if allow else domain
    return f"{line}{EXPIRES}{stamp(expires_at)}" if expires_at is not None else line


def parse(rules: object, now: float | None = None) -> RuleSet:
    if not isinstance(rules, (list, tuple)):
        return RuleSet()

    moment = time.time() if now is None else now
    block: list[str] = []
    allow: list[str] = []
    brief_block: list[str] = []
    brief_allow: list[str] = []
    expiry: list[tuple[str, float]] = []

    for raw in rules:
        entry = read_line(raw)
        if entry is None:
            continue
        if entry.expires_at is None:
            into = allow if entry.allow else block
        elif entry.expires_at <= moment:
            continue
        else:
            into = brief_allow if entry.allow else brief_block
            expiry.append((render(entry.domain, entry.allow), entry.expires_at))
        if entry.domain not in into:
            into.append(entry.domain)

    return RuleSet(
        block=tuple(block),
        allow=tuple(allow),
        temporary_block=tuple(brief_block),
        temporary_allow=tuple(brief_allow),
        expiry=tuple(expiry),
        next_expiry=min((at for _, at in expiry), default=NEVER),
    )


def invalid(rules: object) -> list[str]:
    if not isinstance(rules, (list, tuple)):
        return []

    bad: list[str] = []
    for raw in rules:
        line = str(raw or "").strip().lower()
        if not line or line.startswith(COMMENT_PREFIXES):
            continue
        if read_line(raw) is None:
            bad.append(str(raw))
    return bad


def temporary(rules: object) -> list[Line]:
    if not isinstance(rules, (list, tuple)):
        return []
    found = (read_line(raw) for raw in rules)
    return [entry for entry in found if entry is not None and entry.temporary]


def lapsed(rules: object, now: float) -> bool:
    return any(
        entry.expires_at is not None and entry.expires_at <= now for entry in temporary(rules)
    )


def prune(rules: tuple[str, ...] | list[str], now: float) -> list[str]:
    kept: list[str] = []
    for raw in rules:
        entry = read_line(raw)
        if entry is not None and entry.expires_at is not None and entry.expires_at <= now:
            continue
        kept.append(raw)
    return kept


def normalise(value: str) -> str | None:
    line = str(value or "").strip().lower().removeprefix(ALLOW_PREFIX).strip()
    line = line.removeprefix("||").removeprefix("*.").strip("^").strip(".")
    return line if line and DOMAIN.match(line) else None


def edit(
    lines: tuple[str, ...] | list[str],
    domain: str,
    action: str,
    expires_at: float | None = None,
    only_temporary: bool = False,
) -> list[str]:
    adding = action in ("block", "allow")
    allow = action == "allow"
    brief = adding and expires_at is not None
    kept: list[str] = []
    present = False
    for line in lines:
        entry = read_line(line)
        if entry is None or entry.domain != domain:
            kept.append(line)
        elif entry.temporary:
            continue
        elif brief or only_temporary:
            kept.append(line)
            present = present or entry.allow == allow
        elif adding and entry.allow == allow and not present:
            kept.append(line)
            present = True
    if adding and not present:
        kept.append(render(domain, allow, expires_at if brief else None))
    return kept
