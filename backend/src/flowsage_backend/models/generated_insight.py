"""Cache of Claude-generated narrative text for report surfaces that were
previously deterministic templates: churn.py's Node Intelligence `ai_insight`,
calibration.py's `PersonaCalibration.narrative`, and retraining.py's
`PersonaMemory.note`. Rows are only ever written after a successful
`flowsage_predict.narrative.NarrativeClient` call (see worker.py's
generate_node_insight_job/generate_calibration_narrative_job and
retraining.execute_retraining) -- this also makes "rows created this month"
exactly equal to the insight_generations usage-cap counter in billing.py, with
no separate accounting needed. See
docs/superpowers/specs/2026-08-04-ai-narrative-insights-design.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class GeneratedInsight(Base):
    __tablename__ = "generated_insights"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "cache_key", name="uq_generated_insight_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    cache_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
