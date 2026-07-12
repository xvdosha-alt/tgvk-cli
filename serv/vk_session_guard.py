from __future__ import annotations

import asyncio
import logging
import random

from vkbottle import API

from serv.manager import BridgeManager
from serv.settings import default_vk_token
from serv.user_store import UserStore, WebUser
from serv.vk_messages import not_running_message, relogin_message, setup_message
from tgvk.bridge import HELP_TEXT
from tgvk.session import is_telegram_session_valid
from tgvk.vk_bot import parse_vk_command

logger = logging.getLogger(__name__)

SCAN_INTERVAL = 2.0
_HISTORY_COUNT = 30
_HELP_CMDS = frozenset({"помощь", "help", "?"})
_START_CMDS = frozenset({"старт", "start", "resume", "пуск"})


def _random_id() -> int:
    return random.randint(1, 2_147_483_647)


class VkSessionGuard:
    def __init__(self, user_store: UserStore, bridge_manager: BridgeManager) -> None:
        self.user_store = user_store
        self.bridge_manager = bridge_manager
        self._apis: dict[str, API] = {}
        self._task: asyncio.Task[None] | None = None
        self._last_seen: dict[int, int] = {}

    def _api(self, token: str) -> API:
        if token not in self._apis:
            self._apis[token] = API(token)
        return self._apis[token]

    async def start(self) -> None:
        token = default_vk_token()
        if not token:
            logger.warning("VkGateway: TGVK_DEFAULT_VK_TOKEN не задан — VK-команды выключены")
            return
        await self._prime_last_seen(token)
        self._task = asyncio.create_task(self._loop(), name="tgvk-vk-gateway")
        logger.info("VkGateway: мониторинг VK-команд")

    async def stop(self) -> None:
        if not self._task:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(SCAN_INTERVAL)
            try:
                token = default_vk_token()
                if token:
                    await self._scan_all_peers(token)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("VkGateway: ошибка сканирования")

    async def _prime_last_seen(self, token: str) -> None:
        try:
            resp = await self._api(token).messages.get_conversations(count=200)
            for item in resp.items:
                peer_id = int(item.conversation.peer.id)
                if peer_id <= 0:
                    continue
                try:
                    hist = await self._api(token).messages.get_history(
                        peer_id=peer_id,
                        count=_HISTORY_COUNT,
                    )
                except Exception:
                    continue
                if hist.items:
                    self._last_seen[peer_id] = max(int(m.id) for m in hist.items)
        except Exception:
            logger.exception("VkGateway: не удалось прочитать начальную историю")

    async def _scan_all_peers(self, token: str) -> None:
        resp = await self._api(token).messages.get_conversations(count=200)
        peers: set[int] = set()
        for item in resp.items:
            peer_id = int(item.conversation.peer.id)
            if peer_id > 0:
                peers.add(peer_id)

        users = await self.user_store.list_users_with_vk_peer_id()
        for user in users:
            if user.vk_peer_id > 0:
                peers.add(user.vk_peer_id)

        for peer_id in peers:
            await self._scan_peer(token, peer_id)

    async def _scan_peer(self, token: str, peer_id: int) -> None:
        try:
            hist = await self._api(token).messages.get_history(
                peer_id=peer_id,
                count=_HISTORY_COUNT,
            )
        except Exception:
            logger.debug("VkGateway: history peer=%s", peer_id)
            return

        for msg in sorted(hist.items, key=lambda m: m.id):
            if int(msg.from_id) != peer_id or not msg.text:
                continue
            await self._track_message(peer_id, int(msg.id), msg.text)

    async def _track_message(self, vk_peer_id: int, msg_id: int, text: str) -> None:
        prev = self._last_seen.get(vk_peer_id, 0)
        if msg_id <= prev:
            return
        self._last_seen[vk_peer_id] = msg_id
        logger.info("VkGateway: команда peer=%s msg=%s text=%r", vk_peer_id, msg_id, text[:40])
        await self._route_message(vk_peer_id, text)

    async def _route_message(self, vk_peer_id: int, text: str) -> None:
        cmd, args = parse_vk_command(text)
        if not cmd:
            return

        bridge = self.bridge_manager.get_bridge_by_vk_peer(vk_peer_id)
        if bridge:
            try:
                reply = await bridge.service.handle_vk_command(cmd, args)
                if reply:
                    user = await self.user_store.get_user_by_vk_peer_id(vk_peer_id)
                    await self._send(vk_peer_id, reply, user)
            except Exception:
                logger.exception("VkGateway: ошибка команды %s peer=%s", cmd, vk_peer_id)
                await self._send(vk_peer_id, "❌ Ошибка обработки команды", None)
            return

        await self._handle_offline(vk_peer_id, cmd, args)

    async def _handle_offline(self, vk_peer_id: int, cmd: str, args: list[str]) -> None:
        if cmd in _HELP_CMDS:
            await self._send(vk_peer_id, HELP_TEXT, None)
            return

        user = await self.user_store.get_user_by_vk_peer_id(vk_peer_id)
        if not user:
            await self._send(vk_peer_id, setup_message(vk_peer_id), None)
            return

        if not user.telegram_session:
            await self._send(vk_peer_id, relogin_message(), user)
            return

        cfg = user.to_app_config(default_vk_token())
        valid = await is_telegram_session_valid(
            user.telegram_session,
            api_id=cfg.telegram_api_id,
            api_hash=cfg.telegram_api_hash,
            cache_key=user.id,
        )
        if not valid:
            await self._send(vk_peer_id, relogin_message(), user)
            return

        await self._send(vk_peer_id, not_running_message(), user)

    async def _send(self, peer_id: int, text: str, user: WebUser | None) -> None:
        token = default_vk_token()
        if user and user.vk_token:
            token = user.vk_token
        if not token:
            return
        try:
            await self._api(token).messages.send(
                peer_id=peer_id,
                message=text[:4090],
                random_id=_random_id(),
            )
        except Exception:
            logger.exception("VkGateway: не удалось отправить peer=%s", peer_id)
