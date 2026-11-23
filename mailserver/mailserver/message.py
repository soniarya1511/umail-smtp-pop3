"""The mail message format shared by SMTP DATA, POP3 RETR and the webmail.

The project uses a small fixed layout instead of full RFC 5322:

    Subject: <text>
    From: <address>
    To: <address>
    Date: YYYY-MM-DD HH:MM:SS
    <blank line>
    <body ...>
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
HEADERS = ("Subject", "From", "To", "Date")


@dataclass
class Message:
    subject: str
    sender: str
    recipient: str
    date: str
    body: str

    @classmethod
    def parse(cls, lines: list[str]) -> "Message":
        """Build a Message from DATA lines (terminating "." already removed).

        Raises ValueError with a human readable reason when the layout is wrong.
        """
        if len(lines) < 5:
            raise ValueError("message too short")
        values = []
        for line, name in zip(lines[:4], HEADERS):
            key, sep, value = line.partition(":")
            if not sep or key.strip().lower() != name.lower():
                raise ValueError(f"expected '{name}:' header")
            values.append(value.strip())
        if not DATE_RE.fullmatch(values[3]):
            raise ValueError("Date must be YYYY-MM-DD HH:MM:SS")
        if lines[4].strip():
            raise ValueError("headers must be followed by a blank line")
        body = lines[5:]
        if body and not body[-1].strip():  # the format puts a blank line before "."
            body = body[:-1]
        return cls(*values, body="\n".join(body))

    def to_lines(self) -> list[str]:
        return [
            f"Subject: {self.subject}",
            f"From: {self.sender}",
            f"To: {self.recipient}",
            f"Date: {self.date}",
            "",
            *self.body.split("\n"),
        ]
