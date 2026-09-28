from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

VOWELS = frozenset("aeiouy")
HEX = re.compile(r"^[0-9a-f]+$")
SECOND_LEVEL = frozenset({"ac", "co", "com", "edu", "gov", "ltd", "ne", "net", "or", "org", "plc"})
UNSCORED_TLDS = frozenset({"arpa", "home", "internal", "lan", "local", "localhost", "test"})

ABUSED_TLDS = frozenset(
    {
        "bond",
        "buzz",
        "cf",
        "cfd",
        "click",
        "cyou",
        "ga",
        "gq",
        "icu",
        "lol",
        "ml",
        "monster",
        "mov",
        "quest",
        "rest",
        "sbs",
        "surf",
        "tk",
        "top",
        "work",
        "xyz",
        "zip",
    }
)

EXEMPT = frozenset(
    {
        "1e100.net",
        "aaplimg.com",
        "akadns.net",
        "akamai.net",
        "akamaiedge.net",
        "akamaihd.net",
        "akamaized.net",
        "amazonaws.com",
        "apple-dns.net",
        "azureedge.net",
        "azurefd.net",
        "cloudapp.net",
        "cloudflare.net",
        "cloudfront.net",
        "edgekey.net",
        "edgesuite.net",
        "fastly.net",
        "fbcdn.net",
        "googleusercontent.com",
        "googlevideo.com",
        "gstatic.com",
        "trafficmanager.net",
        "windows.net",
    }
)

POINTS = {
    "entropy": 40,
    "digits": 20,
    "consonants": 20,
    "hex": 25,
    "length": 10,
    "tld": 20,
    "punycode": 25,
    "labels": 10,
    "subdomain": 15,
}


@dataclass(frozen=True)
class Suspicion:
    score: int
    reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {"score": self.score, "reasons": list(self.reasons)}


CLEAN = Suspicion(0, ())


def entropy(text: str) -> float:
    if not text:
        return 0.0
    total = len(text)
    return -sum(n / total * math.log2(n / total) for n in Counter(text).values())


def split(name: str) -> tuple[list[str], str, str] | None:
    labels = [label for label in name.strip().rstrip(".").lower().split(".") if label]
    if len(labels) < 2:
        return None
    tld = labels[-1]
    suffix_size = 2 if len(tld) == 2 and labels[-2] in SECOND_LEVEL and len(labels) > 2 else 1
    core = labels[-suffix_size - 1]
    return labels[: -suffix_size - 1], core, ".".join(labels[-suffix_size:])


def registrable(name: str) -> str:
    parts = split(name)
    if parts is None:
        return name.strip().rstrip(".").lower()
    _, core, suffix = parts
    return f"{core}.{suffix}"


def _switches(text: str) -> int:
    return sum(1 for a, b in zip(text, text[1:], strict=False) if a.isdigit() != b.isdigit())


def _longest_unvoiced(text: str) -> int:
    longest = run = 0
    for char in text:
        run = 0 if char in VOWELS or not char.isalnum() else run + 1
        longest = max(longest, run)
    return longest


def _core_reasons(core: str) -> list[str]:
    found: list[str] = []
    plain = core.replace("-", "")
    if len(plain) < 6:
        return found

    letters = [char for char in plain if char.isalpha()]
    digits = sum(1 for char in plain if char.isdigit())
    vowel_ratio = sum(1 for char in letters if char in VOWELS) / len(letters) if letters else 0.0
    mixed = _switches(plain) >= 3

    if len(plain) >= 8 and entropy(plain) >= 3.0 and (vowel_ratio < 0.25 or mixed):
        found.append("entropy")
    if letters and digits / len(plain) >= 0.25 and mixed:
        found.append("digits")
    if _longest_unvoiced(core) >= 6:
        found.append("consonants")
    if len(plain) >= 10 and HEX.match(plain) and digits >= 2 and len(letters) >= 2:
        found.append("hex")
    if len(plain) >= 20:
        found.append("length")
    return found


def score(name: str) -> Suspicion:
    parts = split(name)
    if parts is None:
        return CLEAN
    subs, core, suffix = parts
    tld = suffix.rsplit(".", 1)[-1]

    if tld in UNSCORED_TLDS or tld.isdigit() or f"{core}.{suffix}" in EXEMPT:
        return CLEAN

    reasons: list[str] = []
    if core.startswith("xn--"):
        reasons.append("punycode")
    else:
        reasons.extend(_core_reasons(core))
        if any(label.startswith("xn--") for label in subs):
            reasons.append("punycode")

    if tld in ABUSED_TLDS:
        reasons.append("tld")
    if len(subs) >= 4:
        reasons.append("labels")
    if len(".".join(subs)) >= 50:
        reasons.append("subdomain")

    return Suspicion(min(100, sum(POINTS[reason] for reason in reasons)), tuple(reasons))
