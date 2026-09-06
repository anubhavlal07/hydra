from __future__ import annotations

import hmac
import logging
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel

from ..config import settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

COOKIE_NAME = "hydra_session"
_SESSION_VALUE = "dashboard"

MAX_ATTEMPTS = 6
LOCKOUT_S = 900.0
GLOBAL_MAX_ATTEMPTS = 40
GLOBAL_WINDOW_S = 900.0


class LoginLimiter:
    def __init__(self) -> None:
        self._per_ip: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()

    @staticmethod
    def _prune(stamps: deque[float], window: float, now: float) -> None:
        while stamps and now - stamps[0] > window:
            stamps.popleft()

    def retry_after(self, client: str) -> int:
        now = time.monotonic()
        stamps = self._per_ip[client]
        self._prune(stamps, LOCKOUT_S, now)
        if len(stamps) >= MAX_ATTEMPTS:
            return max(1, int(LOCKOUT_S - (now - stamps[0])))

        self._prune(self._global, GLOBAL_WINDOW_S, now)
        if len(self._global) >= GLOBAL_MAX_ATTEMPTS:
            return max(1, int(GLOBAL_WINDOW_S - (now - self._global[0])))
        return 0

    def record_failure(self, client: str) -> None:
        now = time.monotonic()
        self._per_ip[client].append(now)
        self._global.append(now)
        if len(self._per_ip) > 4096:
            for key in [k for k, v in self._per_ip.items() if not v]:
                del self._per_ip[key]

    def record_success(self, client: str) -> None:
        self._per_ip.pop(client, None)

    def recent_failures(self, window: float = LOCKOUT_S) -> int:
        now = time.monotonic()
        self._prune(self._global, window, now)
        return len(self._global)


_limiter = LoginLimiter()


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt="hydra-dashboard")


class LoginBody(BaseModel):
    password: str


def require_session(request: Request) -> str:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "sign in"})
    try:
        _serializer().loads(raw, max_age=settings.session_ttl_s)
    except SignatureExpired as exc:
        raise HTTPException(
            status_code=401, detail={"code": "expired", "message": "session expired"}
        ) from exc
    except BadSignature as exc:
        raise HTTPException(
            status_code=401, detail={"code": "unauthorized", "message": "sign in"}
        ) from exc
    return _SESSION_VALUE


SessionDep = Depends(require_session)


def _cookie_kwargs() -> dict:
    kwargs = {
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "path": "/",
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs


@router.post("/api/login")
async def login(request: Request, body: LoginBody, response: Response) -> dict:
    from ..resolver.doh import client_address

    client = client_address(request)

    wait = _limiter.retry_after(client)
    if wait:
        log.warning("sign-in from %s is locked out for another %ss", client, wait)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "too_many_attempts",
                "message": f"Too many failed sign-ins. Try again in {wait // 60 + 1} minute(s).",
            },
            headers={"retry-after": str(wait)},
        )

    if not hmac.compare_digest(body.password, settings.dashboard_password):
        _limiter.record_failure(client)
        log.warning("failed sign-in from %s", client)
        raise HTTPException(
            status_code=401, detail={"code": "bad_password", "message": "wrong password"}
        )

    _limiter.record_success(client)
    response.set_cookie(
        COOKIE_NAME,
        _serializer().dumps(_SESSION_VALUE),
        max_age=settings.session_ttl_s,
        **_cookie_kwargs(),
    )
    return {"ok": True}


@router.post("/api/logout")
async def logout(response: Response) -> dict:
    kwargs = _cookie_kwargs()
    response.delete_cookie(
        COOKIE_NAME, path=kwargs["path"], domain=kwargs.get("domain"), samesite="lax", secure=True
    )
    return {"ok": True}


@router.get("/api/session")
async def session_state(request: Request) -> dict:
    try:
        require_session(request)
    except HTTPException:
        return {"authenticated": False}
    return {"authenticated": True}
