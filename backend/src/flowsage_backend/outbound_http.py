"""HTTPS integration transport with connection-time DNS validation and IP pinning.

Only the TCP destination is replaced. httpcore retains the original origin for
TLS SNI/certificate verification, pooling and the HTTP Host header.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable, Iterable

import httpcore
import httpx

from flowsage_backend.url_safety import is_public_address, validate_outbound_url

Resolver = Callable[[str, int], Awaitable[list[str]]]


class _RedactRequestURL(logging.Filter):
    """httpx logs full URLs at INFO, including secret webhook paths and queries."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg == 'HTTP Request: %s %s "%s %d %s"' and isinstance(record.args, tuple):
            args = list(record.args)
            if len(args) == 5:
                # Keep only the origin; never stringify the original credential path.
                url = httpx.URL(str(args[1]))
                args[1] = f"{url.scheme}://{url.host}" + (f":{url.port}" if url.port else "")
                record.args = tuple(args)
        return True


logging.getLogger("httpx").addFilter(_RedactRequestURL())


async def resolve_addresses(host: str, port: int) -> list[str]:
    answers = await asyncio.get_running_loop().getaddrinfo(
        host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
    )
    return list(dict.fromkeys(str(answer[4][0]) for answer in answers))


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        *,
        inner: httpcore.AsyncNetworkBackend | None = None,
        resolver: Resolver = resolve_addresses,
    ) -> None:
        self.inner = inner if inner is not None else httpcore.AnyIOBackend()
        self.resolver = resolver

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        budget = timeout if timeout is not None else 5.0
        try:
            # The budget includes DNS and TCP. No hostname is handed to the
            # underlying connector, so a second DNS lookup cannot rebind it.
            async with asyncio.timeout(budget):
                try:
                    addresses = await self.resolver(host, port)
                except OSError:
                    raise httpcore.ConnectError("Destination resolution failed") from None
                if not addresses or any(not is_public_address(ip) for ip in addresses):
                    raise httpcore.ConnectError("Destination must resolve only to public addresses")
                for address in addresses:
                    try:
                        return await self.inner.connect_tcp(
                            address,
                            port,
                            timeout=budget,
                            local_address=local_address,
                            socket_options=socket_options,
                        )
                    except (httpcore.ConnectError, httpcore.ConnectTimeout):
                        continue
                raise httpcore.ConnectError("Unable to connect to destination")
        except TimeoutError:
            raise httpcore.ConnectTimeout("Destination connection timed out") from None

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class PublicHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, *, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        super().__init__(verify=True, trust_env=False)
        # httpx 0.27 has no public network_backend constructor argument. Keep
        # its request/response and exception adapter, replace its unused pool.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpcore.default_ssl_context(),
            network_backend=backend if backend is not None else PublicNetworkBackend(),
            retries=0,
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        validate_outbound_url(str(request.url))
        return await super().handle_async_request(request)


def outbound_client(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Injected transports are for tests only; real delivery always uses pinning."""
    return httpx.AsyncClient(
        transport=transport if transport is not None else PublicHTTPTransport(),
        auth=auth,
        verify=True,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(10.0, connect=5.0),
    )
