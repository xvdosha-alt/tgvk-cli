from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from tgvk.config import AppConfig, RuntimeState
from tgvk.formatting import (
    format_chats_list,
    format_history_list,
    format_message_for_vk,
)
from tgvk.image_mode import render_chat_image
from tgvk.session import is_telegram_session_valid
from tgvk.storage import MessageStore
from tgvk.targets import join_reply_text, parse_target_arg
from tgvk.telegram_client import TelegramBridge
from tgvk.vk_bot import VkCommandBot, VkNotifier
from tgvk.vk_messages import cli_relogin_message, relogin_message

logger = logging.getLogger(__name__)

HELP_TEXT = """🤖 Команды tgvk:

стоп / stop — пауза (новые TG не пересылать)
старт / start — снова пересылать
ст / status — статус
ист [id] [n] — история (n сообщений, по умолч. 20)
чат / chats — список чатов
img [id] [n] — чат как картинка
img+ / img- / имг+ / имг- — вкл/выкл img mode (чат как картинка)
лимит <n> — лимит истории по умолч.
отв @user текст — ответить в Telegram (reply в последний чат)
отв 123456 текст — ответ по user id
лс 123456 текст — написать в личку (всегда ЛС)
лс @user текст — написать в личку по username
игнор @user / игнор 123 — не пересылать юзера
анигнор @user / анигнор 123 — снять игнор юзера
игнор — весь список игнора
групп+ / групп- — игнор ВСЕХ групп и супергрупп
канал+ / канал- — игнор ВСЕХ каналов
отстук+ / отстук- — отстук при пересылке из TG
игнорчат <chat_id> — игнор одного чата
анигнорчат <chat_id> — снять игнор чата
помощь / help — эта справка

Примеры:
  отв @ivan привет, как дела?
  лс 8973446217 привет
  лс @ivan привет
  игнор @spam_bot
  групп+          — не пересылать из групп/супергрупп
  канал+          — не пересылать из каналов
  имг+            — пересылать как картинку
  игнорчат -1001234567890"""


class BridgeService:
    def __init__(
        self,
        config: AppConfig,
        store: MessageStore | None = None,
        *,
        web_panel: bool = False,
        panel_url: str | None = None,
        config_persist: Callable[[], None] | None = None,
    ) -> None:
        self.config = config
        self.web_panel = web_panel
        self.panel_url = panel_url
        self._config_persist = config_persist
        self.state = RuntimeState(img_mode=config.img_mode)
        self.store = store or MessageStore()
        self.telegram = TelegramBridge(config)
        self.notifier = VkNotifier(config)
        self.command_bot = VkCommandBot(config)
        self._vk_task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    def _persist_config(self) -> None:
        if self._config_persist:
            self._config_persist()
        else:
            self.config.save()

    async def _bootstrap(self) -> None:
        await self.store.init()

        self.telegram.on_message(self._on_telegram_message)
        self.command_bot.on_command(self._handle_vk_command)
        self.command_bot.on_ack(self._vk_ack)

        await self.telegram.start()
        if not await self.telegram.client.is_user_authorized():
            raise RuntimeError(
                "Telegram сессия не авторизована. Проверь session string."
            )

        me = await self.telegram.me_name()
        self.state.running = True
        try:
            await self.notifier.send_text(
                f"✅ tgvk запущен\n"
                f"Telegram: {me}\n"
                f"Пересылка: {'вкл' if self.config.forwarding else '⏸ стоп'}\n"
                f"Img mode: {'вкл' if self.state.img_mode else 'выкл'}\n\n"
                f"Напиши «помощь» для команд."
            )
        except Exception as exc:
            logger.error(
                "VK: не удалось отправить стартовое сообщение: %s. "
                "Включи «Сообщения сообщества» в настройках VK.",
                exc,
            )

        self._vk_task = None
        if not self.web_panel:
            self._vk_task = asyncio.create_task(self._run_vk_bot())

    async def _shutdown(self) -> None:
        self.state.running = False
        if self._vk_task:
            self._vk_task.cancel()
            try:
                await self._vk_task
            except asyncio.CancelledError:
                pass
        await self.telegram.stop()

    async def start(self) -> None:
        await self._bootstrap()
        try:
            await self.telegram.client.run_until_disconnected()
        finally:
            await self._shutdown()

    async def start_managed(self) -> None:
        await self._bootstrap()
        self._stop_event = asyncio.Event()
        try:
            await self._stop_event.wait()
        finally:
            await self._shutdown()

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        else:
            await self._shutdown()

    async def _on_telegram_message(self, msg: dict[str, Any]) -> None:
        if not self.config.forwarding:
            return

        await self.store.save_message(
            tg_message_id=msg["tg_message_id"],
            tg_chat_id=msg["tg_chat_id"],
            tg_chat_title=msg["tg_chat_title"],
            tg_chat_type=msg["tg_chat_type"],
            sender_id=msg["sender_id"],
            sender_name=msg["sender_name"],
            sender_username=msg.get("sender_username"),
            text=msg["text"],
            has_media=msg.get("has_media", False),
            media_type=msg.get("media_type"),
        )

        if self.config.should_skip_message(
            chat_id=msg["tg_chat_id"],
            chat_type=msg.get("tg_chat_type", ""),
            sender_id=msg["sender_id"],
            sender_username=msg.get("sender_username"),
        ):
            logger.debug(
                "Пропуск: %s · %s",
                msg.get("tg_chat_title"),
                msg.get("sender_name"),
            )
            return

        self.state.messages_forwarded += 1

        if self.state.img_mode:
            stored = await self.store.get_recent(limit=1, chat_id=msg["tg_chat_id"])
            if stored:
                png = render_chat_image(
                    stored,
                    title=msg["tg_chat_title"],
                    subtitle=msg.get("tg_chat_type", ""),
                )
                caption = (
                    f"📩 {msg['sender_name']} · {msg['tg_chat_title']}\n"
                    f"🆔 user:{msg['sender_id']} · chat:{msg['tg_chat_id']}"
                )
                try:
                    await self.notifier.send_photo(png, caption=caption)
                except Exception:
                    logger.exception("VK: не удалось отправить фото")
        else:
            try:
                await self._forward_text_message(msg)
                if self.config.otstuk:
                    await self._vk_ack(
                        f"📨 отстук · {msg['sender_name']} · {msg['tg_chat_title'][:40]}"
                    )
            except Exception:
                logger.exception("VK: не удалось переслать сообщение")

    async def _forward_text_message(self, msg: dict[str, Any]) -> None:
        media = None
        raw = msg.get("raw_message")
        if raw is not None and msg.get("media_type") in ("фото", "видео"):
            media = await self.telegram.download_forwardable_media(raw)

        caption = format_message_for_vk(msg, attach_media=media is not None)

        if media:
            try:
                if media["kind"] == "photo":
                    await self.notifier.send_photo(
                        media["data"],
                        caption=caption,
                        filename=media["filename"],
                    )
                    return
                await self.notifier.send_video(
                    media["data"],
                    caption=caption,
                    filename=media["filename"],
                )
                return
            except Exception:
                logger.exception("VK: не удалось отправить медиа, шлём текстом")

        await self.notifier.send_text(format_message_for_vk(msg))

    async def _vk_ack(self, text: str) -> None:
        try:
            await self.notifier.send_text(text)
        except Exception:
            logger.debug("VK ack failed: %s", text)

    async def _run_vk_bot(self) -> None:
        try:
            await self.command_bot.start()
        except Exception:
            logger.exception("VK бот остановился")

    async def _ensure_telegram_session(self) -> str | None:
        if not await self.telegram.client.is_user_authorized():
            if self.web_panel:
                return relogin_message(self.panel_url)
            return cli_relogin_message()

        if self.web_panel:
            valid = await is_telegram_session_valid(
                self.config.telegram_session,
                api_id=self.config.telegram_api_id,
                api_hash=self.config.telegram_api_hash,
                cache_key=f"bridge-{self.config.vk_peer_id}",
            )
            if not valid:
                return relogin_message(self.panel_url)
        return None

    async def _handle_vk_command(self, cmd: str, args: list[str], message: Any) -> str | None:
        return await self.handle_vk_command(cmd, args)

    async def handle_vk_command(self, cmd: str, args: list[str]) -> str | None:
        relogin = await self._ensure_telegram_session()
        if relogin:
            return relogin
        return await self._on_vk_command(cmd, args, None)

    async def _on_vk_command(self, cmd: str, args: list[str], _message: Any) -> str | None:
        aliases = {
            "status": "ст",
            "help": "помощь",
            "hist": "ист",
            "history": "ист",
            "chats": "чат",
            "limit": "лимит",
            "reply": "отв",
            "dm": "лс",
            "pm": "лс",
            "unignore": "анигнор",
            "unignorechat": "анигнорчат",
            "ignorechat": "игнорчат",
        }
        cmd = aliases.get(cmd, cmd)

        if cmd in ("стоп", "stop", "pause", "пауза"):
            self.config.forwarding = False
            self._persist_config()
            return (
                "⏸ Стоп. Новые сообщения из Telegram не пересылаются.\n"
                "Команды VK работают. Старт: «старт»"
            )

        if cmd in ("старт", "start", "resume", "пуск"):
            self.config.forwarding = True
            self._persist_config()
            return "▶️ Старт. Пересылка из Telegram снова включена."

        if cmd in ("помощь", "help", "?"):
            return HELP_TEXT

        if cmd in ("ст", "status"):
            connected = await self.telegram.is_connected()
            total = await self.store.count()
            return (
                f"📊 Статус tgvk\n"
                f"Работает: {'да' if self.state.running else 'нет'}\n"
                f"Пересылка: {'▶️ вкл' if self.config.forwarding else '⏸ стоп'}\n"
                f"Telegram: {'подключён' if connected else 'нет'}\n"
                f"Переслано: {self.state.messages_forwarded}\n"
                f"В БД: {total} сообщ.\n"
                f"Img mode: {'вкл' if self.state.img_mode else 'выкл'}\n"
                f"Лимит: {self.config.history_limit}\n"
                f"Игнор юзеров: {len(self.config.ignored_user_ids) + len(self.config.ignored_usernames)}\n"
                f"Игнор всех групп: {'вкл' if self.config.ignore_groups else 'выкл'}\n"
                f"Игнор всех каналов: {'вкл' if self.config.ignore_channels else 'выкл'}\n"
                f"Отстук: {'вкл' if self.config.otstuk else 'выкл'}\n"
                f"Игнор чатов: {len(self.config.ignored_chat_ids)}"
            )

        if cmd in ("отстук+", "otstuk+"):
            self.config.otstuk = True
            self._persist_config()
            return "🔔 Отстук включён — после каждой пересылки из TG придёт короткий пинг."

        if cmd in ("отстук-", "otstuk-"):
            self.config.otstuk = False
            self._persist_config()
            return "🔕 Отстук выключен."

        if cmd in ("групп+", "игноргруп+", "groups+"):
            self.config.ignore_groups = True
            self._persist_config()
            return "🚫 Игнор всех групп и супергрупп включён."

        if cmd in ("групп-", "игноргруп-", "groups-"):
            self.config.ignore_groups = False
            self._persist_config()
            return "✅ Группы и супергруппы снова пересылаются."

        if cmd in ("канал+", "игнорканал+", "channels+"):
            self.config.ignore_channels = True
            self._persist_config()
            return "🚫 Игнор всех каналов включён."

        if cmd in ("канал-", "игнорканал-", "channels-"):
            self.config.ignore_channels = False
            self._persist_config()
            return "✅ Каналы снова пересылаются."

        if cmd in ("img+", "imgon", "имг+", "имгон"):
            self.state.img_mode = True
            self.config.img_mode = True
            self._persist_config()
            return "🖼 Img mode включён — новые сообщения как картинка."

        if cmd in ("img-", "imgoff", "имг-", "имгоф"):
            self.state.img_mode = False
            self.config.img_mode = False
            self._persist_config()
            return "📝 Img mode выключен — новые сообщения текстом."

        if cmd == "лимит" and args:
            try:
                n = int(args[0])
                self.config.history_limit = max(5, min(n, 200))
                self._persist_config()
                return f"Лимит истории: {self.config.history_limit}"
            except ValueError:
                return "Использование: лимит <число>"

        if cmd in ("чат", "chats"):
            chats = await self.store.get_chats()
            return format_chats_list(chats)

        if cmd == "ист":
            limit = self.config.history_limit
            chat_id: int | None = None

            if args:
                try:
                    chat_id = int(args[0])
                    if len(args) > 1:
                        limit = int(args[1])
                except ValueError:
                    limit = int(args[0])

            limit = max(1, min(limit, 50))

            messages = await self.store.get_recent(limit=limit, chat_id=chat_id)
            if messages:
                return format_history_list(messages)

            if await self.telegram.is_connected():
                live = await self.telegram.fetch_history(chat_id=chat_id, limit=limit)
                if live:
                    return format_history_list(
                        [
                            {
                                **m,
                                "created_at": "",
                            }
                            for m in live
                        ]
                    )
            return "История пуста."

        if cmd == "img":
            limit = 15
            chat_id = None
            title = "Telegram"

            if args:
                try:
                    chat_id = int(args[0])
                    if len(args) > 1:
                        limit = int(args[1])
                except ValueError:
                    limit = int(args[0])

            limit = max(1, min(limit, 30))
            messages = await self.store.get_recent(limit=limit, chat_id=chat_id)

            if not messages and await self.telegram.is_connected():
                live = await self.telegram.fetch_history(chat_id=chat_id, limit=limit)
                messages = live

            if messages and chat_id:
                title = messages[0].get("tg_chat_title", title)
            elif messages:
                title = "Последние сообщения"

            png = render_chat_image(messages, title=title, subtitle=f"{len(messages)} сообщ.")
            await self.notifier.send_photo(png, caption=f"🖼 {title}")
            return None

        if cmd == "отв":
            reply_text = join_reply_text(args)
            if not args or not reply_text:
                return "Использование: отв @user текст\nили: отв 123456789 текст"
            target = args[0]
            kind, value = parse_target_arg(target)
            sender_id = value if kind == "id" else None
            username = value if kind == "username" else None

            last = await self.store.get_last_from_sender(
                sender_id=sender_id,
                username=username if isinstance(username, str) else None,
            )
            if last and sender_id is None:
                sender_id = last.get("sender_id") or None
            if last and username is None:
                username = last.get("sender_username")

            try:
                return await self.telegram.send_reply(
                    target=target,
                    text=reply_text,
                    last_message=last,
                )
            except Exception as exc:
                return f"❌ Не удалось отправить: {exc}"

        if cmd == "лс":
            dm_text = join_reply_text(args)
            if not args or not dm_text:
                return "Использование: лс 8973446217 привет\nили: лс @user текст"
            target = args[0]
            try:
                return await self.telegram.send_dm(target=target, text=dm_text)
            except Exception as exc:
                return f"❌ Не удалось отправить в ЛС: {exc}"

        if cmd == "игнор":
            if not args:
                return self.config.format_ignored_list()
            target = args[0]
            kind, value = parse_target_arg(target)
            user_id: int | None = value if kind == "id" else None
            username: str | None = value if kind == "username" else None

            if username:
                for sid in await self.store.find_sender_ids_by_username(username):
                    user_id = sid
                    break
            if user_id is None and username:
                resolved_id, resolved_uname = await self.telegram.resolve_target(target)
                user_id = resolved_id
                if resolved_uname:
                    username = resolved_uname

            self.config.add_ignore(user_id=user_id, username=username)
            label = f"@{username}" if username else f"id:{user_id}"
            return f"🚫 В игноре: {label}"

        if cmd == "анигнор":
            if not args:
                return "Использование: анигнор @user\nили: анигнор 123456"
            target = args[0]
            kind, value = parse_target_arg(target)
            user_id: int | None = value if kind == "id" else None
            username: str | None = value if kind == "username" else None

            if username:
                for sid in await self.store.find_sender_ids_by_username(username):
                    user_id = sid

            removed = self.config.remove_ignore(user_id=user_id, username=username)
            if removed:
                label = f"@{username}" if username else f"id:{user_id}"
                return f"✅ Снят игнор: {label}"
            return f"Не найден в игноре: {target}"

        if cmd == "игнорчат":
            if not args:
                chats = await self.store.get_chats(limit=50)
                return self.config.format_ignored_chats_list(chats)
            try:
                chat_id = int(args[0])
            except ValueError:
                return "Использование: игнорчат <chat_id>\nПример: игнорчат -1001234567890"
            self.config.add_chat_ignore(chat_id)
            title = ""
            for chat in await self.store.get_chats(limit=50):
                if int(chat["tg_chat_id"]) == chat_id:
                    title = chat.get("tg_chat_title", "")
                    break
            label = f"«{title}» · " if title else ""
            return f"🚫 Чат в игноре: {label}chat:{chat_id}"

        if cmd == "анигнорчат":
            if not args:
                return "Использование: анигнорчат <chat_id>"
            try:
                chat_id = int(args[0])
            except ValueError:
                return "Использование: анигнорчат <chat_id>"
            if self.config.remove_chat_ignore(chat_id):
                return f"✅ Снят игнор чата: chat:{chat_id}"
            return f"Чат не в игноре: {args[0]}"

        return (
            f"Неизвестная команда: {cmd}\n"
            f"Напиши «помощь»"
        )

