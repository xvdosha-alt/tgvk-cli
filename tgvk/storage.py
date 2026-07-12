from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from tgvk.config import CONFIG_DIR

DB_PATH = CONFIG_DIR / "messages.db"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class MessageStore:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_message_id INTEGER NOT NULL,
                    tg_chat_id INTEGER NOT NULL,
                    tg_chat_title TEXT NOT NULL,
                    tg_chat_type TEXT NOT NULL,
                    sender_id INTEGER NOT NULL,
                    sender_name TEXT NOT NULL,
                    sender_username TEXT,
                    text TEXT NOT NULL,
                    has_media INTEGER NOT NULL DEFAULT 0,
                    media_type TEXT,
                    raw_meta TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at DESC)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(tg_chat_id, created_at DESC)"
            )
            await db.commit()

    async def save_message(
        self,
        *,
        tg_message_id: int,
        tg_chat_id: int,
        tg_chat_title: str,
        tg_chat_type: str,
        sender_id: int,
        sender_name: str,
        sender_username: str | None,
        text: str,
        has_media: bool = False,
        media_type: str | None = None,
        raw_meta: dict[str, Any] | None = None,
    ) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO messages (
                    tg_message_id, tg_chat_id, tg_chat_title, tg_chat_type,
                    sender_id, sender_name, sender_username, text,
                    has_media, media_type, raw_meta, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tg_message_id,
                    tg_chat_id,
                    tg_chat_title,
                    tg_chat_type,
                    sender_id,
                    sender_name,
                    sender_username,
                    text,
                    int(has_media),
                    media_type,
                    json.dumps(raw_meta or {}, ensure_ascii=False),
                    _utcnow(),
                ),
            )
            await db.commit()
            return cursor.lastrowid or 0

    async def get_recent(self, limit: int = 20, chat_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM messages"
        params: list[Any] = []
        if chat_id is not None:
            query += " WHERE tg_chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def get_chats(self, limit: int = 15) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT tg_chat_id, tg_chat_title, tg_chat_type,
                       MAX(created_at) AS last_at,
                       COUNT(*) AS msg_count
                FROM messages
                GROUP BY tg_chat_id
                ORDER BY last_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM messages")
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def get_last_from_sender(
        self,
        *,
        sender_id: int | None = None,
        username: str | None = None,
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if sender_id:
                cursor = await db.execute(
                    """
                    SELECT * FROM messages
                    WHERE sender_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (sender_id,),
                )
                row = await cursor.fetchone()
                if row:
                    return dict(row)
            if username:
                uname = username.lstrip("@").lower()
                cursor = await db.execute(
                    """
                    SELECT * FROM messages
                    WHERE LOWER(sender_username) = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (uname,),
                )
                row = await cursor.fetchone()
                if row:
                    return dict(row)
        return None

    async def find_sender_ids_by_username(self, username: str) -> list[int]:
        uname = username.lstrip("@").lower()
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT DISTINCT sender_id FROM messages
                WHERE LOWER(sender_username) = ? AND sender_id != 0
                """,
                (uname,),
            )
            rows = await cursor.fetchall()
            return [int(row[0]) for row in rows]
