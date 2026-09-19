"""Regression coverage for API-key least privilege and role boundaries."""

import pytest
from pydantic import ValidationError
from flowsage_backend.api.integrations import ApiKeyCreateIn


def test_new_api_keys_default_to_ingestion_only():
    assert ApiKeyCreateIn(name="ingestion").scopes == ["events:write"]


@pytest.mark.parametrize("scopes", [[], ["admin"], ["simulations:*"]])
def test_invalid_scope_selection_is_rejected(scopes):
    with pytest.raises(ValidationError):
        ApiKeyCreateIn(name="invalid", scopes=scopes)


import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
from starlette.requests import Request

from flowsage_backend import deps
from flowsage_backend.models.api_key import ALL_API_KEY_SCOPES
from flowsage_backend.models.workspace import Role


@pytest.mark.parametrize("granted", ALL_API_KEY_SCOPES)
@pytest.mark.parametrize("required", ALL_API_KEY_SCOPES)
async def test_scopes_are_independent(granted, required):
    workspace_id = uuid.uuid4()
    key = SimpleNamespace(
        scopes=[granted], revoked_at=None, workspace_id=workspace_id, last_used_at=None
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: key)),
        get=AsyncMock(return_value=SimpleNamespace(archived=False)),
        commit=AsyncMock(),
    )
    request = Request({"type": "http", "headers": [(b"x-api-key", b"test")]})
    if granted == required:
        assert await deps.require_workspace_api_key(request, session, required) == workspace_id
        session.commit.assert_awaited_once()
    else:
        with pytest.raises(HTTPException) as exc:
            await deps.require_workspace_api_key(request, session, required)
        assert exc.value.status_code == 403
        assert key.last_used_at is None
        session.commit.assert_not_awaited()


@pytest.mark.parametrize("revoked", [False, True])
async def test_invalid_or_revoked_key_is_401_without_cookie_fallback(revoked, monkeypatch):
    key = SimpleNamespace(revoked_at=datetime.now(timezone.utc)) if revoked else None
    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: key))
    )
    fallback = AsyncMock()
    monkeypatch.setattr(deps, "get_current_membership", fallback)
    request = Request({"type": "http", "headers": [(b"x-api-key", b"bad")]})
    with pytest.raises(HTTPException) as exc:
        await deps.require_actor("simulations:write", Role.RESEARCHER)(request, session)
    assert exc.value.status_code == 401
    fallback.assert_not_awaited()


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("required", list(Role))
async def test_actor_cookie_roles(role, required, monkeypatch):
    member = SimpleNamespace(role=role, workspace_id=uuid.uuid4(), user_id=uuid.uuid4())
    monkeypatch.setattr(deps, "get_current_membership", AsyncMock(return_value=(object(), member)))
    request = Request({"type": "http", "headers": []})
    if role.ordinal() >= required.ordinal():
        assert await deps.require_actor("simulations:write", required)(request, object()) == (
            member.workspace_id,
            member.user_id,
        )
    else:
        with pytest.raises(HTTPException) as exc:
            await deps.require_actor("simulations:write", required)(request, object())
        assert exc.value.status_code == 403


async def test_viewer_denied_mutations_before_handlers(app, db_session, monkeypatch):
    from httpx import AsyncClient, ASGITransport
    from tests.conftest import create_workspace_and_admin

    user, membership = await create_workspace_and_admin(
        db_session, f"viewer-security-{uuid.uuid4()}@example.com"
    )
    membership.role = Role.VIEWER
    await db_session.commit()
    unknown = str(uuid.uuid4())
    # Valid auth reaches the permission guard even where the body is missing.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": user.email, "password": "hunter2"})
        await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(membership.workspace_id)}
        )
        for method, path in [
            ("POST", "/personas"),
            ("PATCH", f"/personas/{unknown}"),
            ("DELETE", f"/personas/{unknown}"),
            ("POST", f"/personas/{unknown}/reset"),
            ("POST", "/simulations"),
            ("POST", "/scheduled-simulations"),
            ("PATCH", f"/scheduled-simulations/{unknown}"),
            ("DELETE", f"/scheduled-simulations/{unknown}"),
            ("POST", f"/scheduled-simulations/{unknown}/screenshots"),
            ("POST", "/calibration/retrain"),
            ("POST", "/onboarding/import-sample-data"),
            ("PATCH", "/settings/model-calibration"),
            ("POST", "/alerts/digest/run"),
            ("POST", f"/friction-issues/{unknown}/export/slack"),
            ("POST", f"/friction-issues/{unknown}/export/jira"),
        ]:
            response = await client.request(method, path)
            assert response.status_code == 403, (method, path, response.text)
