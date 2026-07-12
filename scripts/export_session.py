#!/usr/bin/env python3

from __future__ import annotations

import asyncio
import getpass
import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgvk.telegram_defaults import (
    TELEGRAM_DESKTOP_APP_VERSION,
    TELEGRAM_DESKTOP_DEVICE,
    TELEGRAM_DESKTOP_LANG,
    TELEGRAM_DESKTOP_SYSTEM,
    TELEGRAM_DESKTOP_SYSTEM_LANG,
    resolve_telegram_credentials,
    telegram_api_hash_from_env,
    telegram_api_id_from_env,
)


def _prompt(label: str, *, secret: bool = False, default: str = "") -> str:
    if default:
        label = f"{label} [{default}]"
    if secret:
        value = getpass.getpass(f"{label}: ")
    else:
        value = input(f"{label}: ").strip()
    return value or default


def _normalize_phone(phone: str) -> str:
    return "".join(phone.split())


async def main() -> None:
    env_id = telegram_api_id_from_env()
    env_hash = telegram_api_hash_from_env()
    api_id = env_id if env_id is not None else int(_prompt("Telegram api_id"))
    api_hash = env_hash or _prompt("Telegram api_hash")

    print("=== Telethon session string ===\n")

    phone = _normalize_phone(_prompt("Номер телефона (+79991234567)"))
    if not phone:
        print("Ошибка: укажи номер", file=sys.stderr)
        sys.exit(1)

    client = TelegramClient(
        StringSession(),
        api_id,
        api_hash,
        device_model=TELEGRAM_DESKTOP_DEVICE,
        system_version=TELEGRAM_DESKTOP_SYSTEM,
        app_version=TELEGRAM_DESKTOP_APP_VERSION,
        lang_code=TELEGRAM_DESKTOP_LANG,
        system_lang_code=TELEGRAM_DESKTOP_SYSTEM_LANG,
    )

    await client.connect()
    if not await client.is_user_authorized():
        await client.send_code_request(phone)
        print("\nКод пришёл в Telegram (или SMS).")

        for attempt in range(3):
            code = _prompt("Код из Telegram")
            try:
                await client.sign_in(phone, code)
                break
            except PhoneCodeInvalidError:
                print("Неверный код, попробуй ещё раз.")
            except PhoneCodeExpiredError:
                print("Код истёк. Запусти скрипт заново.")
                sys.exit(1)
            except SessionPasswordNeededError:
                password = _prompt("Пароль 2FA", secret=True)
                await client.sign_in(password=password)
                break
        else:
            print("Слишком много попыток.", file=sys.stderr)
            sys.exit(1)

    session_string = client.session.save()
    me = await client.get_me()
    name = " ".join(filter(None, [me.first_name, me.last_name])).strip()

    print("\n" + "=" * 50)
    print(f"Аккаунт: {name}" + (f" (@{me.username})" if me.username else ""))
    print("=" * 50)
    print("\nSession string (сохрани, больше никому не показывай):\n")
    print(session_string)
    print("\n" + "=" * 50)
    print("\nДля tgvk:")
    print(f'  tgvk config set telegram_session "{session_string}"')
    resolved_id, resolved_hash = resolve_telegram_credentials(api_id, api_hash)
    if resolved_id:
        print(f"  tgvk config set telegram_api_id {resolved_id}")
    if resolved_hash:
        print(f'  tgvk config set telegram_api_hash "{resolved_hash}"')

    await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nОтменено.")
        sys.exit(130)
