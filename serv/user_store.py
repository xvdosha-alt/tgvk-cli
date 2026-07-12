from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from serv.crypto import decrypt_text, encrypt_text
from serv.settings import WEB_DB_PATH, harden_data_dir, user_messages_db
from tgvk.config import AppConfig


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _enc(value: str) -> str:
    return encrypt_text(value) if value else ""


def _dec(value: str | None) -> str:
    return decrypt_text(value or "")


@dataclass
class WebUser:
    id: str
    telegram_session: str
    tg_name: str
    tg_username: str | None
    vk_peer_id: int
    vk_token: str
    forwarding: bool
    img_mode: bool
    history_limit: int
    ignored_user_ids: list[int]
    ignored_usernames: list[str]
    ignored_chat_ids: list[int]
    ignore_groups: bool
    ignore_channels: bool
    otstuk: bool
    created_at: str
    updated_at: str

    @property
    def telegram_linked(self) -> bool:
        return bool(self.telegram_session)

    def to_app_config(self, default_vk_token: str) -> AppConfig:
        cfg = AppConfig(
            telegram_session=self.telegram_session,
            vk_peer_id=self.vk_peer_id,
            vk_token=self.vk_token or default_vk_token,
            img_mode=self.img_mode,
            history_limit=self.history_limit,
            ignored_user_ids=list(self.ignored_user_ids),
            ignored_usernames=list(self.ignored_usernames),
            ignored_chat_ids=list(self.ignored_chat_ids),
            ignore_groups=self.ignore_groups,
            ignore_channels=self.ignore_channels,
            otstuk=self.otstuk,
            forwarding=self.forwarding,
        )
        return cfg

    def public_dict(self, *, service_running: bool, default_vk_token: str) -> dict[str, Any]:
        has_custom_vk = bool(self.vk_token)
        ready, missing = self.to_app_config(default_vk_token).is_ready(default_vk_token)
        return {
            "telegram_linked": self.telegram_linked,
            "tg_name": self.tg_name,
            "tg_username": self.tg_username,
            "vk_peer_id": self.vk_peer_id,
            "has_custom_vk_token": has_custom_vk,
            "uses_default_vk_token": not has_custom_vk and bool(default_vk_token),
            "forwarding": self.forwarding,
            "img_mode": self.img_mode,
            "service_running": service_running,
            "ready": ready,
            "missing": missing,
        }


@dataclass
class PendingAuth:
    user_id: str
    phone: str
    telegram_session: str
    phone_code_hash: str
    needs_password: bool = False
    auth_mode: str = "phone"


def _row_to_user(row: aiosqlite.Row) -> WebUser:
    return WebUser(
        id=row["id"],
        telegram_session=_dec(row["telegram_session"]),
        tg_name=row["tg_name"] or "",
        tg_username=row["tg_username"],
        vk_peer_id=int(row["vk_peer_id"] or 0),
        vk_token=_dec(row["vk_token"]),
        forwarding=bool(row["forwarding"]),
        img_mode=bool(row["img_mode"]),
        history_limit=int(row["history_limit"] or 50),
        ignored_user_ids=[int(x) for x in _safe_json_list(row["ignored_user_ids"]) if str(x).lstrip("-").isdigit()],
        ignored_usernames=[str(x) for x in _safe_json_list(row["ignored_usernames"])],
        ignored_chat_ids=[int(x) for x in _safe_json_list(row["ignored_chat_ids"]) if str(x).lstrip("-").isdigit()],
        ignore_groups=bool(row["ignore_groups"]),
        ignore_channels=bool(row["ignore_channels"]),
        otstuk=bool(row["otstuk"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class UserStore:
    def __init__(self, db_path=WEB_DB_PATH) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        harden_data_dir()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    telegram_session TEXT NOT NULL DEFAULT '',
                    tg_name TEXT NOT NULL DEFAULT '',
                    tg_username TEXT,
                    vk_peer_id INTEGER NOT NULL DEFAULT 0,
                    vk_token TEXT NOT NULL DEFAULT '',
                    forwarding INTEGER NOT NULL DEFAULT 1,
                    img_mode INTEGER NOT NULL DEFAULT 0,
                    history_limit INTEGER NOT NULL DEFAULT 50,
                    ignored_user_ids TEXT NOT NULL DEFAULT '[]',
                    ignored_usernames TEXT NOT NULL DEFAULT '[]',
                    ignored_chat_ids TEXT NOT NULL DEFAULT '[]',
                    ignore_groups INTEGER NOT NULL DEFAULT 0,
                    ignore_channels INTEGER NOT NULL DEFAULT 0,
                    otstuk INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_auth (
                    user_id TEXT PRIMARY KEY,
                    phone TEXT NOT NULL,
                    telegram_session TEXT NOT NULL,
                    phone_code_hash TEXT NOT NULL,
                    needs_password INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
            try:
                await db.execute(
                    "ALTER TABLE pending_auth ADD COLUMN needs_password INTEGER NOT NULL DEFAULT 0"
                )
            except Exception:
                pass
            try:
                await db.execute(
                    "ALTER TABLE pending_auth ADD COLUMN auth_mode TEXT NOT NULL DEFAULT 'phone'"
                )
            except Exception:
                pass
            await db.commit()

    async def ensure_user(self, user_id: str) -> WebUser:
        user = await self.get_user(user_id)
        if user:
            return user
        now = _utcnow()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (id, created_at, updated_at)
                VALUES (?, ?, ?)
                """,
                (user_id, now, now),
            )
            await db.commit()
        user = await self.get_user(user_id)
        assert user is not None
        return user

    async def get_user(self, user_id: str) -> WebUser | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return _row_to_user(row)

    async def get_user_by_vk_peer_id(self, vk_peer_id: int) -> WebUser | None:
        if not vk_peer_id:
            return None
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE vk_peer_id = ? LIMIT 1",
                (vk_peer_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return _row_to_user(row)

    async def list_users_with_vk_peer_id(self) -> list[WebUser]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM users WHERE vk_peer_id > 0 ORDER BY updated_at DESC"
            )
            rows = await cursor.fetchall()
            return [_row_to_user(row) for row in rows]

    async def save_user(self, user: WebUser) -> None:
        user.updated_at = _utcnow()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE users SET
                    telegram_session = ?,
                    tg_name = ?,
                    tg_username = ?,
                    vk_peer_id = ?,
                    vk_token = ?,
                    forwarding = ?,
                    img_mode = ?,
                    history_limit = ?,
                    ignored_user_ids = ?,
                    ignored_usernames = ?,
                    ignored_chat_ids = ?,
                    ignore_groups = ?,
                    ignore_channels = ?,
                    otstuk = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    _enc(user.telegram_session),
                    user.tg_name,
                    user.tg_username,
                    user.vk_peer_id,
                    _enc(user.vk_token),
                    int(user.forwarding),
                    int(user.img_mode),
                    user.history_limit,
                    json.dumps(user.ignored_user_ids),
                    json.dumps(user.ignored_usernames),
                    json.dumps(user.ignored_chat_ids),
                    int(user.ignore_groups),
                    int(user.ignore_channels),
                    int(user.otstuk),
                    user.updated_at,
                    user.id,
                ),
            )
            await db.commit()

    async def save_from_config(self, user_id: str, cfg: AppConfig, *, custom_vk: str) -> WebUser:
        user = await self.get_user(user_id)
        if not user:
            raise KeyError(user_id)
        user.telegram_session = cfg.telegram_session
        user.vk_peer_id = cfg.vk_peer_id
        user.vk_token = custom_vk
        user.forwarding = cfg.forwarding
        user.img_mode = cfg.img_mode
        user.history_limit = cfg.history_limit
        user.ignored_user_ids = list(cfg.ignored_user_ids)
        user.ignored_usernames = list(cfg.ignored_usernames)
        user.ignored_chat_ids = list(cfg.ignored_chat_ids)
        user.ignore_groups = cfg.ignore_groups
        user.ignore_channels = cfg.ignore_channels
        user.otstuk = cfg.otstuk
        await self.save_user(user)
        return user

    async def set_telegram(
        self,
        user_id: str,
        *,
        session: str,
        name: str,
        username: str | None,
    ) -> WebUser:
        user = await self.ensure_user(user_id)
        user.telegram_session = session
        user.tg_name = name
        user.tg_username = username
        await self.save_user(user)
        return user

    async def wipe_sensitive(self, user_id: str) -> None:
        await self.delete_user(user_id)

    async def delete_user(self, user_id: str) -> None:
        await self.clear_pending(user_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            await db.commit()
        user_dir = user_messages_db(user_id).parent
        if user_dir.exists():
            shutil.rmtree(user_dir)

    async def reassign_user_id(self, old_id: str, new_id: str) -> None:
        user = await self.get_user(old_id)
        if not user:
            await self.ensure_user(new_id)
            return

        user.id = new_id
        now = _utcnow()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM users WHERE id = ?", (old_id,))
            await db.execute(
                """
                INSERT INTO users (
                    id, telegram_session, tg_name, tg_username, vk_peer_id, vk_token,
                    forwarding, img_mode, history_limit, ignored_user_ids, ignored_usernames,
                    ignored_chat_ids, ignore_groups, ignore_channels, otstuk, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id,
                    _enc(user.telegram_session),
                    user.tg_name,
                    user.tg_username,
                    user.vk_peer_id,
                    _enc(user.vk_token),
                    int(user.forwarding),
                    int(user.img_mode),
                    user.history_limit,
                    json.dumps(user.ignored_user_ids),
                    json.dumps(user.ignored_usernames),
                    json.dumps(user.ignored_chat_ids),
                    int(user.ignore_groups),
                    int(user.ignore_channels),
                    int(user.otstuk),
                    user.created_at or now,
                    now,
                ),
            )
            await db.execute(
                "UPDATE pending_auth SET user_id = ? WHERE user_id = ?",
                (new_id, old_id),
            )
            await db.commit()

        old_dir = user_messages_db(old_id).parent
        new_dir = user_messages_db(new_id).parent
        if old_dir.exists() and old_dir != new_dir:
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            if new_dir.exists():
                shutil.rmtree(new_dir)
            shutil.move(str(old_dir), str(new_dir))

    async def update_settings(
        self,
        user_id: str,
        *,
        vk_peer_id: int | None = None,
        vk_token: str | None = None,
        clear_vk_token: bool = False,
        forwarding: bool | None = None,
        img_mode: bool | None = None,
    ) -> WebUser:
        user = await self.ensure_user(user_id)
        if vk_peer_id is not None:
            user.vk_peer_id = vk_peer_id
        if clear_vk_token:
            user.vk_token = ""
        elif vk_token is not None:
            user.vk_token = vk_token.strip()
        if forwarding is not None:
            user.forwarding = forwarding
        if img_mode is not None:
            user.img_mode = img_mode
        await self.save_user(user)
        return user

    async def save_pending(self, pending: PendingAuth) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO pending_auth (user_id, phone, telegram_session, phone_code_hash, needs_password, auth_mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    phone = excluded.phone,
                    telegram_session = excluded.telegram_session,
                    phone_code_hash = excluded.phone_code_hash,
                    needs_password = excluded.needs_password,
                    auth_mode = excluded.auth_mode,
                    created_at = excluded.created_at
                """,
                (
                    pending.user_id,
                    _enc(pending.phone),
                    _enc(pending.telegram_session),
                    _enc(pending.phone_code_hash),
                    int(pending.needs_password),
                    pending.auth_mode,
                    _utcnow(),
                ),
            )
            await db.commit()

    async def get_pending(self, user_id: str) -> PendingAuth | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM pending_auth WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return PendingAuth(
                user_id=row["user_id"],
                phone=_dec(row["phone"]),
                telegram_session=_dec(row["telegram_session"]),
                phone_code_hash=_dec(row["phone_code_hash"]),
                needs_password=bool(row["needs_password"]),
                auth_mode=row["auth_mode"] if "auth_mode" in row.keys() else "phone",
            )

    async def set_pending_password(self, user_id: str, session: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE pending_auth
                SET telegram_session = ?, needs_password = 1
                WHERE user_id = ?
                """,
                (_enc(session), user_id),
            )
            await db.commit()

    async def clear_pending(self, user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM pending_auth WHERE user_id = ?", (user_id,))
            await db.commit()

    @staticmethod
    def new_user_id() -> str:
        return secrets.token_urlsafe(32)
