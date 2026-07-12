from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from serv.crypto import require_encryption_if_needed
from serv.manager import BridgeManager
from serv.security import (
    check_auth_not_locked,
    clear_session_cookie,
    client_ip,
    install_security_middleware,
    is_valid_session_id,
    public_service_error,
    require_fresh_session,
    require_csrf,
    set_session_cookie,
)
from serv.security_store import SecurityStore
from serv.settings import COOKIE_NAME, cookie_secure, default_vk_token, is_production
from serv.tg_auth import TelegramAuth
from serv.user_store import UserStore
from serv.vk_session_guard import VkSessionGuard

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

user_store = UserStore()
security_store = SecurityStore()
tg_auth = TelegramAuth(user_store)
bridge_manager = BridgeManager(user_store)
vk_session_guard = VkSessionGuard(user_store, bridge_manager)

PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
CODE_RE = re.compile(r"^[0-9]{4,8}$")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    require_encryption_if_needed()
    await user_store.init()
    await security_store.init()
    removed = await security_store.cleanup_expired_pending()
    if removed:
        logger.info("Удалено просроченных pending_auth: %s", removed)
    if is_production():
        logger.info("Production mode: secure cookies=%s", cookie_secure())
    await vk_session_guard.start()
    yield
    await vk_session_guard.stop()
    await bridge_manager.stop_all()


app = FastAPI(title="tgvk", lifespan=lifespan, docs_url=None, redoc_url=None)
install_security_middleware(app)


class PhoneBody(BaseModel):
    phone: str = Field(min_length=10, max_length=20)

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        normalized = "".join(value.split())
        if not PHONE_RE.fullmatch(normalized):
            raise ValueError("Некорректный номер телефона")
        return normalized


class CodeBody(BaseModel):
    code: str = Field(min_length=4, max_length=8)

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        cleaned = value.strip().replace(" ", "")
        if not CODE_RE.fullmatch(cleaned):
            raise ValueError("Некорректный код")
        return cleaned


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class SettingsBody(BaseModel):
    vk_peer_id: int | None = Field(default=None, ge=1, le=2_147_483_647)
    vk_token: str | None = Field(default=None, max_length=512)
    use_default_vk_token: bool | None = None
    forwarding: bool | None = None
    img_mode: bool | None = None

    @field_validator("vk_token")
    @classmethod
    def strip_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def _resolve_user_id(request: Request, response: Response) -> str:
    raw = request.cookies.get(COOKIE_NAME)
    if raw and is_valid_session_id(raw):
        return raw

    user_id = UserStore.new_user_id()
    set_session_cookie(response, user_id)
    return user_id


async def _rotate_session(old_id: str, response: Response) -> str:
    new_id = UserStore.new_user_id()
    await user_store.reassign_user_id(old_id, new_id)
    await security_store.migrate_csrf(old_id, new_id)
    await security_store.migrate_session_activity(old_id, new_id)
    set_session_cookie(response, new_id)
    return new_id


async def _new_anonymous_session(response: Response) -> str:
    user_id = UserStore.new_user_id()
    set_session_cookie(response, user_id)
    await user_store.ensure_user(user_id)
    await security_store.issue_csrf(user_id)
    await security_store.touch_session(user_id)
    return user_id


async def _user_response(user_id: str, response: Response | None = None) -> dict[str, Any]:
    user = await user_store.ensure_user(user_id)
    pending = await user_store.get_pending(user_id)
    data = user.public_dict(
        service_running=bridge_manager.is_running(user_id),
        default_vk_token=default_vk_token(),
    )
    data["csrf_token"] = await security_store.get_or_issue_csrf(user_id)
    data["service_error"] = public_service_error(bridge_manager.get_error(user_id))
    if user.telegram_linked:
        data["auth_step"] = "done"
    elif pending and pending.needs_password:
        data["auth_step"] = "password"
    elif pending and pending.auth_mode == "qr":
        data["auth_step"] = "qr"
    elif pending:
        data["auth_step"] = "code"
    else:
        data["auth_step"] = "phone"
    return data


@app.get("/api/me")
async def api_me(request: Request, response: Response) -> dict[str, Any]:
    await security_store.rate_limit_check(f"me:ip:{client_ip(request)}", 60, 60)
    user_id = _resolve_user_id(request, response)
    if not request.cookies.get(COOKIE_NAME):
        await user_store.ensure_user(user_id)
        await security_store.issue_csrf(user_id)
    await security_store.touch_session(user_id)
    return await _user_response(user_id, response)


@app.post("/api/auth/phone")
async def auth_phone(body: PhoneBody, request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await check_auth_not_locked(security_store, user_id, request)
    await security_store.rate_limit_check(f"auth:ip:{client_ip(request)}", 8, 60)
    await security_store.rate_limit_check(f"auth:user:{user_id}", 5, 60)
    await user_store.ensure_user(user_id)
    await tg_auth.cancel_qr(user_id)
    result = await tg_auth.send_phone(user_id, body.phone)
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    return {"ok": True, "needs_code": True}


@app.post("/api/auth/qr/start")
async def auth_qr_start(request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await check_auth_not_locked(security_store, user_id, request)
    await security_store.rate_limit_check(f"auth:ip:{client_ip(request)}", 8, 60)
    await security_store.rate_limit_check(f"auth:user:{user_id}", 5, 60)
    await user_store.ensure_user(user_id)
    result = await tg_auth.start_qr(user_id)
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "ok": True,
        "auth_step": "qr",
        "qr_url": result.qr_url,
        "qr_image": result.qr_image,
        "qr_expires": result.qr_expires,
        "csrf_token": await security_store.get_or_issue_csrf(user_id),
    }


@app.get("/api/auth/qr/status")
async def auth_qr_status(request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await security_store.rate_limit_check(f"auth:ip:{client_ip(request)}", 30, 60)
    result = await tg_auth.qr_status(user_id)
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    if result.needs_password:
        return {"ok": True, "needs_password": True, **(await _user_response(user_id, response))}
    if result.ok and result.name:
        await security_store.clear_auth_failures(f"user:{user_id}")
        await security_store.clear_auth_failures(f"ip:{client_ip(request)}")
        user_id = await _rotate_session(user_id, response)
        await security_store.touch_session(user_id)
        return await _user_response(user_id, response)
    return {
        "ok": True,
        "waiting": True,
        "qr_url": result.qr_url,
        "qr_image": result.qr_image,
        "qr_expires": result.qr_expires,
    }


@app.post("/api/auth/code")
async def auth_code(body: CodeBody, request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await check_auth_not_locked(security_store, user_id, request)
    await security_store.rate_limit_check(f"auth:ip:{client_ip(request)}", 8, 60)
    await security_store.rate_limit_check(f"auth:user:{user_id}", 5, 60)
    result = await tg_auth.submit_code(user_id, body.code)
    if result.failed:
        await security_store.record_auth_failure(f"user:{user_id}")
        await security_store.record_auth_failure(f"ip:{client_ip(request)}")
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    if result.needs_password:
        return {"ok": True, "needs_password": True, **(await _user_response(user_id, response))}
    await security_store.clear_auth_failures(f"user:{user_id}")
    await security_store.clear_auth_failures(f"ip:{client_ip(request)}")
    user_id = await _rotate_session(user_id, response)
    await security_store.touch_session(user_id)
    return await _user_response(user_id, response)


@app.post("/api/auth/password")
async def auth_password(body: PasswordBody, request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await check_auth_not_locked(security_store, user_id, request)
    await security_store.rate_limit_check(f"auth:ip:{client_ip(request)}", 8, 60)
    await security_store.rate_limit_check(f"auth:user:{user_id}", 5, 60)
    result = await tg_auth.submit_password(user_id, body.password)
    if result.failed:
        await security_store.record_auth_failure(f"user:{user_id}")
        await security_store.record_auth_failure(f"ip:{client_ip(request)}")
    if result.error:
        raise HTTPException(status_code=400, detail=result.error)
    await security_store.clear_auth_failures(f"user:{user_id}")
    await security_store.clear_auth_failures(f"ip:{client_ip(request)}")
    user_id = await _rotate_session(user_id, response)
    await security_store.touch_session(user_id)
    return await _user_response(user_id, response)


@app.post("/api/auth/logout")
async def auth_logout(request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await security_store.rate_limit_check(f"action:user:{user_id}", 20, 60)
    await tg_auth.cancel_qr(user_id)
    await bridge_manager.stop(user_id)
    await user_store.delete_user(user_id)
    await security_store.clear_user_security(user_id)
    clear_session_cookie(response)
    new_id = await _new_anonymous_session(response)
    await security_store.touch_session(new_id)
    return await _user_response(new_id, response)


@app.patch("/api/settings")
async def update_settings(body: SettingsBody, request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await require_fresh_session(security_store, user_id)
    await security_store.rate_limit_check(f"action:user:{user_id}", 20, 60)
    clear_vk = body.use_default_vk_token is True
    await user_store.update_settings(
        user_id,
        vk_peer_id=body.vk_peer_id,
        vk_token=body.vk_token,
        clear_vk_token=clear_vk,
        forwarding=body.forwarding,
        img_mode=body.img_mode,
    )
    await security_store.touch_session(user_id)
    return await _user_response(user_id, response)


@app.post("/api/service/start")
async def service_start(request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await require_fresh_session(security_store, user_id)
    await security_store.rate_limit_check(f"action:user:{user_id}", 20, 60)
    ok, err = await bridge_manager.start(user_id)
    if not ok:
        raise HTTPException(status_code=400, detail=public_service_error(err))
    await security_store.touch_session(user_id)
    return await _user_response(user_id, response)


@app.post("/api/service/stop")
async def service_stop(request: Request, response: Response) -> dict[str, Any]:
    user_id = _resolve_user_id(request, response)
    await require_csrf(security_store, request, user_id)
    await require_fresh_session(security_store, user_id)
    await security_store.rate_limit_check(f"action:user:{user_id}", 20, 60)
    await bridge_manager.stop(user_id)
    await security_store.touch_session(user_id)
    return await _user_response(user_id, response)


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
