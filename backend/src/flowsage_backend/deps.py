"""FastAPI dependency providers: DB session, the current authenticated user, and
the shared-secret API key check used by the server-to-server ingestion endpoint."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.user import User
from flowsage_backend.security import decode_access_token


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> User:
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        user_id = decode_access_token(
            token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")
    return user


async def require_api_key(request: Request) -> None:
    settings = request.app.state.settings
    provided = request.headers.get("X-API-Key")
    if provided is None or not secrets.compare_digest(provided, settings.events_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")
