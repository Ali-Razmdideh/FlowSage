"""Recurring simulation configs, fired by worker.py's scheduled-simulations cron
job. See docs/superpowers/specs/2026-08-01-scheduled-simulations-trend-design.md
for why this only supports an API-push screenshot trigger, not live-URL capture
-- that capture pipeline doesn't exist anywhere in this codebase yet.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class ScheduleInterval(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    ON_PUSH = "on_push"


class ScheduledSimulation(Base):
    __tablename__ = "scheduled_simulations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    flow_name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(String(500))
    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("personas.id"))
    interval: Mapped[ScheduleInterval] = mapped_column(
        SAEnum(ScheduleInterval, name="schedule_interval")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    pending_screenshots_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
