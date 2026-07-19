"""LLM persona records (README §Features: configurable personas).

Mirrors `flowsage_predict.models.Persona`; `to_predict_persona()` converts a row into
that package's Pydantic model so the same LangGraph agent code runs whether it's
invoked from the CLI or from a simulation job here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from flowsage_predict.models import BehavioralSliders, DemographicAnchors
from flowsage_predict.models import Persona as PredictPersona
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowsage_backend.models.base import Base


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    baseline: Mapped[bool] = mapped_column(Boolean, default=False)

    tech_affinity: Mapped[str] = mapped_column(String(64))
    primary_device: Mapped[str] = mapped_column(String(64))
    discovery_mode: Mapped[str] = mapped_column(String(64))
    contextual_triggers: Mapped[list[str]] = mapped_column(JSONB, default=list)

    technical_literacy: Mapped[float] = mapped_column(Float)
    anxiety: Mapped[float] = mapped_column(Float)
    patience: Mapped[float] = mapped_column(Float)
    curiosity: Mapped[float] = mapped_column(Float)

    model: Mapped[str] = mapped_column(String(64), default="claude-sonnet-4-5")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memories: Mapped[list["PersonaMemory"]] = relationship(
        back_populates="persona",
        cascade="all, delete-orphan",
        order_by="PersonaMemory.created_at.desc()",
    )

    def to_predict_persona(self) -> PredictPersona:
        return PredictPersona(
            id=self.slug,
            name=self.name,
            description=self.description,
            baseline=self.baseline,
            demographic_anchors=DemographicAnchors(
                tech_affinity=self.tech_affinity,
                primary_device=self.primary_device,
                discovery_mode=self.discovery_mode,
            ),
            contextual_triggers=list(self.contextual_triggers),
            sliders=BehavioralSliders(
                technical_literacy=self.technical_literacy,
                anxiety=self.anxiety,
                patience=self.patience,
                curiosity=self.curiosity,
            ),
            model=self.model,
        )


class PersonaMemory(Base):
    """A note appended to a persona's history, e.g. by a retraining job explaining
    what behavioral evidence it saw and how it adjusted the persona's sliders."""

    __tablename__ = "persona_memories"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    persona_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("personas.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    note: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="retraining")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    persona: Mapped[Persona] = relationship(back_populates="memories")
