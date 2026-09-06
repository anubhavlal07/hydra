from __future__ import annotations

import logging
import time
from pathlib import Path

from .config import settings

log = logging.getLogger(__name__)

RUNNING = "running"
CLEAN = "clean"
MARKER_NAME = "running.marker"


def marker_path() -> Path | None:
    for candidate in (settings.cache_file, settings.device_cache_file, settings.accounts_file):
        if candidate:
            return Path(candidate).parent / MARKER_NAME
    return None


def claim() -> float | None:
    path = marker_path()
    if path is None:
        return None

    previous = None
    try:
        if path.is_file():
            state, _, stamp = path.read_text(encoding="utf-8").partition(" ")
            if state == RUNNING:
                previous = float(stamp or 0)
    except (OSError, ValueError) as exc:
        log.warning("could not read the runtime marker at %s: %s", path, exc)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{RUNNING} {time.time():.0f}", encoding="utf-8")
    except OSError as exc:
        log.warning("could not write the runtime marker at %s: %s", path, exc)

    return previous


def release() -> None:
    path = marker_path()
    if path is None:
        return
    try:
        path.write_text(f"{CLEAN} {time.time():.0f}", encoding="utf-8")
    except OSError as exc:
        log.warning("could not clear the runtime marker at %s: %s", path, exc)


def describe_gap(started_at: float | None, now: float | None = None) -> str:
    if not started_at:
        return "an unknown time"
    seconds = max(0.0, (now if now is not None else time.time()) - started_at)
    if seconds < 3600:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 86_400:
        return f"{seconds / 3600:.0f} hours"
    return f"{seconds / 86_400:.1f} days"
