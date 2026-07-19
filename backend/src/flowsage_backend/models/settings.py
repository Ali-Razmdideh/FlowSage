"""Global calibration/alerting settings (Phase 2 chunk 4: `/settings/model-calibration`).

Single-tenant, so this is a **singleton row** rather than per-workspace config
(multi-tenant `Integration`/settings rows are Phase 3 scope). Values here override
the hardcoded defaults in `flowsage_backend.calibration` / `flowsage_backend.alerts`
when present -- see `flowsage_backend.settings_store.get_or_create_calibration_settings`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class DigestFrequency(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"


class CalibrationSettings(Base):
    __tablename__ = "calibration_settings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    anomaly_threshold: Mapped[float] = mapped_column(Float, default=0.35)
    churn_risk_alert_threshold: Mapped[float] = mapped_column(Float, default=0.5)
    auto_retrain_on_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    digest_frequency: Mapped[DigestFrequency] = mapped_column(
        SAEnum(DigestFrequency, name="digest_frequency"), default=DigestFrequency.WEEKLY
    )
    digest_last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
