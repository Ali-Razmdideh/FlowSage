from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_healthz_reports_ok_when_db_reachable(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_cors_preflight_allows_null_origin_with_api_key_header(app: FastAPI) -> None:
    """The Figma plugin's UI runs in a sandboxed iframe with a `null` origin and
    sends the non-simple `X-API-Key` header, so the browser preflights with an
    `OPTIONS` request before the real `GET /personas` call. Without CORS
    configured, this preflight fails and the plugin can never reach the
    backend at all."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/personas",
            headers={
                "Origin": "null",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "x-api-key" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers
