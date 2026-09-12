from __future__ import annotations

import datetime as dt
import logging
import os
import re
import ssl
import time

log = logging.getLogger(__name__)

PEM_BLOCK = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.DOTALL)
SEQUENCE = 0x30
INTEGER = 0x02
CONTEXT_0 = 0xA0
UTC_TIME = 0x17
GENERAL_TIME = 0x18

_CACHE: dict[str, tuple[float, tuple[float, int], float | None]] = {}
_TTL = 3600.0


def stamp(path: str) -> tuple[float, int]:
    if not path:
        return (0.0, 0)
    try:
        info = os.stat(path)
    except OSError:
        return (0.0, 0)
    return (info.st_mtime, info.st_size)


def expires_at(cert_file: str) -> float | None:
    if not cert_file:
        return None

    now = time.monotonic()
    current = stamp(cert_file)
    cached = _CACHE.get(cert_file)
    if cached and cached[1] == current and now - cached[0] < _TTL:
        return cached[2]

    found = _read(cert_file)
    _CACHE[cert_file] = (now, current, found)
    return found


def _read(cert_file: str) -> float | None:
    try:
        with open(cert_file, encoding="ascii", errors="ignore") as handle:
            text = handle.read()
    except OSError as exc:
        log.warning("could not read the certificate at %s: %s", cert_file, exc)
        return None

    moments: list[float] = []
    for block in PEM_BLOCK.findall(text):
        try:
            when = _not_after(ssl.PEM_cert_to_DER_cert(block))
        except (ValueError, IndexError):
            continue
        if when is not None:
            moments.append(when)

    if not moments:
        log.warning("no certificate validity could be read from %s", cert_file)
        return None

    return min(moments)


def _tlv(data: bytes, at: int) -> tuple[int, bytes, int]:
    tag = data[at]
    length = data[at + 1]
    at += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[at : at + count], "big")
        at += count
    return tag, data[at : at + length], at + length


def _not_after(der: bytes) -> float | None:
    _, certificate, _ = _tlv(der, 0)
    _, tbs, _ = _tlv(certificate, 0)

    at = 0
    tag, body, at = _tlv(tbs, at)
    if tag == CONTEXT_0:
        tag, body, at = _tlv(tbs, at)
    if tag != INTEGER:
        return None

    for _ in range(3):
        tag, body, at = _tlv(tbs, at)
        if tag != SEQUENCE:
            return None
        if _looks_like_validity(body):
            _, _, after = _tlv(body, 0)
            tag, value, _ = _tlv(body, after)
            return _parse(tag, value)

    return None


def _looks_like_validity(body: bytes) -> bool:
    return len(body) > 2 and body[0] in (UTC_TIME, GENERAL_TIME)


def _parse(tag: int, value: bytes) -> float | None:
    text = value.decode("ascii", "ignore")
    shape = "%y%m%d%H%M%SZ" if tag == UTC_TIME else "%Y%m%d%H%M%SZ"
    try:
        moment = dt.datetime.strptime(text, shape)
    except ValueError:
        return None
    return moment.replace(tzinfo=dt.UTC).timestamp()


def snapshot(cert_file: str) -> dict:
    at = expires_at(cert_file)
    if at is None:
        return {"known": False, "expires_at": None, "days_left": None}
    return {
        "known": True,
        "expires_at": at,
        "days_left": round((at - time.time()) / 86400, 1),
    }
