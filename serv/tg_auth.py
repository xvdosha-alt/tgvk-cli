from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import qrcode
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.custom import QRLogin

from serv.security import GENERIC_AUTH_ERROR
from serv.user_store import PendingAuth, UserStore
from tgvk.telegram_defaults import (
    TELEGRAM_DESKTOP_API_HASH,
    TELEGRAM_DESKTOP_API_ID,
    TELEGRAM_DESKTOP_APP_VERSION,
    TELEGRAM_DESKTOP_DEVICE,
    TELEGRAM_DESKTOP_LANG,
    TELEGRAM_DESKTOP_SYSTEM,
    TELEGRAM_DESKTOP_SYSTEM_LANG,
)

logger = logging.getLogger(__name__)


def normalize_phone(phone: str) -> str:
    return "".join(phone.split())


def _make_client(session: str) -> TelegramClient:
    return TelegramClient(
        StringSession(session),
        TELEGRAM_DESKTOP_API_ID,
        TELEGRAM_DESKTOP_API_HASH,
        device_model=TELEGRAM_DESKTOP_DEVICE,
        system_version=TELEGRAM_DESKTOP_SYSTEM,
        app_version=TELEGRAM_DESKTOP_APP_VERSION,
        lang_code=TELEGRAM_DESKTOP_LANG,
        system_lang_code=TELEGRAM_DESKTOP_SYSTEM_LANG,
    )


def _qr_data_url(url: str) -> str:
    img = qrcode.make(url, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@dataclass
class AuthResult:
    ok: bool = False
    needs_code: bool = False
    needs_password: bool = False
    error: str = ""
    name: str = ""
    username: str | None = None
    failed: bool = False
    qr_url: str = ""
    qr_image: str = ""
    qr_expires: str = ""


@dataclass
class _QrSession:
    user_id: str
    client: TelegramClient
    qr_login: QRLogin
    task: asyncio.Task[None]
    status: str = "waiting"
    error: str = ""
    result: AuthResult | None = None
    url: str = ""
    expires: datetime | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class TelegramAuth:
    def __init__(self, store: UserStore) -> None:
        self.store = store
        self._qr_sessions: dict[str, _QrSession] = {}

    async def send_phone(self, user_id: str, phone: str) -> AuthResult:
        await self.cancel_qr(user_id)
        phone = normalize_phone(phone)
        if not phone:
            return AuthResult(error="Укажи номер телефона")

        client = _make_client("")
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            session = client.session.save()
            await self.store.save_pending(
                PendingAuth(
                    user_id=user_id,
                    phone=phone,
                    telegram_session=session,
                    phone_code_hash=sent.phone_code_hash,
                    auth_mode="phone",
                )
            )
            return AuthResult(ok=True, needs_code=True)
        except Exception:
            logger.exception("send_phone failed user=%s", user_id[:8])
            return AuthResult(error=GENERIC_AUTH_ERROR)
        finally:
            await client.disconnect()

    async def start_qr(self, user_id: str) -> AuthResult:
        await self.cancel_qr(user_id)

        client = _make_client("")
        await client.connect()
        try:
            qr_login = await client.qr_login()
            session = client.session.save()
            await self.store.save_pending(
                PendingAuth(
                    user_id=user_id,
                    phone="",
                    telegram_session=session,
                    phone_code_hash="",
                    auth_mode="qr",
                )
            )

            expires = qr_login.expires
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)

            sess = _QrSession(
                user_id=user_id,
                client=client,
                qr_login=qr_login,
                task=asyncio.create_task(self._qr_wait(user_id), name=f"tgvk-qr-{user_id[:8]}"),
                url=qr_login.url,
                expires=expires,
            )
            self._qr_sessions[user_id] = sess

            return AuthResult(
                ok=True,
                qr_url=qr_login.url,
                qr_image=_qr_data_url(qr_login.url),
                qr_expires=expires.isoformat(),
            )
        except Exception:
            logger.exception("start_qr failed user=%s", user_id[:8])
            await client.disconnect()
            await self.store.clear_pending(user_id)
            return AuthResult(error=GENERIC_AUTH_ERROR)

    async def qr_status(self, user_id: str) -> AuthResult:
        sess = self._qr_sessions.get(user_id)
        if not sess:
            pending = await self.store.get_pending(user_id)
            if pending and pending.auth_mode == "qr" and pending.needs_password:
                return AuthResult(ok=True, needs_password=True)
            if pending and pending.auth_mode == "qr":
                return AuthResult(error="QR-сессия истекла. Обнови код.")
            return AuthResult(error="QR-вход не запущен")

        async with sess.lock:
            if sess.status == "waiting":
                return AuthResult(
                    ok=True,
                    qr_url=sess.url,
                    qr_image=_qr_data_url(sess.url),
                    qr_expires=sess.expires.isoformat() if sess.expires else "",
                )
            if sess.status == "password":
                return AuthResult(ok=True, needs_password=True)
            if sess.status == "done" and sess.result:
                return sess.result
            if sess.status == "expired":
                return AuthResult(error="QR-код истёк. Обнови.")
            if sess.status == "error":
                return AuthResult(error=sess.error or GENERIC_AUTH_ERROR)
            if sess.status == "cancelled":
                return AuthResult(error="QR-вход отменён")
        return AuthResult(error=GENERIC_AUTH_ERROR)

    async def cancel_qr(self, user_id: str) -> None:
        sess = self._qr_sessions.pop(user_id, None)
        if not sess:
            return
        sess.task.cancel()
        try:
            await sess.task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("cancel_qr task failed user=%s", user_id[:8])
        if sess.client.is_connected():
            await sess.client.disconnect()

    async def _qr_wait(self, user_id: str) -> None:
        sess = self._qr_sessions.get(user_id)
        if not sess:
            return
        try:
            await sess.qr_login.wait()
            sess.result = await self._complete(user_id, sess.client)
            sess.status = "done"
        except SessionPasswordNeededError:
            session = sess.client.session.save()
            await self.store.set_pending_password(user_id, session)
            sess.status = "password"
        except asyncio.TimeoutError:
            sess.status = "expired"
            await self.store.clear_pending(user_id)
        except asyncio.CancelledError:
            sess.status = "cancelled"
            raise
        except Exception:
            logger.exception("qr_wait failed user=%s", user_id[:8])
            sess.status = "error"
            sess.error = GENERIC_AUTH_ERROR
            await self.store.clear_pending(user_id)
        finally:
            if sess.client.is_connected():
                await sess.client.disconnect()

    async def submit_code(self, user_id: str, code: str) -> AuthResult:
        await self.cancel_qr(user_id)
        pending = await self.store.get_pending(user_id)
        if not pending:
            return AuthResult(error="Сначала введи номер телефона")

        code = code.strip().replace(" ", "")
        if not code:
            return AuthResult(error="Введи код")

        client = _make_client(pending.telegram_session)
        await client.connect()
        try:
            try:
                await client.sign_in(
                    pending.phone,
                    code,
                    phone_code_hash=pending.phone_code_hash,
                )
            except PhoneCodeInvalidError:
                return AuthResult(error="Неверный код", failed=True)
            except PhoneCodeExpiredError:
                await self.store.clear_pending(user_id)
                return AuthResult(error="Код истёк. Запроси новый.", failed=True)
            except SessionPasswordNeededError:
                session = client.session.save()
                await self.store.set_pending_password(user_id, session)
                return AuthResult(ok=True, needs_password=True)

            return await self._complete(user_id, client)
        except Exception:
            logger.exception("submit_code failed user=%s", user_id[:8])
            return AuthResult(error=GENERIC_AUTH_ERROR, failed=True)
        finally:
            await client.disconnect()

    async def submit_password(self, user_id: str, password: str) -> AuthResult:
        pending = await self.store.get_pending(user_id)
        if not pending:
            return AuthResult(error="Сначала пройди авторизацию")

        if not password:
            return AuthResult(error="Введи пароль 2FA")

        await self.cancel_qr(user_id)
        client = _make_client(pending.telegram_session)
        await client.connect()
        try:
            await client.sign_in(password=password)
            result = await self._complete(user_id, client)
            sess = self._qr_sessions.get(user_id)
            if sess:
                sess.result = result
                sess.status = "done"
            return result
        except Exception:
            logger.exception("submit_password failed user=%s", user_id[:8])
            return AuthResult(error="Неверный пароль 2FA", failed=True)
        finally:
            await client.disconnect()

    async def _complete(self, user_id: str, client: TelegramClient) -> AuthResult:
        session = client.session.save()
        me = await client.get_me()
        name = " ".join(filter(None, [me.first_name, me.last_name])).strip()
        username = me.username

        await self.store.set_telegram(
            user_id,
            session=session,
            name=name,
            username=username,
        )
        await self.store.clear_pending(user_id)
        self._qr_sessions.pop(user_id, None)

        return AuthResult(ok=True, name=name, username=username)
