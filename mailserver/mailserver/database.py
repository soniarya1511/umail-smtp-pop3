"""PostgreSQL access layer.

`Database` owns an asyncpg connection pool and exposes one method per query
the servers need, so no SQL lives in the protocol code and every value is
passed as a bound parameter (the original built SQL with f-strings).

Schema: see infra/db/schema.sql.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg
import bcrypt

from .message import Message

log = logging.getLogger(__name__)

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class User:
    id: int
    email: str
    password_hash: str

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(password.encode(), self.password_hash.encode())


@dataclass(frozen=True)
class StoredMail:
    """One message in a user's inbox, as POP3 sees it."""
    id: int            # inbox.id, used as the POP3 message number
    sender: str
    recipient: str
    subject: str
    body: str
    timestamp: datetime

    def to_message(self) -> Message:
        return Message(self.subject, self.sender, self.recipient,
                       self.timestamp.strftime(TIMESTAMP_FORMAT), self.body)


@dataclass(frozen=True)
class PendingDelivery:
    """An outbox row waiting to be relayed to a remote server."""
    outbox_id: int
    email_id: int
    retry_count: int
    message: Message


class Database:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self, attempts: int = 30) -> None:
        # The DB container may still be starting; retry instead of crashing.
        for attempt in range(1, attempts + 1):
            try:
                self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
                return
            except (OSError, asyncpg.PostgresError) as exc:
                log.warning("database not ready (%s), retry %d/%d", exc, attempt, attempts)
                await asyncio.sleep(1)
        raise RuntimeError("could not connect to the database")

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool, "Database.connect() was not called"
        return self._pool

    # ---- users / trusted clients ------------------------------------------

    async def get_user(self, email: str) -> User | None:
        row = await self.pool.fetchrow(
            "SELECT id, email, password_hash FROM users WHERE lower(email) = lower($1)", email)
        return User(**row) if row else None

    async def is_trusted_mua(self, name: str) -> bool:
        return await self.pool.fetchval("SELECT 1 FROM mua WHERE name = $1", name) is not None

    # ---- SMTP: store a submitted message ----------------------------------

    async def store_message(self, msg: Message, sender_id: int | None,
                            local_recipient_id: int | None) -> int:
        """Save a message and queue it.

        Local recipients get an inbox row straight away (outbox row marked
        'sent'); remote recipients get a 'pending' outbox row for the worker.
        Returns the new email id.
        """
        async with self.pool.acquire() as con, con.transaction():
            email_id = await con.fetchval(
                """INSERT INTO emails (subject, body, sender_id, sender, recipient)
                   VALUES ($1, $2, $3, $4, $5) RETURNING id""",
                msg.subject, msg.body, sender_id, msg.sender, msg.recipient)
            if local_recipient_id is not None:
                await con.execute(
                    "INSERT INTO inbox (email_id, recipient_id) VALUES ($1, $2)",
                    email_id, local_recipient_id)
                status = "sent"
            else:
                status = "pending"
            await con.execute(
                "INSERT INTO outbox (email_id, status) VALUES ($1, $2)", email_id, status)
        return email_id

    # ---- POP3 -------------------------------------------------------------

    async def get_inbox(self, user_id: int) -> list[StoredMail]:
        rows = await self.pool.fetch(
            """SELECT inbox.id, e.sender, e.recipient, e.subject, e.body, e.timestamp
               FROM inbox JOIN emails e ON e.id = inbox.email_id
               WHERE inbox.recipient_id = $1 AND NOT inbox.is_deleted
               ORDER BY e.timestamp DESC""", user_id)
        return [StoredMail(*row) for row in rows]

    async def delete_from_inbox(self, user_id: int, inbox_ids: list[int]) -> None:
        if inbox_ids:
            await self.pool.execute(
                "UPDATE inbox SET is_deleted = TRUE WHERE recipient_id = $1 AND id = ANY($2::int[])",
                user_id, inbox_ids)

    # ---- outbox worker ----------------------------------------------------

    async def pending_deliveries(self, max_retries: int) -> list[PendingDelivery]:
        rows = await self.pool.fetch(
            """SELECT o.id, e.id, o.retry_count, e.subject, e.sender, e.recipient, e.timestamp, e.body
               FROM outbox o JOIN emails e ON e.id = o.email_id
               WHERE o.status IN ('pending', 'failed') AND o.retry_count < $1
               ORDER BY o.last_attempt ASC NULLS FIRST""", max_retries)
        return [
            PendingDelivery(r[0], r[1], r[2],
                            Message(r[3], r[4], r[5], r[6].strftime(TIMESTAMP_FORMAT), r[7]))
            for r in rows
        ]

    async def mark_delivery(self, outbox_id: int, success: bool) -> None:
        if success:
            await self.pool.execute(
                "UPDATE outbox SET status = 'sent', last_attempt = now() WHERE id = $1", outbox_id)
        else:
            await self.pool.execute(
                """UPDATE outbox SET status = 'failed', retry_count = retry_count + 1,
                   last_attempt = now() WHERE id = $1""", outbox_id)
