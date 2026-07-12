from __future__ import annotations

import time

from telethon import TelegramClient
from telethon.sessions import StringSession

from tgvk.telegram_defaults import (
    TELEGRAM_DESKTOP_APP_VERSION,
    TELEGRAM_DESKTOP_DEVICE,
    TELEGRAM_DESKTOP_LANG,
    TELEGRAM_DESKTOP_SYSTEM,
    TELEGRAM_DESKTOP_SYSTEM_LANG,
    resolve_telegram_credentials,
)

_CACHE: dict[str, tuple[bool, float]] = {}
_CACHE_TTL = 60.0


async def is_telegram_session_valid(
    session: str,
    *,
    api_id: int = 0,
    api_hash: str = "",
    cache_key: str | None = None,
) -> bool:
    if not session:
        return False

    resolved_id, resolved_hash = resolve_telegram_credentials(api_id, api_hash)
    if not resolved_id or not resolved_hash:
        return False

    key = cache_key or session[:16]
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[1] < _CACHE_TTL:
        return cached[0]

    client = TelegramClient(
        StringSession(session),
        resolved_id,
        resolved_hash,
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
