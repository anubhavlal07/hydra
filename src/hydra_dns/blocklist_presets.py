from __future__ import annotations

from dataclasses import dataclass

HAGEZI = "https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/wildcard"


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    url: str
    format: str
    names: int
    bytes: int


PRESETS: tuple[Preset, ...] = (
    Preset(
        "hagezi-light",
        "HaGeZi Light",
        f"{HAGEZI}/light-onlydomains.txt",
        "domains",
        44_936,
        884_019,
    ),
    Preset(
        "hagezi-multi",
        "HaGeZi Multi Normal",
        f"{HAGEZI}/multi-onlydomains.txt",
        "domains",
        164_218,
        3_338_915,
    ),
    Preset(
        "hagezi-pro", "HaGeZi Pro", f"{HAGEZI}/pro-onlydomains.txt", "domains", 228_217, 4_365_473
    ),
    Preset(
        "hagezi-pro-plus",
        "HaGeZi Pro++",
        f"{HAGEZI}/pro.plus-onlydomains.txt",
        "domains",
        249_395,
        4_796_539,
    ),
    Preset(
        "hagezi-tif-mini",
        "HaGeZi TIF mini",
        f"{HAGEZI}/tif.mini-onlydomains.txt",
        "domains",
        200_366,
        3_653_830,
    ),
    Preset(
        "hagezi-tif",
        "HaGeZi TIF",
        f"{HAGEZI}/tif-onlydomains.txt",
        "domains",
        2_291_474,
        39_838_576,
    ),
    Preset(
        "oisd-small",
        "OISD small",
        "https://small.oisd.nl/domainswild2",
        "domains",
        56_387,
        1_096_593,
    ),
    Preset(
        "oisd-big", "OISD big", "https://big.oisd.nl/domainswild2", "domains", 243_946, 4_908_501
    ),
)


def _unavailable(
    preset: Preset,
    *,
    subscribed: bool,
    headroom: int,
    max_bytes: int,
    full: bool,
) -> str:
    if subscribed:
        return "already added"
    if preset.bytes > max_bytes:
        return f"{preset.bytes / 1_048_576:.0f} MiB, over HYDRA_BLOCKLIST_MAX_BYTES"
    if preset.names > headroom:
        return f"only {max(0, headroom):,} names left"
    if full:
        return "no list slot left"
    return ""


def catalog(
    *, held_urls: set[str], total_names: int, max_names: int, max_bytes: int, full: bool
) -> list[dict]:
    headroom = max_names - total_names
    found: list[dict] = []
    for preset in PRESETS:
        subscribed = preset.url in held_urls
        reason = _unavailable(
            preset, subscribed=subscribed, headroom=headroom, max_bytes=max_bytes, full=full
        )
        found.append(
            {
                "id": preset.id,
                "name": preset.name,
                "url": preset.url,
                "format": preset.format,
                "names": preset.names,
                "bytes": preset.bytes,
                "subscribed": subscribed,
                "fits": preset.names <= headroom and preset.bytes <= max_bytes,
                "unavailable": reason,
            }
        )
    return found
