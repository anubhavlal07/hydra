from __future__ import annotations

import base64
import json
import os

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from hydra_dns import webpush


def browser_subscription() -> tuple[ec.EllipticCurvePrivateKey, bytes, bytes]:
    private = ec.generate_private_key(webpush.CURVE)
    public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return private, public, os.urandom(16)


def decrypt_as_browser(body: bytes, private: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    salt = body[:16]
    key_length = body[20]
    server_public_raw = body[21 : 21 + key_length]
    sealed = body[21 + key_length :]

    server_public = ec.EllipticCurvePublicKey.from_encoded_point(webpush.CURVE, server_public_raw)
    shared = private.exchange(ec.ECDH(), server_public)
    client_public = private.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    content_key, nonce = webpush._derive(shared, auth, client_public, server_public_raw, salt)
    return AESGCM(content_key).decrypt(nonce, sealed, None)


def test_base64url_round_trips_without_padding() -> None:
    raw = os.urandom(65)
    encoded = webpush.b64url(raw)
    assert "=" not in encoded
    assert webpush.unb64url(encoded) == raw


def test_generated_keys_are_a_usable_p256_pair() -> None:
    public, private = webpush.generate_keys()
    assert len(webpush.unb64url(public)) == 65
    assert len(webpush.unb64url(private)) == 32
    assert webpush.unb64url(public)[0] == 0x04

    loaded = webpush.load_private(private)
    derived = loaded.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert webpush.b64url(derived) == public


def test_a_browser_can_decrypt_what_we_send() -> None:
    private, public, auth = browser_subscription()
    plaintext = b'{"title":"Hydra DNS","body":"alpha has spent its monthly allowance"}'

    body = webpush.encrypt(plaintext, webpush.b64url(public), webpush.b64url(auth))
    recovered = decrypt_as_browser(body, private, auth)

    assert recovered[-1:] == b"\x02"
    assert recovered[:-1] == plaintext


def test_the_body_carries_the_header_the_spec_requires() -> None:
    private, public, auth = browser_subscription()
    body = webpush.encrypt(b"x", webpush.b64url(public), webpush.b64url(auth))

    assert len(body[:16]) == 16
    assert int.from_bytes(body[16:20], "big") == webpush.RECORD_SIZE
    assert body[20] == 65
    assert body[21] == 0x04


def test_two_messages_never_reuse_a_salt() -> None:
    _, public, auth = browser_subscription()
    salts = {
        webpush.encrypt(b"x", webpush.b64url(public), webpush.b64url(auth))[:16] for _ in range(20)
    }
    assert len(salts) == 20


def test_a_different_subscription_cannot_read_it() -> None:
    _, public, auth = browser_subscription()
    other_private, _, other_auth = browser_subscription()

    body = webpush.encrypt(b"secret", webpush.b64url(public), webpush.b64url(auth))
    with pytest.raises(InvalidTag):
        decrypt_as_browser(body, other_private, other_auth)


def test_the_vapid_header_is_a_signed_jwt_for_that_origin() -> None:
    public, private = webpush.generate_keys()
    headers = webpush.vapid_headers(
        "https://fcm.googleapis.com/fcm/send/abc123", "mailto:a@b.test", public, private
    )

    assert headers["authorization"].startswith("vapid t=")
    assert f"k={public}" in headers["authorization"]

    token = headers["authorization"].split("t=")[1].split(",")[0]
    header_b64, claims_b64, signature_b64 = token.split(".")

    claims = json.loads(webpush.unb64url(claims_b64))
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == "mailto:a@b.test"
    assert claims["exp"] > 0

    assert json.loads(webpush.unb64url(header_b64))["alg"] == "ES256"
    assert len(webpush.unb64url(signature_b64)) == 64


def test_the_jwt_signature_verifies_against_the_public_key() -> None:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import utils

    public, private = webpush.generate_keys()
    headers = webpush.vapid_headers(
        "https://updates.push.services.mozilla.com/wpush/v2/xyz", "mailto:a@b.test", public, private
    )
    token = headers["authorization"].split("t=")[1].split(",")[0]
    header_b64, claims_b64, signature_b64 = token.split(".")

    raw = webpush.unb64url(signature_b64)
    der = utils.encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    key = ec.EllipticCurvePublicKey.from_encoded_point(webpush.CURVE, webpush.unb64url(public))
    key.verify(der, f"{header_b64}.{claims_b64}".encode(), ec.ECDSA(hashes.SHA256()))


def test_a_request_is_shaped_the_way_a_push_service_expects() -> None:
    public, private = webpush.generate_keys()
    _, ua_public, auth = browser_subscription()
    subscription = webpush.Subscription(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        p256dh=webpush.b64url(ua_public),
        auth=webpush.b64url(auth),
    )

    url, body, headers = webpush.request_for(
        subscription, {"title": "x"}, "mailto:a@b.test", public, private
    )

    assert url == subscription.endpoint
    assert headers["content-encoding"] == "aes128gcm"
    assert headers["content-type"] == "application/octet-stream"
    assert headers["ttl"] == "3600"
    assert body[:16] != b"\x00" * 16


def test_the_payload_is_never_sent_in_the_clear() -> None:
    public, private = webpush.generate_keys()
    _, ua_public, auth = browser_subscription()
    subscription = webpush.Subscription(
        endpoint="https://fcm.googleapis.com/fcm/send/abc",
        p256dh=webpush.b64url(ua_public),
        auth=webpush.b64url(auth),
    )

    _, body, _ = webpush.request_for(
        subscription,
        {"body": "alpha has spent its monthly allowance"},
        "mailto:a@b.test",
        public,
        private,
    )
    assert b"alpha" not in body
    assert b"allowance" not in body


def test_an_endpoint_origin_is_read_for_the_audience() -> None:
    subscription = webpush.Subscription(
        endpoint="https://fcm.googleapis.com/fcm/send/abc?x=1", p256dh="a", auth="b"
    )
    assert subscription.origin == "https://fcm.googleapis.com"


def test_base64url_accepts_the_padding_browsers_omit() -> None:
    raw = b"\x01\x02\x03"
    assert webpush.unb64url(base64.urlsafe_b64encode(raw).decode()) == raw
    assert webpush.unb64url(base64.urlsafe_b64encode(raw).decode().rstrip("=")) == raw
