"""Singleton accessor for `CalibrationSettings` (Phase 2 chunk 4).

Single-tenant, so there is exactly one settings row; created lazily on first
access with the same defaults as `calibration.ANOMALY_THRESHOLD` /
`alerts.CHURN_RISK_ALERT_THRESHOLD` so behavior is unchanged until a caller
edits `/settings/model-calibration`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.settings import CalibrationSettings


async def get_or_create_calibration_settings(session: AsyncSession) -> CalibrationSettings:
    result = await session.execute(select(CalibrationSettings).limit(1))
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings

    settings = CalibrationSettings()
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings
