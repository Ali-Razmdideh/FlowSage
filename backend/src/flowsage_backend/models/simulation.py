"""Simulation run/step/friction-issue records.

Mirrors the plan's Postgres data model: `SimulationRun` (flow, persona, goal_path,
status, progress) + `SimulationStep` (feeds the Agentic Orchestration log) +
`FrictionIssue` (severity, heuristic, remediation).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowsage_backend.models.base import Base
from flowsage_backend.models.persona import Persona


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class SimulationRun(Base):
    __tablename__ = "simulation_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    flow_name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(String(500))
    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("personas.id"))
    screenshots_dir: Mapped[str] = mapped_column(String(1000))
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus, name="run_status"), default=RunStatus.QUEUED
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    persona: Mapped[Persona] = relationship()
    steps: Mapped[list["SimulationStep"]] = relationship(
        back_populates="run", order_by="SimulationStep.sequence", cascade="all, delete-orphan"
    )
    issues: Mapped[list["FrictionIssue"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SimulationStep(Base):
    __tablename__ = "simulation_steps"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"))
    sequence: Mapped[int] = mapped_column(Integer)
    screen: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[SimulationRun] = relationship(back_populates="steps")


class FrictionIssue(Base):
    __tablename__ = "friction_issues"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("simulation_runs.id", ondelete="CASCADE"))
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("simulation_steps.id", ondelete="SET NULL"), nullable=True
    )
    screen: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(300))
    heuristic_violated: Mapped[str] = mapped_column(String(200))
    persona_impact: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    suggested_fix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[SimulationRun] = relationship(back_populates="issues")
