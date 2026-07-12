from __future__ import annotations

from serv.settings import public_panel_url
from tgvk.vk_messages import relogin_message as _relogin
from tgvk.vk_messages import setup_message as _setup


def relogin_message() -> str:
    return _relogin(public_panel_url())


def setup_message(vk_peer_id: int) -> str:
    return _setup(vk_peer_id, public_panel_url())


def not_running_message() -> str:
    url = public_panel_url()
    return (
        "⏸ Сервис не запущен.\n"
        f"Открой {url} → войди в Telegram → укажи VK peer_id → «Запустить»"
    )
