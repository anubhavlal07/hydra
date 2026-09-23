from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand

CURVE = ec.SECP256R1()
RECORD_SIZE = 4096
JWT_TTL_S = 12 * 3600


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def generate_keys() -> tuple[str, str]:
    private = ec.generate_private_key(CURVE)
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    secret = private.private_numbers().private_value.to_bytes(32, "big")
    return b64url(public), b64url(secret)


def load_private(value: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64url(value), "big"), CURVE)


def _raw_signature(der: bytes) -> bytes:
    r, s = utils.decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def vapid_headers(endpoint: str, subject: str, public_key: str, private_key: str) -> dict[str, str]:
    origin = urlparse(endpoint)
    claims = {
        "aud": f"{origin.scheme}://{origin.netloc}",
        "exp": int(time.time()) + JWT_TTL_S,
        "sub": subject,
    }

    header = b64url(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()

    signature = load_private(private_key).sign(signing_input, ec.ECDSA(hashes.SHA256()))
    token = f"{header}.{body}.{b64url(_raw_signature(signature))}"
    return {"authorization": f"vapid t={token}, k={public_key}"}


def _derive(shared: bytes, auth: bytes, client_public: bytes, server_public: bytes, salt: bytes):
    key_info = b"WebPush: info\x00" + client_public + server_public
    ikm = HKDFExpand(algorithm=hashes.SHA256(), length=32, info=key_info).derive(
        _extract(auth, shared)
    )

    prk = _extract(salt, ikm)
    content = HKDFExpand(
        algorithm=hashes.SHA256(), length=16, info=b"Content-Encoding: aes128gcm\x00"
    ).derive(prk)
    nonce = HKDFExpand(
        algorithm=hashes.SHA256(), length=12, info=b"Content-Encoding: nonce\x00"
    ).derive(prk)
    return content, nonce


def _extract(salt: bytes, ikm: bytes) -> bytes:
    import hmac

    return hmac.new(salt, ikm, hashes.SHA256.name).digest()


def encrypt(payload: bytes, client_public_b64: str, auth_b64: str, salt: bytes | None = None):
    client_public_raw = unb64url(client_public_b64)
    client_public = ec.EllipticCurvePublicKey.from_encoded_point(CURVE, client_public_raw)
    auth = unb64url(auth_b64)

    server_private = ec.generate_private_key(CURVE)
    server_public = server_private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = server_private.exchange(ec.ECDH(), client_public)

    salt = salt or os.urandom(16)
    content_key, nonce = _derive(shared, auth, client_public_raw, server_public, salt)

    sealed = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)
    header = salt + RECORD_SIZE.to_bytes(4, "big") + len(server_public).to_bytes(1, "big")
    return header + server_public + sealed


@dataclass(frozen=True)
class Subscription:
    endpoint: str
    p256dh: str
    auth: str

    @property
    def origin(self) -> str:
        parsed = urlparse(self.endpoint)
        return f"{parsed.scheme}://{parsed.netloc}"


def request_for(
    subscription: Subscription,
    payload: dict,
    subject: str,
    public_key: str,
    private_key: str,
    ttl: int = 3600,
    urgency: str = "normal",
) -> tuple[str, bytes, dict[str, str]]:
    body = encrypt(json.dumps(payload).encode(), subscription.p256dh, subscription.auth)
    headers = {
        **vapid_headers(subscription.endpoint, subject, public_key, private_key),
        "content-encoding": "aes128gcm",
        "content-type": "application/octet-stream",
        "ttl": str(ttl),
        "urgency": urgency,
    }
    return subscription.endpoint, body, headers
