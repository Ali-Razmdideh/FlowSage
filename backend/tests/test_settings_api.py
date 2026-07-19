from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.settings import DigestFrequency
from flowsage_backend.seed import upsert_user
from flowsage_backend.settings_store import get_or_create_calibration_settings


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "settings-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "settings-api@example.com", "password": "hunter2"}
        )
        yield client


async def test_get_model_calibration_settings_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/settings/model-calibration")

    assert response.status_code == 401


async def test_get_model_calibration_settings_returns_defaults_on_first_access(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.get("/settings/model-calibration")

    assert response.status_code == 200
    body = response.json()
    assert 0.0 <= body["anomaly_threshold"] <= 1.0
    assert 0.0 <= body["churn_risk_alert_threshold"] <= 1.0
    assert isinstance(body["auto_retrain_on_anomaly"], bool)
    assert body["digest_frequency"] in ("daily", "weekly")


async def test_patch_model_calibration_settings_persists_and_is_bounded(
    app: FastAPI, db_session: AsyncSession
) -> None:
    calibration_settings = await get_or_create_calibration_settings(db_session)
    original = {
        "anomaly_threshold": calibration_settings.anomaly_threshold,
        "churn_risk_alert_threshold": calibration_settings.churn_risk_alert_threshold,
        "auto_retrain_on_anomaly": calibration_settings.auto_retrain_on_anomaly,
        "digest_frequency": calibration_settings.digest_frequency.value,
    }

    try:
        async with _authed_client(app, db_session) as client:
            update_response = await client.patch(
                "/settings/model-calibration",
                json={
                    "anomaly_threshold": 0.2,
                    "churn_risk_alert_threshold": 0.75,
                    "auto_retrain_on_anomaly": True,
                    "digest_frequency": "daily",
                },
            )
            assert update_response.status_code == 200
            body = update_response.json()
            assert body["anomaly_threshold"] == 0.2
            assert body["churn_risk_alert_threshold"] == 0.75
            assert body["auto_retrain_on_anomaly"] is True
            assert body["digest_frequency"] == "daily"

            get_response = await client.get("/settings/model-calibration")
            assert get_response.json()["anomaly_threshold"] == 0.2

            out_of_bounds_response = await client.patch(
                "/settings/model-calibration",
                json={
                    "anomaly_threshold": 1.5,
                    "churn_risk_alert_threshold": 0.5,
                    "auto_retrain_on_anomaly": False,
                    "digest_frequency": "weekly",
                },
            )
            assert out_of_bounds_response.status_code == 422
    finally:
        calibration_settings.anomaly_threshold = original["anomaly_threshold"]
        calibration_settings.churn_risk_alert_threshold = original["churn_risk_alert_threshold"]
        calibration_settings.auto_retrain_on_anomaly = original["auto_retrain_on_anomaly"]
        calibration_settings.digest_frequency = DigestFrequency(original["digest_frequency"])
        await db_session.commit()
