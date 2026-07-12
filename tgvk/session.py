from __future__ import annotations

import time

from telethon import TelegramClient
from telethon.sessions import StringSession

from tgvk.telegram_defaults import (
    TELEGRAM_DESKTOP_API_HASH,
    TELEGRAM_DESKTOP_API_ID,
    TELEGRAM_DESKTOP_APP_VERSION,
    TELEGRAM_DESKTOP_DEVICE,
    TELEGRAM_DESKTOP_LANG,
    TELEGRAM_DESKTOP_SYSTEM,
    TELEGRAM_DESKTOP_SYSTEM_LANG,
)

_CACHE: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 60.0


async def is_telegram_session_valid(session: str, *, cache_key: str | None = None) -> bool:
    if not session:
        return False

    key = cache_key or session[:16]
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    client = TelegramClient(
        StringSession(session),
        TELEGRAM_DESKTOP_API_ID,
        TELEGRAM_DESKTOP_API_HASH,
        device_model=TELEGRAM_DESKTOP_DEVICE,
        system_version=TELEGRAM_DESKTOP_SYSTEM,
        app_version=TELEGRAM_DESKTOP_APP_VERSION,
        lang_code=TELEGRAM_DESKTOP_LANG,
        system_lang_code=TELEGRAM_DESKTOP_SYSTEM_LANG,
    )
    ok = False
    try:
        await client.connect()
        ok = await client.is_user_authorized()
    except Exception:
        ok = False
    finally:
        await client.disconnect()

    _CACHE[key] = (ok, now)
    return ok


def invalidate_session_cache(cache_key: str) -> None:
    _CACHE.pop(cache_key, None)
