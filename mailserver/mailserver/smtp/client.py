"""Outbound SMTP client used for server-to-server traffic.

Two jobs from the original project:
  * `verify_address` - before accepting a remote RCPT TO, ask the remote MX
    with VRFY whether the mailbox exists.
  * `deliver` - the outbox worker hands a stored message to the remote MX.
"""

from __future__ import annotations

import asyncio
import logging

from ..message import Message
from ..resolver import LOOKUP_ERRORS, DnsResolver

log = logging.getLogger(__name__)


class SmtpError(Exception):
    """The remote server answered with an unexpected reply code."""


class SmtpClient:
    def __init__(self, helo_name: str, resolver: DnsResolver, port: int = 25, timeout: float = 30):
        self.helo_name = helo_name
        self.resolver = resolver
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    # ---- connection -------------------------------------------------------

    async def open(self, host: str) -> None:
        ip = (await self.resolver.a(host))[0]
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(ip, self.port), self.timeout)
        await self.expect(220)
        await self.command(f"HELO {self.helo_name}", 250)

    async def close(self) -> None:
        if self._writer is None:
            return
        try:
            await self.command("QUIT", 221)
        except (SmtpError, OSError, asyncio.TimeoutError):
            pass
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except OSError:
            pass

    # ---- protocol primitives ----------------------------------------------

    async def read_reply(self) -> tuple[int, str]:
        """Read a full reply, following 'code-text' continuation lines."""
        lines = []
        while True:
            raw = await asyncio.wait_for(self._reader.readline(), self.timeout)
            if not raw:
                raise SmtpError("connection closed by remote server")
            line = raw.decode(errors="replace").rstrip("\r\n")
            lines.append(line[4:])
            if line[3:4] != "-":
                return int(line[:3]), "\n".join(lines)

    async def expect(self, code: int) -> str:
        got, text = await self.read_reply()
        if got != code:
            raise SmtpError(f"expected {code}, got {got} {text}")
        return text

    async def command(self, line: str, code: int) -> str:
        self._writer.write(f"{line}\r\n".encode())
        await self._writer.drain()
        return await self.expect(code)

    # ---- high level operations --------------------------------------------

    async def verify_address(self, mx_host: str, address: str) -> bool:
        try:
            await self.open(mx_host)
            await self.command(f"VRFY {address}", 250)
            return True
        except (SmtpError, OSError, asyncio.TimeoutError, *LOOKUP_ERRORS) as exc:
            log.info("VRFY %s at %s failed: %s", address, mx_host, exc)
            return False
        finally:
            await self.close()

    async def deliver(self, mx_host: str, message: Message) -> None:
        """Send one message; raises SmtpError / OSError on failure."""
        try:
            await self.open(mx_host)
            await self.command(f"MAIL FROM:<{message.sender}>", 250)
            await self.command(f"RCPT TO:<{message.recipient}>", 250)
            await self.command("DATA", 354)
            stuffed = ("." + line if line.startswith(".") else line for line in message.to_lines())
            self._writer.write(("\r\n".join(stuffed) + "\r\n.\r\n").encode())
            await self._writer.drain()
            await self.expect(250)
        finally:
            await self.close()
