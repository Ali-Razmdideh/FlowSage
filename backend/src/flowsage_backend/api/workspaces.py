"""Workspace CRUD (`/settings/general`) and member management (`/settings/team`)."""

from __future__ import annotations

import uuid
import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import record_audit_event
from flowsage_backend.billing import check_within_limits
from flowsage_backend.deps import get_current_membership, get_db_session, require_role
from flowsage_backend.models.user import User
from flowsage_backend.models.flow import Flow, FlowVersion
from flowsage_backend.models.workspace import Membership, Role, Workspace, WorkspacePrivacy
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import SimulationRun

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


class WorkspaceDeleteRequest(BaseModel):
    confirmation: str


@router.get("/current/export")
async def export_current_workspace(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    events = list((await session.execute(select(Event).where(Event.workspace_id == membership.workspace_id))).scalars())
    runs = list((await session.execute(select(SimulationRun).where(SimulationRun.workspace_id == membership.workspace_id))).scalars())
    payload = {
        "workspace": {"id": str(workspace.id), "name": workspace.name} if workspace else None,
        "events": [{"session_id": e.session_id, "screen": e.screen, "event": e.event, "timestamp": e.timestamp.isoformat()} for e in events],
        "simulation_runs": [{"id": str(r.id), "flow_name": r.flow_name, "status": r.status.value} for r in runs],
    }
    await record_audit_event(session, membership.workspace_id, actor_user_id=membership.user_id, action="workspace.exported")
    return Response(json.dumps(payload), media_type="application/json", headers={"Content-Disposition": "attachment; filename=flowsage-workspace-export.json"})


@router.delete("/current", status_code=status.HTTP_204_NO_CONTENT)
async def delete_current_workspace(
    payload: WorkspaceDeleteRequest,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    _, membership = membership_pair
    if payload.confirmation != "DELETE WORKSPACE":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Confirmation must be DELETE WORKSPACE")
    workspace = await session.get(Workspace, membership.workspace_id)
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workspace not found")
    workspace_id = membership.workspace_id
    await session.delete(workspace)
    await session.commit()
    try:
        await asyncio.to_thread(request.app.state.graph_sink.purge_before, str(workspace_id), datetime.max)
    except Exception:
        pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class FlowCreate(BaseModel):
    key: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)


class FlowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    key: str
    name: str
    version: int


@router.get("/current/flows", response_model=list[FlowOut])
async def list_flows(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[FlowOut]:
    _, membership = membership_pair
    rows = (await session.execute(
        select(Flow, FlowVersion.version).join(FlowVersion).where(Flow.workspace_id == membership.workspace_id)
    )).all()
    return [FlowOut(id=flow.id, key=flow.key, name=flow.name, version=version) for flow, version in rows]


@router.post("/current/flows", response_model=FlowOut, status_code=status.HTTP_201_CREATED)
async def create_flow(
    payload: FlowCreate,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.RESEARCHER)),
    session: AsyncSession = Depends(get_db_session),
) -> FlowOut:
    _, membership = membership_pair
    flow = Flow(workspace_id=membership.workspace_id, key=payload.key, name=payload.name)
    session.add(flow)
    await session.flush()
    version = FlowVersion(flow_id=flow.id, version=1)
    session.add(version)
    await session.commit()
    return FlowOut(id=flow.id, key=flow.key, name=flow.name, version=1)


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
    await record_audit_event(
        session,
        membership.workspace_id,
        actor_user_id=membership.user_id,
        action="workspace.archived",
    )
    return workspace


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: Role
    created_at: datetime


class MemberAdd(BaseModel):
    email: str = Field(min_length=1)
    role: Role


class MemberRoleUpdate(BaseModel):
    role: Role


async def _admin_count(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    result = await session.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.role == Role.ADMIN
        )
    )
    return len(result.scalars().all())


async def _get_membership_in_workspace(
    session: AsyncSession, membership_id: uuid.UUID, workspace_id: uuid.UUID
) -> Membership:
    membership = await session.get(Membership, membership_id)
    if membership is None or membership.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found in this workspace")
    return membership


@router.get("/current/members", response_model=list[MemberOut])
async def list_members(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[MemberOut]:
    _, membership = membership_pair
    result = await session.execute(
        select(Membership, User)
        .join(User, Membership.user_id == User.id)
        .where(Membership.workspace_id == membership.workspace_id)
        .order_by(User.email)
    )
    return [
        MemberOut(
            id=m.id, user_id=m.user_id, email=user.email, role=m.role, created_at=m.created_at
        )
        for m, user in result.all()
    ]


@router.post("/current/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberAdd,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> MemberOut:
    _, membership = membership_pair
    await check_within_limits(session, membership.workspace_id, "seats")
    result = await session.execute(select(User).where(User.email == payload.email))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account with that email")

    existing = await session.execute(
        select(Membership).where(
            Membership.user_id == target_user.id, Membership.workspace_id == membership.workspace_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That user is already a member")

    new_membership = Membership(
        user_id=target_user.id, workspace_id=membership.workspace_id, role=payload.role
    )
    session.add(new_membership)
    await session.commit()
    await session.refresh(new_membership)
    await record_audit_event(
        session,
        membership.workspace_id,
        actor_user_id=membership.user_id,
        action="member.invited",
        target_type="membership",
        target_id=str(new_membership.id),
        extra_data={"email": target_user.email, "role": new_membership.role.value},
    )
    return MemberOut(
        id=new_membership.id,
        user_id=target_user.id,
        email=target_user.email,
        role=new_membership.role,
        created_at=new_membership.created_at,
    )


@router.patch("/current/members/{membership_id}", response_model=MemberOut)
async def update_member_role(
    membership_id: uuid.UUID,
    payload: MemberRoleUpdate,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> MemberOut:
    _, membership = membership_pair
    target = await _get_membership_in_workspace(session, membership_id, membership.workspace_id)

    if target.role == Role.ADMIN and payload.role != Role.ADMIN:
        if await _admin_count(session, membership.workspace_id) <= 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Workspace must keep at least one admin"
            )

    target.role = payload.role
    await session.commit()
    await session.refresh(target)
    await record_audit_event(
        session,
        membership.workspace_id,
        actor_user_id=membership.user_id,
        action="member.role_changed",
        target_type="membership",
        target_id=str(target.id),
        extra_data={"to_role": target.role.value},
    )
    user = await session.get(User, target.user_id)
    assert user is not None
    return MemberOut(
        id=target.id,
        user_id=target.user_id,
        email=user.email,
        role=target.role,
        created_at=target.created_at,
    )


@router.delete(
    "/current/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
async def remove_member(
    membership_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    target = await _get_membership_in_workspace(session, membership_id, membership.workspace_id)

    if target.role == Role.ADMIN and await _admin_count(session, membership.workspace_id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workspace must keep at least one admin")

    await session.delete(target)
    await session.commit()
    await record_audit_event(
        session,
        membership.workspace_id,
        actor_user_id=membership.user_id,
        action="member.removed",
        target_type="membership",
        target_id=str(target.id),
    )
