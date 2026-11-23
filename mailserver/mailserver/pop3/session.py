"""POP3 server session (port 995, plain text as in the original project).

States follow RFC 1939:
  AUTHORIZATION  USER, PASS (plus NOOP, HELP, QUIT)
  TRANSACTION    STAT, LIST, RETR, DELE, TOP, RSET, NOOP, HELP, QUIT
  UPDATE         on QUIT, messages marked with DELE are removed
Multi-line replies end with a line holding only "." (lines starting with "."
are byte-stuffed), so standard clients such as Python's poplib work.
"""

from __future__ import annotations

import re

from ..database import User
from ..session import LineSession
from .mailbox import Mailbox

EMAIL = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"

# verb -> (argument regex, allowed before login)
COMMANDS = {
    "USER": (rf"({EMAIL})", True),
    "PASS": (r"(.+)", True),
    "STAT": (r"", False),
    "LIST": (r"(\d+)?", False),
    "RETR": (r"(\d+)", False),
    "DELE": (r"(\d+)", False),
    "TOP":  (r"(\d+) (\d+)", False),
    "RSET": (r"", False),
    "NOOP": (r"", True),
    "HELP": (r"", True),
    "QUIT": (r"", True),
}

HELP_LINES = [
    "USER name - Specify user",
    "PASS password - Specify password",
    "STAT - Get mailbox status",
    "LIST [msg] - List message numbers and sizes",
    "RETR msg - Retrieve message content",
    "DELE msg - Mark message for deletion",
    "TOP msg lines - Get message headers and first lines of the body",
    "NOOP - No operation, keep connection alive",
    "RSET - Unmark messages marked for deletion",
    "QUIT - End session, delete marked messages",
    "HELP - This help text",
]


class Pop3Session(LineSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_user: User | None = None
        self.mailbox: Mailbox | None = None

    # ---- replies ----------------------------------------------------------

    async def ok(self, text: str = "", lines: list[str] | None = None) -> None:
        out = f"+OK {text}".rstrip() + "\r\n"
        if lines is not None:
            out += "".join(("." + l if l.startswith(".") else l) + "\r\n" for l in lines) + ".\r\n"
        await self.write(out)

    async def err(self, text: str) -> None:
        await self.write(f"-ERR {text}\r\n")

    async def greeting(self) -> None:
        await self.ok("POP3 server ready")

    async def on_timeout(self) -> None:
        await self.err("Connection timeout")

    # ---- dispatch ---------------------------------------------------------

    async def handle(self, line: str) -> None:
        verb, _, arg = line.strip().partition(" ")
        verb = verb.upper()
        spec = COMMANDS.get(verb)
        if spec is None:
            await self.err("Command not found, use HELP to see manuals")
            return
        pattern, open_command = spec
        match = re.fullmatch(pattern, arg.strip())
        if match is None:
            await self.err(f"Syntax error in {verb}, use HELP to see manuals")
        elif not open_command and self.mailbox is None:
            await self.err("Invalid sequence of commands, log in with USER and PASS")
        else:
            await getattr(self, f"cmd_{verb.lower()}")(*match.groups())

    def message_or_none(self, number: str):
        return self.mailbox.get(int(number))

    # ---- AUTHORIZATION ----------------------------------------------------

    async def cmd_user(self, email: str) -> None:
        if self.mailbox is not None:
            await self.err("Already authenticated")
            return
        self.pending_user = await self.db.get_user(email)
        if self.pending_user is None:
            await self.err("No such user")
        else:
            await self.ok(f"User {email} exists")

    async def cmd_pass(self, password: str) -> None:
        user, self.pending_user = self.pending_user, None
        if self.mailbox is not None or user is None:
            await self.err("Invalid sequence of commands, send USER first")
        elif not user.check_password(password):
            await self.err("Invalid password")
        else:
            self.mailbox = await Mailbox.load(self.db, user.id)
            await self.ok("User auth success")

    # ---- TRANSACTION ------------------------------------------------------

    async def cmd_stat(self) -> None:
        count, size = self.mailbox.stat()
        await self.ok(f"{count} {size}")

    async def cmd_list(self, number: str | None) -> None:
        if number is None:
            count, size = self.mailbox.stat()
            await self.ok(f"{count} messages ({size} octets)",
                          [f"{m.id} {self.mailbox.size(m)}" for m in self.mailbox.visible()])
        elif mail := self.message_or_none(number):
            await self.ok(f"{mail.id} {self.mailbox.size(mail)}")
        else:
            await self.err("Mail not found")

    async def cmd_retr(self, number: str) -> None:
        if mail := self.message_or_none(number):
            await self.ok(f"{self.mailbox.size(mail)} octets", Mailbox.render(mail))
        else:
            await self.err("Mail not found")

    async def cmd_top(self, number: str, body_lines: str) -> None:
        if mail := self.message_or_none(number):
            lines = Mailbox.render(mail)
            header_end = lines.index("") + 1
            await self.ok("Top of message follows", lines[:header_end + int(body_lines)])
        else:
            await self.err("Mail not found")

    async def cmd_dele(self, number: str) -> None:
        if self.mailbox.mark_deleted(int(number)):
            await self.ok("Marked for deletion")
        else:
            await self.err("Not found")

    async def cmd_rset(self) -> None:
        self.mailbox.reset()
        await self.ok(f"{self.mailbox.stat()[0]} messages")

    # ---- any state --------------------------------------------------------

    async def cmd_noop(self) -> None:
        await self.ok()

    async def cmd_help(self) -> None:
        await self.ok("Help text follows", HELP_LINES)

    async def cmd_quit(self) -> None:
        if self.mailbox is not None:
            await self.mailbox.commit()  # UPDATE state
        await self.ok("Closing connection")
        self.closed = True
