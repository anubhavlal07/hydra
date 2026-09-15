from __future__ import annotations

import re
from collections.abc import Iterable

GOOGLE = re.compile(r"^(www\.)?google\.(com|[a-z]{2}|com?\.[a-z]{2})$")

SERVICES: dict[str, dict[str, str]] = {
    "youtube": {
        "www.youtube.com": "restrictmoderate.youtube.com",
        "youtube.com": "restrictmoderate.youtube.com",
        "m.youtube.com": "restrictmoderate.youtube.com",
        "youtubei.googleapis.com": "restrictmoderate.youtube.com",
        "youtube.googleapis.com": "restrictmoderate.youtube.com",
        "www.youtube-nocookie.com": "restrictmoderate.youtube.com",
    },
    "bing": {
        "www.bing.com": "strict.bing.com",
        "bing.com": "strict.bing.com",
    },
    "duckduckgo": {
        "duckduckgo.com": "safe.duckduckgo.com",
        "www.duckduckgo.com": "safe.duckduckgo.com",
    },
    "pixiv": {
        "www.pixiv.net": "safe.pixiv.net",
    },
}

GOOGLE_TARGET = "forcesafesearch.google.com"


def service_for(name: str) -> str | None:
    host = name.strip(".").lower()
    if GOOGLE.match(host):
        return "google"
    for service, hosts in SERVICES.items():
        if host in hosts:
            return service
    return None


def target_for(name: str, skip: Iterable[str] = ()) -> str | None:
    service = service_for(name)
    if service is None or service in skip:
        return None
    if service == "google":
        return GOOGLE_TARGET
    return SERVICES[service][name.strip(".").lower()]


def covers() -> list[str]:
    return ["google", *SERVICES]


def normalise_skip(values: Iterable[object]) -> tuple[str, ...]:
    known = covers()
    wanted = {str(v).strip().lower() for v in values}
    unknown = sorted(wanted - set(known) - {""})
    if unknown:
        raise ValueError(f"safe search does not cover {', '.join(unknown)}")
    return tuple(service for service in known if service in wanted)
