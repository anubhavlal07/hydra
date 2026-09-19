from __future__ import annotations

from dataclasses import dataclass

import dns.exception
import dns.reversename

DEFAULT_ZONES: tuple[str, ...] = (
    "local",
    "localhost",
    "home.arpa",
    "internal",
    "intranet",
    "lan",
    "home",
    "corp",
    "invalid",
    "onion",
    "0.in-addr.arpa",
    "10.in-addr.arpa",
    "127.in-addr.arpa",
    "254.169.in-addr.arpa",
    "168.192.in-addr.arpa",
    "255.in-addr.arpa",
    "16.172.in-addr.arpa",
    "17.172.in-addr.arpa",
    "18.172.in-addr.arpa",
    "19.172.in-addr.arpa",
    "20.172.in-addr.arpa",
    "21.172.in-addr.arpa",
    "22.172.in-addr.arpa",
    "23.172.in-addr.arpa",
    "24.172.in-addr.arpa",
    "25.172.in-addr.arpa",
    "26.172.in-addr.arpa",
    "27.172.in-addr.arpa",
    "28.172.in-addr.arpa",
    "29.172.in-addr.arpa",
    "30.172.in-addr.arpa",
    "31.172.in-addr.arpa",
    "8.e.f.ip6.arpa",
    "9.e.f.ip6.arpa",
    "a.e.f.ip6.arpa",
    "b.e.f.ip6.arpa",
    "c.f.ip6.arpa",
    "d.f.ip6.arpa",
)

LOOPBACK = {"A": "127.0.0.1", "AAAA": "::1"}


@dataclass(frozen=True)
class Verdict:
    zone: str
    address: str = ""
    exists: bool = False


class Zones:
    def __init__(self, names: tuple[str, ...] | list[str]) -> None:
        cleaned = {n.strip().strip(".").lower() for n in names}
        self._names = frozenset(n for n in cleaned if n)
        self._depth = max((n.count(".") + 1 for n in self._names), default=0)

    def __bool__(self) -> bool:
        return bool(self._names)

    def __len__(self) -> int:
        return len(self._names)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._names))

    def zone_for(self, name: str) -> str | None:
        if not self._names:
            return None
        probe = name.strip().strip(".").lower()
        if not probe:
            return None
        labels = probe.split(".")
        for start in range(max(0, len(labels) - self._depth), len(labels)):
            candidate = ".".join(labels[start:])
            if candidate in self._names:
                return candidate
        return None

    def verdict_for(self, name: str, qtype: str) -> Verdict | None:
        zone = self.zone_for(name)
        if zone is None:
            return None
        if zone == "localhost":
            return Verdict(zone=zone, address=LOOPBACK.get(qtype, ""), exists=True)
        return Verdict(zone=zone)


def parse(raw: str) -> tuple[str, ...]:
    found: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        name = item.strip().strip(".").lower()
        if not name or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return tuple(found)


def reverse_name(address: str) -> str:
    try:
        return dns.reversename.from_address(address.strip()).to_text(omit_final_dot=True).lower()
    except (dns.exception.SyntaxError, ValueError):
        return ""


def ptr_index(rewrites: dict[str, str]) -> dict[str, tuple[str, ...]]:
    found: dict[str, list[str]] = {}
    for host, address in rewrites.items():
        reverse = reverse_name(address)
        name = host.strip().strip(".").lower()
        if not reverse or not name:
            continue
        found.setdefault(reverse, []).append(name)
    return {reverse: tuple(sorted(set(names))) for reverse, names in found.items()}
