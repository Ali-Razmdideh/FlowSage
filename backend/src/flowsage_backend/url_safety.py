"""Outbound-URL validation for user-supplied integration endpoints (Slack webhook
URL, Jira base URL, custom webhook URL) -- the backend POSTs to these on the
user's behalf, so an unvalidated URL is a Server-Side Request Forgery vector
(pointing "Slack webhook_url" at an internal service, a cloud metadata endpoint,
etc. and using FlowSage as a proxy to reach it).

This is a syntactic check only (scheme + literal-IP-in-hostname), not a
DNS-resolution-time check -- a hostname that *currently* resolves to a public IP
but is later repointed to an internal one (DNS rebinding) would slip through.
Full hardening (resolve-and-check at request time, disable redirects, block
rebinding) belongs with the rest of the SOC2-track work (Phase 3 chunk 3), not
bolted onto this chunk; this catches the common, obvious cases (literal
loopback/private/link-local addresses, `localhost`, non-https) cheaply and
without a network round-trip on every settings save.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied outbound URL is rejected as unsafe."""


def validate_outbound_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise UnsafeUrlError("URL must start with https://")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL must include a hostname")
    if hostname.lower() in ("localhost", "0.0.0.0"):
        raise UnsafeUrlError("URL must not point at a local address")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        return url  # a normal DNS hostname, not a literal IP -- allowed

    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise UnsafeUrlError(f"URL must not resolve to a non-public address ({ip})")
    return url
