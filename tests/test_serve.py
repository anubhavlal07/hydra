from __future__ import annotations

import dataclasses

import pytest

from hydra_dns import serve


@pytest.fixture
def at(monkeypatch):
    def apply(**changes):
        monkeypatch.setattr(serve, "settings", dataclasses.replace(serve.settings, **changes))

    return apply


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("INFO", "info"),
        ("debug", "debug"),
        ("WARN", "warning"),
        ("fatal", "critical"),
        ("nonsense", "info"),
        ("", "info"),
    ],
)
def test_the_log_level_is_one_uvicorn_accepts(at, configured, expected) -> None:
    at(log_level=configured)
    assert serve.log_level() == expected


def test_the_listener_takes_its_port_from_the_settings(at) -> None:
    at(http_port=8443, http_cert_file="", http_key_file="")
    config = serve.build_config()

    assert config.port == 8443
    assert config.ssl is None
    assert config.server_header is False
    assert config.proxy_headers is True


def test_https_is_served_when_a_certificate_is_configured(at, tmp_path) -> None:
    from test_dot import write_wildcard_cert

    cert_file, key_file = write_wildcard_cert(tmp_path)
    at(http_cert_file=cert_file, http_key_file=key_file, http_port=8443)

    config = serve.build_config()
    assert config.ssl is not None


def test_a_hangup_asks_the_watcher_for_a_reload() -> None:
    from hydra_dns.certwatch import watcher

    watcher._nudged = False
    serve._on_hangup(1, None)
    assert watcher._nudged is True
    watcher._nudged = False
