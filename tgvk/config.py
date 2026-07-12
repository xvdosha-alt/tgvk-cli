from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_validator

from tgvk.telegram_defaults import TELEGRAM_DESKTOP_API_HASH, TELEGRAM_DESKTOP_API_ID

CONFIG_DIR = Path.home() / ".config" / "tgvk"
CONFIG_FILE = CONFIG_DIR / "config.json"


class AppConfig(BaseModel):
    telegram_session: str = ""
    telegram_api_id: int = TELEGRAM_DESKTOP_API_ID
    telegram_api_hash: str = TELEGRAM_DESKTOP_API_HASH
    vk_token: str = ""
    vk_peer_id: int = 0
    img_mode: bool = False
    history_limit: int = 50
    ignored_user_ids: list[int] = []
    ignored_usernames: list[str] = []
    ignored_chat_ids: list[int] = []
    ignore_groups: bool = False
    ignore_channels: bool = False
    otstuk: bool = False
    forwarding: bool = True

    @model_validator(mode="after")
    def _apply_telegram_defaults(self) -> AppConfig:
        wrong_hash = "b18441a1ff607e10a989921a7cbcc0"
        if not self.telegram_api_id:
            self.telegram_api_id = TELEGRAM_DESKTOP_API_ID
        if not self.telegram_api_hash or self.telegram_api_hash == wrong_hash:
            self.telegram_api_hash = TELEGRAM_DESKTOP_API_HASH
        return self

    @classmethod
    def load(cls) -> AppConfig:
        if not CONFIG_FILE.exists():
            return cls()
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            self.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def resolved_vk_token(self, default: str = "") -> str:
        return self.vk_token or default

    def is_ready(self, default_vk_token: str = "") -> tuple[bool, list[str]]:
        missing: list[str] = []
        if not self.telegram_session:
            missing.append("telegram_session")
        if not self.resolved_vk_token(default_vk_token):
            missing.append("vk_token")
        if not self.vk_peer_id:
            missing.append("vk_peer_id")
        return len(missing) == 0, missing

    def set_field(self, key: str, value: Any) -> None:
        if key not in self.model_fields:
            raise KeyError(f"Неизвестный параметр: {key}")
        setattr(self, key, value)
        self.save()

    def is_ignored(self, sender_id: int, sender_username: str | None) -> bool:
        if sender_id and sender_id in self.ignored_user_ids:
            return True
        if sender_username:
            uname = sender_username.lstrip("@").lower()
            if uname in self.ignored_usernames:
                return True
        return False

    def is_chat_ignored(self, chat_id: int) -> bool:
        return chat_id in self.ignored_chat_ids

    def is_group_chat_type(self, chat_type: str) -> bool:
        return chat_type in ("group", "supergroup")

    def is_channel_chat_type(self, chat_type: str) -> bool:
        return chat_type == "channel"

    def should_skip_message(
        self,
        *,
        chat_id: int,
        chat_type: str,
        sender_id: int,
        sender_username: str | None,
    ) -> bool:
        if self.ignore_groups and self.is_group_chat_type(chat_type):
            return True
        if self.ignore_channels and self.is_channel_chat_type(chat_type):
            return True
        if self.is_chat_ignored(chat_id):
            return True
        return self.is_ignored(sender_id, sender_username)

    def add_chat_ignore(self, chat_id: int) -> None:
        if chat_id not in self.ignored_chat_ids:
            self.ignored_chat_ids.append(chat_id)
            self.save()

    def remove_chat_ignore(self, chat_id: int) -> bool:
        if chat_id in self.ignored_chat_ids:
            self.ignored_chat_ids.remove(chat_id)
            self.save()
            return True
        return False

    def add_ignore(self, *, user_id: int | None = None, username: str | None = None) -> None:
        if user_id and user_id not in self.ignored_user_ids:
            self.ignored_user_ids.append(user_id)
        if username:
            uname = username.lstrip("@").lower()
            if uname and uname not in self.ignored_usernames:
                self.ignored_usernames.append(uname)
        self.save()

    def remove_ignore(self, *, user_id: int | None = None, username: str | None = None) -> bool:
        removed = False
        if user_id and user_id in self.ignored_user_ids:
            self.ignored_user_ids.remove(user_id)
            removed = True
        if username:
            uname = username.lstrip("@").lower()
            if uname in self.ignored_usernames:
                self.ignored_usernames.remove(uname)
                removed = True
        if removed:
            self.save()
        return removed

    def format_ignored_list(self) -> str:
        if (
            not self.ignored_user_ids
            and not self.ignored_usernames
            and not self.ignored_chat_ids
        ):
            return "Список игнора пуст."

        lines = ["🚫 Игнор пользователей:\n"]
        if not self.ignored_user_ids and not self.ignored_usernames:
            lines.append("  (пусто)")
        for uid in self.ignored_user_ids:
            lines.append(f"  · user:{uid}")
        for uname in self.ignored_usernames:
            lines.append(f"  · @{uname}")

        lines.append("\n🚫 Игнор чатов/групп:\n")
        lines.append(
            f"  · все группы/супергруппы: {'вкл' if self.ignore_groups else 'выкл'}"
        )
        lines.append(
            f"  · все каналы: {'вкл' if self.ignore_channels else 'выкл'}"
        )
        if not self.ignored_chat_ids:
            lines.append("  · отдельные чаты: (пусто)")
        for cid in self.ignored_chat_ids:
            lines.append(f"  · chat:{cid}")

        lines.append("\nСнять чат: анигнорчат <chat_id>")
        lines.append("Все группы: групп+ / групп-")
        lines.append("Все каналы: канал+ / канал-")
        return "\n".join(lines)

    def format_ignored_chats_list(self, chats: list[dict[str, Any]] | None = None) -> str:
        if not self.ignored_chat_ids:
            return "Игнор чатов пуст.\nДобавить: игнорчат <chat_id>"

        titles = {int(c["tg_chat_id"]): c["tg_chat_title"] for c in (chats or [])}
        lines = ["🚫 Игнор чатов/групп:\n"]
        for cid in self.ignored_chat_ids:
            title = titles.get(cid)
            label = f"{title} · " if title else ""
            lines.append(f"  · {label}chat:{cid}")
        lines.append("\nСнять: анигнорчат <chat_id>")
        return "\n".join(lines)


class RuntimeState(BaseModel):
    img_mode: bool = False
    running: bool = False
    messages_forwarded: int = 0
    last_error: str = ""
