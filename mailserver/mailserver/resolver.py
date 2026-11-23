"""DNS lookups against the project's own DNS server (dnsmasq container).

The servers trust peers based on DNS, exactly like the original project:
  * a trusted MUA must have  A(name) == peer IP  and  PTR(peer IP) == name
  * a remote MTA must also be the MX host of its own domain
"""

from __future__ import annotations

import logging

import dns.asyncresolver
import dns.exception
import dns.resolver
import dns.reversename

log = logging.getLogger(__name__)

LOOKUP_ERRORS = (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN,
                 dns.resolver.NoNameservers, dns.exception.Timeout)


def _strip_dot(name: str) -> str:
    return name.rstrip(".").lower()


class DnsResolver:
    def __init__(self, nameserver: str):
        self._resolver = dns.asyncresolver.Resolver(configure=False)
        self._resolver.nameservers = [nameserver]
        self._resolver.lifetime = 5

    async def a(self, name: str) -> list[str]:
        answer = await self._resolver.resolve(name, "A")
        return [r.address for r in answer]

    async def ptr(self, ip: str) -> list[str]:
        answer = await self._resolver.resolve(dns.reversename.from_address(ip), "PTR")
        return [_strip_dot(r.to_text()) for r in answer]

    async def mx(self, domain: str) -> str | None:
        """Best (lowest preference) MX host of a domain, or None."""
        try:
            answer = await self._resolver.resolve(domain, "MX")
        except LOOKUP_ERRORS:
            return None
        best = min(answer, key=lambda r: r.preference)
        return _strip_dot(best.exchange.to_text())

    async def host_matches_ip(self, name: str, ip: str) -> bool:
        """Forward-confirmed reverse DNS: A(name) contains ip and PTR(ip) contains name."""
        try:
            return ip in await self.a(name) and _strip_dot(name) in await self.ptr(ip)
        except LOOKUP_ERRORS as exc:
            log.info("DNS check failed for %s / %s: %s", name, ip, exc)
            return False
