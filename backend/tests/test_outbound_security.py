from unittest.mock import AsyncMock

import httpcore
import httpx
import pytest

from flowsage_backend.outbound_http import PublicNetworkBackend, outbound_client
from flowsage_backend.url_safety import UnsafeUrlError, validate_outbound_url
from flowsage_backend.integrations.slack import SlackDeliveryError, post_slack_message
from flowsage_backend.integrations.webhooks import deliver_webhook


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/path",
        "https://example.com:bad",
        "https://example.com/#fragment",
        "https://example.com\\@127.0.0.1",
        "https://100.64.0.1",
        "https://[::ffff:127.0.0.1]",
    ],
)
def test_reject_unsafe_url(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_outbound_url(url)


@pytest.mark.parametrize(
    "addresses",
    [
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.169.254"],
        ["::1"],
        ["fc00::1"],
        ["8.8.8.8", "192.168.1.1"],
        ["8.8.8.8", "::1"],
        [],
    ],
)
async def test_blocks_every_nonpublic_dns_answer(addresses: list[str]) -> None:
    inner = AsyncMock(spec=httpcore.AsyncNetworkBackend)
    resolver = AsyncMock(return_value=addresses)
    backend = PublicNetworkBackend(inner=inner, resolver=resolver)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.com", 443, timeout=2)
    inner.connect_tcp.assert_not_called()


async def test_connects_to_validated_numeric_address_and_rechecks_new_connections() -> None:
    inner = AsyncMock(spec=httpcore.AsyncNetworkBackend)
    resolver = AsyncMock(side_effect=[["8.8.8.8", "2001:4860:4860::8888"], ["127.0.0.1"]])
    backend = PublicNetworkBackend(inner=inner, resolver=resolver)
    await backend.connect_tcp("example.com", 443, timeout=2)
    assert inner.connect_tcp.call_args.args == ("8.8.8.8", 443)
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("example.com", 443, timeout=2)
    assert inner.connect_tcp.call_count == 1


async def test_dns_failure_sanitized() -> None:
    backend = PublicNetworkBackend(resolver=AsyncMock(side_effect=OSError("sensitive host")))
    with pytest.raises(httpcore.ConnectError, match="Destination resolution failed"):
        await backend.connect_tcp("example.com", 443)


async def test_redirects_not_followed_and_proxies_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://127.0.0.1"})

    async with outbound_client(transport=httpx.MockTransport(handler)) as client:
        response = await client.get("https://example.com")
        assert client.trust_env is False
        assert client.timeout.connect is not None
    assert response.status_code == 302
    assert len(seen) == 1


async def test_saved_unsafe_integration_rejected_before_transport() -> None:
    handler = AsyncMock(return_value=httpx.Response(200))
    transport = httpx.MockTransport(handler)
    with pytest.raises(SlackDeliveryError):
        await post_slack_message("https://127.0.0.1/private", text="hi", transport=transport)
    assert await deliver_webhook(
        "https://127.0.0.1", secret="s", event_type="e", payload={}, transport=transport
    ) == (None, False)
    handler.assert_not_called()


async def test_transport_preserves_tls_hostname_host_header_and_certificate_checks() -> None:
    import ssl
    from flowsage_backend.outbound_http import PublicHTTPTransport

    stream = httpcore.AsyncMockStream([b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"])
    stream.start_tls = AsyncMock(return_value=stream)
    stream.write = AsyncMock()
    inner = AsyncMock(spec=httpcore.AsyncNetworkBackend)
    inner.connect_tcp.return_value = stream
    resolver = AsyncMock(return_value=["8.8.8.8"])
    transport = PublicHTTPTransport(backend=PublicNetworkBackend(inner=inner, resolver=resolver))
    async with outbound_client(transport=transport) as client:
        response = await client.post("https://service.example/path", content=b"payload")
    assert response.status_code == 200
    inner.connect_tcp.assert_awaited_once()
    assert inner.connect_tcp.call_args.args == ("8.8.8.8", 443)
    tls = stream.start_tls.call_args.kwargs
    assert tls["server_hostname"] == "service.example"
    assert tls["ssl_context"].check_hostname is True
    assert tls["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert b"Host: service.example\r\n" in b"".join(
        call.args[0] for call in stream.write.call_args_list
    )
    resolver.assert_awaited_once_with("service.example", 443)


async def test_dns_timeout_is_bounded() -> None:
    import asyncio

    async def slow_resolver(host: str, port: int) -> list[str]:
        await asyncio.sleep(10)
        return ["8.8.8.8"]

    with pytest.raises(httpcore.ConnectTimeout):
        await PublicNetworkBackend(resolver=slow_resolver).connect_tcp(
            "example.com", 443, timeout=0.001
        )


async def test_jira_unsafe_saved_url_and_network_errors_are_sanitized() -> None:
    from flowsage_backend.integrations.jira import JiraDeliveryError, create_jira_issue

    handler = AsyncMock(side_effect=httpx.ConnectError("secret-token-in-url"))
    for url in ["https://127.0.0.1", "https://example.com"]:
        with pytest.raises(JiraDeliveryError) as error:
            await create_jira_issue(
                base_url=url,
                email="e@example.com",
                api_token="secret",
                project_key="P",
                summary="s",
                description="d",
                transport=httpx.MockTransport(handler),
            )
        assert "secret" not in str(error.value)
    assert handler.call_count == 1


async def test_slack_response_and_network_errors_do_not_expose_secrets() -> None:
    for transport in [
        httpx.MockTransport(lambda request: httpx.Response(400, text="secret-token-in-body")),
        httpx.MockTransport(AsyncMock(side_effect=httpx.ConnectError("secret-token-in-url"))),
    ]:
        with pytest.raises(SlackDeliveryError) as error:
            await post_slack_message(
                "https://example.com/secret-token", text="hello", transport=transport
            )
        assert "secret-token" not in str(error.value)


async def test_httpx_request_logs_hide_webhook_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="httpx"):
        await post_slack_message(
            "https://example.com/secret-path?token=secret-query",
            text="hello",
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        )
    assert "example.com" in caplog.text
    assert "secret-path" not in caplog.text
    assert "secret-query" not in caplog.text
