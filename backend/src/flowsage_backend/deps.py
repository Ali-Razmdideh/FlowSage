"""FastAPI dependency providers: DB session, the current authenticated membership
(user + their role in the active workspace), and the per-workspace API key check
used by the server-to-server ingestion endpoint."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role
from flowsage_backend.security import decode_access_token, hash_api_key


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def get_current_membership(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> tuple[User, Membership]:
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        user_id, workspace_id = decode_access_token(
            token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")

    result = await session.execute(
        select(Membership)
        .where(Membership.user_id == user_id, Membership.workspace_id == workspace_id)
        .options(selectinload(Membership.workspace))
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No membership in the active workspace")
    if membership.workspace.archived:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This workspace has been archived")

    return user, membership


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    """Deprecated: pre-workspace user lookup, kept only so routers not yet migrated
    to `get_current_membership` (Task 6 of the workspace-multitenancy plan) still
    import and run. New code should depend on `get_current_membership` instead."""
    user, _ = await get_current_membership(request, session)
    return user


def require_role(
    min_role: Role,
) -> Callable[..., Coroutine[Any, Any, tuple[User, Membership]]]:
    async def _dependency(
        membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    ) -> tuple[User, Membership]:
        _, membership = membership_pair
        if membership.role.ordinal() < min_role.ordinal():
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this action")
        return membership_pair

    return _dependency


async def require_workspace_api_key(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> uuid.UUID:
    provided = request.headers.get("X-API-Key")
    if provided is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")

    result = await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(provided)))
    api_key = result.scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")

    api_key.last_used_at = datetime.now(timezone.utc)
    await session.commit()
    return api_key.workspace_id
