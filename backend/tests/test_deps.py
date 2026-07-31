import uuid

from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_actor
from flowsage_backend.models.workspace import Workspace

from .conftest import create_api_key_for, create_workspace_and_admin, login_to_default_workspace

_test_router = APIRouter()


@_test_router.get("/_test/actor")
async def _actor_probe(
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
) -> dict[str, str | None]:
    workspace_id, user_id = actor
    return {"workspace_id": str(workspace_id), "user_id": str(user_id) if user_id else None}


async def test_get_current_actor_rejects_unauthenticated_request(app: FastAPI) -> None:
    app.include_router(_test_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor")

    assert response.status_code == 401


async def test_get_current_actor_rejects_invalid_api_key(app: FastAPI) -> None:
    app.include_router(_test_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor", headers={"X-API-Key": "not-a-real-key"})

    assert response.status_code == 401


async def test_get_current_actor_accepts_valid_api_key(
    app: FastAPI, db_session: AsyncSession
) -> None:
    app.include_router(_test_router)
    workspace_id = await login_to_default_workspace(
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        db_session,
        "actor-apikey@example.com",
    )
    api_key = await create_api_key_for(db_session, workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["user_id"] is None


async def test_get_current_actor_accepts_valid_session_cookie(
    app: FastAPI, db_session: AsyncSession
) -> None:
    app.include_router(_test_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_id = await login_to_default_workspace(
            client, db_session, "actor-cookie@example.com"
        )
        response = await client.get("/_test/actor")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["user_id"] is not None


async def test_get_current_actor_rejects_api_key_for_archived_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    app.include_router(_test_router)
    _, membership = await create_workspace_and_admin(db_session, "actor-archived@example.com")
    workspace_id = membership.workspace_id
    api_key = await create_api_key_for(db_session, workspace_id)

    workspace = (
        await db_session.execute(select(Workspace).where(Workspace.id == workspace_id))
    ).scalar_one()
    workspace.archived = True
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor", headers={"X-API-Key": api_key})

    assert response.status_code == 403
