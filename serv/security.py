from __future__ import annotations

import logging
import re
from typing import Callable

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from serv.settings import COOKIE_MAX_AGE, COOKIE_NAME, cookie_secure

logger = logging.getLogger(__name__)

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{32,96}$")

GENERIC_AUTH_ERROR = "Не удалось выполнить авторизацию. Попробуй позже."
GENERIC_SERVICE_ERROR = "Сервис недоступен. Проверь настройки и попробуй снова."

_SAFE_PREFIXES = ("Не хватает настроек", "Пользователь не найден")


def is_valid_session_id(value: str) -> bool:
    return bool(value and SESSION_ID_RE.fullmatch(value))


def client_ip(request: Request) -> str:
    from serv.settings import trusted_proxy_ips

    peer = request.client.host if request.client else ""
    if peer in trusted_proxy_ips():
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
    return peer or "unknown"


def public_service_error(message: str) -> str:
    if not message:
        return ""
    if any(message.startswith(prefix) for prefix in _SAFE_PREFIXES):
        return message
    return GENERIC_SERVICE_ERROR


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=user_id,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=cookie_secure(),
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=cookie_secure(),
        samesite="strict",
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
        if cookie_secure():
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


def install_security_middleware(app) -> None:
    from serv.settings import is_production, trusted_hosts

    hosts = trusted_hosts()
    if is_production() and hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(SecurityHeadersMiddleware)


async def require_csrf(security_store, request: Request, user_id: str) -> None:
    token = request.headers.get("X-CSRF-Token")
    if not await security_store.verify_csrf(user_id, token):
        raise HTTPException(status_code=403, detail="Недействительный CSRF-токен")


async def require_fresh_session(security_store, user_id: str) -> None:
    await security_store.require_fresh_session(user_id)


async def check_auth_not_locked(security_store, user_id: str, request: Request) -> None:
    keys = (f"user:{user_id}", f"ip:{client_ip(request)}")
    for key in keys:
        if await security_store.is_auth_locked(key):
            raise HTTPException(
                status_code=429,
                detail="Слишком много неудачных попыток. Подожди 15 минут.",
            )
