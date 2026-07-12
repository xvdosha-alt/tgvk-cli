from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Any, Awaitable, Callable

import httpx
from vkbottle import API, Bot
from vkbottle.bot import Message as VkMessage
from vkbottle.tools import VideoUploader

from tgvk.config import AppConfig

logger = logging.getLogger(__name__)

CommandHandler = Callable[[str, list[str], VkMessage], Awaitable[str | None]]

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _random_id() -> int:
    import random

    return random.randint(1, 2_147_483_647)


def parse_vk_command(text: str) -> tuple[str, list[str]]:
    text = text.strip()
    if not text:
        return "", []
    if text.startswith("/"):
        text = text[1:]
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]
    return cmd, args


class VkNotifier:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.api = API(config.vk_token)

    async def send_text(self, text: str) -> None:
        await self.api.messages.send(
            peer_id=self.config.vk_peer_id,
            message=text[:4090],
            random_id=_random_id(),
        )

    async def send_photo(
        self,
        image_bytes: bytes,
        caption: str = "",
        *,
        filename: str = "photo.jpg",
    ) -> None:
        peer_id = self.config.vk_peer_id
        upload_url_resp = await self.api.photos.get_messages_upload_server(peer_id=peer_id)
        upload_url = upload_url_resp.upload_url

        mime = "image/png" if filename.lower().endswith(".png") else "image/jpeg"

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                upload_url,
                files={"photo": (filename, image_bytes, mime)},
            )
            resp.raise_for_status()
            upload_data = resp.json()

        saved = await self.api.photos.save_messages_photo(
            photo=upload_data["photo"],
            server=upload_data["server"],
            hash=upload_data["hash"],
        )
        photo = saved[0]
        attachment = f"photo{photo.owner_id}_{photo.id}"

        await self.api.messages.send(
            peer_id=peer_id,
            message=caption[:1024] if caption else None,
            attachment=attachment,
            random_id=_random_id(),
        )

    async def send_video(
        self,
        video_bytes: bytes,
        caption: str = "",
        *,
        filename: str = "video.mp4",
    ) -> None:
        if len(video_bytes) > MAX_UPLOAD_BYTES:
            raise ValueError("Видео слишком большое для VK")

        uploader = VideoUploader(self.api)
        file_io = BytesIO(video_bytes)
        file_io.name = filename
        attachment = await uploader.upload(
            file_source=file_io,
            name=filename.rsplit(".", 1)[0][:128],
            is_private=1,
        )
        await self.api.messages.send(
            peer_id=self.config.vk_peer_id,
            message=caption[:1024] if caption else None,
            attachment=attachment,
            random_id=_random_id(),
        )


class VkCommandBot:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.bot = Bot(token=config.vk_token)
        self._handler: CommandHandler | None = None
        self._owner_id = config.vk_peer_id
        self._ack: Callable[[str], Awaitable[None]] | None = None

    def on_command(self, handler: CommandHandler) -> None:
        self._handler = handler

    def on_ack(self, ack: Callable[[str], Awaitable[None]]) -> None:
        self._ack = ack

    def _parse_command(self, text: str) -> tuple[str, list[str]]:
        return parse_vk_command(text)

    async def _send_owner(self, text: str) -> None:
        await self.bot.api.messages.send(
            peer_id=self._owner_id,
            message=text[:4090],
            random_id=_random_id(),
        )

    async def _process_command(self, text: str) -> None:
        cmd, args = self._parse_command(text)
        if not cmd or not self._handler:
            return

        if cmd in ("лс", "отв", "dm", "reply") and self._ack:
            preview = " ".join(args[:2])
            await self._ack(f"⏳ {cmd} {preview}…")

        try:
            reply = await self._handler(cmd, args, None)  # type: ignore[arg-type]
            if reply:
                await self._send_owner(reply)
        except Exception as exc:
            logger.exception("Ошибка VK команды %s", cmd)
            await self._send_owner(f"❌ Ошибка: {exc}")

    async def _poll_history(self) -> None:
        last_id = 0
        try:
            hist = await self.bot.api.messages.get_history(peer_id=self._owner_id, count=1)
            if hist.items:
                last_id = hist.items[0].id
        except Exception:
            logger.exception("VK: не удалось прочитать историю")

        logger.info("VK: команды через messages.getHistory (peer=%s)", self._owner_id)

        while True:
            await asyncio.sleep(2)
            try:
                hist = await self.bot.api.messages.get_history(
                    peer_id=self._owner_id,
                    count=30,
                )
                new_items = sorted(
                    (m for m in hist.items if m.id > last_id),
                    key=lambda m: m.id,
                )
                for msg in new_items:
                    last_id = max(last_id, msg.id)
                    if msg.from_id != self._owner_id:
                        continue
                    if not msg.text:
                        continue
                    await self._process_command(msg.text)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("VK history poll")
                await asyncio.sleep(5)

    async def start(self) -> None:
        await self._poll_history()

    async def stop(self) -> None:
        pass
