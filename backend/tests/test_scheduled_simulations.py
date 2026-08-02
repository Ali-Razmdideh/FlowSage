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
    assert friction_score_for_run([], 0) == 0.0


def test_friction_score_for_run_zero_screens_walked_is_zero() -> None:
    # Defensive: even with issues present, a 0 (or negative) walked-screen
    # count must not divide-by-zero -- it should read as "no data" (0.0).
    assert friction_score_for_run([_issue("cart", "high")], 0) == 0.0


def test_friction_score_for_run_averages_per_screen_max_severity() -> None:
    # screen "cart": max(low=0.2, high=0.7) = 0.7; screen "pay": medium = 0.45
    # mean = (0.7 + 0.45) / 2 = 0.575 (2 screens walked, matches issue count)
    issues = [
        _issue("cart", "low"),
        _issue("cart", "high"),
        _issue("pay", "medium"),
    ]
    assert friction_score_for_run(issues, 2) == 0.575


def test_friction_score_for_run_denominator_is_screens_walked_not_screens_with_issues() -> None:
    # A screen the persona walked but that produced NO issue must still count
    # in the denominator (as an implicit 0.0), not be dropped from it -- the
    # bug this fix addresses (regression finding #2).
    issues = [_issue("cart", "high")]  # only "cart" has an issue
    # Persona also walked "pay" cleanly -- 2 screens walked total.
    assert friction_score_for_run(issues, 2) == 0.35  # 0.7 / 2, not 0.7 / 1


def test_friction_score_for_run_fix_does_not_falsely_raise_score() -> None:
    """Regression test for the exact bug scenario the review found: a run
    with cart=critical, pay=low (2 screens walked, 2 issues) followed by a
    'fix' run where only cart=critical remains but the persona still walked
    BOTH screens (2 screens walked, 1 issue). The score must not increase --
    fixing the pay issue is a strict improvement, and the denominator must
    keep reflecting screens walked, not screens with issues."""
    before_issues = [_issue("cart", "critical"), _issue("pay", "low")]
    before_score = friction_score_for_run(before_issues, 2)

    after_issues = [_issue("cart", "critical")]
    after_score = friction_score_for_run(after_issues, 2)

    assert after_score <= before_score


from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing import TIER_LIMITS
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.simulation import SimulationRun
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


async def test_fire_due_scheduled_simulations_stops_at_free_tier_runs_cap(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Regression test: a workspace already at its Free-tier runs-per-month
    cap must not have a scheduled config fire unthrottled -- and the
    config's pending set must be left staged (not silently dropped) so it
    can fire once the cap clears."""
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    cap = TIER_LIMITS[SubscriptionTier.FREE].runs_per_month
    db_session.add_all(
        [
            SimulationRun(
                workspace_id=workspace_id,
                flow_name="Checkout",
                goal="Complete purchase",
                persona_id=personas[0].id,
                screenshots_dir="/tmp/does-not-matter",
            )
            for _ in range(cap)
        ]
    )
    await db_session.commit()

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

    assert fired == []
    await db_session.refresh(config)
    assert config.pending_screenshots_dir == str(screenshots_dir)
    assert config.last_fired_at is None


async def test_fire_due_scheduled_simulations_fires_normally_when_under_cap(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Regression guard: a workspace with room left in its budget (one slot
    short of the cap) must still fire its due config normally."""
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    cap = TIER_LIMITS[SubscriptionTier.FREE].runs_per_month
    db_session.add_all(
        [
            SimulationRun(
                workspace_id=workspace_id,
                flow_name="Checkout",
                goal="Complete purchase",
                persona_id=personas[0].id,
                screenshots_dir="/tmp/does-not-matter",
            )
            for _ in range(cap - 1)
        ]
    )
    await db_session.commit()

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
    await db_session.refresh(config)
    assert config.pending_screenshots_dir is None
    assert config.last_fired_at is not None


async def test_fire_due_scheduled_simulations_isolates_per_config_failures(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Regression test: one config's `create_run` failure (here, an empty
    staged directory tripping `SimulationError`'s "No screenshots found"
    path) must not abort the whole batch -- an unrelated, independently due
    config must still fire, and the function itself must not raise."""
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    failing_config = await create_scheduled_simulation(
        db_session,
        workspace_id=workspace_id,
        persona_id=personas[0].id,
        flow_name="Failing Flow",
        goal="Complete purchase",
        interval=ScheduleInterval.ON_PUSH,
        created_by=None,
    )
    good_config = await create_scheduled_simulation(
        db_session,
        workspace_id=workspace_id,
        persona_id=personas[0].id,
        flow_name="Good Flow",
        goal="Complete purchase",
        interval=ScheduleInterval.ON_PUSH,
        created_by=None,
    )

    failing_dir = tmp_path / "failing"
    failing_dir.mkdir()  # left empty -- create_run raises SimulationError
    await stage_screenshots(db_session, failing_config, failing_dir)

    good_dir = tmp_path / "good"
    good_dir.mkdir()
    (good_dir / "01_cart.png").write_bytes(_PNG_BYTES)
    await stage_screenshots(db_session, good_config, good_dir)

    fired = await fire_due_scheduled_simulations(
        db_session, workspace_id, datetime.now(timezone.utc)
    )

    assert len(fired) == 1
    assert fired[0].scheduled_simulation_id == good_config.id

    await db_session.refresh(failing_config)
    await db_session.refresh(good_config)
    # The failing config's pending set is left staged so it can be retried
    # (e.g. once the caller pushes a non-empty set), not silently dropped.
    assert failing_config.pending_screenshots_dir == str(failing_dir)
    assert failing_config.last_fired_at is None
    # The unrelated good config fired normally despite the other's failure.
    assert good_config.pending_screenshots_dir is None
    assert good_config.last_fired_at is not None
