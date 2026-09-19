"""Syntactic endpoint validation; outbound_http also validates DNS at connection time."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx


class UnsafeUrlError(ValueError):
    """Raised when a user-supplied outbound URL is rejected as unsafe."""


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return is_public_address(str(ip.ipv4_mapped))
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and "%" not in address


def validate_outbound_url(url: str) -> str:
    if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise UnsafeUrlError("URL contains invalid characters")
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
        httpx.URL(url)
    except (ValueError, httpx.InvalidURL, UnicodeError):
        raise UnsafeUrlError("URL is malformed") from None
    if parsed.scheme != "https":
        raise UnsafeUrlError("URL must start with https://")
    if not hostname:
        raise UnsafeUrlError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeUrlError("URL must not contain credentials")
    if parsed.fragment or port == 0:
        raise UnsafeUrlError("URL contains an invalid fragment or port")
    if hostname.rstrip(".").lower() == "localhost":
        raise UnsafeUrlError("URL must not point at a local address")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if "%" in hostname:
            raise UnsafeUrlError("URL contains an invalid hostname")
        return url
    if not is_public_address(hostname):
        raise UnsafeUrlError("URL must not resolve to a non-public address")
    return url
