"""Base class for line-oriented TCP protocol sessions (SMTP and POP3).

Handles what both protocols share: reading CRLF/LF terminated lines, the idle
timeout (180 s in the original), writing replies and closing the socket.
Subclasses implement `greeting()`, `handle(line)` and `on_timeout()`.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from .config import Settings
from .database import Database
from .resolver import DnsResolver

log = logging.getLogger(__name__)


class LineSession(ABC):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 settings: Settings, db: Database, resolver: DnsResolver):
        self.reader = reader
        self.writer = writer
        self.settings = settings
        self.db = db
        self.resolver = resolver
        self.peer_ip: str = writer.get_extra_info("peername")[0]
        self.closed = False

    # ---- hooks for subclasses ---------------------------------------------

    @abstractmethod
    async def greeting(self) -> None: ...

    @abstractmethod
    async def handle(self, line: str) -> None:
        """Process one command line."""

    @abstractmethod
    async def on_timeout(self) -> None:
        """Tell the client it idled out (connection is closed afterwards)."""

    # ---- shared machinery -------------------------------------------------

    async def run(self) -> None:
        log.info("%s connected from %s", type(self).__name__, self.peer_ip)
        try:
            await self.greeting()
            while not self.closed:
                line = await self.read_line()
                if line is None:
                    break
                await self.handle(line)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            log.exception("session with %s crashed", self.peer_ip)
        finally:
            await self.close()
            log.info("%s from %s closed", type(self).__name__, self.peer_ip)

    async def read_line(self) -> str | None:
        """Next line without its line ending; None on EOF, timeout or oversize line."""
        try:
            raw = await asyncio.wait_for(self.reader.readline(), self.settings.idle_timeout)
        except asyncio.TimeoutError:
            await self.on_timeout()
            return None
        except ValueError:  # line longer than the StreamReader limit (64 KiB)
            return None
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def write(self, text: str) -> None:
        if self.writer.is_closing():
            return
        self.writer.write(text.encode())
        await self.writer.drain()

    async def close(self) -> None:
        self.closed = True
        if not self.writer.is_closing():
            self.writer.close()
        try:
            await self.writer.wait_closed()
        except ConnectionError:
            pass
