"""SMTP server sessions.

`SubmissionSession` (port 578) serves end users and trusted MUAs (webmail).
`RelaySession` (port 25) serves other mail servers and only differs in how a
peer is trusted and which addresses it may use, so it overrides three hooks.

Command flow (enforced, otherwise `503 Bad sequence`):

    HELO -> [AUTH PLAIN] -> MAIL FROM -> RCPT TO -> DATA ... "." -> (MAIL FROM ...)

A HELO from a name listed in the `mua` table whose A and PTR records match the
peer IP is authenticated immediately (reply 235). Everyone else must use AUTH
PLAIN. One recipient per message, as in the original design.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass

from publicsuffix2 import get_sld

from ..database import User
from ..message import Message
from ..session import LineSession
from .client import SmtpClient

log = logging.getLogger(__name__)

ADDRESS_RE = re.compile(r"^(?:MAIL\s+FROM|RCPT\s+TO)\s*:\s*<?([^<>\s]+@[^<>\s]+)>?", re.I)

HELP_LINES = [
    "Available SMTP commands:",
    " HELO <domain>      Greet the server",
    " AUTH PLAIN         Authenticate, base64 of \\0username\\0password",
    " MAIL FROM:<addr>   Start a new mail transaction",
    " RCPT TO:<addr>     Recipient of the email",
    " DATA               Send the message, end with a line holding only '.'",
    " RSET               Abort the current mail transaction",
    " NOOP               No operation (keeps the connection alive)",
    " VRFY <addr>        Check that a local address exists",
    " HELP               Show this help",
    " QUIT               Close the connection",
]


@dataclass
class Transaction:
    """State of one MAIL FROM ... DATA exchange."""
    sender: str | None = None
    sender_id: int | None = None
    recipient: str | None = None
    recipient_id: int | None = None  # set for local recipients only


class SubmissionSession(LineSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_name = ""
        self.helo_done = False
        self.authenticated = False
        self.trusted_mua = False
        self.user: User | None = None
        self.tx = Transaction()
        self.commands = {
            "HELO": self.cmd_helo, "AUTH": self.cmd_auth, "MAIL": self.cmd_mail,
            "RCPT": self.cmd_rcpt, "DATA": self.cmd_data, "RSET": self.cmd_rset,
            "VRFY": self.cmd_vrfy, "NOOP": self.cmd_noop, "HELP": self.cmd_help,
            "QUIT": self.cmd_quit,
        }

    # ---- replies ----------------------------------------------------------

    async def reply(self, code: int, *lines: str) -> None:
        """Send a reply; several lines use the RFC 5321 'code-text' continuation."""
        last = len(lines) - 1
        await self.write("".join(
            f"{code}{' ' if i == last else '-'}{text}\r\n" for i, text in enumerate(lines)))

    async def greeting(self) -> None:
        await self.reply(220, f"{self.settings.hostname} ESMTP umail")

    async def on_timeout(self) -> None:
        await self.reply(221, "Connection timeout")

    # ---- dispatch ---------------------------------------------------------

    # Commands that work before HELO / authentication.
    OPEN_COMMANDS = {"HELO", "NOOP", "HELP", "QUIT"}

    async def handle(self, line: str) -> None:
        verb, _, arg = line.strip().partition(" ")
        verb = verb.upper()
        handler = self.commands.get(verb)
        if handler is None:
            await self.reply(500, "Command not recognized", "Use HELP to see manuals")
        elif verb not in self.OPEN_COMMANDS and not self.helo_done:
            await self.reply(503, "Initial handshake not done", "Use HELO <domain> first")
        elif verb not in self.OPEN_COMMANDS | {"AUTH"} and not self.authenticated:
            await self.reply(503, "Client not authorized", "Use AUTH PLAIN to log in")
        else:
            await handler(arg.strip(), line.strip())

    async def bad_sequence(self) -> None:
        await self.reply(503, "Bad sequence of commands", "Use HELP for manuals")

    # ---- trust hooks (overridden by RelaySession) --------------------------

    async def verify_helo(self, name: str) -> None:
        if await self.db.is_trusted_mua(name):
            if not await self.resolver.host_matches_ip(name, self.peer_ip):
                await self.reply(535, "MUA authentication failed")
                return
            self.helo_done = self.authenticated = self.trusted_mua = True
            await self.reply(235, "MUA authentication successful",
                             f"{self.settings.hostname} Hello {name}")
            return
        self.helo_done = True
        await self.reply(250, f"{self.settings.hostname} Hello {name}", "AUTH PLAIN")

    async def accept_sender(self, address: str) -> bool:
        """Users may only send as themselves; a trusted MUA as any local user."""
        if self.trusted_mua:
            user = await self.db.get_user(address) if self.settings.is_local(address) else None
            if user is None:
                await self.reply(551, "Sender not found")
                return False
            self.tx.sender_id = user.id
        else:
            if address.lower() != self.user.email.lower():
                await self.reply(550, "Sender address rejected: can only send mail from the logged in address")
                return False
            self.tx.sender_id = self.user.id
        self.tx.sender = address
        return True

    async def accept_recipient(self, address: str) -> bool:
        if self.settings.is_local(address):
            return await self.accept_local_recipient(address)
        # Remote: find the MX and ask it with VRFY before accepting.
        domain = address.rpartition("@")[2]
        mx_host = await self.resolver.mx(domain)
        if mx_host is None:
            await self.reply(551, "MX record not found")
            return False
        client = SmtpClient(self.settings.hostname, self.resolver, self.settings.relay_port)
        if not await client.verify_address(mx_host, address):
            await self.reply(551, "Email not found")
            return False
        self.tx.recipient = address
        await self.reply(250, f"User {address} exists")
        return True

    async def accept_local_recipient(self, address: str) -> bool:
        user = await self.db.get_user(address)
        if user is None:
            await self.reply(551, "Email not found")
            return False
        self.tx.recipient, self.tx.recipient_id = address, user.id
        await self.reply(250, f"User {address} exists")
        return True

    # ---- commands ---------------------------------------------------------

    async def cmd_helo(self, arg: str, line: str) -> None:
        if self.helo_done:
            await self.reply(503, "Handshake already done", "Use HELP for manuals")
        elif not arg or " " in arg:
            await self.reply(501, "Syntax: HELO <domain>")
        else:
            self.client_name = arg
            await self.verify_helo(arg)

    async def cmd_auth(self, arg: str, line: str) -> None:
        mechanism, _, initial = arg.partition(" ")
        if self.authenticated:
            await self.reply(503, "Authentication already done")
            return
        if mechanism.upper() != "PLAIN":
            await self.reply(504, "Only AUTH PLAIN is supported")
            return
        if not initial:
            await self.write("334 \r\n")
            initial = await self.read_line() or ""
        try:
            _, username, password = base64.b64decode(initial, validate=True).decode().split("\x00")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            await self.reply(535, "Authentication failed", "Expected base64 of \\0username\\0password")
            return
        user = await self.db.get_user(username) if self.settings.is_local(username) else None
        if user is None or not user.check_password(password):
            await self.reply(535, "Authentication failed: invalid username or password")
            return
        self.authenticated, self.user = True, user
        await self.reply(235, "Authentication successful")

    async def cmd_mail(self, arg: str, line: str) -> None:
        match = ADDRESS_RE.match(line)
        if not match or not line.upper().startswith("MAIL FROM"):
            await self.reply(501, "Syntax: MAIL FROM:<address>")
        elif self.tx.sender is not None:
            await self.bad_sequence()
        elif await self.accept_sender(match.group(1)):
            await self.reply(250, "OK")

    async def cmd_rcpt(self, arg: str, line: str) -> None:
        match = ADDRESS_RE.match(line)
        if not match or not line.upper().startswith("RCPT TO"):
            await self.reply(501, "Syntax: RCPT TO:<address>")
        elif self.tx.sender is None or self.tx.recipient is not None:
            await self.bad_sequence()
        else:
            await self.accept_recipient(match.group(1))

    async def cmd_data(self, arg: str, line: str) -> None:
        if self.tx.recipient is None:
            await self.bad_sequence()
            return
        await self.reply(354, "End data with <CR><LF>.<CR><LF>")
        lines = []
        while (data_line := await self.read_line()) != ".":
            if data_line is None:  # client vanished mid-message
                self.closed = True
                return
            lines.append(data_line[1:] if data_line.startswith("..") else data_line)  # dot-unstuffing

        tx, self.tx = self.tx, Transaction()
        try:
            message = Message.parse(lines)
            if message.sender.lower() != tx.sender.lower() or message.recipient.lower() != tx.recipient.lower():
                raise ValueError("From/To headers must match MAIL FROM/RCPT TO")
        except ValueError as exc:
            await self.reply(500, f"Data not proper: {exc}")
            return
        email_id = await self.db.store_message(message, tx.sender_id, tx.recipient_id)
        log.info("queued email %d %s -> %s", email_id, tx.sender, tx.recipient)
        await self.reply(250, f"OK: Queued for delivery, ID: {email_id}")

    async def cmd_rset(self, arg: str, line: str) -> None:
        self.tx = Transaction()
        await self.reply(250, "OK")

    async def cmd_vrfy(self, arg: str, line: str) -> None:
        address = arg.strip("<>")
        if self.settings.is_local(address) and await self.db.get_user(address):
            await self.reply(250, f"User {address} exists")
        else:
            await self.reply(551, "Email not found")

    async def cmd_noop(self, arg: str, line: str) -> None:
        await self.reply(250, "OK")

    async def cmd_help(self, arg: str, line: str) -> None:
        await self.reply(214, *HELP_LINES)

    async def cmd_quit(self, arg: str, line: str) -> None:
        await self.reply(221, f"{self.settings.hostname} closing connection")
        self.closed = True


class RelaySession(SubmissionSession):
    """Port 25: another mail server delivering mail for our local users.

    A peer is trusted when its HELO name has A == peer IP, PTR(peer IP) == name
    and the name is the MX host of its own domain. No AUTH is needed.
    """

    async def verify_helo(self, name: str) -> None:
        domain = get_sld(name.lower())
        if (await self.resolver.host_matches_ip(name, self.peer_ip)
                and await self.resolver.mx(domain) == name.lower()):
            self.helo_done = self.authenticated = True
            await self.reply(250, f"{self.settings.hostname} Hello {name}")
        else:
            await self.reply(535, "SMTP authentication failed: DNS records do not match")

    async def accept_sender(self, address: str) -> bool:
        self.tx.sender, self.tx.sender_id = address, None  # remote sender, no local user id
        return True

    async def accept_recipient(self, address: str) -> bool:
        if not self.settings.is_local(address):
            await self.reply(550, "Relay access denied")
            return False
        return await self.accept_local_recipient(address)
