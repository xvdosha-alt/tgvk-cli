from __future__ import annotations

DEFAULT_PANEL_URL = "http://127.0.0.1:8080"


def relogin_message(panel_url: str | None = None) -> str:
    url = (panel_url or DEFAULT_PANEL_URL).rstrip("/")
    return (
        "⚠️ Telegram не привязан или сессия устарела.\n"
        f"Зайди на веб-панель и войди заново:\n{url}"
    )


def setup_message(vk_peer_id: int, panel_url: str | None = None) -> str:
    url = (panel_url or DEFAULT_PANEL_URL).rstrip("/")
    return (
        "👋 Аккаунт ещё не настроен.\n\n"
        f"1. Открой {url}\n"
        "2. Войди в Telegram (номер → код)\n"
        f"3. Укажи VK peer_id: {vk_peer_id}\n"
        "4. Запусти сервис в панели"
    )


def cli_relogin_message() -> str:
    return (
        "⚠️ Telegram-сессия не активна.\n"
        "Обнови session string: tgvk init"
    )
