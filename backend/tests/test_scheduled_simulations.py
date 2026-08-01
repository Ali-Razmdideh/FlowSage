import uuid
from datetime import datetime, timedelta, timezone

from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import FrictionIssue
from flowsage_backend.scheduled_simulations import friction_score_for_run, is_due

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _config(
    interval: ScheduleInterval,
    *,
    active: bool = True,
    pending: str | None = "/tmp/pending",
    last_fired_at: datetime | None = None,
) -> ScheduledSimulation:
    return ScheduledSimulation(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=uuid.uuid4(),
        interval=interval,
        active=active,
        pending_screenshots_dir=pending,
        last_fired_at=last_fired_at,
    )


def test_is_due_false_when_inactive() -> None:
    config = _config(ScheduleInterval.ON_PUSH, active=False)
    assert is_due(config, _NOW) is False


def test_is_due_false_when_no_pending_screenshots() -> None:
    config = _config(ScheduleInterval.ON_PUSH, pending=None)
    assert is_due(config, _NOW) is False


def test_is_due_on_push_fires_immediately_once_pending() -> None:
    config = _config(ScheduleInterval.ON_PUSH)
    assert is_due(config, _NOW) is True


def test_is_due_daily_fires_when_never_fired() -> None:
    config = _config(ScheduleInterval.DAILY, last_fired_at=None)
    assert is_due(config, _NOW) is True


def test_is_due_daily_false_just_under_24h() -> None:
    config = _config(ScheduleInterval.DAILY, last_fired_at=_NOW - timedelta(hours=23, minutes=59))
    assert is_due(config, _NOW) is False


def test_is_due_daily_true_at_24h() -> None:
    config = _config(ScheduleInterval.DAILY, last_fired_at=_NOW - timedelta(hours=24))
    assert is_due(config, _NOW) is True


def test_is_due_weekly_false_just_under_7d() -> None:
    config = _config(ScheduleInterval.WEEKLY, last_fired_at=_NOW - timedelta(days=6, hours=23))
    assert is_due(config, _NOW) is False


def test_is_due_weekly_true_at_7d() -> None:
    config = _config(ScheduleInterval.WEEKLY, last_fired_at=_NOW - timedelta(days=7))
    assert is_due(config, _NOW) is True


def _issue(screen: str, severity: str) -> FrictionIssue:
    return FrictionIssue(
        workspace_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        step_id=uuid.uuid4(),
        screen=screen,
        severity=severity,
        title="t",
        heuristic_violated="h",
        persona_impact="p",
        description="d",
        suggested_fix="f",
    )


def test_friction_score_for_run_empty_issues_is_zero() -> None:
    assert friction_score_for_run([]) == 0.0


def test_friction_score_for_run_averages_per_screen_max_severity() -> None:
    # screen "cart": max(low=0.2, high=0.7) = 0.7; screen "pay": medium = 0.45
    # mean = (0.7 + 0.45) / 2 = 0.575
    issues = [
        _issue("cart", "low"),
        _issue("cart", "high"),
        _issue("pay", "medium"),
    ]
    assert friction_score_for_run(issues) == 0.575


from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.workspace import Workspace
from flowsage_backend.scheduled_simulations import (
    ScheduledSimulationError,
    build_trend,
    create_scheduled_simulation,
    fire_due_scheduled_simulations,
    latest_two_completed_runs,
    stage_screenshots,
)
from flowsage_backend.seed import seed_baseline_personas

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-but-good-enough-for-a-suffix-check"


async def _create_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


async def test_create_scheduled_simulation_rejects_unknown_persona(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _create_workspace(db_session)
    try:
        await create_scheduled_simulation(
            db_session,
            workspace_id=workspace_id,
            persona_id=uuid.uuid4(),
            flow_name="Checkout",
            goal="Complete purchase",
            interval=ScheduleInterval.DAILY,
            created_by=None,
        )
        assert False, "expected ScheduledSimulationError"
    except ScheduledSimulationError:
        pass


async def test_fire_due_scheduled_simulations_creates_run_and_clears_pending(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    config = await create_scheduled_simulation(
        db_session,
        workspace_id=workspace_id,
        persona_id=personas[0].id,
        flow_name="Checkout",
        goal="Complete purchase",
        interval=ScheduleInterval.ON_PUSH,
        created_by=None,
    )

    screenshots_dir = tmp_path / "pending"
    screenshots_dir.mkdir()
    (screenshots_dir / "01_cart.png").write_bytes(_PNG_BYTES)
    await stage_screenshots(db_session, config, screenshots_dir)

    fired = await fire_due_scheduled_simulations(
        db_session, workspace_id, datetime.now(timezone.utc)
    )

    assert len(fired) == 1
    assert fired[0].scheduled_simulation_id == config.id
    await db_session.refresh(config)
    assert config.pending_screenshots_dir is None
    assert config.last_fired_at is not None


async def test_fire_due_scheduled_simulations_skips_when_not_due(db_session: AsyncSession) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    await create_scheduled_simulation(
        db_session,
        workspace_id=workspace_id,
        persona_id=personas[0].id,
        flow_name="Checkout",
        goal="Complete purchase",
        interval=ScheduleInterval.DAILY,
        created_by=None,
    )
    # No screenshots staged -- is_due is False for every interval.
    fired = await fire_due_scheduled_simulations(
        db_session, workspace_id, datetime.now(timezone.utc)
    )
    assert fired == []


async def test_build_trend_and_latest_two_completed_runs(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    config = await create_scheduled_simulation(
        db_session,
        workspace_id=workspace_id,
        persona_id=personas[0].id,
        flow_name="Checkout",
        goal="Complete purchase",
        interval=ScheduleInterval.ON_PUSH,
        created_by=None,
    )

    # No completed runs yet.
    assert await build_trend(db_session, workspace_id, config.id) == []
    assert await latest_two_completed_runs(db_session, workspace_id, config.id) == []
