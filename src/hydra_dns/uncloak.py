from __future__ import annotations

import dns.message
import dns.rdatatype

LABEL = "cname"
LAYER = "cname"
_PREFIX = f"{LABEL} "
_ARROW = " → "
_CNAME = dns.rdatatype.CNAME


def label_for(target: str, by: str) -> str:
    return f"{LABEL} {target} · {by}"


def uncloaked(upstream: str) -> bool:
    return upstream.startswith(_PREFIX)


def reason_of(upstream: str) -> str:
    cut = upstream.find(_ARROW)
    return upstream[:cut] if cut >= 0 else upstream


def targets(message: dns.message.Message, asked: str) -> list[str]:
    found: list[str] = []
    for rrset in message.answer:
        if rrset.rdtype != _CNAME:
            continue
        for rdata in rrset:
            target = rdata.target.to_text(omit_final_dot=True).lower()
            if target and target != asked and target not in found:
                found.append(target)
    return found


class Uncloaker:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.blocked = 0
        self.failed = 0

    def note(self) -> None:
        self.blocked += 1

    def snapshot(self) -> dict:
        return {"enabled": self.enabled, "blocked": self.blocked, "failed": self.failed}


def build(settings: object) -> Uncloaker:
    return Uncloaker(enabled=bool(getattr(settings, "cname_uncloak", True)))
