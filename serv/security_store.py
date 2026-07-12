from __future__ import annotations

import json
import secrets
import time
from datetime import datetime, timedelta, timezone

import aiosqlite
from fastapi import HTTPException

from serv.settings import SESSION_IDLE_TIMEOUT, WEB_DB_PATH

PENDING_TTL = timedelta(hours=24)
AUTH_LOCK_AFTER = 5
AUTH_LOCK_MINUTES = 15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


class SecurityStore:
    def __init__(self, db_path=WEB_DB_PATH) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS csrf_tokens (
                    user_id TEXT PRIMARY KEY,
                    token TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key TEXT PRIMARY KEY,
                    hits TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_locks (
                    key TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS session_activity (
                    user_id TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL
                )
                """
            )
            await db.commit()

    async def _begin_immediate(self, db: aiosqlite.Connection) -> None:
        await db.execute("BEGIN IMMEDIATE")

    async def get_csrf(self, user_id: str) -> str | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT token FROM csrf_tokens WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
            return row[0] if row else None

    async def set_csrf(self, user_id: str, token: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO csrf_tokens (user_id, token) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET token = excluded.token
                """,
                (user_id, token),
            )
            await db.commit()

    async def delete_csrf(self, user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM csrf_tokens WHERE user_id = ?", (user_id,))
            await db.commit()

    async def migrate_csrf(self, old_user_id: str, new_user_id: str) -> None:
        token = await self.get_csrf(old_user_id)
        if token:
            await self.set_csrf(new_user_id, token)
        await self.delete_csrf(old_user_id)

    async def issue_csrf(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        await self.set_csrf(user_id, token)
        return token

    async def get_or_issue_csrf(self, user_id: str) -> str:
        token = await self.get_csrf(user_id)
        if token:
            return token
        return await self.issue_csrf(user_id)

    async def verify_csrf(self, user_id: str, token: str | None) -> bool:
        if not token:
            return False
        expected = await self.get_csrf(user_id)
        if not expected:
            return False
        return secrets.compare_digest(expected, token)

    async def rate_limit_allow(self, key: str, max_calls: int, window_sec: int) -> bool:
        now = time.time()
        window_start = now - window_sec
        async with aiosqlite.connect(self.db_path) as db:
            await self._begin_immediate(db)
            try:
                cursor = await db.execute(
                    "SELECT hits FROM rate_limits WHERE key = ?",
                    (key,),
                )
                row = await cursor.fetchone()
                hits: list[float] = []
                if row:
                    try:
                        hits = [float(v) for v in json.loads(row[0])]
                    except (json.JSONDecodeError, TypeError, ValueError):
                        hits = []
                hits = [t for t in hits if t > window_start]
                allowed = len(hits) < max_calls
                if allowed:
                    hits.append(now)
                await db.execute(
                    "INSERT OR REPLACE INTO rate_limits (key, hits) VALUES (?, ?)",
                    (key, json.dumps(hits)),
                )
                await db.commit()
                return allowed
            except Exception:
                await db.rollback()
                raise

    async def rate_limit_check(self, key: str, max_calls: int, window_sec: int) -> None:
        if not await self.rate_limit_allow(key, max_calls, window_sec):
            raise HTTPException(
                status_code=429,
                detail="Слишком много запросов. Подожди немного.",
            )

    async def touch_session(self, user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO session_activity (user_id, last_seen) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen
                """,
                (user_id, _iso(_utcnow())),
            )
            await db.commit()

    async def migrate_session_activity(self, old_user_id: str, new_user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT last_seen FROM session_activity WHERE user_id = ?",
                (old_user_id,),
            )
            row = await cursor.fetchone()
            if row:
                await db.execute(
                    """
                    INSERT INTO session_activity (user_id, last_seen) VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET last_seen = excluded.last_seen
                    """,
                    (new_user_id, row[0]),
                )
            await db.execute("DELETE FROM session_activity WHERE user_id = ?", (old_user_id,))
            await db.commit()

    async def delete_session_activity(self, user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM session_activity WHERE user_id = ?", (user_id,))
            await db.commit()

    async def require_fresh_session(self, user_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT last_seen FROM session_activity WHERE user_id = ?",
                (user_id,),
            )
            row = await cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=401,
                detail="Сессия устарела. Обнови страницу.",
            )
        try:
            last_seen = datetime.fromisoformat(row[0])
        except ValueError:
            raise HTTPException(
                status_code=401,
                detail="Сессия устарела. Обнови страницу.",
            ) from None
        if (_utcnow() - last_seen).total_seconds() > SESSION_IDLE_TIMEOUT:
            raise HTTPException(
                status_code=401,
                detail="Сессия устарела из-за неактивности. Обнови страницу.",
            )

    async def is_auth_locked(self, key: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT locked_until FROM auth_locks WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if not row or not row[0]:
                return False
            try:
                locked_until = datetime.fromisoformat(row[0])
            except ValueError:
                return False
            if locked_until > _utcnow():
                return True
            await self._begin_immediate(db)
            try:
                await db.execute(
                    "UPDATE auth_locks SET failures = 0, locked_until = NULL WHERE key = ?",
                    (key,),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise
            return False

    async def record_auth_failure(self, key: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await self._begin_immediate(db)
            try:
                cursor = await db.execute(
                    "SELECT failures FROM auth_locks WHERE key = ?",
                    (key,),
                )
                row = await cursor.fetchone()
                failures = int(row[0]) + 1 if row else 1
                locked_until = None
                if failures >= AUTH_LOCK_AFTER:
                    locked_until = _iso(_utcnow() + timedelta(minutes=AUTH_LOCK_MINUTES))
                    failures = 0
                await db.execute(
                    """
                    INSERT INTO auth_locks (key, failures, locked_until)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        failures = excluded.failures,
                        locked_until = excluded.locked_until
                    """,
                    (key, failures, locked_until),
                )
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    async def clear_auth_failures(self, key: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM auth_locks WHERE key = ?", (key,))
            await db.commit()

    async def clear_user_security(self, user_id: str) -> None:
        await self.delete_csrf(user_id)
        await self.delete_session_activity(user_id)
        await self.clear_auth_failures(f"user:{user_id}")

    async def cleanup_expired_pending(self) -> int:
        cutoff = _iso(_utcnow() - PENDING_TTL)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM pending_auth WHERE created_at < ?",
                (cutoff,),
            )
            await db.commit()
            return cursor.rowcount or 0
