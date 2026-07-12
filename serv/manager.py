from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from serv.security import public_service_error
from serv.settings import default_vk_token, public_panel_url, user_messages_db
from serv.user_store import UserStore, WebUser
from tgvk.bridge import BridgeService
from tgvk.config import AppConfig
from tgvk.storage import MessageStore

logger = logging.getLogger(__name__)


@dataclass
class ManagedBridge:
    user_id: str
    service: BridgeService
    task: asyncio.Task[None]
    error: str = ""


class BridgeManager:
    def __init__(self, user_store: UserStore) -> None:
        self.user_store = user_store
        self._bridges: dict[str, ManagedBridge] = {}

    def is_running(self, user_id: str) -> bool:
        entry = self._bridges.get(user_id)
        return bool(entry and not entry.task.done())

    def get_error(self, user_id: str) -> str:
        entry = self._bridges.get(user_id)
        return entry.error if entry else ""

    def is_running_for_vk_peer(self, vk_peer_id: int) -> bool:
        if not vk_peer_id:
            return False
        for entry in self._bridges.values():
            if entry.task.done():
                continue
            if entry.service.config.vk_peer_id == vk_peer_id:
                return True
        return False

    def get_bridge_by_vk_peer(self, vk_peer_id: int) -> ManagedBridge | None:
        if not vk_peer_id:
            return None
        for entry in self._bridges.values():
            if entry.task.done():
                continue
            if entry.service.config.vk_peer_id == vk_peer_id:
                return entry
        return None

    def _make_persist(self, user: WebUser, cfg: AppConfig) -> Callable[[], None]:
        custom_vk = user.vk_token

        def persist() -> None:
            asyncio.create_task(
                self._persist_config(user.id, cfg, custom_vk),
                name=f"tgvk-save-{user.id[:8]}",
            )

        return persist

    async def _persist_config(self, user_id: str, cfg: AppConfig, custom_vk: str) -> None:
        try:
            await self.user_store.save_from_config(user_id, cfg, custom_vk=custom_vk)
        except Exception:
            logger.exception("Не удалось сохранить конфиг user=%s", user_id[:8])

    def _build_config(self, user: WebUser) -> AppConfig:
        return user.to_app_config(default_vk_token())

    async def start(self, user_id: str) -> tuple[bool, str]:
        if self.is_running(user_id):
            return True, ""

        user = await self.user_store.get_user(user_id)
        if not user:
            return False, "Пользователь не найден"

        token = default_vk_token()
        ready, missing = user.to_app_config(token).is_ready(token)
        if not ready:
            return False, f"Не хватает настроек: {', '.join(missing)}"

        cfg = self._build_config(user)
        db_path = user_messages_db(user_id)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = MessageStore(db_path=db_path)
        service = BridgeService(
            cfg,
            store=store,
            web_panel=True,
            panel_url=public_panel_url(),
            config_persist=self._make_persist(user, cfg),
        )

        async def _run() -> None:
            try:
                await service.start_managed()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Мост user=%s упал", user_id[:8])
                entry = self._bridges.get(user_id)
                if entry:
                    entry.error = public_service_error(str(exc))
            finally:
                self._bridges.pop(user_id, None)

        task = asyncio.create_task(_run(), name=f"tgvk-bridge-{user_id[:8]}")
        self._bridges[user_id] = ManagedBridge(user_id=user_id, service=service, task=task)
        await asyncio.sleep(0.3)
        if task.done() and task.exception():
            exc = task.exception()
            self._bridges.pop(user_id, None)
            return False, public_service_error(str(exc)) if exc else "Не удалось запустить"
        return True, ""

    async def stop(self, user_id: str) -> None:
        entry = self._bridges.get(user_id)
        if not entry:
            return
        await entry.service.stop()
        try:
            await asyncio.wait_for(entry.task, timeout=15)
        except asyncio.TimeoutError:
            entry.task.cancel()
        self._bridges.pop(user_id, None)

    async def stop_all(self) -> None:
        for user_id in list(self._bridges):
            await self.stop(user_id)
