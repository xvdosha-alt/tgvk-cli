from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events
from telethon.errors import (
    ChatWriteForbiddenError,
    PeerIdInvalidError,
    UserPrivacyRestrictedError,
)
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    User,
)

from tgvk.config import AppConfig
from tgvk.telegram_defaults import (
    TELEGRAM_DESKTOP_APP_VERSION,
    TELEGRAM_DESKTOP_DEVICE,
    TELEGRAM_DESKTOP_LANG,
    TELEGRAM_DESKTOP_SYSTEM,
    TELEGRAM_DESKTOP_SYSTEM_LANG,
)

logger = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]

MAX_MEDIA_BYTES = 50 * 1024 * 1024


def _chat_type(entity: Any) -> str:
    if isinstance(entity, User):
        return "private"
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "supergroup"
    if isinstance(entity, Chat):
        return "group"
    return "unknown"


def _chat_title(entity: Any, sender: Any) -> str:
    if isinstance(entity, User):
        name = " ".join(filter(None, [entity.first_name, entity.last_name])).strip()
        return name or (f"@{entity.username}" if entity.username else f"user:{entity.id}")
    if hasattr(entity, "title") and entity.title:
        return entity.title
    return "Unknown chat"


def _sender_name(sender: Any) -> str:
    if isinstance(sender, User):
        name = " ".join(filter(None, [sender.first_name, sender.last_name])).strip()
        return name or (f"@{sender.username}" if sender.username else f"id:{sender.id}")
    if hasattr(sender, "title"):
        return sender.title
    return "Unknown"


def _media_info(message: Any) -> tuple[bool, str | None]:
    media = message.media
    if not media or isinstance(media, MessageMediaWebPage):
        return False, None
    if isinstance(media, MessageMediaPhoto):
        return True, "фото"
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        if doc:
            for attr in doc.attributes:
                kind = type(attr).__name__
                if "Video" in kind:
                    return True, "видео"
                if "Audio" in kind or "Voice" in kind:
                    return True, "аудио"
                if "Sticker" in kind:
                    return True, "стикер"
        return True, "файл"
    return True, "медиа"


def _document_filename(document: Any) -> str | None:
    if not document:
        return None
    for attr in document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


def _default_filename(media_type: str) -> str:
    if media_type == "фото":
        return "photo.jpg"
    if media_type == "видео":
        return "video.mp4"
    return "file.bin"


class TelegramBridge:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.client = TelegramClient(
            StringSession(config.telegram_session),
            config.telegram_api_id,
            config.telegram_api_hash,
            device_model=TELEGRAM_DESKTOP_DEVICE,
            system_version=TELEGRAM_DESKTOP_SYSTEM,
            app_version=TELEGRAM_DESKTOP_APP_VERSION,
            lang_code=TELEGRAM_DESKTOP_LANG,
            system_lang_code=TELEGRAM_DESKTOP_SYSTEM_LANG,
        )
        self._handler: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self) -> None:
        await self.client.start()
        me = await self.client.get_me()
        logger.info("Telegram: подключён как %s", _sender_name(me))

        @self.client.on(events.NewMessage(incoming=True))
        async def _on_new_message(event: events.NewMessage.Event) -> None:
            if not self._handler:
                return
            try:
                msg = await self._parse_event(event)
                if msg:
                    await self._handler(msg)
            except Exception:
                logger.exception("Ошибка обработки Telegram сообщения")

    async def _parse_event(self, event: events.NewMessage.Event) -> dict[str, Any] | None:
        message = event.message
        if not message:
            return None

        chat = await event.get_chat()
        sender = await event.get_sender()

        has_media, media_type = _media_info(message)
        text = message.message or ""
        if has_media and not text:
            text = f"[{media_type}]"

        return {
            "tg_message_id": message.id,
            "tg_chat_id": event.chat_id or 0,
            "tg_chat_title": _chat_title(chat, sender),
            "tg_chat_type": _chat_type(chat),
            "sender_id": sender.id if sender else 0,
            "sender_name": _sender_name(sender) if sender else "Unknown",
            "sender_username": getattr(sender, "username", None),
            "text": text,
            "has_media": has_media,
            "media_type": media_type,
            "raw_message": message,
        }

    async def fetch_history(
        self,
        chat_id: int | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        if chat_id is not None:
            async for message in self.client.iter_messages(chat_id, limit=limit):
                if not message:
                    continue
                chat = await message.get_chat()
                sender = await message.get_sender()
                has_media, media_type = _media_info(message)
                text = message.message or ""
                if has_media and not text:
                    text = f"[{media_type}]"
                results.append(
                    {
                        "tg_message_id": message.id,
                        "tg_chat_id": chat_id,
                        "tg_chat_title": _chat_title(chat, sender),
                        "tg_chat_type": _chat_type(chat),
                        "sender_id": sender.id if sender else 0,
                        "sender_name": _sender_name(sender) if sender else "Unknown",
                        "sender_username": getattr(sender, "username", None),
                        "text": text,
                        "has_media": has_media,
                        "media_type": media_type,
                    }
                )
            return list(reversed(results))

        dialogs = await self.client.get_dialogs(limit=min(limit, 20))
        for dialog in dialogs:
            msg = dialog.message
            if not msg:
                continue
            entity = dialog.entity
            sender = await msg.get_sender()
            has_media, media_type = _media_info(msg)
            text = msg.message or ""
            if has_media and not text:
                text = f"[{media_type}]"
            results.append(
                {
                    "tg_message_id": msg.id,
                    "tg_chat_id": dialog.id,
                    "tg_chat_title": _chat_title(entity, sender),
                    "tg_chat_type": _chat_type(entity),
                    "sender_id": sender.id if sender else 0,
                    "sender_name": _sender_name(sender) if sender else "Unknown",
                    "sender_username": getattr(sender, "username", None),
                    "text": text,
                    "has_media": has_media,
                    "media_type": media_type,
                }
            )
        return results

    async def stop(self) -> None:
        await self.client.disconnect()

    async def is_connected(self) -> bool:
        return self.client.is_connected()

    async def me_name(self) -> str:
        me = await self.client.get_me()
        return _sender_name(me)

    async def download_forwardable_media(self, message: Any) -> dict[str, Any] | None:
        has_media, media_type = _media_info(message)
        if not has_media or media_type not in ("фото", "видео"):
            return None
        if not message.media:
            return None

        try:
            data = await self.client.download_media(message, file=bytes)
        except Exception:
            logger.exception("Не удалось скачать медиа из Telegram")
            return None

        if not data:
            return None
        if len(data) > MAX_MEDIA_BYTES:
            logger.warning("Медиа %s байт — пропуск загрузки в VK", len(data))
            return None

        filename = _document_filename(
            message.media.document if isinstance(message.media, MessageMediaDocument) else None
        ) or _default_filename(media_type)

        return {
            "kind": "photo" if media_type == "фото" else "video",
            "data": data,
            "filename": filename,
        }

    async def resolve_target(self, target: str) -> tuple[int | None, str | None]:
        raw = target.strip().lstrip("@")
        if raw.lstrip("-").isdigit():
            uid = int(raw)
            try:
                entity = await self.client.get_entity(uid)
                return entity.id, getattr(entity, "username", None)
            except Exception:
                return uid, None
        try:
            entity = await self.client.get_entity(raw)
            return entity.id, getattr(entity, "username", None)
        except Exception:
            return None, raw.lower()

    async def send_reply(
        self,
        *,
        target: str,
        text: str,
        last_message: dict[str, Any] | None = None,
    ) -> str:
        user_id, username = await self.resolve_target(target)

        chat_id: int | None = None
        reply_to: int | None = None
        chat_title = ""

        if last_message:
            chat_id = int(last_message["tg_chat_id"])
            reply_to = int(last_message["tg_message_id"])
            chat_title = last_message.get("tg_chat_title", "")

        if chat_id is None and user_id:
            chat_id = user_id
            chat_title = "личка"

        if chat_id is None:
            raise ValueError(
                f"Не найден чат для «{target}». "
                f"Дождись сообщения от этого человека или укажи id."
            )

        await self.client.send_message(chat_id, text, reply_to=reply_to)

        who = f"@{username}" if username else (f"id:{user_id}" if user_id else target)
        if reply_to:
            label = chat_title or str(chat_id)
            return f"✅ Ответ {who} в «{label}» (reply #{reply_to})"
        return f"✅ Сообщение отправлено {who} в личку"

    async def send_dm(self, *, target: str, text: str) -> str:
        user_id, username = await self.resolve_target(target)
        if not user_id:
            raise ValueError(
                f"Не найден пользователь «{target}». "
                f"Проверь id или @username."
            )

        try:
            entity = await self.client.get_input_entity(user_id)
        except Exception as exc:
            raise ValueError(
                f"Telegram не знает user:{user_id}. "
                f"Напиши этому человеку сначала из TG или укажи @username. "
                f"({exc})"
            ) from exc

        try:
            sent = await self.client.send_message(entity, text)
        except UserPrivacyRestrictedError as exc:
            raise ValueError(
                f"user:{user_id} запретил сообщения от незнакомцев."
            ) from exc
        except (PeerIdInvalidError, ChatWriteForbiddenError) as exc:
            raise ValueError(f"Не могу написать user:{user_id}: {exc}") from exc

        who = f"@{username}" if username else f"id:{user_id}"
        return (
            f"✅ ЛС доставлено → {who}\n"
            f"📝 {text}\n"
            f"🆔 tg_msg:{sent.id}"
        )
