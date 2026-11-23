"""Runtime configuration, read once from environment variables.

Every value that the original project hard-coded (domain name, DNS server IP,
database path, ports) now comes from the environment so the same image can
serve any mail domain.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    domain: str                 # local mail domain, e.g. "posthawk.com"
    hostname: str               # this server's FQDN, used in greetings and HELO
    dns_server: str             # resolver that holds the A / PTR / MX / SRV records
    database_url: str           # PostgreSQL DSN
    submission_port: int = 578  # SMTP for users and trusted MUAs
    relay_port: int = 25        # SMTP for other mail servers (MTA to MTA)
    pop3_port: int = 995        # POP3 (plain text, as in the original project)
    idle_timeout: float = 180.0
    outbox_interval: float = 10.0
    outbox_max_retries: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        domain = os.environ["MAIL_DOMAIN"]
        return cls(
            domain=domain,
            hostname=os.environ.get("MAIL_HOSTNAME", f"mail.{domain}"),
            dns_server=os.environ["DNS_SERVER"],
            database_url=os.environ["DATABASE_URL"],
            submission_port=int(os.environ.get("SMTP_SUBMISSION_PORT", 578)),
            relay_port=int(os.environ.get("SMTP_RELAY_PORT", 25)),
            pop3_port=int(os.environ.get("POP3_PORT", 995)),
            idle_timeout=float(os.environ.get("IDLE_TIMEOUT", 180)),
            outbox_interval=float(os.environ.get("OUTBOX_INTERVAL", 10)),
            outbox_max_retries=int(os.environ.get("OUTBOX_MAX_RETRIES", 3)),
        )

    def is_local(self, address: str) -> bool:
        """True when the address belongs to this server's domain."""
        return address.rpartition("@")[2].lower() == self.domain
