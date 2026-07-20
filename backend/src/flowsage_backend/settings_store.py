"""Per-workspace accessor for `CalibrationSettings`.

Created lazily on first access per workspace, with the same defaults as
`calibration.ANOMALY_THRESHOLD` / `alerts.CHURN_RISK_ALERT_THRESHOLD` so behavior is
unchanged until a caller edits `/settings/model-calibration`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.settings import CalibrationSettings


async def get_or_create_calibration_settings(
    session: AsyncSession, workspace_id: uuid.UUID
) -> CalibrationSettings:
    result = await session.execute(
        select(CalibrationSettings).where(CalibrationSettings.workspace_id == workspace_id).limit(1)
    )
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings

    settings = CalibrationSettings(workspace_id=workspace_id)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings
