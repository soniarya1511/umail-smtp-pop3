"""In-memory view of a user's maildrop for one POP3 session.

Loaded at login; DELE only marks messages, RSET unmarks them and the marks
are written to the database when the client sends QUIT (RFC 1939 UPDATE
state). Message numbers are inbox row ids, so they stay stable across
sessions (the webmail relies on that).
"""

from __future__ import annotations

from ..database import Database, StoredMail


class Mailbox:
    def __init__(self, db: Database, user_id: int, mails: list[StoredMail]):
        self._db = db
        self._user_id = user_id
        self._mails = {m.id: m for m in mails}
        self._deleted: set[int] = set()

    @classmethod
    async def load(cls, db: Database, user_id: int) -> "Mailbox":
        return cls(db, user_id, await db.get_inbox(user_id))

    @staticmethod
    def render(mail: StoredMail) -> list[str]:
        return mail.to_message().to_lines()

    def size(self, mail: StoredMail) -> int:
        """Size in octets as sent on the wire (CRLF line endings)."""
        return sum(len(line.encode()) + 2 for line in self.render(mail))

    def get(self, number: int) -> StoredMail | None:
        return None if number in self._deleted else self._mails.get(number)

    def visible(self) -> list[StoredMail]:
        return [m for m in self._mails.values() if m.id not in self._deleted]

    def stat(self) -> tuple[int, int]:
        mails = self.visible()
        return len(mails), sum(self.size(m) for m in mails)

    def mark_deleted(self, number: int) -> bool:
        if self.get(number) is None:
            return False
        self._deleted.add(number)
        return True

    def reset(self) -> None:
        self._deleted.clear()

    async def commit(self) -> None:
        await self._db.delete_from_inbox(self._user_id, sorted(self._deleted))
