"""`GET /audit-logs`: the Security Logs view's data source (`/settings/security`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import list_audit_logs
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership

router = APIRouter(
    prefix="/audit-logs", tags=["audit"], dependencies=[Depends(get_current_membership)]
)


class AuditLogEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    extra_data: dict[str, object]
    ip_address: str | None
    created_at: datetime


class AuditLogPageOut(BaseModel):
    entries: list[AuditLogEntryOut]
    next_cursor: str | None


@router.get("", response_model=AuditLogPageOut)
async def get_audit_logs(
    action: str | None = Query(default=None),
    actor_id: uuid.UUID | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> AuditLogPageOut:
    _, membership = membership_pair
    entries, next_cursor = await list_audit_logs(
        session,
        membership.workspace_id,
        action=action,
        actor_user_id=actor_id,
        cursor=cursor,
        limit=limit,
    )
    return AuditLogPageOut(
        entries=[AuditLogEntryOut.model_validate(e) for e in entries], next_cursor=next_cursor
    )
