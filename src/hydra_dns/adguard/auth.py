from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import httpx

log = logging.getLogger(__name__)

TOKEN_URL = "https://api.adguard-dns.io/oapi/v1/oauth_token"

REFRESH_MARGIN_S = 300
MIN_RETRY_S = 10.0
MAX_RETRY_S = 1800.0


class AuthError(RuntimeError):
    pass


class TokenManager:
    def __init__(self, label: str, username: str, password: str, client: httpx.AsyncClient) -> None:
        self.label = label
        self._username = username
        self._password = password
        self._client = client

        self._access_token = ""
        self._refresh_token = ""
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

        self.last_error = ""
        self.last_success_at = 0.0
        self.consecutive_failures = 0
        self._retry_after = 0.0

    @property
    def authenticated(self) -> bool:
        return bool(self._access_token) and time.monotonic() < self._expires_at

    @property
    def status(self) -> str:
        if self.authenticated:
            return "ok"
        if self.last_error:
            return "error"
        return "pending"

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "authenticated": self.authenticated,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at or None,
            "consecutive_failures": self.consecutive_failures,
        }

    async def token(self) -> str:
        if self.authenticated:
            return self._access_token

        async with self._lock:
            if self.authenticated:
                return self._access_token

            now = time.monotonic()
            if now < self._retry_after:
                raise AuthError(
                    f"not retrying authentication for another "
                    f"{int(self._retry_after - now)}s ({self.last_error})"
                )

            if self._refresh_token:
                try:
                    await self._grant(
                        {"grant_type": "refresh_token", "refresh_token": self._refresh_token}
                    )
                    return self._access_token
                except AuthError as exc:
                    log.warning(
                        "%s: refresh failed (%s), falling back to password", self.label, exc
                    )
                    self._refresh_token = ""

            await self._grant(
                {
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._password,
                }
            )
            return self._access_token

    def invalidate(self) -> None:
        self._access_token = ""
        self._expires_at = 0.0
        self._retry_after = 0.0

    async def _grant(self, data: dict[str, str]) -> None:
        try:
            response = await self._client.post(
                TOKEN_URL,
                data=data,
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            self._note_failure(f"network error contacting AdGuard: {exc}")
            raise AuthError(str(exc)) from exc

        if response.status_code != 200:
            detail = _describe(response)
            self._note_failure(detail)
            raise AuthError(detail)

        try:
            payload = response.json()
        except ValueError as exc:
            self._note_failure("AdGuard returned a non-JSON token response")
            raise AuthError(str(exc)) from exc

        access = payload.get("access_token", "")
        if not access:
            self._note_failure("token response contained no access_token")
            raise AuthError("no access_token in response")

        self._access_token = access
        self._refresh_token = payload.get("refresh_token", self._refresh_token)
        lifetime = int(payload.get("expires_in", 3600))
        self._expires_at = time.monotonic() + max(60, lifetime - REFRESH_MARGIN_S)

        self.last_error = ""
        self.last_success_at = time.time()
        self.consecutive_failures = 0
        self._retry_after = 0.0
        log.info("%s: authenticated, token valid for %ss", self.label, lifetime)

    def _note_failure(self, detail: str) -> None:
        self.consecutive_failures += 1
        self.last_error = detail
        backoff = min(MIN_RETRY_S * (2 ** (self.consecutive_failures - 1)), MAX_RETRY_S)
        self._retry_after = time.monotonic() + backoff
        log.warning(
            "%s: authentication failed (%s); next attempt in %ss",
            self.label,
            detail,
            int(backoff),
        )

    async def revoke(self) -> None:
        if not self._refresh_token:
            return
        with contextlib.suppress(httpx.HTTPError):
            await self._client.post(
                "https://api.adguard-dns.io/oapi/v1/revoke_token",
                data={"refresh_token": self._refresh_token},
                timeout=5.0,
            )


def _describe(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"

    code = body.get("error_code") or body.get("error") or ""
    message = body.get("error_description") or body.get("message") or ""

    hints = {
        "2fa_required": "this account has two-factor auth enabled, which the password grant "
        "cannot complete unattended -- disable 2FA for it or supply a refresh token",
        "bad_credentials": "wrong username or password",
        "account_disabled": "AdGuard has disabled this account",
    }
    hint = hints.get(str(code), "")
    parts = [p for p in (f"HTTP {response.status_code}", str(code), message, hint) if p]
    return " / ".join(parts)
