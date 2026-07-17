from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_healthz_reports_ok_when_db_reachable(app: FastAPI) -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
