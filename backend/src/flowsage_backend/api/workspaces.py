"""Workspace CRUD (`/settings/general`) and member management (`/settings/team`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session, require_role
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role, Workspace, WorkspacePrivacy

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str
    avatar_url: str | None
    privacy: WorkspacePrivacy
    region: str
    retention_days: int
    archived: bool
    created_at: datetime


class WorkspaceSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: Role


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str
    avatar_url: str | None = None
    privacy: WorkspacePrivacy
    region: str = Field(min_length=1, max_length=64)
    retention_days: int = Field(ge=1, le=3650)


@router.get("", response_model=list[WorkspaceSummaryOut])
async def list_workspaces(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[WorkspaceSummaryOut]:
    user, _ = membership_pair
    result = await session.execute(
        select(Membership, Workspace)
        .join(Workspace, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
    )
    return [
        WorkspaceSummaryOut(id=workspace.id, name=workspace.name, role=membership.role)
        for membership, workspace in result.all()
    ]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    user, _ = membership_pair
    workspace = Workspace(name=payload.name, slug=f"fs-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.flush()
    session.add(Membership(user_id=user.id, workspace_id=workspace.id, role=Role.ADMIN))
    await session.commit()
    await session.refresh(workspace)
    return workspace


@router.get("/current", response_model=WorkspaceOut)
async def get_current_workspace(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None  # guaranteed by the FK + get_current_membership's lookup
    return workspace


@router.patch("/current", response_model=WorkspaceOut)
async def update_current_workspace(
    payload: WorkspaceUpdate,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    workspace.name = payload.name
    workspace.description = payload.description
    workspace.avatar_url = payload.avatar_url
    workspace.privacy = payload.privacy
    workspace.region = payload.region
    workspace.retention_days = payload.retention_days
    await session.commit()
    await session.refresh(workspace)
    return workspace


@router.post("/current/archive", response_model=WorkspaceOut)
async def archive_current_workspace(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    workspace.archived = True
    await session.commit()
    await session.refresh(workspace)
    return workspace
