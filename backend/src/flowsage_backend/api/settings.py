"""`/settings/model-calibration`: the per-workspace calibration/alerting
settings row (see `flowsage_backend.models.settings.CalibrationSettings`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.settings import CalibrationSettings, DigestFrequency
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership
from flowsage_backend.settings_store import get_or_create_calibration_settings

router = APIRouter(
    prefix="/settings/model-calibration",
    tags=["settings"],
    dependencies=[Depends(get_current_membership)],
)


class CalibrationSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    anomaly_threshold: float
    churn_risk_alert_threshold: float
    auto_retrain_on_anomaly: bool
    digest_frequency: DigestFrequency


class CalibrationSettingsUpdate(BaseModel):
    anomaly_threshold: float = Field(ge=0.0, le=1.0)
    churn_risk_alert_threshold: float = Field(ge=0.0, le=1.0)
    auto_retrain_on_anomaly: bool
    digest_frequency: DigestFrequency


@router.get("", response_model=CalibrationSettingsOut)
async def get_model_calibration_settings(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CalibrationSettings:
    _, membership = membership_pair
    return await get_or_create_calibration_settings(session, membership.workspace_id)


@router.patch("", response_model=CalibrationSettingsOut)
async def update_model_calibration_settings(
    payload: CalibrationSettingsUpdate,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CalibrationSettings:
    _, membership = membership_pair
    settings = await get_or_create_calibration_settings(session, membership.workspace_id)
    settings.anomaly_threshold = payload.anomaly_threshold
    settings.churn_risk_alert_threshold = payload.churn_risk_alert_threshold
    settings.auto_retrain_on_anomaly = payload.auto_retrain_on_anomaly
    settings.digest_frequency = payload.digest_frequency
    await session.commit()
    await session.refresh(settings)
    return settings
