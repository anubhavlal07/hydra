from __future__ import annotations

import contextlib
import logging
import signal

import uvicorn

from .certwatch import watcher
from .config import settings

log = logging.getLogger("hydra.serve")

LOG_LEVELS = {"critical", "debug", "error", "info", "trace", "warning"}
LOG_ALIASES = {"warn": "warning", "fatal": "critical", "notset": "info"}


def log_level() -> str:
    wanted = settings.log_level.strip().lower()
    wanted = LOG_ALIASES.get(wanted, wanted)
    return wanted if wanted in LOG_LEVELS else "info"


def build_config() -> uvicorn.Config:
    options: dict = {
        "app": "hydra_dns.main:app",
        "host": settings.http_host,
        "port": settings.http_port,
        "timeout_keep_alive": settings.http_keep_alive_s,
        "server_header": False,
        "proxy_headers": True,
        "log_level": log_level(),
    }
    if settings.http_cert_file and settings.http_key_file:
        options["ssl_certfile"] = settings.http_cert_file
        options["ssl_keyfile"] = settings.http_key_file

    config = uvicorn.Config(**options)
    config.load()
    return config


def _on_hangup(_signum, _frame) -> None:
    watcher.nudge()


def main() -> None:
    config = build_config()
    if config.ssl is not None:
        watcher.register("https", config.ssl, settings.http_cert_file, settings.http_key_file)
        log.info("HTTPS is serving %s and reloads it in place", settings.http_cert_file)

    with contextlib.suppress(AttributeError, ValueError, OSError):
        signal.signal(signal.SIGHUP, _on_hangup)

    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
