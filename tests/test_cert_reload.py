from __future__ import annotations

import dataclasses
import ssl
from pathlib import Path

import pytest
from test_dot import BASE_DOMAIN, write_wildcard_cert

from hydra_dns import certs
from hydra_dns.certwatch import CertificateWatcher


@pytest.fixture
def paths(tmp_path):
    return write_wildcard_cert(tmp_path)


@pytest.fixture
def watcher(monkeypatch, paths):
    from hydra_dns import certwatch as module

    cert_file, key_file = paths
    tweaked = dataclasses.replace(
        module.settings,
        dot_cert_file=cert_file,
        dot_key_file=key_file,
        dot_cert_poll_s=300,
    )
    monkeypatch.setattr(module, "settings", tweaked)
    return CertificateWatcher()


def copy_over(source: str, target: str) -> None:
    Path(target).write_bytes(Path(source).read_bytes())


def context_for(cert_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert_file, key_file)
    return context


def test_nothing_is_reloaded_while_the_certificate_is_unchanged(watcher, paths) -> None:
    watcher.register("dot", context_for(*paths))
    assert watcher.reload() == []
    assert watcher.reloads == 0


def test_a_replaced_certificate_is_picked_up(watcher, paths, tmp_path) -> None:
    watcher.register("dot", context_for(*paths))

    renewed = tmp_path / "renewed"
    renewed.mkdir(exist_ok=True)
    fresh_cert, fresh_key = write_wildcard_cert(renewed)
    cert_file, key_file = paths
    copy_over(fresh_cert, cert_file)
    copy_over(fresh_key, key_file)

    assert watcher.reload() == ["dot"]
    assert watcher.reloads == 1
    assert watcher.last_reload_at > 0


def test_every_listener_is_reloaded_together(watcher, paths, tmp_path) -> None:
    watcher.register("https", context_for(*paths))
    watcher.register("dot", context_for(*paths))

    renewed = tmp_path / "renewed"
    renewed.mkdir(exist_ok=True)
    fresh_cert, fresh_key = write_wildcard_cert(renewed)
    cert_file, key_file = paths
    copy_over(fresh_cert, cert_file)
    copy_over(fresh_key, key_file)

    assert watcher.reload() == ["https", "dot"]


def test_a_forced_reload_does_not_wait_for_a_change(watcher, paths) -> None:
    watcher.register("dot", context_for(*paths))
    assert watcher.reload(force=True) == ["dot"]


def test_a_broken_certificate_leaves_the_old_one_serving(watcher, paths) -> None:
    context = context_for(*paths)
    watcher.register("dot", context)

    cert_file, _ = paths
    Path(cert_file).write_text("not a certificate", encoding="utf-8")

    assert watcher.reload() == []
    assert watcher.failures == 1
    assert "dot" in watcher.last_error
    assert context.get_ca_certs() is not None


def test_a_nudge_is_remembered_until_the_loop_reads_it(watcher, paths) -> None:
    watcher.register("dot", context_for(*paths))
    assert watcher._nudged is False
    watcher.nudge()
    assert watcher._nudged is True


def test_the_snapshot_names_what_is_being_watched(watcher, paths) -> None:
    watcher.register("https", context_for(*paths))
    watcher.register("dot", context_for(*paths))

    snapshot = watcher.snapshot()
    assert snapshot["watching"] is True
    assert snapshot["listeners"] == ["https", "dot"]
    assert snapshot["reloads"] == 0
    assert snapshot["poll_s"] == 300


def test_an_unwatched_process_says_so() -> None:
    assert CertificateWatcher().snapshot()["watching"] is False


def test_the_expiry_cache_notices_a_replaced_file(paths, tmp_path) -> None:
    cert_file, key_file = paths
    certs._CACHE.clear()

    first = certs.expires_at(cert_file)
    assert first is not None
    assert certs.expires_at(cert_file) == first

    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"*.{BASE_DOMAIN}")])
    now = datetime.datetime.now(datetime.UTC)
    longer = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=60))
        .sign(key, hashes.SHA256())
    )
    Path(cert_file).write_bytes(longer.public_bytes(serialization.Encoding.PEM))

    assert certs.expires_at(cert_file) != first


def test_a_missing_file_stamps_as_nothing(tmp_path) -> None:
    assert certs.stamp(str(tmp_path / "gone.pem")) == (0.0, 0)
    assert certs.stamp("") == (0.0, 0)
