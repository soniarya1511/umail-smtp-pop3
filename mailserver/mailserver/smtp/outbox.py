"""Outbox worker: relays queued messages to remote mail servers.

Every `outbox_interval` seconds it picks up outbox rows that are 'pending' or
'failed' with fewer than `outbox_max_retries` attempts, looks up the
recipient domain's MX and delivers over SMTP port 25.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from ..database import Database, PendingDelivery
from ..resolver import LOOKUP_ERRORS, DnsResolver
from .client import SmtpClient, SmtpError

log = logging.getLogger(__name__)


class OutboxWorker:
    def __init__(self, settings: Settings, db: Database, resolver: DnsResolver):
        self.settings = settings
        self.db = db
        self.resolver = resolver

    async def run_forever(self) -> None:
        while True:
            try:
                await self.process_once()
            except Exception:
                log.exception("outbox pass failed")
            await asyncio.sleep(self.settings.outbox_interval)

    async def process_once(self) -> None:
        for delivery in await self.db.pending_deliveries(self.settings.outbox_max_retries):
            ok = await self.deliver(delivery)
            await self.db.mark_delivery(delivery.outbox_id, ok)

    async def deliver(self, delivery: PendingDelivery) -> bool:
        msg = delivery.message
        domain = msg.recipient.rpartition("@")[2]
        mx_host = await self.resolver.mx(domain)
        if mx_host is None:
            log.warning("email %d: no MX for %s", delivery.email_id, domain)
            return False
        client = SmtpClient(self.settings.hostname, self.resolver, self.settings.relay_port)
        try:
            await client.deliver(mx_host, msg)
        except (SmtpError, OSError, asyncio.TimeoutError, *LOOKUP_ERRORS) as exc:
            log.warning("email %d to %s failed (attempt %d): %s",
                        delivery.email_id, msg.recipient, delivery.retry_count + 1, exc)
            return False
        log.info("email %d delivered to %s via %s", delivery.email_id, msg.recipient, mx_host)
        return True
