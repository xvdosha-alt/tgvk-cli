from __future__ import annotations

from typing import Any


CHAT_TYPE_LABELS = {
    "private": "личка",
    "group": "группа",
    "supergroup": "супергруппа",
    "channel": "канал",
    "unknown": "чат",
}


def chat_type_label(chat_type: str) -> str:
    return CHAT_TYPE_LABELS.get(chat_type, chat_type)


def format_message_for_vk(msg: dict[str, Any], *, attach_media: bool = False) -> str:
    username = msg.get("sender_username")
    uname = f" (@{username})" if username else ""
    chat_type = chat_type_label(msg.get("tg_chat_type", "unknown"))
    media = ""
    if msg.get("has_media") and not attach_media:
        media = f" [{msg.get('media_type', 'медиа')}]"

    text = msg.get("text") or ""
    if text.startswith("[") and text.endswith("]") and attach_media:
        text = ""

    sender_id = msg.get("sender_id", 0)
    chat_id = msg.get("tg_chat_id", 0)

    lines = [
        f"📩 {msg['sender_name']}{uname}",
        f"🆔 user:{sender_id} · chat:{chat_id}",
        f"📍 {msg['tg_chat_title']} · {chat_type}",
        "—" * 20,
    ]
    body = f"{text}{media}".strip()
    if body:
        lines.append(body)
    return "\n".join(lines)


def format_history_list(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "История пуста."

    lines = ["📋 Последние сообщения:\n"]
    for i, msg in enumerate(reversed(messages), 1):
        username = msg.get("sender_username")
        uname = f" @{username}" if username else ""
        chat_type = chat_type_label(msg.get("tg_chat_type", "unknown"))
        text = (msg["text"] or "")[:120]
        if len(msg["text"] or "") > 120:
            text += "…"
        sender_id = msg.get("sender_id", 0)
        chat_id = msg.get("tg_chat_id", 0)
        lines.append(
            f"{i}. [{msg['tg_chat_title']} · {chat_type}]\n"
            f"   {msg['sender_name']}{uname} (user:{sender_id}, chat:{chat_id})\n"
            f"   {text}"
        )
    return "\n".join(lines)


def format_chats_list(chats: list[dict[str, Any]]) -> str:
    if not chats:
        return "Чатов пока нет."

    lines = ["💬 Недавние чаты:\n"]
    for i, chat in enumerate(chats, 1):
        chat_type = chat_type_label(chat.get("tg_chat_type", "unknown"))
        lines.append(
            f"{i}. {chat['tg_chat_title']} ({chat_type})\n"
            f"   id: {chat['tg_chat_id']} · {chat['msg_count']} сообщ."
        )
    lines.append("\nДля истории: ист <id> [кол-во]")
    lines.append("Для картинки: img <id> [кол-во]")
    lines.append("Игнор группы: игнорчат <id>")
    return "\n".join(lines)
