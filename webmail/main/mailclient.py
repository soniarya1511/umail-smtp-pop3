"""SMTP + POP3 client used by the webmail (the MUA).

`UserConnection` keeps one SMTP and one POP3 connection open per logged in
user, like the original project, and sends NOOP on both every few minutes so
the servers' 180 s idle timeout never closes them.

Service discovery goes through the project DNS server:
  * SMTP host  = MX record of the user's domain          (port 578)
  * POP3 host  = SRV record _pop3._tcp.<domain>          (RFC 6186)

The client speaks standard SMTP/POP3, so it uses Python's smtplib/poplib.
"""

from __future__ import annotations

import logging
import poplib
import smtplib
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import dns.exception
import dns.resolver
from django.conf import settings

log = logging.getLogger(__name__)

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
HEARTBEAT_EVERY = 150   # seconds between heartbeat checks
IDLE_BEFORE_NOOP = 120  # only send NOOP after this much silence


class MailError(Exception):
    """Something the user should see: server down, bad login, rejected mail."""


# ---- DNS service discovery -------------------------------------------------

def _resolver() -> dns.resolver.Resolver:
    r = dns.resolver.Resolver(configure=False)
    r.nameservers = [settings.MAIL_DNS_SERVER]
    r.lifetime = 5
    return r


@dataclass(frozen=True)
class MailServers:
    smtp_host: str
    smtp_ip: str
    pop3_host: str
    pop3_ip: str
    pop3_port: int

    @classmethod
    def discover(cls, email: str) -> "MailServers":
        domain = email.rpartition("@")[2]
        r = _resolver()
        try:
            mx = min(r.resolve(domain, "MX"), key=lambda rec: rec.preference)
            srv = min(r.resolve(f"_pop3._tcp.{domain}", "SRV"), key=lambda rec: rec.priority)
            smtp_host = mx.exchange.to_text().rstrip(".")
            pop3_host = srv.target.to_text().rstrip(".")
            return cls(smtp_host, r.resolve(smtp_host, "A")[0].address,
                       pop3_host, r.resolve(pop3_host, "A")[0].address, srv.port)
        except dns.exception.DNSException as exc:
            raise MailError(f"No mail server found for {domain}") from exc


# ---- messages --------------------------------------------------------------

@dataclass
class Mail:
    id: int
    size: int
    subject: str
    sender: str
    recipient: str
    timestamp: datetime
    body: str

    @classmethod
    def from_lines(cls, mail_id: int, size: int, lines: list[str]) -> "Mail":
        """Parse a RETR reply: Subject/From/To/Date headers, blank line, body."""
        headers = {}
        for i, line in enumerate(lines):
            if not line:
                body = lines[i + 1:]
                break
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
        else:
            body = []
        try:
            ts = datetime.strptime(headers.get("date", ""), DATE_FORMAT).replace(tzinfo=timezone.utc)
        except ValueError:
            ts = datetime.now(timezone.utc)
        return cls(mail_id, size, headers.get("subject", ""), headers.get("from", ""),
                   headers.get("to", ""), ts, "\n".join(body))


# ---- the connection pair ---------------------------------------------------

class UserConnection:
    def __init__(self, email: str, servers: MailServers):
        self.email = email
        self.servers = servers
        self.smtp_port = settings.SMTP_SUBMISSION_PORT
        self.box: dict[int, Mail] = {}
        self._password = ""          # kept in server memory only, to re-open POP3
        self._smtp: smtplib.SMTP | None = None
        self._pop3: poplib.POP3 | None = None
        self._lock = threading.Lock()  # sockets are shared with the heartbeat thread
        self._last_used = time.monotonic()
        self._running = False

    # ---- lifecycle ----------------------------------------------------------

    def login(self, password: str) -> None:
        """POP3 USER/PASS checks the password, SMTP HELO proves we are a trusted MUA."""
        self._password = password
        with self._lock:
            self._open_pop3()
            self._open_smtp()
        self._running = True
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def close(self) -> None:
        self._running = False
        with self._lock:
            for conn in (self._pop3, self._smtp):
                try:
                    if conn:
                        conn.quit()  # POP3 QUIT also commits deletions
                except (OSError, smtplib.SMTPException, poplib.error_proto):
                    pass
            self._pop3 = self._smtp = None

    def _open_pop3(self) -> None:
        s = self.servers
        try:
            self._pop3 = poplib.POP3(s.pop3_ip, s.pop3_port, timeout=10)
            self._pop3.user(self.email)
            self._pop3.pass_(self._password)
        except poplib.error_proto as exc:
            raise MailError("Username or password incorrect.") from exc
        except OSError as exc:
            raise MailError("POP3 server is down. Please try again later.") from exc

    def _open_smtp(self) -> None:
        try:
            self._smtp = smtplib.SMTP(self.servers.smtp_ip, self.smtp_port, timeout=10)
            code, msg = self._smtp.helo(settings.MUA_HOSTNAME)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError("SMTP server is down. Please try again later.") from exc
        if code != 235:
            raise MailError(f"SMTP server did not trust this webmail: {code} {msg.decode()}")

    def _call(self, which: str, fn):
        """Run fn(connection); if the server dropped us, reconnect once and retry."""
        with self._lock:
            self._last_used = time.monotonic()
            for attempt in (1, 2):
                try:
                    return fn(self._smtp if which == "smtp" else self._pop3)
                except (OSError, smtplib.SMTPServerDisconnected, AttributeError):
                    if attempt == 2:
                        raise MailError(f"Lost connection to the {which.upper()} server")
                    self._open_smtp() if which == "smtp" else self._open_pop3()

    def _heartbeat(self) -> None:
        while self._running:
            time.sleep(HEARTBEAT_EVERY)
            if self._running and time.monotonic() - self._last_used >= IDLE_BEFORE_NOOP:
                try:
                    self._call("smtp", lambda c: c.noop())
                    self._call("pop3", lambda c: c.noop())
                except MailError:
                    log.warning("heartbeat failed for %s", self.email)

    # ---- POP3 ---------------------------------------------------------------

    def refresh_inbox(self) -> None:
        """Re-open the POP3 session so new mail shows up and DELEs are committed."""
        with self._lock:
            try:
                self._pop3.quit()
            except (OSError, poplib.error_proto, AttributeError):
                pass
            self._open_pop3()

        def fetch(pop: poplib.POP3) -> dict[int, Mail]:
            box = {}
            for entry in pop.list()[1]:
                mail_id, size = map(int, entry.split())
                lines = [l.decode(errors="replace") for l in pop.retr(mail_id)[1]]
                box[mail_id] = Mail.from_lines(mail_id, size, lines)
            return box

        self.box = self._call("pop3", fetch)

    def stat(self) -> tuple[int, int]:
        return self._call("pop3", lambda pop: pop.stat())

    def delete_email(self, mail_id: int) -> bool:
        try:
            self._call("pop3", lambda pop: pop.dele(mail_id))
        except poplib.error_proto:
            return False
        self.box.pop(mail_id, None)
        return True

    # ---- SMTP ---------------------------------------------------------------

    def send_email(self, subject: str, to: str, body: str) -> None:
        lines = [
            f"Subject: {subject}",
            f"From: {self.email}",
            f"To: {to}",
            f"Date: {datetime.now(timezone.utc).strftime(DATE_FORMAT)}",
            "",
            *body.replace("\r\n", "\n").split("\n"),
            "",
        ]
        data = "\r\n".join(lines).encode()
        try:
            self._call("smtp", lambda smtp: smtp.sendmail(self.email, [to], data))
        except smtplib.SMTPRecipientsRefused as exc:
            code, msg = exc.recipients[to]
            raise MailError(f"Recipient rejected: {code} {msg.decode()}") from exc
        except smtplib.SMTPResponseException as exc:
            raise MailError(f"Server rejected the mail: {exc.smtp_code} {exc.smtp_error.decode()}") from exc
