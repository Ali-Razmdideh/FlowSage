"""Workspace (tenant) and per-user role membership within it.

`Workspace` is the row-level scoping boundary for every tenant-owned table
(personas, simulation runs, events, etc.) added in Phase 3. `Membership` is
the join between a `User` and a `Workspace`, carrying that user's role in
that workspace -- a user can belong to more than one workspace, each with
its own role.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowsage_backend.models.base import Base


class WorkspacePrivacy(str, enum.Enum):
    PRIVATE = "private"
    RESTRICTED = "restricted"


class Role(str, enum.Enum):
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    ADMIN = "admin"

    def ordinal(self) -> int:
        return {Role.VIEWER: 1, Role.RESEARCHER: 2, Role.ADMIN: 3}[self]


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    privacy: Mapped[WorkspacePrivacy] = mapped_column(
        SAEnum(WorkspacePrivacy, name="workspace_privacy"), default=WorkspacePrivacy.PRIVATE
    )
    region: Mapped[str] = mapped_column(String(64), default="us")
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="membership_role"), default=Role.VIEWER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
