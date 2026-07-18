import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.calibration import ScreenCalibration
from flowsage_backend.models.calibration import RetrainingStatus
from flowsage_backend.models.event import Event
from flowsage_backend.models.persona import Persona, PersonaMemory
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.retraining import (
    RetrainingError,
    create_retraining_job,
    execute_retraining,
    nudge_sliders,
)
from flowsage_backend.seed import seed_baseline_personas


def _persona(**overrides: float) -> Persona:
    defaults = dict(
        slug="test",
        name="Test",
        description="d",
        baseline=False,
        tech_affinity="high",
        primary_device="desktop",
        discovery_mode="search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    defaults.update(overrides)
    return Persona(**defaults)  # type: ignore[arg-type]


def test_nudge_sliders_raises_anxiety_when_reality_is_worse_than_predicted() -> None:
    persona = _persona(technical_literacy=0.5, anxiety=0.5, patience=0.5)
    anomalies = [
        ScreenCalibration(
            screen="checkout", predicted_score=0.2, observed_score=0.9, delta=0.7, anomaly=True
        )
    ]

    literacy, anxiety, patience = nudge_sliders(persona, anomalies)

    assert anxiety > 0.5
    assert patience < 0.5
    assert literacy < 0.5


def test_nudge_sliders_lowers_anxiety_when_reality_is_better_than_predicted() -> None:
    persona = _persona(technical_literacy=0.5, anxiety=0.5, patience=0.5)
    anomalies = [
        ScreenCalibration(
            screen="checkout", predicted_score=0.9, observed_score=0.1, delta=-0.8, anomaly=True
        )
    ]

    literacy, anxiety, patience = nudge_sliders(persona, anomalies)

    assert anxiety < 0.5
    assert patience > 0.5
    assert literacy > 0.5


def test_nudge_sliders_clamps_to_zero_one() -> None:
    persona = _persona(technical_literacy=0.02, anxiety=0.98, patience=0.02)
    anomalies = [
        ScreenCalibration(
            screen="checkout", predicted_score=0.0, observed_score=1.0, delta=1.0, anomaly=True
        )
    ] * 10

    literacy, anxiety, patience = nudge_sliders(persona, anomalies)

    assert 0.0 <= literacy <= 1.0
    assert 0.0 <= anxiety <= 1.0
    assert 0.0 <= patience <= 1.0


async def test_create_retraining_job_requires_existing_persona(db_session: AsyncSession) -> None:
    with pytest.raises(RetrainingError, match="No persona"):
        await create_retraining_job(db_session, uuid.uuid4())


async def test_execute_retraining_raises_for_unknown_job(db_session: AsyncSession) -> None:
    with pytest.raises(RetrainingError, match="No retraining job"):
        await execute_retraining(db_session, uuid.uuid4())


async def test_execute_retraining_nudges_sliders_and_writes_memory(
    db_session: AsyncSession,
) -> None:
    personas = await seed_baseline_personas(db_session)
    persona = personas[0]
    original_anxiety = persona.anxiety

    run = SimulationRun(
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            run_id=run.id,
            screen="cal_retrain_checkout",
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    # Namespaced session ids so this test's rows don't collide with other
    # files' event data in the shared, un-truncated `events` table (this
    # suite's Postgres fixture is session-scoped -- see conftest.py) -- cleaned
    # up in the `finally` block below.
    session_ids = [f"cal-retrain-{i}" for i in range(10)]
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                session_id=session_id,
                screen="cal_retrain_checkout",
                event="view",
                timestamp=now,
                device="desktop",
                cohort="default",
            )
        )
    # Only the first session continues on to "confirmation" -- discover_funnel's
    # drop_off_rate is measured against the *next* screen in the discovered path,
    # so a single-screen event set would always compute 0 drop-off (nothing to
    # compare against). 1 of 10 sessions continuing -> 90% drop-off, far above the
    # "low" predicted severity -> anomaly.
    db_session.add(
        Event(
            session_id=session_ids[0],
            screen="cal_retrain_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
            device="desktop",
            cohort="default",
        )
    )
    await db_session.commit()

    try:
        job = await create_retraining_job(db_session, persona.id)
        assert job.status == RetrainingStatus.QUEUED

        await execute_retraining(db_session, job.id)

        await db_session.refresh(job)
        assert job.status == RetrainingStatus.COMPLETED
        assert job.progress == 100.0
        assert job.finished_at is not None

        await db_session.refresh(persona)
        assert persona.anxiety != original_anxiety

        result = await db_session.execute(
            select(PersonaMemory).where(PersonaMemory.persona_id == persona.id)
        )
        memories = result.scalars().all()
        assert len(memories) == 1
        assert memories[0].kind == "retraining"
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
