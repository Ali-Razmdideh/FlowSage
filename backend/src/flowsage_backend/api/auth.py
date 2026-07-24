"""Auth endpoints: email+password login with a JWT httpOnly cookie carrying the
active workspace, plus switching between a user's workspaces."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.audit import record_audit_event
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role
from flowsage_backend.rate_limit import AUTH_RATE_LIMIT, limiter, resolve_signature
from flowsage_backend.security import create_access_token, dummy_password_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SwitchWorkspaceRequest(BaseModel):
    workspace_id: uuid.UUID


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    name: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime
    workspace_id: uuid.UUID
    role: Role
    workspaces: list[WorkspaceSummary]


def _set_session_cookie(
    response: Response, request: Request, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    settings = request.app.state.settings
    token = create_access_token(
        user_id,
        workspace_id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expires_minutes,
    )
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expires_minutes * 60,
    )


async def _first_membership_or_401(session: AsyncSession, user_id: uuid.UUID) -> Membership:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at)
    )
    membership = result.scalars().first()
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User has no workspace membership")
    return membership


@router.post("/login", response_model=MeOut)
@resolve_signature
@limiter.limit(AUTH_RATE_LIMIT)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> MeOut:
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    # Always hash-verify, even for an unknown email, so a wrong password and an unknown
    # email take about the same time -- see dummy_password_hash's docstring.
    password_ok = verify_password(
        payload.password, user.hashed_password if user is not None else dummy_password_hash()
    )
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    membership = await _first_membership_or_401(session, user.id)
    _set_session_cookie(response, request, user.id, membership.workspace_id)
    await record_audit_event(
        session,
        membership.workspace_id,
        actor_user_id=user.id,
        action="auth.login",
        ip_address=request.client.host if request.client else None,
    )
    return await _build_me_out(session, user, membership)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Deliberately unauthenticated (unlike every other route here): logout must
    stay safe to call even with no session or an already-expired one, so it can't
    401 a client that's just trying to clear a stale cookie. It still audits the
    logout when a valid session happens to be present."""
    settings = request.app.state.settings
    response.delete_cookie(
        settings.cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    try:
        user, membership = await get_current_membership(request, session)
    except HTTPException:
        pass
    else:
        await record_audit_event(
            session, membership.workspace_id, actor_user_id=user.id, action="auth.logout"
        )
    return {"status": "logged_out"}


async def _build_me_out(session: AsyncSession, user: User, membership: Membership) -> MeOut:
    result = await session.execute(
        select(Membership)
        .where(Membership.user_id == user.id)
        .options(selectinload(Membership.workspace))
    )
    memberships = result.scalars().all()
    return MeOut(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        workspace_id=membership.workspace_id,
        role=membership.role,
        workspaces=[
            WorkspaceSummary(id=m.workspace_id, name=m.workspace.name) for m in memberships
        ],
    )


@router.get("/me", response_model=MeOut)
async def me(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> MeOut:
    user, membership = membership_pair
    return await _build_me_out(session, user, membership)


@router.post("/switch-workspace", response_model=MeOut)
async def switch_workspace(
    payload: SwitchWorkspaceRequest,
    request: Request,
    response: Response,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> MeOut:
    user, _ = membership_pair
    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id, Membership.workspace_id == payload.workspace_id
        )
    )
    target_membership = result.scalar_one_or_none()
    if target_membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of that workspace")

    _set_session_cookie(response, request, user.id, target_membership.workspace_id)
    return await _build_me_out(session, user, target_membership)
