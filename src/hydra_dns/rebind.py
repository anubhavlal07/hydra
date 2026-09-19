from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Iterable

import dns.message
import dns.rdatatype

log = logging.getLogger(__name__)

EXEMPT_ZONES = ("in-addr.arpa", "ip6.arpa", "localhost")
DEFAULT_ALLOW = ("plex.direct",)
MAX_ALLOW = 64
LABEL = "rebind guard"


def refused_by_guard(upstream: str) -> bool:
    return upstream == LABEL or upstream.startswith(f"{LABEL} ")


_A = dns.rdatatype.A
_AAAA = dns.rdatatype.AAAA
_SVCB = dns.rdatatype.SVCB
_HTTPS = dns.rdatatype.HTTPS
_IPV4HINT = 4
_IPV6HINT = 6


def _ranges(*cidrs: str) -> tuple[tuple[int, int], ...]:
    networks = [ipaddress.ip_network(c) for c in cidrs]
    return tuple((int(n.network_address), int(n.netmask)) for n in networks)


_V4 = _ranges(
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
)
_V6 = _ranges("::1/128", "::/128", "fe80::/10", "fec0::/10", "fc00::/7")
_V4_IN_V6 = _ranges("::ffff:0:0/96", "::/96", "64:ff9b::/96", "::ffff:0:0:0/96")
_SUSPECT_V4 = frozenset({"0", "10", "100", "127", "169", "172", "192"})


def private_v4(text: str) -> bool:
    if text[: text.find(".")] not in _SUSPECT_V4:
        return False
    value = int.from_bytes(socket.inet_aton(text), "big")
    return _in(value, _V4)


def private_v6(text: str) -> bool:
    if text[0] in "23":
        return False
    value = int.from_bytes(socket.inet_pton(socket.AF_INET6, text), "big")
    if _in(value, _V6):
        return True
    if _in(value, _V4_IN_V6):
        return _in(value & 0xFFFFFFFF, _V4)
    return False


def _in(value: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(value & mask == network for network, mask in ranges)


def normalise(suffixes: Iterable[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for raw in suffixes:
        suffix = raw.strip().strip(".").lower()
        if suffix and suffix not in seen:
            seen.append(suffix)
    return tuple(seen[:MAX_ALLOW])


class RebindGuard:
    def __init__(self, enabled: bool = True, allow: Iterable[str] = DEFAULT_ALLOW) -> None:
        self.enabled = enabled
        self.allow = normalise(allow)
        passed = self.allow + EXEMPT_ZONES
        self._exact = frozenset(passed)
        self._dotted = tuple(f".{s}" for s in passed)
        self.refused = 0
        self.failed = 0

    def exempt(self, name: str) -> bool:
        return name in self._exact or name.endswith(self._dotted)

    def refuses(self, name: str, message: dns.message.Message) -> bool:
        if not self.enabled:
            return False
        try:
            if not message.answer or self.exempt(name):
                return False
            return _carries_private(message)
        except Exception as exc:
            self.failed += 1
            log.debug("rebind check failed for %s, passing the answer through: %s", name, exc)
            return False

    def note(self) -> None:
        self.refused += 1

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "allow": list(self.allow),
            "refused": self.refused,
            "failed": self.failed,
        }


def _carries_private(message: dns.message.Message) -> bool:
    for rrset in message.answer:
        kind = rrset.rdtype
        if kind == _A:
            for rdata in rrset:
                if private_v4(rdata.address):
                    return True
        elif kind == _AAAA:
            for rdata in rrset:
                if private_v6(rdata.address):
                    return True
        elif kind in (_HTTPS, _SVCB):
            for rdata in rrset:
                if _hints_private(rdata.params):
                    return True
    return False


def _hints_private(params: dict) -> bool:
    four = params.get(_IPV4HINT)
    if four is not None and any(private_v4(a) for a in four.addresses):
        return True
    six = params.get(_IPV6HINT)
    return six is not None and any(private_v6(a) for a in six.addresses)


def build(settings: object) -> RebindGuard:
    return RebindGuard(
        enabled=bool(getattr(settings, "rebind_guard", True)),
        allow=getattr(settings, "rebind_allow", DEFAULT_ALLOW),
    )
