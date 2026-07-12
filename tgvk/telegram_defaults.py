from __future__ import annotations

import os

TELEGRAM_DESKTOP_DEVICE = "Desktop"
TELEGRAM_DESKTOP_SYSTEM = "Windows 10"
TELEGRAM_DESKTOP_APP_VERSION = "6.2.4 x64"
TELEGRAM_DESKTOP_LANG = "en"
TELEGRAM_DESKTOP_SYSTEM_LANG = "en-US"


def telegram_api_id_from_env() -> int | None:
    raw = os.environ.get("TGVK_TELEGRAM_API_ID", "").strip()
    if not raw:
        return None
    return int(raw)


def telegram_api_hash_from_env() -> str:
    return os.environ.get("TGVK_TELEGRAM_API_HASH", "").strip()


def resolve_telegram_credentials(api_id: int = 0, api_hash: str = "") -> tuple[int, str]:
    resolved_id = api_id or telegram_api_id_from_env() or 0
    resolved_hash = api_hash or telegram_api_hash_from_env()
    return resolved_id, resolved_hash
