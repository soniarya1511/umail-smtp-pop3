"""Wires the protocol sessions to TCP listeners.

One image serves two roles so each can run in its own container:
  smtp  -> submission listener (578), relay listener (25) and the outbox worker
  pop3  -> POP3 listener (995)
"""

from __future__ import annotations

import asyncio
import logging

from .config import Settings
from .database import Database
from .pop3.session import Pop3Session
from .resolver import DnsResolver
from .session import LineSession
from .smtp.outbox import OutboxWorker
from .smtp.session import RelaySession, SubmissionSession

log = logging.getLogger(__name__)

ROLES = ("smtp", "pop3")


class MailServer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_url)
        self.resolver = DnsResolver(settings.dns_server)

    async def listen(self, session_cls: type[LineSession], port: int) -> asyncio.Server:
        async def on_connect(reader, writer):
            await session_cls(reader, writer, self.settings, self.db, self.resolver).run()

        server = await asyncio.start_server(on_connect, "0.0.0.0", port)
        log.info("%s listening on port %d", session_cls.__name__, port)
        return server

    async def run(self, roles: set[str]) -> None:
        await self.db.connect()
        s = self.settings
        tasks = []
        if "smtp" in roles:
            for cls, port in ((SubmissionSession, s.submission_port), (RelaySession, s.relay_port)):
                tasks.append((await self.listen(cls, port)).serve_forever())
            tasks.append(OutboxWorker(s, self.db, self.resolver).run_forever())
        if "pop3" in roles:
            tasks.append((await self.listen(Pop3Session, s.pop3_port)).serve_forever())
        log.info("%s ready for %s (roles: %s)", s.hostname, s.domain, ", ".join(sorted(roles)))
        try:
            await asyncio.gather(*tasks)
        finally:
            await self.db.close()
