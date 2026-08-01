# Scheduled Simulations + Friction Trend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a workspace configure a recurring simulation (daily/weekly/on-push), have it fire automatically off a shared cron job, track a friction-score trend across its fired runs, and raise a regression alert when the score jumps.

**Architecture:** A new `ScheduledSimulation` config row per recurring simulation, fired by a new arq cron job (`run_scheduled_simulations_job`, hourly tick, per-config due-check — same pattern as the existing `run_digest_job`). Firing reuses `simulations.py`'s existing `create_run()`. Trend and regression scoring reuse `calibration.py`'s existing severity weighting — computed on read, never persisted. Regression alerts extend the existing `AlertsReport`/digest pipeline (`alerts.py`, Slack/webhook delivery already wired in `worker.py`). Frontend gets one new route, `/predictive/scheduled`, linked from the existing Predictive Engine page.

**Tech Stack:** FastAPI + SQLAlchemy (async) + Alembic + arq (Python backend), React + TypeScript + Vitest (frontend), pytest + testcontainers (Postgres/Redis) for backend tests.

## Global Constraints

- Every new route is workspace-scoped: filter every query by `workspace_id` from `get_current_actor` (session cookie or `X-API-Key`), matching `api/simulations.py`'s existing pattern — never trust a path/body `workspace_id`.
- No new persisted score column anywhere: friction score is always computed on read from `FrictionIssue.severity` via `calibration.py`'s `predicted_scores_by_screen()`, so it can never drift out of sync with severity-weight changes.
- Live-URL screenshot capture is explicitly out of scope (see the design spec) — the only new trigger is an authenticated API push of a screenshot set.
- Follow existing formatting/lint/type gates before considering any task done: backend `black` + `autoflake8` + strict `mypy` (per-package config), frontend `tsc` + `oxlint`; full details in Task 8.
- Commit after each task's tests pass, per this repo's established cadence (small, working, tested commits — see recent commit history).

---

### Task 1: Data model — `ScheduledSimulation` + `SimulationRun.scheduled_simulation_id`

**Files:**
- Create: `backend/src/flowsage_backend/models/scheduled_simulation.py`
- Modify: `backend/src/flowsage_backend/models/simulation.py` (add FK column to `SimulationRun`)
- Modify: `backend/src/flowsage_backend/models/__init__.py` (register new model)
- Modify: `backend/src/flowsage_backend/simulations.py` (`create_run` gains an optional `scheduled_simulation_id` kwarg)
- Create: `backend/migrations/versions/7c2f9a4d18be_add_scheduled_simulations_table.py`
- Test: `backend/tests/test_simulations.py` (extend existing file — one new test)

**Interfaces:**
- Produces: `ScheduledSimulation` ORM model (`id`, `workspace_id`, `flow_name`, `goal`, `persona_id`, `interval: ScheduleInterval`, `active`, `pending_screenshots_dir: str | None`, `last_fired_at: datetime | None`, `created_by: uuid.UUID | None`, `created_at`). `ScheduleInterval` enum (`DAILY`, `WEEKLY`, `ON_PUSH`, values `"daily"`/`"weekly"`/`"on_push"`). `SimulationRun.scheduled_simulation_id: uuid.UUID | None`. `create_run(..., scheduled_simulation_id: uuid.UUID | None = None)`.

- [ ] **Step 1: Write the failing test for `create_run`'s new kwarg**

Open `backend/tests/test_simulations.py` and add this test directly after `test_create_run_succeeds` (it reuses that test's exact `_create_workspace`/`_seed_persona`/`tmp_path` pattern, already defined earlier in the file):

```python
async def test_create_run_stamps_scheduled_simulation_id(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    persona = await _seed_persona(db_session, workspace_id)
    (tmp_path / "01_cart.png").write_bytes(b"fake")
    scheduled_id = uuid.uuid4()

    run = await create_run(
        db_session,
        workspace_id=workspace_id,
        persona_id=persona.id,
        flow_name="Checkout",
        goal="Complete purchase",
        screenshots_dir=tmp_path,
        scheduled_simulation_id=scheduled_id,
    )

    assert run.scheduled_simulation_id == scheduled_id
```

`uuid` is already imported in this file (used by `_create_workspace` and other existing tests) — no new import needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_simulations.py::test_create_run_stamps_scheduled_simulation_id -v`
Expected: FAIL — `TypeError: create_run() got an unexpected keyword argument 'scheduled_simulation_id'`

- [ ] **Step 3: Create the `ScheduledSimulation` model**

Write `backend/src/flowsage_backend/models/scheduled_simulation.py`:

```python
"""Recurring simulation configs, fired by worker.py's scheduled-simulations cron
job. See docs/superpowers/specs/2026-08-01-scheduled-simulations-trend-design.md
for why this only supports an API-push screenshot trigger, not live-URL capture
-- that capture pipeline doesn't exist anywhere in this codebase yet.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class ScheduleInterval(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    ON_PUSH = "on_push"


class ScheduledSimulation(Base):
    __tablename__ = "scheduled_simulations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    flow_name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(String(500))
    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("personas.id"))
    interval: Mapped[ScheduleInterval] = mapped_column(
        SAEnum(ScheduleInterval, name="schedule_interval")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    pending_screenshots_dir: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 4: Add the FK column to `SimulationRun`**

In `backend/src/flowsage_backend/models/simulation.py`, find the `SimulationRun` class's `persona_id` column and add a new column directly after it:

```python
    scheduled_simulation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("scheduled_simulations.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

(`ForeignKey` and `Mapped`/`mapped_column` are already imported in this file — no new imports needed. The string table name `"scheduled_simulations"` avoids any Python import of the new model, so there's no circular-import risk between the two model files.)

- [ ] **Step 5: Register the new model in `models/__init__.py`**

In `backend/src/flowsage_backend/models/__init__.py`, add the import and `__all__` entries:

```python
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
```

Insert it alphabetically (after the `Persona`/`PersonaMemory` import, before `CalibrationSettings`... actually alphabetically `scheduled_simulation` sorts after `persona` and before `settings` — place the import line between the `persona` and `settings` imports). Add `"ScheduledSimulation"` and `"ScheduleInterval"` to `__all__` in the same relative position.

- [ ] **Step 6: Add the `scheduled_simulation_id` kwarg to `create_run`**

In `backend/src/flowsage_backend/simulations.py`, modify `create_run`'s signature and body:

```python
async def create_run(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    persona_id: uuid.UUID,
    flow_name: str,
    goal: str,
    screenshots_dir: Path,
    run_id: uuid.UUID | None = None,
    scheduled_simulation_id: uuid.UUID | None = None,
) -> SimulationRun:
    """`run_id` lets a caller that already picked a directory name (e.g. the upload
    endpoint, which needs an id before it can save files) reuse it as the row's id.
    `scheduled_simulation_id` tags a run fired by the scheduled-simulations cron job
    (flowsage_backend.scheduled_simulations.fire_due_scheduled_simulations) so its
    trend/regression queries can find it; a manually-triggered run leaves it None."""
    persona = (
        await session.execute(
            select(Persona).where(Persona.id == persona_id, Persona.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if persona is None:
        raise SimulationError(f"No persona with id {persona_id}")

    if not discover_screenshots(screenshots_dir):
        raise SimulationError(f"No screenshots found in {screenshots_dir}")

    run = SimulationRun(
        id=run_id if run_id is not None else uuid.uuid4(),
        workspace_id=workspace_id,
        flow_name=flow_name,
        goal=goal,
        persona_id=persona.id,
        screenshots_dir=str(screenshots_dir),
        status=RunStatus.QUEUED,
        scheduled_simulation_id=scheduled_simulation_id,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_simulations.py::test_create_run_stamps_scheduled_simulation_id -v`
Expected: PASS

- [ ] **Step 8: Run the full existing simulations test files to check nothing broke**

Run: `cd backend && uv run pytest tests/test_simulations.py tests/test_simulations_api.py -v`
Expected: all PASS (the new kwarg is optional/backward-compatible, so every existing call site is unaffected)

- [ ] **Step 9: Generate and fill in the Alembic migration**

Run: `cd backend && uv run alembic revision -m "add scheduled_simulations table"`

This creates a new file under `backend/migrations/versions/` with an auto-generated revision id and `down_revision = "39f42fc17348"` (the current head). Rename the file to `7c2f9a4d18be_add_scheduled_simulations_table.py` and replace its contents entirely with:

```python
"""add scheduled_simulations table

Revision ID: 7c2f9a4d18be
Revises: 39f42fc17348
Create Date: 2026-08-01 18:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c2f9a4d18be"
down_revision: Union[str, Sequence[str], None] = "39f42fc17348"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scheduled_simulations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("flow_name", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.String(length=500), nullable=False),
        sa.Column("persona_id", sa.Uuid(), nullable=False),
        sa.Column(
            "interval",
            sa.Enum("DAILY", "WEEKLY", "ON_PUSH", name="schedule_interval"),
            nullable=False,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("pending_screenshots_dir", sa.String(length=1000), nullable=True),
        sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_scheduled_simulations_workspace_id"),
        "scheduled_simulations",
        ["workspace_id"],
    )
    op.add_column(
        "simulation_runs", sa.Column("scheduled_simulation_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_simulation_runs_scheduled_simulation_id",
        "simulation_runs",
        "scheduled_simulations",
        ["scheduled_simulation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_simulation_runs_scheduled_simulation_id"),
        "simulation_runs",
        ["scheduled_simulation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_simulation_runs_scheduled_simulation_id"), table_name="simulation_runs"
    )
    op.drop_constraint(
        "fk_simulation_runs_scheduled_simulation_id", "simulation_runs", type_="foreignkey"
    )
    op.drop_column("simulation_runs", "scheduled_simulation_id")
    op.drop_index(
        op.f("ix_scheduled_simulations_workspace_id"), table_name="scheduled_simulations"
    )
    op.drop_table("scheduled_simulations")
    # Postgres native Enum types survive table drop; must drop explicitly, or a
    # down-then-up cycle fails with "type schedule_interval already exists" (same
    # fix as run_status/digest_frequency in earlier migrations).
    sa.Enum(name="schedule_interval").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 10: Verify the migration applies and rolls back cleanly**

Run (against a scratch Postgres — use the same `postgres_url` the test containers use, or a local dev DB per this repo's usual migration workflow):
```bash
cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: all three commands exit 0, no "already exists" errors.

- [ ] **Step 11: Commit**

```bash
git add backend/src/flowsage_backend/models/scheduled_simulation.py \
        backend/src/flowsage_backend/models/simulation.py \
        backend/src/flowsage_backend/models/__init__.py \
        backend/src/flowsage_backend/simulations.py \
        backend/migrations/versions/7c2f9a4d18be_add_scheduled_simulations_table.py \
        backend/tests/test_simulations.py
git commit -m "feat: add ScheduledSimulation model + scheduled_simulation_id on SimulationRun"
```

---

### Task 2: Core module — `scheduled_simulations.py`

**Files:**
- Create: `backend/src/flowsage_backend/scheduled_simulations.py`
- Test: `backend/tests/test_scheduled_simulations.py`

**Interfaces:**
- Consumes: `ScheduledSimulation`, `ScheduleInterval` (Task 1). `create_run(..., scheduled_simulation_id=...)` (Task 1). `calibration.predicted_scores_by_screen(issues: list[FrictionIssue]) -> dict[str, float]` (existing). `models.simulation.{FrictionIssue, RunStatus, SimulationRun}` (existing).
- Produces: `ScheduledSimulationError` (exception). `create_scheduled_simulation(session, *, workspace_id, persona_id, flow_name, goal, interval, created_by) -> ScheduledSimulation`. `stage_screenshots(session, config, screenshots_dir: Path) -> None`. `is_due(config, now: datetime) -> bool` (pure). `fire_due_scheduled_simulations(session, workspace_id, now) -> list[SimulationRun]`. `friction_score_for_run(issues: list[FrictionIssue]) -> float` (pure). `TrendPoint` (pydantic: `run_id, created_at, score, issue_count`). `build_trend(session, workspace_id, config_id) -> list[TrendPoint]`. `latest_two_completed_runs(session, workspace_id, config_id) -> list[SimulationRun]` (newest-first).

- [ ] **Step 1: Write failing tests for the pure functions first**

Create `backend/tests/test_scheduled_simulations.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_scheduled_simulations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowsage_backend.scheduled_simulations'`

- [ ] **Step 3: Write the core module**

Create `backend/src/flowsage_backend/scheduled_simulations.py`:

```python
"""Recurring simulation configs: create/edit, stage a fresh screenshot set via
API push, and fire due ones from worker.py's cron job. See the design spec
(docs/superpowers/specs/2026-08-01-scheduled-simulations-trend-design.md) --
this only supports the API-push trigger, not live-URL capture.

Friction-score trend reuses calibration.py's severity weighting rather than
inventing a second scoring model -- a run's score is the mean of its
per-screen predicted scores (predicted_scores_by_screen already takes the max
severity per screen; this module just averages that across screens).

Everything here is computed on demand from current data -- no persisted score
column, so it can't drift out of sync with a future severity-weight change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.calibration import predicted_scores_by_screen
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.simulations import create_run

_DUE_INTERVALS = {
    ScheduleInterval.DAILY: timedelta(days=1),
    ScheduleInterval.WEEKLY: timedelta(days=7),
}


class ScheduledSimulationError(Exception):
    """Raised when a scheduled-simulation config can't be created."""


async def create_scheduled_simulation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    persona_id: uuid.UUID,
    flow_name: str,
    goal: str,
    interval: ScheduleInterval,
    created_by: uuid.UUID | None,
) -> ScheduledSimulation:
    persona = (
        await session.execute(
            select(Persona).where(Persona.id == persona_id, Persona.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if persona is None:
        raise ScheduledSimulationError(f"No persona with id {persona_id}")

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name=flow_name,
        goal=goal,
        persona_id=persona_id,
        interval=interval,
        created_by=created_by,
    )
    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config


async def stage_screenshots(
    session: AsyncSession, config: ScheduledSimulation, screenshots_dir: Path
) -> None:
    """Records a freshly-written screenshot directory as this config's pending
    set. The caller (the push endpoint) must already have written the files to
    `screenshots_dir` before calling this -- this function only updates the
    pointer, replacing whatever pending set (if any) was there before."""
    config.pending_screenshots_dir = str(screenshots_dir)
    await session.commit()


def is_due(config: ScheduledSimulation, now: datetime) -> bool:
    if not config.active or config.pending_screenshots_dir is None:
        return False
    if config.interval == ScheduleInterval.ON_PUSH:
        return True
    if config.last_fired_at is None:
        return True
    return now - config.last_fired_at >= _DUE_INTERVALS[config.interval]


async def fire_due_scheduled_simulations(
    session: AsyncSession, workspace_id: uuid.UUID, now: datetime
) -> list[SimulationRun]:
    result = await session.execute(
        select(ScheduledSimulation).where(
            ScheduledSimulation.workspace_id == workspace_id,
            ScheduledSimulation.active.is_(True),
        )
    )
    configs = list(result.scalars().all())

    fired_runs: list[SimulationRun] = []
    for config in configs:
        if not is_due(config, now):
            continue
        # is_due already guarantees pending_screenshots_dir is not None here.
        run = await create_run(
            session,
            workspace_id=workspace_id,
            persona_id=config.persona_id,
            flow_name=config.flow_name,
            goal=config.goal,
            screenshots_dir=Path(config.pending_screenshots_dir),  # type: ignore[arg-type]
            scheduled_simulation_id=config.id,
        )
        config.last_fired_at = now
        config.pending_screenshots_dir = None
        await session.commit()
        fired_runs.append(run)
    return fired_runs


def friction_score_for_run(issues: list[FrictionIssue]) -> float:
    scores = predicted_scores_by_screen(issues)
    if not scores:
        return 0.0
    return sum(scores.values()) / len(scores)


class TrendPoint(BaseModel):
    run_id: uuid.UUID
    created_at: datetime
    score: float
    issue_count: int


async def build_trend(
    session: AsyncSession, workspace_id: uuid.UUID, config_id: uuid.UUID
) -> list[TrendPoint]:
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id,
            SimulationRun.scheduled_simulation_id == config_id,
            SimulationRun.status == RunStatus.COMPLETED,
        )
        .options(selectinload(SimulationRun.issues))
        .order_by(SimulationRun.created_at.asc())
    )
    runs = result.scalars().all()
    return [
        TrendPoint(
            run_id=run.id,
            created_at=run.created_at,
            score=friction_score_for_run(run.issues),
            issue_count=len(run.issues),
        )
        for run in runs
    ]


async def latest_two_completed_runs(
    session: AsyncSession, workspace_id: uuid.UUID, config_id: uuid.UUID
) -> list[SimulationRun]:
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id,
            SimulationRun.scheduled_simulation_id == config_id,
            SimulationRun.status == RunStatus.COMPLETED,
        )
        .options(selectinload(SimulationRun.issues))
        .order_by(SimulationRun.created_at.desc())
        .limit(2)
    )
    return list(result.scalars().all())
```

Check `models/simulation.py` for the exact `FrictionIssue` column names before writing the test's `_issue()` helper in Step 1 — if any name there differs from `screen/severity/title/heuristic_violated/persona_impact/description/suggested_fix`, fix the test helper to match, not the model.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_scheduled_simulations.py -v`
Expected: PASS (all 9 tests)

- [ ] **Step 5: Write failing DB-backed tests for the async functions**

Append to `backend/tests/test_scheduled_simulations.py`:

```python
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

    fired = await fire_due_scheduled_simulations(db_session, workspace_id, datetime.now(timezone.utc))

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
    fired = await fire_due_scheduled_simulations(db_session, workspace_id, datetime.now(timezone.utc))
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
```

This last test only covers the empty case -- `build_trend`/`latest_two_completed_runs` against actual COMPLETED runs (which requires running `execute_simulation`, an LLM-calling flow already exercised end-to-end in `test_simulations.py`) are covered at the API layer in Task 3 instead, using the same `RunStatus.COMPLETED` + `FrictionIssue` fixture-row pattern `test_calibration.py` uses (construct `SimulationRun`/`FrictionIssue` rows directly rather than executing a real simulation).

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_scheduled_simulations.py -v`
Expected: PASS (all tests, including the 4 new DB-backed ones)

- [ ] **Step 7: Commit**

```bash
git add backend/src/flowsage_backend/scheduled_simulations.py backend/tests/test_scheduled_simulations.py
git commit -m "feat: add scheduled-simulations core module (due-check, firing, trend scoring)"
```

---

### Task 3: API router — `api/scheduled_simulations.py`

**Files:**
- Create: `backend/src/flowsage_backend/api/scheduled_simulations.py`
- Modify: `backend/src/flowsage_backend/main.py` (register router)
- Test: `backend/tests/test_scheduled_simulations_api.py`

**Interfaces:**
- Consumes: everything from Task 2 (`ScheduledSimulationError`, `TrendPoint`, `build_trend`, `create_scheduled_simulation`, `stage_screenshots`), `get_current_actor`/`get_db_session` (existing `deps.py`), `record_audit_event` (existing `audit.py`), `IMAGE_SUFFIXES` (existing `simulations.py`).
- Produces: `router: APIRouter` mounted at `/scheduled-simulations` with `POST ""`, `GET ""`, `PATCH "/{id}"`, `DELETE "/{id}"`, `POST "/{id}/screenshots"`, `GET "/{id}/trend"`.

- [ ] **Step 1: Write failing API tests**

Create `backend/tests/test_scheduled_simulations_api.py`:

```python
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Workspace
from flowsage_backend.seed import seed_baseline_personas, upsert_user

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-but-good-enough-for-a-suffix-check"


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "sched-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/auth/login", json={"email": "sched-api@example.com", "password": "hunter2"}
        )
        yield client


async def _sched_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    from sqlalchemy import select

    user = await upsert_user(db_session, "sched-api@example.com", "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def _create_workspace(db_session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return workspace.id


async def test_create_scheduled_simulation_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/scheduled-simulations", json={})
    assert response.status_code == 401


async def test_create_list_update_delete_scheduled_simulation(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(persona.id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "daily",
            },
        )
        assert create_response.status_code == 201
        config = create_response.json()
        assert config["flow_name"] == "Checkout"
        assert config["active"] is True
        assert config["has_pending_screenshots"] is False

        list_response = await client.get("/scheduled-simulations")
        assert list_response.status_code == 200
        assert any(c["id"] == config["id"] for c in list_response.json())

        update_response = await client.patch(
            f"/scheduled-simulations/{config['id']}", json={"active": False}
        )
        assert update_response.status_code == 200
        assert update_response.json()["active"] is False

        delete_response = await client.delete(f"/scheduled-simulations/{config['id']}")
        assert delete_response.status_code == 204

        list_after_delete = await client.get("/scheduled-simulations")
        assert all(c["id"] != config["id"] for c in list_after_delete.json())


async def test_create_scheduled_simulation_rejects_unknown_persona(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(uuid.uuid4()),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "daily",
            },
        )
    assert response.status_code == 422


async def test_push_screenshots_stages_pending_set(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        push_response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("01_cart.png", _PNG_BYTES, "image/png")},
        )
        assert push_response.status_code == 200
        assert push_response.json()["has_pending_screenshots"] is True


async def test_push_screenshots_rejects_disallowed_file_type(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, db_session) as client:
        create_response = await client.post(
            "/scheduled-simulations",
            json={
                "persona_id": str(personas[0].id),
                "flow_name": "Checkout",
                "goal": "Complete purchase",
                "interval": "on_push",
            },
        )
        config_id = create_response.json()["id"]

        response = await client.post(
            f"/scheduled-simulations/{config_id}/screenshots",
            files={"files": ("notes.txt", b"hello", "text/plain")},
        )
        assert response.status_code == 422


async def test_get_trend_computes_score_from_completed_runs(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _sched_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        interval=ScheduleInterval.ON_PUSH,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/irrelevant",
        status=RunStatus.COMPLETED,
        scheduled_simulation_id=config.id,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cart",
            severity="high",
            title="t",
            heuristic_violated="h",
            persona_impact="p",
            description="d",
            suggested_fix="f",
        )
    )
    await db_session.commit()

    async with _authed_client(app, db_session) as client:
        response = await client.get(f"/scheduled-simulations/{config.id}/trend")

    assert response.status_code == 200
    points = response.json()
    assert len(points) == 1
    assert points[0]["run_id"] == str(run.id)
    assert points[0]["score"] == 0.7
    assert points[0]["issue_count"] == 1


async def test_scheduled_simulations_isolate_by_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    other_workspace_id = await _create_workspace(db_session)
    other_personas = await seed_baseline_personas(db_session, other_workspace_id)
    other_config = ScheduledSimulation(
        workspace_id=other_workspace_id,
        flow_name="Other Flow",
        goal="Do the other thing",
        persona_id=other_personas[0].id,
        interval=ScheduleInterval.WEEKLY,
    )
    db_session.add(other_config)
    await db_session.commit()
    await db_session.refresh(other_config)

    async with _authed_client(app, db_session) as client:
        list_response = await client.get("/scheduled-simulations")
        assert all(c["id"] != str(other_config.id) for c in list_response.json())

        get_response = await client.patch(
            f"/scheduled-simulations/{other_config.id}", json={"active": False}
        )
        assert get_response.status_code == 404
```

`FrictionIssue.step_id` is nullable (`models/simulation.py`), so omitting it here (as in every fixture-row `FrictionIssue` construction throughout this plan) is valid.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_scheduled_simulations_api.py -v`
Expected: FAIL — 404s (no such route registered yet) / import errors once the router module doesn't exist

- [ ] **Step 3: Write the router**

Create `backend/src/flowsage_backend/api/scheduled_simulations.py`:

```python
"""Recurring simulation config endpoints: create/list/edit/delete a schedule,
push a fresh screenshot set for it to consume on its next due check, and read
its friction-score trend. Firing itself happens in worker.py's cron job, not
here -- see flowsage_backend.scheduled_simulations.fire_due_scheduled_simulations.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.audit import record_audit_event
from flowsage_backend.deps import get_current_actor, get_db_session
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
from flowsage_backend.scheduled_simulations import (
    ScheduledSimulationError,
    TrendPoint,
    build_trend,
    create_scheduled_simulation,
    stage_screenshots,
)
from flowsage_backend.simulations import IMAGE_SUFFIXES

router = APIRouter(prefix="/scheduled-simulations", tags=["scheduled-simulations"])


class ScheduledSimulationOut(BaseModel):
    id: uuid.UUID
    flow_name: str
    goal: str
    persona_id: uuid.UUID
    interval: ScheduleInterval
    active: bool
    has_pending_screenshots: bool
    last_fired_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, config: ScheduledSimulation) -> "ScheduledSimulationOut":
        return cls(
            id=config.id,
            flow_name=config.flow_name,
            goal=config.goal,
            persona_id=config.persona_id,
            interval=config.interval,
            active=config.active,
            has_pending_screenshots=config.pending_screenshots_dir is not None,
            last_fired_at=config.last_fired_at,
            created_at=config.created_at,
        )


class ScheduledSimulationCreate(BaseModel):
    persona_id: uuid.UUID
    flow_name: str
    goal: str
    interval: ScheduleInterval


class ScheduledSimulationUpdate(BaseModel):
    goal: str | None = None
    interval: ScheduleInterval | None = None
    active: bool | None = None


async def _get_config(
    session: AsyncSession, workspace_id: uuid.UUID, config_id: uuid.UUID
) -> ScheduledSimulation:
    result = await session.execute(
        select(ScheduledSimulation).where(
            ScheduledSimulation.id == config_id, ScheduledSimulation.workspace_id == workspace_id
        )
    )
    config = result.scalar_one_or_none()
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scheduled simulation not found")
    return config


@router.post("", response_model=ScheduledSimulationOut, status_code=status.HTTP_201_CREATED)
async def create_scheduled_simulation_endpoint(
    payload: ScheduledSimulationCreate,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduledSimulationOut:
    workspace_id, user_id = actor
    try:
        config = await create_scheduled_simulation(
            session,
            workspace_id=workspace_id,
            persona_id=payload.persona_id,
            flow_name=payload.flow_name,
            goal=payload.goal,
            interval=payload.interval,
            created_by=user_id,
        )
    except ScheduledSimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await record_audit_event(
        session,
        workspace_id,
        actor_user_id=user_id,
        action="scheduled_simulation.created",
        target_type="scheduled_simulation",
        target_id=str(config.id),
        extra_data={"flow_name": payload.flow_name, "interval": payload.interval.value},
    )
    return ScheduledSimulationOut.from_model(config)


@router.get("", response_model=list[ScheduledSimulationOut])
async def list_scheduled_simulations(
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> list[ScheduledSimulationOut]:
    workspace_id, _ = actor
    result = await session.execute(
        select(ScheduledSimulation).where(ScheduledSimulation.workspace_id == workspace_id)
    )
    return [ScheduledSimulationOut.from_model(c) for c in result.scalars().all()]


@router.patch("/{config_id}", response_model=ScheduledSimulationOut)
async def update_scheduled_simulation(
    config_id: uuid.UUID,
    payload: ScheduledSimulationUpdate,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduledSimulationOut:
    workspace_id, _ = actor
    config = await _get_config(session, workspace_id, config_id)
    if payload.goal is not None:
        config.goal = payload.goal
    if payload.interval is not None:
        config.interval = payload.interval
    if payload.active is not None:
        config.active = payload.active
    await session.commit()
    await session.refresh(config)
    return ScheduledSimulationOut.from_model(config)


@router.delete("/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduled_simulation(
    config_id: uuid.UUID,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    workspace_id, _ = actor
    config = await _get_config(session, workspace_id, config_id)
    await session.delete(config)
    await session.commit()


@router.post("/{config_id}/screenshots", response_model=ScheduledSimulationOut)
async def push_screenshots(
    request: Request,
    config_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> ScheduledSimulationOut:
    workspace_id, _ = actor
    config = await _get_config(session, workspace_id, config_id)
    settings = request.app.state.settings
    screenshots_dir = Path(settings.upload_dir) / "scheduled" / str(config.id)
    shutil.rmtree(screenshots_dir, ignore_errors=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        # .name strips directory components -- see the identical guard in
        # api/simulations.py's create_simulation for why this matters.
        filename = Path(upload.filename or "").name
        if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unsupported file type: {filename!r}"
            )
        (screenshots_dir / filename).write_bytes(await upload.read())

    await stage_screenshots(session, config, screenshots_dir)
    return ScheduledSimulationOut.from_model(config)


@router.get("/{config_id}/trend", response_model=list[TrendPoint])
async def get_trend(
    config_id: uuid.UUID,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> list[TrendPoint]:
    workspace_id, _ = actor
    await _get_config(session, workspace_id, config_id)  # 404s if missing/wrong workspace
    return await build_trend(session, workspace_id, config_id)
```

- [ ] **Step 4: Register the router in `main.py`**

In `backend/src/flowsage_backend/main.py`, add the import alongside the other `api.*` imports (alphabetically, between `personas` and `settings`):

```python
from flowsage_backend.api.scheduled_simulations import router as scheduled_simulations_router
```

And register it alongside the other `app.include_router(...)` calls:

```python
app.include_router(scheduled_simulations_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_scheduled_simulations_api.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Run the full backend test suite to check nothing broke**

Run: `cd backend && uv run pytest -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/flowsage_backend/api/scheduled_simulations.py \
        backend/src/flowsage_backend/main.py \
        backend/tests/test_scheduled_simulations_api.py
git commit -m "feat: add scheduled-simulations API (CRUD, screenshot push, trend)"
```

---

### Task 4: Regression alerts — extend `alerts.py`

**Files:**
- Modify: `backend/src/flowsage_backend/alerts.py`
- Test: `backend/tests/test_alerts.py` (extend existing file)

**Interfaces:**
- Consumes: `ScheduledSimulation` (Task 1), `friction_score_for_run`/`latest_two_completed_runs` (Task 2).
- Produces: `FrictionRegressionAlert` (pydantic: `scheduled_simulation_id, flow_name, previous_score, current_score, delta`). `FRICTION_REGRESSION_ALERT_THRESHOLD = 0.15`. `AlertsReport.friction_regression_alerts: list[FrictionRegressionAlert]` (new field — **note this changes `AlertsReport`'s shape**, which `test_alerts_api.py` and `test_worker.py` already construct/compare against; existing tests that build an `AlertsReport()` without this field will now fail until updated — check for that when running the full suite in Step 5). `check_friction_regression_alerts(session, workspace_id) -> list[FrictionRegressionAlert]`.

- [ ] **Step 1: Write the failing test**

Open `backend/tests/test_alerts.py`, read it in full for its existing helper functions (there is already a `_create_workspace`-style helper and fixture-row construction for `SimulationRun`/`FrictionIssue` — reuse it, matching the shape used in Task 3's `test_get_trend_computes_score_from_completed_runs`). Add:

```python
async def test_check_friction_regression_alerts_flags_score_jump(db_session: AsyncSession) -> None:
    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
    from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
    from flowsage_backend.alerts import check_friction_regression_alerts

    workspace_id = await _create_workspace(db_session)
    persona = Persona(
        workspace_id=workspace_id,
        slug="test-persona",
        name="Test Persona",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Mobile",
        discovery_mode="Search-driven",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        interval=ScheduleInterval.DAILY,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    async def _completed_run_with_severity(severity: str) -> SimulationRun:
        run = SimulationRun(
            workspace_id=workspace_id,
            flow_name="Checkout",
            goal="Complete purchase",
            persona_id=persona.id,
            screenshots_dir="/tmp/irrelevant",
            status=RunStatus.COMPLETED,
            scheduled_simulation_id=config.id,
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(
            FrictionIssue(
                workspace_id=workspace_id,
                run_id=run.id,
                screen="cart",
                severity=severity,
                title="t",
                heuristic_violated="h",
                persona_impact="p",
                description="d",
                suggested_fix="f",
            )
        )
        await db_session.commit()
        return run

    await _completed_run_with_severity("low")  # score 0.2, previous
    await _completed_run_with_severity("critical")  # score 0.9, current -- delta 0.7

    alerts = await check_friction_regression_alerts(db_session, workspace_id)

    assert len(alerts) == 1
    assert alerts[0].scheduled_simulation_id == config.id
    assert alerts[0].previous_score == 0.2
    assert alerts[0].current_score == 0.9
    assert round(alerts[0].delta, 2) == 0.7


async def test_check_friction_regression_alerts_ignores_delta_just_under_threshold(
    db_session: AsyncSession,
) -> None:
    """Two screens per run, averaged: previous (low, medium) -> 0.325; current
    (medium, medium) -> 0.45. Delta 0.125 sits just under
    FRICTION_REGRESSION_ALERT_THRESHOLD (0.15) -- must not fire."""
    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
    from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
    from flowsage_backend.alerts import check_friction_regression_alerts

    workspace_id = await _create_workspace(db_session)
    persona = Persona(
        workspace_id=workspace_id,
        slug="test-persona-2",
        name="Test Persona 2",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Mobile",
        discovery_mode="Search-driven",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        interval=ScheduleInterval.DAILY,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    async def _completed_run_with_severities(severities: dict[str, str]) -> None:
        run = SimulationRun(
            workspace_id=workspace_id,
            flow_name="Checkout",
            goal="Complete purchase",
            persona_id=persona.id,
            screenshots_dir="/tmp/irrelevant",
            status=RunStatus.COMPLETED,
            scheduled_simulation_id=config.id,
        )
        db_session.add(run)
        await db_session.flush()
        for screen, severity in severities.items():
            db_session.add(
                FrictionIssue(
                    workspace_id=workspace_id,
                    run_id=run.id,
                    screen=screen,
                    severity=severity,
                    title="t",
                    heuristic_violated="h",
                    persona_impact="p",
                    description="d",
                    suggested_fix="f",
                )
            )
        await db_session.commit()

    await _completed_run_with_severities({"cart": "low", "pay": "medium"})  # avg 0.325, previous
    await _completed_run_with_severities({"cart": "medium", "pay": "medium"})  # avg 0.45, current

    alerts = await check_friction_regression_alerts(db_session, workspace_id)
    assert alerts == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_alerts.py -k friction_regression -v`
Expected: FAIL — `ImportError: cannot import name 'check_friction_regression_alerts'`

- [ ] **Step 3: Extend `alerts.py`**

In `backend/src/flowsage_backend/alerts.py`:

Add imports (alongside the existing ones):
```python
from flowsage_backend.models.scheduled_simulation import ScheduledSimulation
from flowsage_backend.scheduled_simulations import friction_score_for_run, latest_two_completed_runs
```
(`sqlalchemy.select` and `AsyncSession` are already imported in this file.)

Add the threshold constant near `CHURN_RISK_ALERT_THRESHOLD`:
```python
FRICTION_REGRESSION_ALERT_THRESHOLD = 0.15
"""A scheduled config's latest fired run scoring this much higher than its
previous fired run is alert-worthy. Tighter than calibration.py's
ANOMALY_THRESHOLD (0.35) because this compares a metric against its own
recent history, not against an independent observed signal -- a smaller
jump is already meaningful there."""
```

Add the new alert model next to `ChurnAlert`:
```python
class FrictionRegressionAlert(BaseModel):
    scheduled_simulation_id: uuid.UUID
    flow_name: str
    previous_score: float
    current_score: float
    delta: float
```

Add the new field to `AlertsReport`:
```python
class AlertsReport(BaseModel):
    calibration_alerts: list[CalibrationAlert]
    churn_alerts: list[ChurnAlert]
    friction_regression_alerts: list[FrictionRegressionAlert]
```

Update `has_alerts`:
```python
def has_alerts(report: AlertsReport) -> bool:
    return bool(
        report.calibration_alerts or report.churn_alerts or report.friction_regression_alerts
    )
```

Add the check function, next to `check_churn_alerts`:
```python
async def check_friction_regression_alerts(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[FrictionRegressionAlert]:
    configs = (
        await session.execute(
            select(ScheduledSimulation).where(
                ScheduledSimulation.workspace_id == workspace_id,
                ScheduledSimulation.active.is_(True),
            )
        )
    ).scalars().all()

    alerts: list[FrictionRegressionAlert] = []
    for config in configs:
        runs = await latest_two_completed_runs(session, workspace_id, config.id)
        if len(runs) < 2:
            continue
        current, previous = runs[0], runs[1]
        current_score = friction_score_for_run(current.issues)
        previous_score = friction_score_for_run(previous.issues)
        delta = current_score - previous_score
        if delta >= FRICTION_REGRESSION_ALERT_THRESHOLD:
            alerts.append(
                FrictionRegressionAlert(
                    scheduled_simulation_id=config.id,
                    flow_name=config.flow_name,
                    previous_score=previous_score,
                    current_score=current_score,
                    delta=delta,
                )
            )
    return alerts
```

Update `build_alerts_report`:
```python
async def build_alerts_report(session: AsyncSession, workspace_id: uuid.UUID) -> AlertsReport:
    events = await query_events(session, workspace_id)
    funnel = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, workspace_id)
    calibration_report = await build_calibration_report(
        session, workspace_id, funnel, settings.anomaly_threshold
    )
    churn_segments = await build_churn_risk_segments(session, workspace_id)
    return AlertsReport(
        calibration_alerts=check_calibration_anomalies(calibration_report),
        churn_alerts=check_churn_alerts(churn_segments, settings.churn_risk_alert_threshold),
        friction_regression_alerts=await check_friction_regression_alerts(session, workspace_id),
    )
```

Update `build_digest_text`:
```python
def build_digest_text(report: AlertsReport) -> str:
    """Plain-text fallback for Slack's top-level `text` field (used in
    notification previews; `build_digest_blocks` is the rendered body)."""
    if not has_alerts(report):
        return "FlowSage Digest: no calibration, churn, or friction-regression alerts."
    parts = [
        f"{len(report.calibration_alerts)} calibration anomalies",
        f"{len(report.churn_alerts)} churn-risk segments",
        f"{len(report.friction_regression_alerts)} friction regressions",
    ]
    return "FlowSage Digest: " + ", ".join(parts)
```

Update `build_digest_blocks` — change the no-alerts message and append a loop after the existing churn-alert loop:
```python
    if not has_alerts(report):
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "No calibration, churn, or friction-regression alerts.",
                },
            }
        )
        return blocks
```
and, after the existing `for churn_alert in report.churn_alerts:` loop, before `return blocks`:
```python
    for regression in report.friction_regression_alerts:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Friction regression*: {regression.flow_name} jumped to "
                        f"{regression.current_score:.2f} (+{regression.delta:.2f})"
                    ),
                },
            }
        )
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_alerts.py -k friction_regression -v`
Expected: PASS

- [ ] **Step 5: Fix any existing tests broken by `AlertsReport`'s new required field**

Run: `cd backend && uv run pytest tests/test_alerts.py tests/test_alerts_api.py tests/test_worker.py -v`

If any test constructs `AlertsReport(...)` directly (rather than via `build_alerts_report`) without `friction_regression_alerts`, it will now fail Pydantic validation. Search for `AlertsReport(` across `backend/tests/` and add `friction_regression_alerts=[]` to each direct construction found. Re-run until all PASS.

- [ ] **Step 6: Run the full backend test suite**

Run: `cd backend && uv run pytest -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/flowsage_backend/alerts.py backend/tests/test_alerts.py
git commit -m "feat: add friction-regression alerts to the alerts/digest pipeline"
```

---

### Task 5: Worker cron job — fire due scheduled simulations

**Files:**
- Modify: `backend/src/flowsage_backend/worker.py`
- Test: `backend/tests/test_worker.py` (extend existing file)

**Interfaces:**
- Consumes: `fire_due_scheduled_simulations` (Task 2), `Workspace` (existing).
- Produces: `run_scheduled_simulations_job(ctx: dict[str, Any]) -> None`, registered in `WorkerSettings.cron_jobs`.

- [ ] **Step 1: Write the failing test**

Open `backend/tests/test_worker.py`, read its `_FakeRedis` class and `ensure_default_workspace` import (already used by other tests in this file) — reuse both. Add:

```python
async def test_run_scheduled_simulations_job_fires_due_config_and_enqueues_run(
    db_session: AsyncSession,
) -> None:
    from pathlib import Path

    from flowsage_backend.models.scheduled_simulation import ScheduledSimulation, ScheduleInterval
    from flowsage_backend.seed import seed_baseline_personas
    from flowsage_backend.worker import run_scheduled_simulations_job

    workspace_id = await ensure_default_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)

    config = ScheduledSimulation(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=personas[0].id,
        interval=ScheduleInterval.ON_PUSH,
    )
    db_session.add(config)
    await db_session.commit()
    await db_session.refresh(config)

    screenshots_dir = Path(f"/tmp/flowsage-worker-test-{uuid.uuid4().hex}")
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    (screenshots_dir / "01_cart.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    config.pending_screenshots_dir = str(screenshots_dir)
    await db_session.commit()

    fake_redis = _FakeRedis()
    ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": fake_redis}

    try:
        await run_scheduled_simulations_job(ctx)

        assert len(fake_redis.enqueued) == 1
        assert fake_redis.enqueued[0][0] == "run_simulation_job"
        await db_session.refresh(config)
        assert config.pending_screenshots_dir is None
        assert config.last_fired_at is not None
    finally:
        await db_session.delete(config)
        await db_session.commit()
        import shutil

        shutil.rmtree(screenshots_dir, ignore_errors=True)


async def test_run_scheduled_simulations_job_one_workspace_failure_does_not_block_others(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from flowsage_backend import scheduled_simulations as scheduled_simulations_module
    from flowsage_backend.worker import run_scheduled_simulations_job

    async def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduled_simulations_module, "fire_due_scheduled_simulations", _boom)

    workspace_id = await ensure_default_workspace(db_session)
    fake_redis = _FakeRedis()
    ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": fake_redis}

    try:
        # Must not raise -- a workspace-level failure is swallowed and logged,
        # same as run_digest_job/run_retention_purge_job.
        await run_scheduled_simulations_job(ctx)
    finally:
        monkeypatch.undo()
```

The second test monkeypatches the function at its definition module; `worker.py` must call it as `scheduled_simulations.fire_due_scheduled_simulations(...)` (module-qualified) rather than importing the name directly, or the monkeypatch won't take effect. Write `worker.py` accordingly in Step 3 — `import flowsage_backend.scheduled_simulations as scheduled_simulations` and call `scheduled_simulations.fire_due_scheduled_simulations(...)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_worker.py -k scheduled_simulations_job -v`
Expected: FAIL — `ImportError: cannot import name 'run_scheduled_simulations_job'`

- [ ] **Step 3: Add the cron job to `worker.py`**

In `backend/src/flowsage_backend/worker.py`, add the import (module-qualified, per Step 1's note):
```python
from flowsage_backend import scheduled_simulations
```

Add the job function, near `run_retention_purge_job`:
```python
async def run_scheduled_simulations_job(ctx: dict[str, Any]) -> None:
    """Fires hourly; the actual daily/weekly/on_push cadence is decided per-config
    inside fire_due_scheduled_simulations (same 'cron fixed, cadence in application
    logic' pattern as run_digest_job). One workspace's failure doesn't block the
    others, mirroring run_digest_job's and run_retention_purge_job's per-workspace
    loops. Each newly-fired run still needs its QUEUED->RUNNING->COMPLETED walk,
    so this enqueues the existing run_simulation_job for it exactly like the
    one-shot upload endpoint does."""
    session_factory = ctx["session_factory"]
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        result = await session.execute(select(Workspace.id).where(Workspace.archived.is_(False)))
        workspace_ids = list(result.scalars().all())

    for workspace_id in workspace_ids:
        try:
            async with session_factory() as session:
                fired_runs = await scheduled_simulations.fire_due_scheduled_simulations(
                    session, workspace_id, now
                )
            for run in fired_runs:
                await ctx["redis"].enqueue_job("run_simulation_job", str(run.id))
        except Exception:  # noqa: BLE001 - one workspace's failure must not stop
            # the scheduled-simulations job from running for every other workspace.
            logger.warning(
                "Scheduled simulations job failed for workspace %s", workspace_id, exc_info=True
            )
```

Register it in `WorkerSettings.cron_jobs`:
```python
class WorkerSettings:
    functions = [run_simulation_job, run_retraining_job]
    cron_jobs = [
        cron(run_digest_job, hour=9, minute=0),
        cron(run_retention_purge_job, hour=3, minute=0),
        cron(run_scheduled_simulations_job, minute=0),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_worker.py -k scheduled_simulations_job -v`
Expected: PASS

- [ ] **Step 5: Run the full backend test suite**

Run: `cd backend && uv run pytest -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/worker.py backend/tests/test_worker.py
git commit -m "feat: fire due scheduled simulations from an hourly worker cron job"
```

---

### Task 6: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/api.test.ts` (extend existing file)

**Interfaces:**
- Produces: `ScheduleInterval` (TS union), `ScheduledSimulation`, `ScheduledSimulationCreatePayload`, `ScheduledSimulationUpdatePayload`, `TrendPoint` (types). `api.listScheduledSimulations`, `api.createScheduledSimulation`, `api.updateScheduledSimulation`, `api.deleteScheduledSimulation`, `api.pushScheduledSimulationScreenshots`, `api.getScheduledSimulationTrend`.

- [ ] **Step 1: Add the types**

In `frontend/src/lib/types.ts`, add (near the existing `SimulationRun`/`SimulationRunDetail` interfaces):

```typescript
export type ScheduleInterval = "daily" | "weekly" | "on_push";

export interface ScheduledSimulation {
  id: string;
  flow_name: string;
  goal: string;
  persona_id: string;
  interval: ScheduleInterval;
  active: boolean;
  has_pending_screenshots: boolean;
  last_fired_at: string | null;
  created_at: string;
}

export interface ScheduledSimulationCreatePayload {
  persona_id: string;
  flow_name: string;
  goal: string;
  interval: ScheduleInterval;
}

export interface ScheduledSimulationUpdatePayload {
  goal?: string;
  interval?: ScheduleInterval;
  active?: boolean;
}

export interface TrendPoint {
  run_id: string;
  created_at: string;
  score: number;
  issue_count: number;
}
```

- [ ] **Step 2: Write the failing test for the API client**

Open `frontend/src/lib/api.test.ts`, read its existing pattern for testing a `request`-backed call (it mocks `global.fetch`). Add tests following that exact pattern:

```typescript
describe("scheduled simulations", () => {
  it("createScheduledSimulation posts the payload and returns the created config", async () => {
    const config: ScheduledSimulation = {
      id: "sched-1",
      flow_name: "Checkout",
      goal: "Complete purchase",
      persona_id: "persona-1",
      interval: "daily",
      active: true,
      has_pending_screenshots: false,
      last_fired_at: null,
      created_at: "2026-08-01T00:00:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(config), { status: 201 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.createScheduledSimulation({
      persona_id: "persona-1",
      flow_name: "Checkout",
      goal: "Complete purchase",
      interval: "daily",
    });

    expect(result).toEqual(config);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/scheduled-simulations");
    expect(init.method).toBe("POST");
  });

  it("pushScheduledSimulationScreenshots sends a multipart FormData body", async () => {
    const config: ScheduledSimulation = {
      id: "sched-1",
      flow_name: "Checkout",
      goal: "Complete purchase",
      persona_id: "persona-1",
      interval: "on_push",
      active: true,
      has_pending_screenshots: true,
      last_fired_at: null,
      created_at: "2026-08-01T00:00:00Z",
    };
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(config), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const file = new File([new Uint8Array([1, 2, 3])], "01_cart.png", { type: "image/png" });
    const result = await api.pushScheduledSimulationScreenshots("sched-1", [file]);

    expect(result.has_pending_screenshots).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/scheduled-simulations/sched-1/screenshots");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("getScheduledSimulationTrend fetches the trend array", async () => {
    const points: TrendPoint[] = [
      { run_id: "run-1", created_at: "2026-08-01T00:00:00Z", score: 0.7, issue_count: 2 },
    ];
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(points), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await api.getScheduledSimulationTrend("sched-1");

    expect(result).toEqual(points);
  });
});
```

Add `ScheduledSimulation` and `TrendPoint` to this test file's existing type imports from `"./types"`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/api.test.ts`
Expected: FAIL — `api.createScheduledSimulation is not a function`

- [ ] **Step 4: Add the API client methods**

In `frontend/src/lib/api.ts`, add to the type-only import block at the top:
```typescript
  ScheduledSimulation,
  ScheduledSimulationCreatePayload,
  ScheduledSimulationUpdatePayload,
  ...
  TrendPoint,
```
(insert alphabetically among the existing named imports from `"./types"`).

Add these methods to the `api` object, after `deletePersona` and before `createSimulation` (grouping with the predictive-engine-adjacent calls):

```typescript
  listScheduledSimulations: (): Promise<ScheduledSimulation[]> =>
    request<ScheduledSimulation[]>("/scheduled-simulations"),

  createScheduledSimulation: (
    payload: ScheduledSimulationCreatePayload,
  ): Promise<ScheduledSimulation> =>
    request<ScheduledSimulation>("/scheduled-simulations", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  updateScheduledSimulation: (
    id: string,
    payload: ScheduledSimulationUpdatePayload,
  ): Promise<ScheduledSimulation> =>
    request<ScheduledSimulation>(`/scheduled-simulations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteScheduledSimulation: (id: string): Promise<void> =>
    request<void>(`/scheduled-simulations/${id}`, { method: "DELETE" }),

  pushScheduledSimulationScreenshots: (
    id: string,
    files: File[],
  ): Promise<ScheduledSimulation> => {
    const formData = new FormData();
    for (const file of files) formData.append("files", file);
    return request<ScheduledSimulation>(`/scheduled-simulations/${id}/screenshots`, {
      method: "POST",
      body: formData,
    });
  },

  getScheduledSimulationTrend: (id: string): Promise<TrendPoint[]> =>
    request<TrendPoint[]>(`/scheduled-simulations/${id}/trend`),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/api.test.ts`
Expected: PASS

- [ ] **Step 6: Run `tsc` to check types**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat: add frontend types + API client for scheduled simulations"
```

---

### Task 7: Frontend page — `ScheduledSimulationsPage`

**Files:**
- Create: `frontend/src/routes/predictive/ScheduledSimulationsPage.tsx`
- Modify: `frontend/src/App.tsx` (register route)
- Modify: `frontend/src/routes/predictive/PredictiveEnginePage.tsx` (nav link)
- Test: `frontend/src/routes/predictive/ScheduledSimulationsPage.test.tsx`

**Interfaces:**
- Consumes: `api.listScheduledSimulations`, `api.createScheduledSimulation`, `api.updateScheduledSimulation`, `api.deleteScheduledSimulation`, `api.pushScheduledSimulationScreenshots`, `api.getScheduledSimulationTrend`, `api.listPersonas` (Task 6 + existing). `ScheduledSimulation`, `ScheduleInterval`, `TrendPoint`, `Persona` types.
- Produces: `ScheduledSimulationsPage` component, mounted at `/predictive/scheduled`.

- [ ] **Step 1: Write the failing test**

Read `frontend/src/routes/predictive/PersonaConfigurationPage.test.tsx` in full first (already reviewed during planning) for the `vi.mock("../../lib/api", ...)` + `MemoryRouter` pattern this test must follow. Create `frontend/src/routes/predictive/ScheduledSimulationsPage.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Persona, ScheduledSimulation, TrendPoint } from "../../lib/types";
import { ScheduledSimulationsPage } from "./ScheduledSimulationsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      listPersonas: vi.fn(),
      listScheduledSimulations: vi.fn(),
      createScheduledSimulation: vi.fn(),
      updateScheduledSimulation: vi.fn(),
      deleteScheduledSimulation: vi.fn(),
      pushScheduledSimulationScreenshots: vi.fn(),
      getScheduledSimulationTrend: vi.fn(),
    },
  };
});

const PERSONA: Persona = {
  id: "persona-1",
  slug: "novice-user",
  name: "Novice User",
  description: "Represents users with limited domain knowledge.",
  baseline: true,
  tech_affinity: "Low",
  primary_device: "Mobile / Tablet",
  discovery_mode: "Search-driven",
  contextual_triggers: [],
  technical_literacy: 0.2,
  anxiety: 0.85,
  patience: 0.3,
  curiosity: 0.4,
};

const CONFIG: ScheduledSimulation = {
  id: "sched-1",
  flow_name: "Checkout",
  goal: "Ship checkout",
  persona_id: "persona-1",
  interval: "daily",
  active: true,
  has_pending_screenshots: false,
  last_fired_at: null,
  created_at: "2026-08-01T00:00:00Z",
};

const TREND: TrendPoint[] = [
  { run_id: "run-1", created_at: "2026-07-30T00:00:00Z", score: 0.4, issue_count: 1 },
  { run_id: "run-2", created_at: "2026-07-31T00:00:00Z", score: 0.7, issue_count: 3 },
];

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/predictive/scheduled"]}>
      <Routes>
        <Route path="/predictive/scheduled" element={<ScheduledSimulationsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ScheduledSimulationsPage", () => {
  it("lists existing schedules with their trend", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue(TREND);

    renderPage();

    expect(await screen.findByText("Checkout")).toBeInTheDocument();
    expect(screen.getByText(/Novice User/)).toBeInTheDocument();
    expect(screen.getByText(/Daily/)).toBeInTheDocument();
  });

  it("creates a new schedule from the form", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([]);
    vi.mocked(api.createScheduledSimulation).mockResolvedValue(CONFIG);

    renderPage();
    await waitFor(() => expect(api.listPersonas).toHaveBeenCalled());

    fireEvent.change(screen.getByLabelText(/Flow name/i), { target: { value: "Checkout" } });
    fireEvent.click(screen.getByRole("button", { name: /Create Schedule/i }));

    await waitFor(() =>
      expect(api.createScheduledSimulation).toHaveBeenCalledWith(
        expect.objectContaining({ flow_name: "Checkout", persona_id: "persona-1" }),
      ),
    );
    expect(await screen.findByText("Checkout")).toBeInTheDocument();
  });

  it("edits an existing schedule's goal and interval", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue([]);
    vi.mocked(api.updateScheduledSimulation).mockResolvedValue({
      ...CONFIG,
      goal: "Ship checkout faster",
      interval: "weekly",
    });

    renderPage();
    await screen.findByText("Checkout");

    fireEvent.click(screen.getByRole("button", { name: /Edit/i }));
    fireEvent.change(screen.getByDisplayValue("Ship checkout"), {
      target: { value: "Ship checkout faster" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Save/i }));

    await waitFor(() =>
      expect(api.updateScheduledSimulation).toHaveBeenCalledWith("sched-1", {
        goal: "Ship checkout faster",
        interval: "daily",
      }),
    );
  });

  it("deletes a schedule", async () => {
    vi.mocked(api.listPersonas).mockResolvedValue([PERSONA]);
    vi.mocked(api.listScheduledSimulations).mockResolvedValue([CONFIG]);
    vi.mocked(api.getScheduledSimulationTrend).mockResolvedValue([]);
    vi.mocked(api.deleteScheduledSimulation).mockResolvedValue(undefined);

    renderPage();
    await screen.findByText("Checkout");

    fireEvent.click(screen.getByRole("button", { name: /Delete/i }));

    await waitFor(() => expect(api.deleteScheduledSimulation).toHaveBeenCalledWith("sched-1"));
    await waitFor(() => expect(screen.queryByText("Checkout")).not.toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/predictive/ScheduledSimulationsPage.test.tsx`
Expected: FAIL — module not found

- [ ] **Step 3: Write the component**

Create `frontend/src/routes/predictive/ScheduledSimulationsPage.tsx`:

```tsx
import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../../lib/api";
import type { Persona, ScheduledSimulation, ScheduleInterval, TrendPoint } from "../../lib/types";

const INTERVAL_LABELS: Record<ScheduleInterval, string> = {
  daily: "Daily",
  weekly: "Weekly",
  on_push: "On push",
};

const TREND_WIDTH = 320;
const TREND_HEIGHT = 120;

function FrictionTrendChart({ points }: { points: TrendPoint[] }) {
  if (points.length === 0) {
    return <p className="text-on-surface-variant text-sm">No completed runs yet.</p>;
  }
  const step = points.length > 1 ? TREND_WIDTH / (points.length - 1) : 0;
  const coords = points.map((point, index) => ({
    x: index * step,
    y: (1 - point.score) * TREND_HEIGHT,
  }));
  const path = coords.map((c, i) => `${i === 0 ? "M" : "L"}${c.x},${c.y}`).join(" ");

  return (
    <svg
      viewBox={`0 0 ${TREND_WIDTH} ${TREND_HEIGHT}`}
      className="w-full max-w-xs"
      role="img"
      aria-label="Friction score trend over scheduled runs"
    >
      <line
        x1={0}
        y1={TREND_HEIGHT}
        x2={TREND_WIDTH}
        y2={TREND_HEIGHT}
        className="stroke-outline-variant"
        strokeWidth={1}
      />
      <path d={path} fill="none" className="stroke-primary" strokeWidth={2} />
      {coords.map((c, i) => {
        const point = points[i];
        if (!point) return null;
        return (
          <circle key={point.run_id} cx={c.x} cy={c.y} r={4} className="fill-primary">
            <title>
              {new Date(point.created_at).toLocaleDateString()}: {(point.score * 100).toFixed(0)}%
            </title>
          </circle>
        );
      })}
    </svg>
  );
}

function ScheduledSimulationCard({
  config,
  personaName,
  trend,
  onToggleActive,
  onDelete,
  onPushScreenshots,
  onSaveEdits,
}: {
  config: ScheduledSimulation;
  personaName: string;
  trend: TrendPoint[];
  onToggleActive: () => void;
  onDelete: () => void;
  onPushScreenshots: (files: File[]) => void;
  onSaveEdits: (goal: string, interval: ScheduleInterval) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [goalDraft, setGoalDraft] = useState(config.goal);
  const [intervalDraft, setIntervalDraft] = useState<ScheduleInterval>(config.interval);

  function startEditing() {
    setGoalDraft(config.goal);
    setIntervalDraft(config.interval);
    setEditing(true);
  }

  function save() {
    onSaveEdits(goalDraft, intervalDraft);
    setEditing(false);
  }

  return (
    <div className="ghost-border rounded-lg p-4 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-medium">{config.flow_name}</p>
          <p className="text-sm text-on-surface-variant">
            {personaName} · {INTERVAL_LABELS[config.interval]}
            {config.has_pending_screenshots ? " · screenshots staged" : ""}
          </p>
        </div>
        <div className="flex items-center gap-3">
          {editing ? null : (
            <button
              type="button"
              onClick={startEditing}
              className="text-sm font-medium text-primary hover:underline"
            >
              Edit
            </button>
          )}
          <button
            type="button"
            onClick={onToggleActive}
            className="text-sm font-medium text-primary hover:underline"
          >
            {config.active ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            onClick={onDelete}
            className="text-sm font-medium text-error hover:underline"
          >
            Delete
          </button>
        </div>
      </div>

      {editing ? (
        <div className="flex flex-col gap-3 ghost-border rounded-lg p-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Goal</span>
            <input
              value={goalDraft}
              onChange={(event) => setGoalDraft(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Interval</span>
            <select
              value={intervalDraft}
              onChange={(event) => setIntervalDraft(event.target.value as ScheduleInterval)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="on_push">On push</option>
            </select>
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={save}
              className="rounded-lg bg-primary px-3 py-1.5 text-sm text-on-primary font-medium hover:opacity-90 transition"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="text-sm font-medium text-on-surface-variant hover:underline"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <p className="text-sm text-on-surface-variant">{config.goal}</p>
      )}

      <label className="flex flex-col gap-1 text-sm">
        <span className="text-on-surface-variant">Push fresh screenshots (png/jpg/webp)</span>
        <input
          type="file"
          multiple
          accept="image/png,image/jpeg,image/webp"
          onChange={(event) => onPushScreenshots(Array.from(event.target.files ?? []))}
          className="ghost-border rounded-lg px-3 py-2"
        />
      </label>

      <FrictionTrendChart points={trend} />
    </div>
  );
}

export function ScheduledSimulationsPage() {
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  const [configs, setConfigs] = useState<ScheduledSimulation[] | null>(null);
  const [trends, setTrends] = useState<Record<string, TrendPoint[]>>({});
  const [personaId, setPersonaId] = useState("");
  const [flowName, setFlowName] = useState("");
  const [goal, setGoal] = useState("Complete purchase");
  const [scheduleInterval, setScheduleInterval] = useState<ScheduleInterval>("daily");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void loadAll();
  }, []);

  async function loadAll() {
    try {
      const [personaList, configList] = await Promise.all([
        api.listPersonas(),
        api.listScheduledSimulations(),
      ]);
      setPersonas(personaList);
      const first = personaList[0];
      if (first) setPersonaId(first.id);
      setConfigs(configList);
      const trendEntries = await Promise.all(
        configList.map(
          async (config) => [config.id, await api.getScheduledSimulationTrend(config.id)] as const,
        ),
      );
      setTrends(Object.fromEntries(trendEntries));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to load scheduled simulations.");
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const config = await api.createScheduledSimulation({
        persona_id: personaId,
        flow_name: flowName,
        goal,
        interval: scheduleInterval,
      });
      setConfigs((prev) => [...(prev ?? []), config]);
      setTrends((prev) => ({ ...prev, [config.id]: [] }));
      setFlowName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create scheduled simulation.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleActive(config: ScheduledSimulation) {
    try {
      const updated = await api.updateScheduledSimulation(config.id, { active: !config.active });
      setConfigs((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update scheduled simulation.");
    }
  }

  async function handleSaveEdits(configId: string, goalEdit: string, intervalEdit: ScheduleInterval) {
    try {
      const updated = await api.updateScheduledSimulation(configId, {
        goal: goalEdit,
        interval: intervalEdit,
      });
      setConfigs((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update scheduled simulation.");
    }
  }

  async function handleDelete(configId: string) {
    try {
      await api.deleteScheduledSimulation(configId);
      setConfigs((prev) => prev?.filter((c) => c.id !== configId) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to delete scheduled simulation.");
    }
  }

  async function handlePushScreenshots(configId: string, files: File[]) {
    if (files.length === 0) return;
    try {
      const updated = await api.pushScheduledSimulationScreenshots(configId, files);
      setConfigs((prev) => prev?.map((c) => (c.id === updated.id ? updated : c)) ?? null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to push screenshots.");
    }
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Scheduled Runs</h1>
        <p className="text-on-surface-variant mt-1">
          Recurring simulations with a friction-score trend across releases.{" "}
          <Link to="/predictive" className="text-primary hover:underline">
            Back to Predictive Engine
          </Link>
        </p>
      </div>

      <section className="bg-surface-container-lowest rounded-xl p-6">
        <h2 className="font-headline text-xl mb-4">New Schedule</h2>
        <form onSubmit={(event) => void handleCreate(event)} className="flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Persona</span>
            <select
              required
              value={personaId}
              onChange={(event) => setPersonaId(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              {personas?.map((persona) => (
                <option key={persona.id} value={persona.id}>
                  {persona.name}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Flow name</span>
            <input
              required
              value={flowName}
              onChange={(event) => setFlowName(event.target.value)}
              placeholder="Checkout Flow"
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Goal</span>
            <input
              required
              value={goal}
              onChange={(event) => setGoal(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2"
            />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="text-on-surface-variant">Interval</span>
            <select
              value={scheduleInterval}
              onChange={(event) => setScheduleInterval(event.target.value as ScheduleInterval)}
              className="ghost-border rounded-lg px-3 py-2"
            >
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="on_push">On push</option>
            </select>
          </label>

          {error !== null ? (
            <p role="alert" className="text-sm text-error">
              {error}
            </p>
          ) : null}

          <button
            type="submit"
            disabled={submitting || personas === null || personas.length === 0}
            className="rounded-lg bg-primary py-2.5 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Creating…" : "Create Schedule"}
          </button>
        </form>
      </section>

      <section className="flex flex-col gap-4">
        <h2 className="font-headline text-xl">Schedules</h2>
        {configs === null ? (
          <p className="text-on-surface-variant text-sm">Loading…</p>
        ) : configs.length === 0 ? (
          <p className="text-on-surface-variant text-sm">No scheduled runs yet.</p>
        ) : (
          configs.map((config) => (
            <ScheduledSimulationCard
              key={config.id}
              config={config}
              personaName={
                personas?.find((p) => p.id === config.persona_id)?.name ?? config.persona_id
              }
              trend={trends[config.id] ?? []}
              onToggleActive={() => void handleToggleActive(config)}
              onDelete={() => void handleDelete(config.id)}
              onPushScreenshots={(files) => void handlePushScreenshots(config.id, files)}
              onSaveEdits={(goalEdit, intervalEdit) =>
                void handleSaveEdits(config.id, goalEdit, intervalEdit)
              }
            />
          ))
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Register the route in `App.tsx`**

In `frontend/src/App.tsx`, add the import next to `PersonaConfigurationPage`:
```typescript
import { ScheduledSimulationsPage } from "./routes/predictive/ScheduledSimulationsPage";
```
And the route next to `/predictive/personas/:personaId`:
```tsx
<Route path="/predictive/scheduled" element={<ScheduledSimulationsPage />} />
```

- [ ] **Step 5: Add the nav link from `PredictiveEnginePage`**

In `frontend/src/routes/predictive/PredictiveEnginePage.tsx`, find the opening `<div>` with `<h1>Predictive Engine</h1>` and its `<p>` description, and add a link after the paragraph:

```tsx
      <div>
        <h1 className="font-headline text-3xl">Predictive Engine</h1>
        <p className="text-on-surface-variant mt-1">
          Walk a screenshot sequence with an LLM persona and get a friction report before a
          real user sees it.
        </p>
        <Link
          to="/predictive/scheduled"
          className="text-sm font-medium text-primary hover:underline mt-2 inline-block"
        >
          Scheduled Runs →
        </Link>
      </div>
```

(`Link` is already imported in this file.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/predictive/ScheduledSimulationsPage.test.tsx`
Expected: PASS (all 4 tests)

- [ ] **Step 7: Run the full frontend test suite + type check**

Run: `cd frontend && npx vitest run && npx tsc --noEmit && npx oxlint`
Expected: all PASS, no type errors, no lint errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/predictive/ScheduledSimulationsPage.tsx \
        frontend/src/routes/predictive/ScheduledSimulationsPage.test.tsx \
        frontend/src/App.tsx \
        frontend/src/routes/predictive/PredictiveEnginePage.tsx
git commit -m "feat: add Scheduled Runs page with friction trend chart"
```

---

### Task 8: Full verification + docs

**Files:**
- Modify: `README.md` or the predictive-engine section of whatever top-level docs already describe the one-shot simulation flow (locate via `grep -rn "one-shot\|POST /simulations" README.md docs/ frontend/README.md backend/README.md` — add a short section describing the new recurring/scheduled flow next to it, following whatever doc's existing style for describing an API endpoint).
- No other files — this task is verification + documentation, not new code.

**Interfaces:**
- Consumes: everything built in Tasks 1-7.
- Produces: nothing new — confirms the whole feature is internally consistent and matches this repo's established quality bar (per the project's build-process convention: format/lint/type-check/test every big change before considering it done).

- [ ] **Step 1: Backend formatting + linting + type checks**

Run:
```bash
cd backend
uv run autoflake8 --in-place --recursive src/ tests/
uv run black src/ tests/
uv run mypy src/
```
Expected: `black` reports no changes needed (or applies them — re-run `git diff` to review), `mypy` exits 0 with no errors in the new/modified files.

- [ ] **Step 2: Backend full test suite**

Run: `cd backend && uv run pytest -v`
Expected: all tests PASS (this repo's suite was at 254+ backend tests before this feature; expect that count to grow by ~25-30 new tests from Tasks 1-5).

- [ ] **Step 3: Frontend formatting + linting + type checks**

Run:
```bash
cd frontend
npx oxlint
npx tsc --noEmit
npx vitest run
```
Expected: no lint errors, no type errors, all tests PASS.

- [ ] **Step 4: Docker smoke test**

Confirm the new migration and worker cron job actually work end-to-end in the project's Docker stack, defined at `infra/docker-compose.yml` (services: `postgres`, `redis`, `backend`, `worker`, `frontend`):
```bash
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend uv run alembic upgrade head
```
Expected: migration applies cleanly against the containerized Postgres; `docker compose -f infra/docker-compose.yml logs worker` shows no startup errors (confirms `WorkerSettings.cron_jobs` loads without import errors).
```bash
docker compose -f infra/docker-compose.yml down
```

- [ ] **Step 5: Add/update docs**

Add a short paragraph (2-4 sentences, matching the tone of whatever surrounding doc you found in the file list above) describing: scheduled simulations support daily/weekly/on-push recurrence, screenshots are pushed via `POST /scheduled-simulations/{id}/screenshots`, and each config exposes a friction-score trend at `GET /scheduled-simulations/{id}/trend`. Link to the design spec (`docs/superpowers/specs/2026-08-01-scheduled-simulations-trend-design.md`) if the doc's existing style links to specs elsewhere; otherwise omit the link.

- [ ] **Step 6: Commit and push**

```bash
git add -A
git status  # review what's staged -- should only be the doc change from Step 5, plus
            # any formatting fixes autoflake8/black applied in Step 1
git commit -m "docs: document scheduled simulations + friction trend"
git push origin main
```

(Per this repo's established cadence — see recent commit history — push straight to `main` after a big change passes its full verification pass, no separate PR step.)
