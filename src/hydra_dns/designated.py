from __future__ import annotations

from urllib.parse import urlsplit

from .config import device_token, settings

NAME = "_dns.resolver.arpa"

_listening = {"dot": False, "doq": False}


def listening(dot: bool, doq: bool) -> None:
    _listening["dot"] = bool(dot)
    _listening["doq"] = bool(doq)


def advertised() -> dict[str, bool]:
    return dict(_listening)


def _api_host() -> tuple[str, int]:
    split = urlsplit(settings.public_api_base or "")
    host = split.hostname or ""
    port = split.port or (443 if split.scheme == "https" else 0)
    return host, port


def _dot_host(token: str) -> str:
    base = settings.dot_base_domain
    return f"{token}.{base}" if base and token else ""


def records_for(device: str) -> list[str]:
    if not settings.designated:
        return []

    token = device_token(device) if device else ""
    found: list[str] = []
    priority = 1

    host, port = _api_host()
    if host and port and token:
        path = f"/dns-query/{token}{{?dns}}"
        found.append(f'{priority} {host}. alpn="h2" port={port} dohpath="{path}"')
        priority += 1

    dot_host = _dot_host(token)
    if dot_host and _listening["dot"]:
        found.append(f'{priority} {dot_host}. alpn="dot" port={settings.dot_port}')
        priority += 1

    if dot_host and _listening["doq"]:
        found.append(f'{priority} {dot_host}. alpn="doq" port={settings.doq_port}')

    return found
