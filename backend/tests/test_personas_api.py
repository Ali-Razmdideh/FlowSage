import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.workspace import Membership
from flowsage_backend.seed import seed_baseline_personas, upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "personas-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "personas-api@example.com", "password": "hunter2"}
        )
        yield client


async def _personas_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    user = await upsert_user(db_session, "personas-api@example.com", "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


def _create_payload(slug: str) -> dict[str, object]:
    return {
        "slug": slug,
        "name": "Custom Persona",
        "description": "A hand-crafted persona for testing.",
        "tech_affinity": "Medium",
        "primary_device": "Desktop",
        "discovery_mode": "Search-driven",
        "contextual_triggers": ["Time Constraint"],
        "technical_literacy": 0.6,
        "anxiety": 0.4,
        "patience": 0.5,
        "curiosity": 0.7,
    }


async def test_list_personas_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/personas")

    assert response.status_code == 401


async def test_create_get_update_persona(app: FastAPI, db_session: AsyncSession) -> None:
    slug = f"custom-persona-{uuid.uuid4().hex[:8]}"
    async with _authed_client(app, db_session) as client:
        create_response = await client.post("/personas", json=_create_payload(slug))
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["slug"] == slug
        assert created["baseline"] is False

        get_response = await client.get(f"/personas/{created['id']}")
        assert get_response.status_code == 200
        detail = get_response.json()
        assert detail["name"] == "Custom Persona"
        assert detail["memories"] == []

        update_payload = {**_create_payload(slug), "name": "Renamed Persona", "anxiety": 0.9}
        del update_payload["slug"]
        update_response = await client.patch(f"/personas/{created['id']}", json=update_payload)
        assert update_response.status_code == 200
        updated = update_response.json()
        assert updated["name"] == "Renamed Persona"
        assert updated["anxiety"] == 0.9


async def test_create_persona_rejects_duplicate_slug(
    app: FastAPI, db_session: AsyncSession
) -> None:
    slug = f"dup-persona-{uuid.uuid4().hex[:8]}"
    async with _authed_client(app, db_session) as client:
        first = await client.post("/personas", json=_create_payload(slug))
        assert first.status_code == 201

        second = await client.post("/personas", json=_create_payload(slug))
        assert second.status_code == 409


async def test_create_persona_rejects_invalid_slug(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/personas", json=_create_payload("Not A Valid Slug!"))

    assert response.status_code == 422


async def test_delete_non_baseline_persona(app: FastAPI, db_session: AsyncSession) -> None:
    slug = f"deletable-persona-{uuid.uuid4().hex[:8]}"
    async with _authed_client(app, db_session) as client:
        create_response = await client.post("/personas", json=_create_payload(slug))
        persona_id = create_response.json()["id"]

        delete_response = await client.delete(f"/personas/{persona_id}")
        assert delete_response.status_code == 204

        get_response = await client.get(f"/personas/{persona_id}")
        assert get_response.status_code == 404


async def test_baseline_persona_cannot_be_deleted(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _personas_api_workspace_id(db_session)
    async with _authed_client(app, db_session) as client:
        personas = await seed_baseline_personas(db_session, workspace_id)
        baseline = personas[0]

        delete_response = await client.delete(f"/personas/{baseline.id}")

    assert delete_response.status_code == 409


async def test_reset_baseline_persona_reverts_edits(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _personas_api_workspace_id(db_session)
    async with _authed_client(app, db_session) as client:
        personas = await seed_baseline_personas(db_session, workspace_id)
        baseline = personas[0]
        original_anxiety = baseline.anxiety

        edit_payload = {
            "name": baseline.name,
            "description": baseline.description,
            "tech_affinity": baseline.tech_affinity,
            "primary_device": baseline.primary_device,
            "discovery_mode": baseline.discovery_mode,
            "contextual_triggers": list(baseline.contextual_triggers),
            "technical_literacy": baseline.technical_literacy,
            "anxiety": 0.01,
            "patience": baseline.patience,
            "curiosity": baseline.curiosity,
        }
        edit_response = await client.patch(f"/personas/{baseline.id}", json=edit_payload)
        assert edit_response.status_code == 200
        assert edit_response.json()["anxiety"] == 0.01

        reset_response = await client.post(f"/personas/{baseline.id}/reset")
        assert reset_response.status_code == 200
        assert reset_response.json()["anxiety"] == original_anxiety


async def test_reset_rejects_non_baseline_persona(app: FastAPI, db_session: AsyncSession) -> None:
    slug = f"reset-rejected-{uuid.uuid4().hex[:8]}"
    async with _authed_client(app, db_session) as client:
        create_response = await client.post("/personas", json=_create_payload(slug))
        persona_id = create_response.json()["id"]

        reset_response = await client.post(f"/personas/{persona_id}/reset")

    assert reset_response.status_code == 409
