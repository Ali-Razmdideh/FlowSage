"""Retraining job records.

`RetrainingJob` mirrors `SimulationRun`'s async-job-with-progress shape: enqueued on
arq, worked through by `flowsage_backend.retraining.execute_retraining`, polled via
SSE (see `api/calibration.py`) so the "Persona Re-calibration in Progress" UI has
real data to show instead of a client-side fake progress bar.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowsage_backend.models.base import Base
from flowsage_backend.models.persona import Persona


class RetrainingStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RetrainingJob(Base):
    __tablename__ = "retraining_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("personas.id", ondelete="CASCADE"))
    status: Mapped[RetrainingStatus] = mapped_column(
        SAEnum(RetrainingStatus, name="retraining_status"), default=RetrainingStatus.QUEUED
    )
    epoch: Mapped[int] = mapped_column(Integer, default=0)
    total_epochs: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    persona: Mapped[Persona] = relationship()
