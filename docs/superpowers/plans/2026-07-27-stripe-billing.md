# Stripe Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Stripe-backed subscription tiers (Free/Pro/Team) with hard freemium usage caps (events/month, simulation runs/month, seats) and hosted Checkout/Portal flows, per `docs/superpowers/specs/2026-07-27-stripe-billing-design.md`.

**Architecture:** A per-workspace `WorkspaceSubscription` singleton row (created lazily, mirrors the existing `CalibrationSettings` pattern) tracks tier/status/Stripe IDs. Usage is counted on-demand (`COUNT(*)` over `events`/`simulation_runs`/`memberships`, no new aggregate tables). A `billing.py` module exposes `get_usage()`/`check_within_limits()`, called explicitly at the top of the three enforced route handlers (`POST /v1/events`, `POST /simulations`, `POST /workspaces/current/members`). Stripe Checkout/Portal/webhook are thin wrapper functions in `integrations/stripe_client.py`, mirroring `integrations/slack.py`'s shape exactly.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, `stripe` Python SDK (new dependency), React 19 + TS strict (frontend).

## Global Constraints

- No live Stripe API keys this session — all `stripe` SDK calls are real application code, verified only via monkeypatched/mocked `stripe` module calls in tests (same as `integrations/slack.py`/`integrations/jira.py`'s no-live-network test pattern).
- Tier limits (`docs/superpowers/specs/2026-07-27-stripe-billing-design.md`): Free = 1 seat / 1,000 events/mo / 5 runs/mo. Pro ($49/mo) = 10 seats / 50,000 events/mo / 100 runs/mo. Team ($199/mo) = unlimited seats / 500,000 events/mo / 1,000 runs/mo.
- Every big change: `autoflake8` → `black` → `mypy --strict` → tests, per the project's standing process rules. Run these from the `backend` directory (`uv run <tool>`), never `uv sync` from inside `backend/` (it prunes the shared workspace venv — always `cd` to repo root first if a sync is needed).
- Follow existing codebase conventions exactly: `from __future__ import annotations` at the top of every new Python file, `Mapped[...]`/`mapped_column` SQLAlchemy 2.0 style, pydantic `BaseModel` DTOs colocated with the logic that produces them (not a separate schemas file), plain `useState`/`useEffect`/try-catch on the frontend (no react-query).

---

## Why this order

Tasks 1-4 build the pure-Python billing domain (model → store → usage → enforcement logic) bottom-up, fully unit-tested with no HTTP surface yet. Task 5 adds the Stripe SDK wrapper (independent of the domain layer). Task 6 is a **test-infrastructure fix that must land before Task 8** — it inoculates the existing 200+ test suite against the new caps before enforcement is wired into real routes. Task 7 exposes the domain layer over HTTP. Task 8 flips enforcement on for real and fixes the handful of pre-existing tests that now hit real caps. Tasks 9-11 are frontend. Task 12 is final verification.

---

### Task 1: `WorkspaceSubscription` model + migration

**Files:**
- Create: `backend/src/flowsage_backend/models/billing.py`
- Modify: `backend/src/flowsage_backend/models/__init__.py`
- Create: `backend/migrations/versions/<new_revision>_add_workspace_subscriptions_table.py`
- Test: `backend/tests/test_billing_models.py`

**Interfaces:**
- Produces: `SubscriptionTier` enum (`FREE`, `PRO`, `TEAM`), `SubscriptionStatus` enum (`ACTIVE`, `PAST_DUE`, `CANCELED`), `WorkspaceSubscription` ORM model with fields `id: uuid.UUID`, `workspace_id: uuid.UUID`, `tier: SubscriptionTier` (default `FREE`), `status: SubscriptionStatus` (default `ACTIVE`), `stripe_customer_id: str | None`, `stripe_subscription_id: str | None`, `current_period_end: datetime | None`, `updated_at: datetime`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_billing_models.py
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.billing import SubscriptionStatus, SubscriptionTier, WorkspaceSubscription
from flowsage_backend.models.workspace import Workspace


async def test_workspace_subscription_defaults(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Sub Test", slug=f"sub-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()

    subscription = WorkspaceSubscription(workspace_id=workspace.id)
    db_session.add(subscription)
    await db_session.commit()
    await db_session.refresh(subscription)

    assert subscription.tier == SubscriptionTier.FREE
    assert subscription.status == SubscriptionStatus.ACTIVE
    assert subscription.stripe_customer_id is None
    assert subscription.stripe_subscription_id is None
    assert subscription.current_period_end is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_billing_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.models.billing'`

- [ ] **Step 3: Write the model**

```python
# backend/src/flowsage_backend/models/billing.py
"""Per-workspace Stripe subscription state (`/settings/billing`).

One row per workspace, created lazily on first access (see
`flowsage_backend.billing_store.get_or_create_subscription`) -- mirrors
`CalibrationSettings`'s existing per-workspace-singleton pattern exactly. A
workspace with no row yet is always Free tier / Active, so nothing needs to
create this row at workspace-creation time.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class SubscriptionTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    TEAM = "team"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class WorkspaceSubscription(Base):
    __tablename__ = "workspace_subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    tier: Mapped[SubscriptionTier] = mapped_column(
        SAEnum(SubscriptionTier, name="subscription_tier"), default=SubscriptionTier.FREE
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        SAEnum(SubscriptionStatus, name="subscription_status"), default=SubscriptionStatus.ACTIVE
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Register the model in `models/__init__.py`**

Modify `backend/src/flowsage_backend/models/__init__.py`: add the import and `__all__` entries.

```python
from flowsage_backend.models.billing import SubscriptionStatus, SubscriptionTier, WorkspaceSubscription
```

Add `"SubscriptionTier"`, `"SubscriptionStatus"`, `"WorkspaceSubscription"` to `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_billing_models.py -v`
Expected: PASS

- [ ] **Step 6: Generate and hand-fix the Alembic migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "add workspace_subscriptions table"`

Open the generated file (in `backend/migrations/versions/`) and confirm the `upgrade()` body looks like this (adjust column order/defaults from what autogenerate produces so it matches exactly -- autogenerate captures enum labels from the Python enum's `.name`, e.g. `"FREE"`/`"PRO"`/`"TEAM"`, not `.value`, matching every other enum column in this codebase such as `digest_frequency`):

```python
def upgrade() -> None:
    op.create_table(
        "workspace_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tier", sa.Enum("FREE", "PRO", "TEAM", name="subscription_tier"), nullable=False
        ),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "PAST_DUE", "CANCELED", name="subscription_status"),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_index(
        op.f("ix_workspace_subscriptions_workspace_id"),
        "workspace_subscriptions",
        ["workspace_id"],
        unique=True,
    )
```

Replace the `downgrade()` body (autogenerate won't add the enum-drop; this is the same fix applied to every prior native-Enum migration in this project, e.g. `1c165b4afcfa_add_calibration_settings_table.py`):

```python
def downgrade() -> None:
    op.drop_index(
        op.f("ix_workspace_subscriptions_workspace_id"), table_name="workspace_subscriptions"
    )
    op.drop_table("workspace_subscriptions")
    # Postgres native Enum types survive table drop; must drop explicitly, or a
    # down-then-up cycle fails with "type subscription_tier already exists".
    sa.Enum(name="subscription_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="subscription_tier").drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 7: Verify formatting/typing**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`
Expected: all clean (if `black` reformats, run `uv run black src tests` and re-check)

- [ ] **Step 8: Commit**

```bash
git add backend/src/flowsage_backend/models/billing.py backend/src/flowsage_backend/models/__init__.py backend/migrations/versions/*_add_workspace_subscriptions_table.py backend/tests/test_billing_models.py
git commit -m "feat: add WorkspaceSubscription model + migration"
```

---

### Task 2: `billing_store.get_or_create_subscription`

**Files:**
- Create: `backend/src/flowsage_backend/billing_store.py`
- Test: `backend/tests/test_billing_store.py`

**Interfaces:**
- Consumes: `WorkspaceSubscription`, `SubscriptionTier` from Task 1.
- Produces: `async def get_or_create_subscription(session: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceSubscription`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_billing_store.py
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.workspace import Workspace


async def test_get_or_create_subscription_creates_free_tier_on_first_access(
    db_session: AsyncSession,
) -> None:
    workspace = Workspace(name="Store Test", slug=f"store-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    await db_session.commit()

    subscription = await get_or_create_subscription(db_session, workspace.id)

    assert subscription.workspace_id == workspace.id
    assert subscription.tier == SubscriptionTier.FREE


async def test_get_or_create_subscription_returns_existing_row(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Store Test 2", slug=f"store-test2-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    await db_session.commit()

    first = await get_or_create_subscription(db_session, workspace.id)
    first.tier = SubscriptionTier.PRO
    await db_session.commit()

    second = await get_or_create_subscription(db_session, workspace.id)
    assert second.id == first.id
    assert second.tier == SubscriptionTier.PRO
```

Add the missing import at the top: `from flowsage_backend.models.billing import SubscriptionTier`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_billing_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.billing_store'`

- [ ] **Step 3: Write the store**

```python
# backend/src/flowsage_backend/billing_store.py
"""Per-workspace accessor for `WorkspaceSubscription`. Created lazily on first
access, defaulting to Free tier -- mirrors `settings_store.py`'s
`get_or_create_calibration_settings` exactly."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.billing import WorkspaceSubscription


async def get_or_create_subscription(
    session: AsyncSession, workspace_id: uuid.UUID
) -> WorkspaceSubscription:
    result = await session.execute(
        select(WorkspaceSubscription)
        .where(WorkspaceSubscription.workspace_id == workspace_id)
        .limit(1)
    )
    subscription = result.scalar_one_or_none()
    if subscription is not None:
        return subscription

    subscription = WorkspaceSubscription(workspace_id=workspace_id)
    session.add(subscription)
    await session.commit()
    await session.refresh(subscription)
    return subscription
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_billing_store.py -v`
Expected: PASS

- [ ] **Step 5: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/billing_store.py backend/tests/test_billing_store.py
git commit -m "feat: add get_or_create_subscription store helper"
```

---

### Task 3: `billing.py` — tier limits + usage snapshot

**Files:**
- Create: `backend/src/flowsage_backend/billing.py`
- Test: `backend/tests/test_billing.py`

**Interfaces:**
- Consumes: `get_or_create_subscription` (Task 2), `SubscriptionTier` (Task 1), `Event` (`flowsage_backend.models.event`), `SimulationRun` (`flowsage_backend.models.simulation`), `Membership` (`flowsage_backend.models.workspace`).
- Produces: `TIER_LIMITS: dict[SubscriptionTier, TierLimits]`, `class TierLimits(BaseModel)` (`events_per_month: int`, `runs_per_month: int`, `seats: int` — `-1` means unlimited), `class UsageSnapshot(BaseModel)` (`tier: SubscriptionTier`, `events_used: int`, `events_limit: int`, `runs_used: int`, `runs_limit: int`, `seats_used: int`, `seats_limit: int`), `async def get_usage(session: AsyncSession, workspace_id: uuid.UUID) -> UsageSnapshot`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_billing.py
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing import TIER_LIMITS, get_usage
from flowsage_backend.models.billing import SubscriptionTier, WorkspaceSubscription
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.seed import seed_baseline_personas, upsert_user


def _month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def test_get_usage_counts_events_runs_and_seats_this_month(
    db_session: AsyncSession,
) -> None:
    workspace = Workspace(name="Usage Test", slug=f"usage-test-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()

    this_month = _month_start_utc() + timedelta(days=1)
    last_month = _month_start_utc() - timedelta(days=1)

    db_session.add_all(
        [
            Event(
                workspace_id=workspace.id,
                session_id="s1",
                screen="landing",
                event="screen_view",
                timestamp=this_month,
            ),
            Event(
                workspace_id=workspace.id,
                session_id="s1",
                screen="cart",
                event="screen_view",
                timestamp=this_month,
            ),
            Event(
                workspace_id=workspace.id,
                session_id="s2",
                screen="landing",
                event="screen_view",
                timestamp=last_month,
            ),
        ]
    )

    persona_user = await upsert_user(db_session, f"usage-persona-{uuid.uuid4().hex[:8]}@example.com", "hunter2")
    persona_membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == persona_user.id))
    ).scalar_one()
    personas = await seed_baseline_personas(db_session, persona_membership.workspace_id)
    persona = personas[0]

    db_session.add_all(
        [
            SimulationRun(
                workspace_id=workspace.id,
                flow_name="checkout",
                goal="buy",
                persona_id=persona.id,
                screenshots_dir="/tmp/does-not-matter",
                status=RunStatus.COMPLETED,
            ),
            SimulationRun(
                workspace_id=workspace.id,
                flow_name="checkout",
                goal="buy",
                persona_id=persona.id,
                screenshots_dir="/tmp/does-not-matter",
                status=RunStatus.COMPLETED,
                created_at=last_month,
            ),
        ]
    )

    admin_user = await upsert_user(db_session, f"usage-admin-{uuid.uuid4().hex[:8]}@example.com", "hunter2")
    db_session.add(Membership(user_id=admin_user.id, workspace_id=workspace.id, role=Role.ADMIN))
    await db_session.commit()

    usage = await get_usage(db_session, workspace.id)

    assert usage.tier == SubscriptionTier.FREE
    assert usage.events_used == 2  # only this-month events counted
    assert usage.events_limit == TIER_LIMITS[SubscriptionTier.FREE].events_per_month
    assert usage.runs_used == 1  # only this-month run counted
    assert usage.runs_limit == TIER_LIMITS[SubscriptionTier.FREE].runs_per_month
    assert usage.seats_used == 1
    assert usage.seats_limit == TIER_LIMITS[SubscriptionTier.FREE].seats


async def test_get_usage_reflects_upgraded_tier(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Usage Pro Test", slug=f"usage-pro-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(WorkspaceSubscription(workspace_id=workspace.id, tier=SubscriptionTier.PRO))
    await db_session.commit()

    usage = await get_usage(db_session, workspace.id)

    assert usage.tier == SubscriptionTier.PRO
    assert usage.events_limit == TIER_LIMITS[SubscriptionTier.PRO].events_per_month
```

Note: `SimulationRun`'s `created_at` has a `server_default=func.now()` (see `models/simulation.py`), but SQLAlchemy lets an explicit value passed at construction override the server default for a plain insert -- if this doesn't take effect in your SQLAlchemy version, use `await db_session.execute(sa.text("UPDATE simulation_runs SET created_at = :d WHERE id = :id"), {...})` after flush instead. Verify with the failing-test run before assuming either way.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_billing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.billing'`

- [ ] **Step 3: Write `billing.py` (usage half only — enforcement comes in Task 4)**

```python
# backend/src/flowsage_backend/billing.py
"""Freemium tier limits and on-demand usage counting.

Everything here is computed on demand from current data (no counters/aggregate
table), same philosophy as `calibration.py`/`churn.py`. Tier limits are code
constants, not DB rows -- nothing here needs to be admin-editable yet."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.models.billing import SubscriptionTier
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import SimulationRun
from flowsage_backend.models.workspace import Membership


class TierLimits(BaseModel):
    events_per_month: int
    runs_per_month: int
    seats: int  # -1 means unlimited


TIER_LIMITS: dict[SubscriptionTier, TierLimits] = {
    SubscriptionTier.FREE: TierLimits(events_per_month=1_000, runs_per_month=5, seats=1),
    SubscriptionTier.PRO: TierLimits(events_per_month=50_000, runs_per_month=100, seats=10),
    SubscriptionTier.TEAM: TierLimits(events_per_month=500_000, runs_per_month=1_000, seats=-1),
}


class UsageSnapshot(BaseModel):
    tier: SubscriptionTier
    events_used: int
    events_limit: int
    runs_used: int
    runs_limit: int
    seats_used: int
    seats_limit: int


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def get_usage(session: AsyncSession, workspace_id: uuid.UUID) -> UsageSnapshot:
    subscription = await get_or_create_subscription(session, workspace_id)
    limits = TIER_LIMITS[subscription.tier]
    month_start = _month_start()

    events_used = (
        await session.execute(
            select(func.count())
            .select_from(Event)
            .where(Event.workspace_id == workspace_id, Event.timestamp >= month_start)
        )
    ).scalar_one()
    runs_used = (
        await session.execute(
            select(func.count())
            .select_from(SimulationRun)
            .where(
                SimulationRun.workspace_id == workspace_id,
                SimulationRun.created_at >= month_start,
            )
        )
    ).scalar_one()
    seats_used = (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)
        )
    ).scalar_one()

    return UsageSnapshot(
        tier=subscription.tier,
        events_used=events_used,
        events_limit=limits.events_per_month,
        runs_used=runs_used,
        runs_limit=limits.runs_per_month,
        seats_used=seats_used,
        seats_limit=limits.seats,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_billing.py -v`
Expected: PASS

- [ ] **Step 5: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/billing.py backend/tests/test_billing.py
git commit -m "feat: add tier limits + on-demand usage snapshot"
```

---

### Task 4: `billing.py` — `check_within_limits` enforcement helper

**Files:**
- Modify: `backend/src/flowsage_backend/billing.py`
- Modify: `backend/tests/test_billing.py`

**Interfaces:**
- Consumes: `get_usage` (this file, Task 3).
- Produces: `async def check_within_limits(session: AsyncSession, workspace_id: uuid.UUID, resource: Literal["events", "runs", "seats"]) -> None` — raises `HTTPException(402)` if at/over cap for that resource, no-op otherwise. `-1` limit (seats on Team) always passes.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_billing.py`:

```python
import pytest
from fastapi import HTTPException

from flowsage_backend.billing import check_within_limits


async def test_check_within_limits_passes_when_under_cap(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Under Cap", slug=f"under-cap-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()

    await check_within_limits(db_session, workspace.id, "events")
    await check_within_limits(db_session, workspace.id, "runs")
    await check_within_limits(db_session, workspace.id, "seats")


async def test_check_within_limits_raises_402_at_cap(db_session: AsyncSession) -> None:
    workspace = Workspace(name="At Cap", slug=f"at-cap-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add_all(
        [
            Event(
                workspace_id=workspace.id,
                session_id=f"s{i}",
                screen="landing",
                event="screen_view",
                timestamp=now,
            )
            for i in range(TIER_LIMITS[SubscriptionTier.FREE].events_per_month)
        ]
    )
    await db_session.commit()

    with pytest.raises(HTTPException) as excinfo:
        await check_within_limits(db_session, workspace.id, "events")
    assert excinfo.value.status_code == 402


async def test_check_within_limits_seats_unlimited_on_team_tier(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Team Unlimited", slug=f"team-unlimited-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(WorkspaceSubscription(workspace_id=workspace.id, tier=SubscriptionTier.TEAM))
    await db_session.commit()

    # No memberships exist for this workspace at all, but the point is the -1
    # sentinel never trips regardless of count -- Team tier's seats=-1 must
    # short-circuit before the count comparison.
    await check_within_limits(db_session, workspace.id, "seats")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_billing.py -v -k check_within_limits`
Expected: FAIL with `ImportError: cannot import name 'check_within_limits'`

- [ ] **Step 3: Add `check_within_limits` to `billing.py`**

Append to `backend/src/flowsage_backend/billing.py` (add `Literal` to the `typing` import and `HTTPException` to imports):

```python
from typing import Literal

from fastapi import HTTPException
```

```python
async def check_within_limits(
    session: AsyncSession, workspace_id: uuid.UUID, resource: Literal["events", "runs", "seats"]
) -> None:
    """Raises 402 if the workspace is already at/over its tier's cap for
    `resource`. Checked BEFORE the action that would add one more unit (an
    event batch, a simulation run, a member) -- for event batches this means
    the check is a point-in-time gate, not a precise per-event cutoff: a
    workspace at 999/1000 posting a 50-event batch still gets the whole batch
    through. That's an intentional simplification, not a bug -- precise
    mid-batch cutoff isn't worth the complexity for a hard-cap freemium gate.
    """
    usage = await get_usage(session, workspace_id)
    used, limit = {
        "events": (usage.events_used, usage.events_limit),
        "runs": (usage.runs_used, usage.runs_limit),
        "seats": (usage.seats_used, usage.seats_limit),
    }[resource]

    if limit == -1:
        return
    if used >= limit:
        raise HTTPException(
            status_code=402,
            detail=(
                f"{usage.tier.value.title()} plan limit reached for {resource} "
                f"({used}/{limit}). Upgrade to continue."
            ),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_billing.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/billing.py backend/tests/test_billing.py
git commit -m "feat: add check_within_limits enforcement helper"
```

---

### Task 5: Stripe SDK dependency + `integrations/stripe_client.py`

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/src/flowsage_backend/config.py`
- Create: `backend/src/flowsage_backend/integrations/stripe_client.py`
- Test: `backend/tests/test_integrations_stripe.py`

**Interfaces:**
- Produces: `class StripeNotConfiguredError(Exception)`, `async def create_checkout_session(*, secret_key: str | None, price_id: str | None, customer_email: str, existing_customer_id: str | None, workspace_id: uuid.UUID, tier: str, success_url: str, cancel_url: str) -> str` (returns Checkout URL), `async def create_portal_session(*, secret_key: str | None, customer_id: str, return_url: str) -> str` (returns Portal URL), `def verify_webhook(*, payload: bytes, sig_header: str, webhook_secret: str) -> stripe.Event`. Adds `Settings` fields: `stripe_secret_key: str | None`, `stripe_webhook_secret: str | None`, `stripe_price_id_pro: str | None`, `stripe_price_id_team: str | None`, `app_base_url: str = "http://localhost:5173"`.

- [ ] **Step 1: Add the `stripe` dependency**

Modify `backend/pyproject.toml` — add `"stripe>=11,<12",` to the `dependencies` list (alphabetical position after `"slowapi>=0.1.9,<0.2",`).

Run: `cd /home/asus/Projects/personal/FlowSage && uv sync --all-extras` (from repo root, never from inside `backend/` — see Global Constraints). If this environment's outbound network needs the proxy workaround documented in project memory (PyPI download timeouts through the sandboxed egress proxy), retry with:
```bash
HTTP_PROXY=http://172.17.0.1:11180 HTTPS_PROXY=http://172.17.0.1:11180 uv sync --all-extras
```

- [ ] **Step 2: Add Settings fields**

Modify `backend/src/flowsage_backend/config.py` — add after the `neo4j_password` field:

```python
    # Stripe billing (optional -- unconfigured means checkout/portal 400 cleanly,
    # same as Slack/Jira; no startup placeholder-guard, unlike JWT_SECRET/
    # SECRET_ENCRYPTION_KEY, since this feature must work fully unconfigured).
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_id_pro: str | None = None
    stripe_price_id_team: str | None = None
    # Absolute origin the frontend is served from -- used to build Stripe
    # Checkout's success_url/cancel_url. Defaults to the docker-compose
    # frontend service's exposed port (see infra/docker-compose.yml).
    app_base_url: str = "http://localhost:5173"
```

- [ ] **Step 3: Write the failing test**

```python
# backend/tests/test_integrations_stripe.py
import hashlib
import hmac
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
import stripe

from flowsage_backend.integrations.stripe_client import (
    StripeNotConfiguredError,
    create_checkout_session,
    create_portal_session,
    verify_webhook,
)


async def test_create_checkout_session_requires_secret_key() -> None:
    with pytest.raises(StripeNotConfiguredError):
        await create_checkout_session(
            secret_key=None,
            price_id="price_123",
            customer_email="a@example.com",
            existing_customer_id=None,
            workspace_id=uuid.uuid4(),
            tier="pro",
            success_url="https://app.example.com/settings/billing?checkout=success",
            cancel_url="https://app.example.com/settings/billing?checkout=cancel",
        )


async def test_create_checkout_session_returns_url() -> None:
    fake_session = MagicMock(url="https://checkout.stripe.com/pay/cs_test_123")
    with patch("stripe.checkout.Session.create", return_value=fake_session) as mock_create:
        url = await create_checkout_session(
            secret_key="sk_test_fake",
            price_id="price_123",
            customer_email="a@example.com",
            existing_customer_id=None,
            workspace_id=uuid.uuid4(),
            tier="pro",
            success_url="https://app.example.com/settings/billing?checkout=success",
            cancel_url="https://app.example.com/settings/billing?checkout=cancel",
        )
    assert url == "https://checkout.stripe.com/pay/cs_test_123"
    assert mock_create.call_args.kwargs["mode"] == "subscription"
    assert mock_create.call_args.kwargs["customer_email"] == "a@example.com"


async def test_create_portal_session_requires_secret_key() -> None:
    with pytest.raises(StripeNotConfiguredError):
        await create_portal_session(
            secret_key=None, customer_id="cus_123", return_url="https://app.example.com/settings/billing"
        )


async def test_create_portal_session_returns_url() -> None:
    fake_session = MagicMock(url="https://billing.stripe.com/session/bps_test_123")
    with patch("stripe.billing_portal.Session.create", return_value=fake_session):
        url = await create_portal_session(
            secret_key="sk_test_fake",
            customer_id="cus_123",
            return_url="https://app.example.com/settings/billing",
        )
    assert url == "https://billing.stripe.com/session/bps_test_123"


def test_verify_webhook_valid_signature_roundtrip() -> None:
    payload = b'{"id": "evt_test", "type": "checkout.session.completed"}'
    secret = "whsec_test_fake"
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    sig_header = f"t={timestamp},v1={signature}"

    event = verify_webhook(payload=payload, sig_header=sig_header, webhook_secret=secret)
    assert event["type"] == "checkout.session.completed"


def test_verify_webhook_rejects_bad_signature() -> None:
    payload = b'{"id": "evt_test", "type": "checkout.session.completed"}'
    with pytest.raises(stripe.SignatureVerificationError):
        verify_webhook(payload=payload, sig_header="t=1,v1=deadbeef", webhook_secret="whsec_test_fake")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_integrations_stripe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.integrations.stripe_client'`

- [ ] **Step 5: Write `integrations/stripe_client.py`**

```python
# backend/src/flowsage_backend/integrations/stripe_client.py
"""Stripe SDK wrapper: hosted Checkout (upgrade), hosted Customer Portal
(manage/cancel), and webhook signature verification. Mirrors
`integrations/slack.py`'s shape -- thin functions, `StripeNotConfiguredError`
when the secret key is missing, no live network calls in this test suite."""

from __future__ import annotations

import uuid

import stripe


class StripeNotConfiguredError(Exception):
    """Raised when STRIPE_SECRET_KEY is not configured."""


async def create_checkout_session(
    *,
    secret_key: str | None,
    price_id: str | None,
    customer_email: str,
    existing_customer_id: str | None,
    workspace_id: uuid.UUID,
    tier: str,
    success_url: str,
    cancel_url: str,
) -> str:
    if secret_key is None or price_id is None:
        raise StripeNotConfiguredError("Stripe is not configured for this tier")

    customer_kwargs: dict[str, str] = (
        {"customer": existing_customer_id}
        if existing_customer_id is not None
        else {"customer_email": customer_email}
    )
    session = stripe.checkout.Session.create(
        api_key=secret_key,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"workspace_id": str(workspace_id), "tier": tier},
        **customer_kwargs,
    )
    assert session.url is not None
    return session.url


async def create_portal_session(*, secret_key: str | None, customer_id: str, return_url: str) -> str:
    if secret_key is None:
        raise StripeNotConfiguredError("Stripe is not configured")

    session = stripe.billing_portal.Session.create(
        api_key=secret_key, customer=customer_id, return_url=return_url
    )
    assert session.url is not None
    return session.url


def verify_webhook(*, payload: bytes, sig_header: str, webhook_secret: str) -> stripe.Event:
    return stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_integrations_stripe.py -v`
Expected: PASS

- [ ] **Step 7: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

If `mypy --strict` complains about missing stubs for `stripe`, add to `backend/pyproject.toml`'s `[tool.mypy]` section (or wherever this package's per-package mypy config lives, per the project's "each package has its own self-contained `[tool.mypy]`" convention) an override:
```toml
[[tool.mypy.overrides]]
module = "stripe.*"
ignore_missing_imports = true
```

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/flowsage_backend/config.py backend/src/flowsage_backend/integrations/stripe_client.py backend/tests/test_integrations_stripe.py
git commit -m "feat: add stripe SDK dependency + Checkout/Portal/webhook client"
```

---

### Task 6: Test-isolation fix — shield existing tests from real caps

**Why this task exists:** `POST /v1/events` is exercised only by `test_events.py`, always against the shared, session-scoped `fs-default` workspace (via `ensure_default_workspace` in `conftest.py`) — every event ever ingested into it across the *entire* pytest run accumulates, since this suite's Postgres fixture is session-scoped with no truncation between tests (see the Phase 2 chunk 1 test-isolation gotcha in project memory). Once Task 8 wires `check_within_limits` into the real ingestion route, that shared workspace would silently be Free tier (1,000 events/mo) and could tip over from unrelated tests' cumulative event volume, causing unpredictable, order-dependent 402 failures in tests that have nothing to do with billing. Bumping it to a generous tier once, here, is the same category of fix as the existing `_reset_rate_limiter` autouse fixture in `conftest.py` — pre-empting a new cross-cutting concern from leaking into unrelated tests.

**Files:**
- Modify: `backend/tests/conftest.py`

**Interfaces:**
- Consumes: `WorkspaceSubscription`, `SubscriptionTier` (Task 1).
- No new public interface — this only changes test fixture behavior.

- [ ] **Step 1: Add the import**

Modify `backend/tests/conftest.py` — add to the existing import block:

```python
from flowsage_backend.models.billing import SubscriptionTier, WorkspaceSubscription
```

- [ ] **Step 2: Bump `ensure_default_workspace`'s tier on creation**

Modify the `ensure_default_workspace` function in `backend/tests/conftest.py`:

```python
async def ensure_default_workspace(session: AsyncSession) -> uuid.UUID:
    """Get-or-create the "fs-default" workspace that `POST /v1/events` (API-key
    ingestion, not yet workspace-scoped -- proper per-workspace API keys are
    Phase 3 chunk 2) always writes into, and the digest cron job reads from.

    In a real deployment this row is created by the `e463496b1d0f` backfill
    migration; test fixtures build tables straight from ORM metadata (no
    migrations run), so it doesn't exist until a test needs it here."""
    result = await session.execute(select(Workspace.id).where(Workspace.slug == "fs-default"))
    workspace_id = result.scalar_one_or_none()
    if workspace_id is not None:
        return workspace_id

    workspace = Workspace(name="Default", slug="fs-default")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    # This workspace is reused by every event-ingestion test in the whole
    # session (no per-test truncation -- see this suite's session-scoped
    # Postgres fixture). Pin it to Team tier so accumulated event volume
    # across unrelated test files never trips the real Free-tier cap once
    # billing.check_within_limits is wired into POST /v1/events -- dedicated
    # billing tests use their own freshly-created, deliberately Free-tier
    # workspaces instead (see test_billing_enforcement_api.py).
    session.add(WorkspaceSubscription(workspace_id=workspace.id, tier=SubscriptionTier.TEAM))
    await session.commit()
    return workspace.id
```

- [ ] **Step 3: Verify the whole existing suite is still green**

Run: `cd backend && uv run pytest -q`
Expected: PASS (same count as before this task — this step should be a no-op behaviorally since `check_within_limits` isn't wired into any route yet)

- [ ] **Step 4: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/conftest.py
git commit -m "test: pin shared fs-default workspace to Team tier ahead of billing enforcement"
```

---

### Task 7: `api/billing.py` router — usage, checkout, portal, webhook

**Files:**
- Create: `backend/src/flowsage_backend/api/billing.py`
- Modify: `backend/src/flowsage_backend/main.py`
- Test: `backend/tests/test_billing_api.py`

**Interfaces:**
- Consumes: `get_usage` (Task 3), `get_or_create_subscription` (Task 2), `create_checkout_session`/`create_portal_session`/`verify_webhook` (Task 5), `get_current_membership`/`get_db_session` (`flowsage_backend.deps`), `record_audit_event` (`flowsage_backend.audit`).
- Produces: `router: APIRouter` exported from this module, mounted in `main.py`. Routes: `GET /billing/usage` → `UsageSnapshot`, `POST /billing/checkout` (body `{tier: "pro" | "team"}`) → `{url: str}`, `POST /billing/portal` → `{url: str}`, `POST /billing/webhook` (no auth dependency).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_billing_api.py
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.billing import SubscriptionStatus, SubscriptionTier, WorkspaceSubscription
from flowsage_backend.models.workspace import Membership
from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, "billing-api@example.com", "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": "billing-api@example.com", "password": "hunter2"})
        yield client


async def _billing_api_workspace_id(db_session: AsyncSession) -> uuid.UUID:
    user = await upsert_user(db_session, "billing-api@example.com", "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def test_get_usage_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/billing/usage")
    assert response.status_code == 401


async def test_get_usage_returns_free_tier_defaults(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.get("/billing/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "free"
    assert body["seats_limit"] == 1


async def test_checkout_returns_400_when_stripe_unconfigured(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/billing/checkout", json={"tier": "pro"})

    assert response.status_code == 400


async def test_portal_returns_400_when_no_stripe_customer_yet(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with _authed_client(app, db_session) as client:
        response = await client.post("/billing/portal")

    assert response.status_code == 400


async def test_webhook_rejects_bad_signature(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook",
            content=b'{"type": "checkout.session.completed"}',
            headers={"stripe-signature": "t=1,v1=deadbeef"},
        )

    assert response.status_code == 400


def _sign(payload: bytes, secret: str) -> str:
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.{payload.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


async def test_webhook_checkout_completed_upgrades_tier(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _billing_api_workspace_id(db_session)
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"

    payload = json.dumps(
        {
            "id": "evt_test_1",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_test_123",
                    "subscription": "sub_test_123",
                    "metadata": {"workspace_id": str(workspace_id), "tier": "pro"},
                }
            },
        }
    ).encode()
    sig_header = _sign(payload, "whsec_test_fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook", content=payload, headers={"stripe-signature": sig_header}
        )

    assert response.status_code == 200
    result = await db_session.execute(
        select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
    )
    subscription = result.scalar_one()
    assert subscription.tier == SubscriptionTier.PRO
    assert subscription.stripe_customer_id == "cus_test_123"
    assert subscription.stripe_subscription_id == "sub_test_123"


async def test_webhook_subscription_deleted_resets_to_free(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _billing_api_workspace_id(db_session)
    db_session.add(
        WorkspaceSubscription(
            workspace_id=workspace_id,
            tier=SubscriptionTier.PRO,
            status=SubscriptionStatus.ACTIVE,
            stripe_customer_id="cus_test_123",
            stripe_subscription_id="sub_test_123",
        )
    )
    await db_session.commit()
    app.state.settings.stripe_webhook_secret = "whsec_test_fake"

    payload = json.dumps(
        {
            "id": "evt_test_2",
            "type": "customer.subscription.deleted",
            "data": {"object": {"id": "sub_test_123", "customer": "cus_test_123"}},
        }
    ).encode()
    sig_header = _sign(payload, "whsec_test_fake")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/billing/webhook", content=payload, headers={"stripe-signature": sig_header}
        )

    assert response.status_code == 200
    result = await db_session.execute(
        select(WorkspaceSubscription).where(WorkspaceSubscription.workspace_id == workspace_id)
    )
    subscription = result.scalar_one()
    assert subscription.tier == SubscriptionTier.FREE
    assert subscription.status == SubscriptionStatus.CANCELED
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_billing_api.py -v`
Expected: FAIL with 404s (router not mounted) / `ModuleNotFoundError`

- [ ] **Step 3: Write `api/billing.py`**

```python
# backend/src/flowsage_backend/api/billing.py
"""Billing endpoints: usage snapshot, Stripe Checkout/Portal redirects, and the
Stripe webhook that keeps `WorkspaceSubscription` in sync. The webhook route
has no auth dependency -- Stripe calls it directly -- and always returns 200
on a recognized-but-irrelevant event or a workspace lookup miss (never lets a
downstream bug surface as a 5xx that triggers Stripe's retry storm); it 400s
only on signature failure, mirroring `record_audit_event`'s best-effort spirit."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import stripe

from flowsage_backend.audit import record_audit_event
from flowsage_backend.billing import UsageSnapshot, get_usage
from flowsage_backend.billing_store import get_or_create_subscription
from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.integrations.stripe_client import (
    StripeNotConfiguredError,
    create_checkout_session,
    create_portal_session,
    verify_webhook,
)
from flowsage_backend.models.billing import SubscriptionStatus, SubscriptionTier, WorkspaceSubscription
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(BaseModel):
    tier: Literal["pro", "team"]


class CheckoutResult(BaseModel):
    url: str


class PortalResult(BaseModel):
    url: str


_STRIPE_STATUS_MAP: dict[str, SubscriptionStatus] = {
    "active": SubscriptionStatus.ACTIVE,
    "trialing": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "unpaid": SubscriptionStatus.PAST_DUE,
}


def _map_stripe_status(stripe_status: str) -> SubscriptionStatus:
    return _STRIPE_STATUS_MAP.get(stripe_status, SubscriptionStatus.CANCELED)


@router.get("/usage", response_model=UsageSnapshot, dependencies=[Depends(get_current_membership)])
async def get_billing_usage(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> UsageSnapshot:
    _, membership = membership_pair
    return await get_usage(session, membership.workspace_id)


@router.post("/checkout", response_model=CheckoutResult, dependencies=[Depends(get_current_membership)])
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CheckoutResult:
    user, membership = membership_pair
    settings = request.app.state.settings
    price_id = (
        settings.stripe_price_id_pro if payload.tier == "pro" else settings.stripe_price_id_team
    )

    subscription = await get_or_create_subscription(session, membership.workspace_id)
    base_url = settings.app_base_url

    try:
        url = await create_checkout_session(
            secret_key=settings.stripe_secret_key,
            price_id=price_id,
            customer_email=user.email,
            existing_customer_id=subscription.stripe_customer_id,
            workspace_id=membership.workspace_id,
            tier=payload.tier,
            success_url=f"{base_url}/settings/billing?checkout=success",
            cancel_url=f"{base_url}/settings/billing?checkout=cancel",
        )
    except StripeNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc

    return CheckoutResult(url=url)


@router.post("/portal", response_model=PortalResult, dependencies=[Depends(get_current_membership)])
async def create_portal(
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> PortalResult:
    _, membership = membership_pair
    settings = request.app.state.settings
    subscription = await get_or_create_subscription(session, membership.workspace_id)

    if subscription.stripe_customer_id is None:
        raise HTTPException(400, "No billing account yet -- upgrade first")

    try:
        url = await create_portal_session(
            secret_key=settings.stripe_secret_key,
            customer_id=subscription.stripe_customer_id,
            return_url=f"{settings.app_base_url}/settings/billing",
        )
    except StripeNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc

    return PortalResult(url=url)


async def _find_subscription_by_customer_id(
    session: AsyncSession, customer_id: str
) -> WorkspaceSubscription | None:
    result = await session.execute(
        select(WorkspaceSubscription).where(WorkspaceSubscription.stripe_customer_id == customer_id)
    )
    return result.scalar_one_or_none()


@router.post("/webhook")
async def stripe_webhook(request: Request, session: AsyncSession = Depends(get_db_session)) -> dict[str, str]:
    settings = request.app.state.settings
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if settings.stripe_webhook_secret is None:
        raise HTTPException(400, "Stripe webhook is not configured")

    try:
        event = verify_webhook(
            payload=payload, sig_header=sig_header, webhook_secret=settings.stripe_webhook_secret
        )
    except stripe.SignatureVerificationError as exc:
        raise HTTPException(400, f"Invalid Stripe signature: {exc}") from exc

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        metadata = data.get("metadata", {})
        workspace_id_str = metadata.get("workspace_id")
        tier_str = metadata.get("tier")
        if workspace_id_str is None or tier_str is None:
            logger.warning("checkout.session.completed missing workspace_id/tier metadata")
            return {"status": "ignored"}

        workspace_id = uuid.UUID(workspace_id_str)
        subscription = await get_or_create_subscription(session, workspace_id)
        subscription.tier = SubscriptionTier(tier_str)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.stripe_customer_id = data.get("customer")
        subscription.stripe_subscription_id = data.get("subscription")
        await session.commit()
        await record_audit_event(
            session, workspace_id, action="billing.checkout_completed", extra_data={"tier": tier_str}
        )

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        if customer_id is None:
            return {"status": "ignored"}
        subscription = await _find_subscription_by_customer_id(session, customer_id)
        if subscription is None:
            logger.warning("subscription.updated for unknown customer %s", customer_id)
            return {"status": "ignored"}
        subscription.status = _map_stripe_status(data.get("status", ""))
        if subscription.status == SubscriptionStatus.CANCELED:
            subscription.tier = SubscriptionTier.FREE
        current_period_end = data.get("current_period_end")
        if current_period_end is not None:
            subscription.current_period_end = datetime.fromtimestamp(
                current_period_end, tz=timezone.utc
            )
        await session.commit()

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        if customer_id is None:
            return {"status": "ignored"}
        subscription = await _find_subscription_by_customer_id(session, customer_id)
        if subscription is None:
            return {"status": "ignored"}
        subscription.status = SubscriptionStatus.CANCELED
        subscription.tier = SubscriptionTier.FREE
        await session.commit()

    return {"status": "ok"}
```

- [ ] **Step 4: Wire the router into `main.py`**

Modify `backend/src/flowsage_backend/main.py`:

```python
from flowsage_backend.api.billing import router as billing_router
```

Add `app.include_router(billing_router)` alongside the other `include_router` calls (any position is fine — routers don't depend on registration order here).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_billing_api.py -v`
Expected: PASS

- [ ] **Step 6: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

- [ ] **Step 7: Commit**

```bash
git add backend/src/flowsage_backend/api/billing.py backend/src/flowsage_backend/main.py backend/tests/test_billing_api.py
git commit -m "feat: add /billing usage, checkout, portal, and webhook endpoints"
```

---

### Task 8: Wire enforcement into events, simulations, and member invites

**Files:**
- Modify: `backend/src/flowsage_backend/api/events.py`
- Modify: `backend/src/flowsage_backend/api/simulations.py`
- Modify: `backend/src/flowsage_backend/api/workspaces.py`
- Modify: `backend/tests/test_workspaces_api.py` (fix 3 pre-existing tests)
- Test: `backend/tests/test_billing_enforcement_api.py`

**Interfaces:**
- Consumes: `check_within_limits` (Task 4).
- No new public interface — this wires an existing function into three existing route handlers.

- [ ] **Step 1: Write the failing tests (new dedicated 402 tests)**

```python
# backend/tests/test_billing_enforcement_api.py
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.models.workspace import Workspace
from flowsage_backend.billing import TIER_LIMITS
from flowsage_backend.models.billing import SubscriptionTier

from .conftest import create_api_key_for


async def _free_tier_workspace(db_session: AsyncSession, name: str) -> uuid.UUID:
    """A brand-new workspace, deliberately left at the default Free tier (no
    `WorkspaceSubscription` row) -- unlike `create_workspace_and_admin`/
    `ensure_default_workspace`, which Task 6 pinned to Team tier for
    unrelated tests' sake."""
    workspace = Workspace(name=name, slug=f"{name}-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return workspace.id


async def test_ingest_returns_402_when_over_free_event_cap(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _free_tier_workspace(db_session, "events-cap")
    api_key = await create_api_key_for(db_session, workspace_id)
    now = datetime.now(timezone.utc)
    cap = TIER_LIMITS[SubscriptionTier.FREE].events_per_month
    db_session.add_all(
        [
            Event(
                workspace_id=workspace_id,
                session_id=f"s{i}",
                screen="landing",
                event="screen_view",
                timestamp=now,
            )
            for i in range(cap)
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events",
            json=[
                {
                    "session_id": "over-cap",
                    "screen": "landing",
                    "event": "screen_view",
                    "timestamp": (now + timedelta(minutes=1)).isoformat(),
                }
            ],
            headers={"X-API-Key": api_key},
        )

    assert response.status_code == 402
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_billing_enforcement_api.py -v`
Expected: FAIL — `POST /v1/events` currently returns 201 regardless of usage

- [ ] **Step 3: Wire enforcement into `POST /v1/events`**

Modify `backend/src/flowsage_backend/api/events.py`:

```python
from flowsage_backend.billing import check_within_limits
```

In `ingest()`, immediately after the `workspace_id` dependency resolves and before `graph_events = [...]`:

```python
async def ingest(
    payload: list[EventIn],
    request: Request,
    workspace_id: uuid.UUID = Depends(require_workspace_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> IngestResult:
    await check_within_limits(session, workspace_id, "events")
    graph_events = [GraphEvent.model_validate(e.model_dump()) for e in payload]
    ...
```

- [ ] **Step 4: Wire enforcement into `POST /simulations`**

Modify `backend/src/flowsage_backend/api/simulations.py`:

```python
from flowsage_backend.billing import check_within_limits
```

In `create_simulation()`, right after `_, membership = membership_pair` and before `settings = request.app.state.settings`:

```python
async def create_simulation(
    request: Request,
    persona_id: uuid.UUID = Form(...),
    goal: str = Form(...),
    flow_name: str = Form(...),
    files: list[UploadFile] = File(...),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> SimulationRun:
    _, membership = membership_pair
    await check_within_limits(session, membership.workspace_id, "runs")
    settings = request.app.state.settings
    ...
```

- [ ] **Step 5: Wire enforcement into `POST /workspaces/current/members`**

Modify `backend/src/flowsage_backend/api/workspaces.py`:

```python
from flowsage_backend.billing import check_within_limits
```

In `add_member()`, right after `_, membership = membership_pair` and before the `select(User)` lookup:

```python
async def add_member(
    payload: MemberAdd,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> MemberOut:
    _, membership = membership_pair
    await check_within_limits(session, membership.workspace_id, "seats")
    result = await session.execute(select(User).where(User.email == payload.email))
    ...
```

- [ ] **Step 6: Run the new test to verify it passes**

Run: `cd backend && uv run pytest tests/test_billing_enforcement_api.py -v`
Expected: PASS

- [ ] **Step 7: Run the full suite — expect 3 pre-existing failures in `test_workspaces_api.py`**

Run: `cd backend && uv run pytest -q`
Expected: `test_add_member_by_email`, `test_add_member_rejects_duplicate`, and `test_non_admin_cannot_add_member` now FAIL with 402 instead of their expected status codes — each of those tests adds a 2nd member to a fresh (Free-tier, 1-seat) workspace created via `upsert_user`, which is exactly the new seat cap doing its job correctly. These tests' actual intent (email validation, duplicate detection, role authorization) is unrelated to billing, so fix the test setup rather than the new behavior.

- [ ] **Step 8: Fix the 3 pre-existing tests**

Modify `backend/tests/test_workspaces_api.py` — add this helper near the top (after existing imports):

```python
from flowsage_backend.models.billing import SubscriptionTier, WorkspaceSubscription


async def _upgrade_to_team(db_session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """These tests exercise add_member's email/duplicate/role-authorization
    logic, not billing -- give the workspace a Team-tier (unlimited seats)
    subscription first so the new seat cap (billing.check_within_limits)
    doesn't block the 2nd invite these tests need to make their real point."""
    db_session.add(WorkspaceSubscription(workspace_id=workspace_id, tier=SubscriptionTier.TEAM))
    await db_session.commit()
```

In `test_add_member_by_email`, after the `_authed_client` context resolves the admin's workspace (you'll need the workspace id — resolve it via `/auth/me` inside the client context, same pattern `test_non_admin_cannot_add_member` already uses at line ~169, or via a membership lookup on `db_session` before entering `_authed_client` if this test's `admin_email` is known ahead of time):

```python
async def test_add_member_by_email(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-member-admin-{uuid.uuid4().hex[:8]}@example.com"
    invitee_email = f"ws-member-invitee-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, invitee_email, "hunter2")
    admin_user = await upsert_user(db_session, admin_email, "hunter2")
    admin_membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == admin_user.id))
    ).scalar_one()
    await _upgrade_to_team(db_session, admin_membership.workspace_id)

    async with _authed_client(app, db_session, admin_email) as client:
        response = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "researcher"}
        )

    assert response.status_code == 201
    assert response.json()["email"] == invitee_email
    assert response.json()["role"] == "researcher"
```

Apply the identical pattern (`upsert_user` the admin first, resolve their `Membership`, call `_upgrade_to_team`, then enter `_authed_client`) to `test_add_member_rejects_duplicate` and `test_non_admin_cannot_add_member`. `_authed_client` in this file already accepts `admin_email` as a parameter (confirmed from the existing call sites), so calling `upsert_user` on the same email before entering it is safe — `upsert_user` is idempotent (resets password if the user already exists, per its docstring).

Note: check whether `Membership` and `select` are already imported at the top of `test_workspaces_api.py` — if not, add `from sqlalchemy import select` and `from flowsage_backend.models.workspace import Membership` (or extend the existing import line if `Membership`/`Workspace` are already partially imported there).

- [ ] **Step 9: Run the full suite again — verify all green**

Run: `cd backend && uv run pytest -q`
Expected: PASS (full suite, including the 3 fixed tests and the new `test_billing_enforcement_api.py`)

- [ ] **Step 10: Format/typecheck**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src`

- [ ] **Step 11: Commit**

```bash
git add backend/src/flowsage_backend/api/events.py backend/src/flowsage_backend/api/simulations.py backend/src/flowsage_backend/api/workspaces.py backend/tests/test_workspaces_api.py backend/tests/test_billing_enforcement_api.py
git commit -m "feat: enforce freemium caps on event ingestion, simulation runs, and seat invites"
```

---

### Task 9: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces (types.ts): `export type SubscriptionTier = "free" | "pro" | "team";`, `export interface UsageSnapshot { tier: SubscriptionTier; events_used: number; events_limit: number; runs_used: number; runs_limit: number; seats_used: number; seats_limit: number; }`, `export interface CheckoutResult { url: string }`, `export interface PortalResult { url: string }`.
- Produces (api.ts): `api.getBillingUsage(): Promise<UsageSnapshot>`, `api.startCheckout(tier: "pro" | "team"): Promise<CheckoutResult>`, `api.openBillingPortal(): Promise<PortalResult>`.

- [ ] **Step 1: Write the failing test**

Check the existing pattern in `frontend/src/lib/api.test.ts` first (read the file to match its exact `fetchMock`/`vi.stubGlobal` convention), then append a test block following that same convention:

```typescript
describe("billing", () => {
  it("getBillingUsage calls GET /billing/usage", async () => {
    mockFetchOnce({ tier: "free", events_used: 0, events_limit: 1000, runs_used: 0, runs_limit: 5, seats_used: 1, seats_limit: 1 });
    const result = await api.getBillingUsage();
    expect(result.tier).toBe("free");
    expect(fetchSpy).toHaveBeenCalledWith(expect.stringContaining("/billing/usage"), expect.anything());
  });

  it("startCheckout posts the tier and returns a url", async () => {
    mockFetchOnce({ url: "https://checkout.stripe.com/pay/cs_test_123" });
    const result = await api.startCheckout("pro");
    expect(result.url).toBe("https://checkout.stripe.com/pay/cs_test_123");
  });

  it("openBillingPortal posts to /billing/portal", async () => {
    mockFetchOnce({ url: "https://billing.stripe.com/session/bps_test_123" });
    const result = await api.openBillingPortal();
    expect(result.url).toBe("https://billing.stripe.com/session/bps_test_123");
  });
});
```

Adjust `mockFetchOnce`/`fetchSpy` names to whatever helper names `api.test.ts` actually uses (read the file first — do not assume these exact helper names exist; match the file's real mocking convention, e.g. it may use `vi.spyOn(global, "fetch")` directly per-test instead of a shared helper).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- api.test.ts`
Expected: FAIL — `api.getBillingUsage is not a function`

- [ ] **Step 3: Add the types**

Append to `frontend/src/lib/types.ts`:

```typescript
export type SubscriptionTier = "free" | "pro" | "team";

export interface UsageSnapshot {
  tier: SubscriptionTier;
  events_used: number;
  events_limit: number;
  runs_used: number;
  runs_limit: number;
  seats_used: number;
  seats_limit: number;
}

export interface CheckoutResult {
  url: string;
}

export interface PortalResult {
  url: string;
}
```

- [ ] **Step 4: Add the API client methods**

Modify `frontend/src/lib/api.ts` — add `CheckoutResult`, `PortalResult`, `UsageSnapshot` to the type-only import block at the top, then append to the `api` object (before the closing `};`):

```typescript
  getBillingUsage: (): Promise<UsageSnapshot> => request<UsageSnapshot>("/billing/usage"),

  startCheckout: (tier: "pro" | "team"): Promise<CheckoutResult> =>
    request<CheckoutResult>("/billing/checkout", {
      method: "POST",
      body: JSON.stringify({ tier }),
    }),

  openBillingPortal: (): Promise<PortalResult> =>
    request<PortalResult>("/billing/portal", { method: "POST" }),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm test -- api.test.ts`
Expected: PASS

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat: add billing types and API client methods"
```

---

### Task 10: `BillingSettingsPage` + route + nav

**Files:**
- Create: `frontend/src/routes/settings/BillingSettingsPage.tsx`
- Create: `frontend/src/routes/settings/BillingSettingsPage.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`

**Interfaces:**
- Consumes: `api.getBillingUsage`, `api.startCheckout`, `api.openBillingPortal` (Task 9).

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/routes/settings/BillingSettingsPage.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { UsageSnapshot } from "../../lib/types";
import { BillingSettingsPage } from "./BillingSettingsPage";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: {
      ...actual.api,
      getBillingUsage: vi.fn(),
      startCheckout: vi.fn(),
      openBillingPortal: vi.fn(),
    },
  };
});

const FREE_USAGE: UsageSnapshot = {
  tier: "free",
  events_used: 250,
  events_limit: 1000,
  runs_used: 2,
  runs_limit: 5,
  seats_used: 1,
  seats_limit: 1,
};

describe("BillingSettingsPage", () => {
  it("renders the current plan and usage", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);

    render(<BillingSettingsPage />);

    expect(await screen.findByText(/Free/i)).toBeInTheDocument();
    expect(screen.getByText(/250/)).toBeInTheDocument();
    expect(screen.getByText(/1,000|1000/)).toBeInTheDocument();
  });

  it("redirects to Stripe Checkout on Upgrade to Pro", async () => {
    vi.mocked(api.getBillingUsage).mockResolvedValue(FREE_USAGE);
    vi.mocked(api.startCheckout).mockResolvedValue({ url: "https://checkout.stripe.com/pay/cs_test_123" });
    const assignMock = vi.fn();
    Object.defineProperty(window, "location", { value: { assign: assignMock }, writable: true });

    render(<BillingSettingsPage />);
    await screen.findByText(/Free/i);
    fireEvent.click(screen.getByRole("button", { name: /Upgrade to Pro/i }));

    await waitFor(() => {
      expect(api.startCheckout).toHaveBeenCalledWith("pro");
      expect(assignMock).toHaveBeenCalledWith("https://checkout.stripe.com/pay/cs_test_123");
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- BillingSettingsPage.test.tsx`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write `BillingSettingsPage.tsx`**

```tsx
// frontend/src/routes/settings/BillingSettingsPage.tsx
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { UsageSnapshot } from "../../lib/types";

const TIER_LABELS: Record<UsageSnapshot["tier"], string> = {
  free: "Free",
  pro: "Pro",
  team: "Team",
};

function UsageBar({ label, used, limit }: { label: string; used: number; limit: number }) {
  const unlimited = limit === -1;
  const pct = unlimited ? 0 : Math.min(100, (used / limit) * 100);
  const color = pct >= 100 ? "bg-error" : pct >= 80 ? "bg-tertiary" : "bg-primary";

  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-sm">
        <span className="text-on-surface-variant">{label}</span>
        <span>{unlimited ? `${used.toLocaleString()} / Unlimited` : `${used.toLocaleString()} / ${limit.toLocaleString()}`}</span>
      </div>
      {!unlimited ? (
        <div className="h-2 rounded-full bg-surface-container overflow-hidden">
          <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
        </div>
      ) : null}
    </div>
  );
}

export function BillingSettingsPage() {
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    api
      .getBillingUsage()
      .then(setUsage)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load billing usage.");
      });
  }, []);

  async function handleUpgrade(tier: "pro" | "team") {
    setError(null);
    setRedirecting(true);
    try {
      const result = await api.startCheckout(tier);
      window.location.assign(result.url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start checkout.");
      setRedirecting(false);
    }
  }

  async function handleManageBilling() {
    setError(null);
    setRedirecting(true);
    try {
      const result = await api.openBillingPortal();
      window.location.assign(result.url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to open billing portal.");
      setRedirecting(false);
    }
  }

  if (usage === null) {
    return error !== null ? (
      <p className="text-error text-sm">{error}</p>
    ) : (
      <p className="text-on-surface-variant text-sm">Loading…</p>
    );
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Billing</h1>
        <p className="text-on-surface-variant mt-1">
          Current plan: <span className="font-medium">{TIER_LABELS[usage.tier]}</span>
        </p>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Usage This Month</h2>
        <UsageBar label="Events ingested" used={usage.events_used} limit={usage.events_limit} />
        <UsageBar label="Simulation runs" used={usage.runs_used} limit={usage.runs_limit} />
        <UsageBar label="Seats" used={usage.seats_used} limit={usage.seats_limit} />
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Manage Plan</h2>
        <div className="flex gap-3 flex-wrap">
          {usage.tier !== "pro" && usage.tier !== "team" ? (
            <button
              type="button"
              onClick={() => void handleUpgrade("pro")}
              disabled={redirecting}
              className="rounded-lg bg-primary py-2.5 px-6 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
            >
              Upgrade to Pro
            </button>
          ) : null}
          {usage.tier !== "team" ? (
            <button
              type="button"
              onClick={() => void handleUpgrade("team")}
              disabled={redirecting}
              className="rounded-lg ghost-border py-2.5 px-6 font-medium hover:bg-surface-container transition disabled:opacity-50"
            >
              Upgrade to Team
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void handleManageBilling()}
            disabled={redirecting}
            className="rounded-lg ghost-border py-2.5 px-6 font-medium hover:bg-surface-container transition disabled:opacity-50"
          >
            Manage Billing
          </button>
        </div>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- BillingSettingsPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Wire the route and nav item**

Modify `frontend/src/App.tsx`:

```typescript
import { BillingSettingsPage } from "./routes/settings/BillingSettingsPage";
```

Add inside the `<Route element={<RequireAuth><Shell /></RequireAuth>}>` block, alongside the other settings routes:

```tsx
<Route path="/settings/billing" element={<BillingSettingsPage />} />
```

Modify `frontend/src/components/Sidebar.tsx` — add to `NAV_ITEMS` (after `/settings/general`, before `/settings/team`, matching the settings-pages grouping):

```typescript
{ to: "/settings/billing", label: "Billing", icon: "credit_card" },
```

- [ ] **Step 6: Typecheck + full frontend test run**

Run: `cd frontend && npm run typecheck && npm test`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/settings/BillingSettingsPage.tsx frontend/src/routes/settings/BillingSettingsPage.test.tsx frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: add /settings/billing page with usage bars and upgrade CTAs"
```

---

### Task 11: `UsageLimitBanner` — wire 402s into Predictive Engine + Team Settings

**Note on scope vs. the approved spec:** the spec's wording ("Dashboard, Predictive Engine, Journey Graph") was written before tracing exactly which frontend actions call the three newly-enforced endpoints. Only two real user-facing actions can actually trigger a 402: creating a simulation run (`PredictiveEnginePage`, `POST /simulations`) and inviting a teammate (`TeamSettingsPage`, `POST /workspaces/current/members`). `POST /v1/events` is server-to-server (API-key auth) and is never called from the browser, so there is no frontend surface for that 402 to land on. Wiring the banner into Dashboard would be dead code — it never calls an enforced endpoint. This task wires the two real surfaces instead.

**Files:**
- Create: `frontend/src/components/UsageLimitBanner.tsx`
- Create: `frontend/src/components/UsageLimitBanner.test.tsx`
- Modify: `frontend/src/routes/predictive/PredictiveEnginePage.tsx`
- Modify: `frontend/src/routes/settings/TeamSettingsPage.tsx`

**Interfaces:**
- Produces: `UsageLimitBanner({ message }: { message: string | null })` — renders `null` when `message` is `null`, otherwise an upgrade CTA banner linking to `/settings/billing`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/UsageLimitBanner.test.tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { UsageLimitBanner } from "./UsageLimitBanner";

describe("UsageLimitBanner", () => {
  it("renders nothing when message is null", () => {
    const { container } = render(
      <MemoryRouter>
        <UsageLimitBanner message={null} />
      </MemoryRouter>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the message and an upgrade link when present", () => {
    render(
      <MemoryRouter>
        <UsageLimitBanner message="Free plan limit reached for runs (5/5). Upgrade to continue." />
      </MemoryRouter>,
    );
    expect(screen.getByText(/Free plan limit reached for runs/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Upgrade/i })).toHaveAttribute("href", "/settings/billing");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- UsageLimitBanner.test.tsx`
Expected: FAIL — module doesn't exist

- [ ] **Step 3: Write `UsageLimitBanner.tsx`**

```tsx
// frontend/src/components/UsageLimitBanner.tsx
import { Link } from "react-router-dom";

export function UsageLimitBanner({ message }: { message: string | null }) {
  if (message === null) return null;

  return (
    <div className="rounded-xl border-l-4 border-error bg-error-container/20 p-4 flex items-center justify-between gap-4 flex-wrap">
      <p className="text-sm text-on-error-container">{message}</p>
      <Link
        to="/settings/billing"
        className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium hover:opacity-90 transition whitespace-nowrap"
      >
        Upgrade
      </Link>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- UsageLimitBanner.test.tsx`
Expected: PASS

- [ ] **Step 5: Wire into `PredictiveEnginePage.tsx`**

Modify `frontend/src/routes/predictive/PredictiveEnginePage.tsx`:

```typescript
import { UsageLimitBanner } from "../../components/UsageLimitBanner";
```

Add state and update the catch block in `handleSubmit`:

```typescript
  const [usageLimitMessage, setUsageLimitMessage] = useState<string | null>(null);
```

```typescript
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setUsageLimitMessage(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : "Failed to start simulation.");
      }
    } finally {
```

Also clear it at the top of `handleSubmit` alongside `setError(null)`: add `setUsageLimitMessage(null);`.

Render it in the JSX, near the existing error paragraph: `<UsageLimitBanner message={usageLimitMessage} />`.

- [ ] **Step 6: Wire into `TeamSettingsPage.tsx`**

Modify `frontend/src/routes/settings/TeamSettingsPage.tsx` following the identical pattern: import `UsageLimitBanner`, add `usageLimitMessage` state, branch on `err instanceof ApiError && err.status === 402` in the `addMember` catch block (the one at the line matching `"Failed to add member."`), clear it alongside the existing `setError(null)` at the top of that handler, and render `<UsageLimitBanner message={usageLimitMessage} />` in the JSX.

- [ ] **Step 7: Typecheck + full frontend test run**

Run: `cd frontend && npm run typecheck && npm test`

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/UsageLimitBanner.tsx frontend/src/components/UsageLimitBanner.test.tsx frontend/src/routes/predictive/PredictiveEnginePage.tsx frontend/src/routes/settings/TeamSettingsPage.tsx
git commit -m "feat: surface 402 usage-limit banners on simulation creation and member invites"
```

---

### Task 12: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend verification**

Run: `cd backend && uv run autoflake8 --recursive --check src tests && uv run black --check src tests && uv run mypy --strict src && uv run pytest -v`
Expected: all clean, full suite green

- [ ] **Step 2: Full frontend verification**

Run: `cd frontend && npx oxlint && npm run typecheck && npm test && npm run build`
Expected: all clean (use `npx oxlint` directly rather than `npm run lint` — this repo's `rtk` CLI wrapper has a known false-failure on plain oxlint output, per project memory)

- [ ] **Step 3: Migration upgrade → downgrade → upgrade cycle**

Against a real standalone Postgres (e.g. via `docker run --rm -p 5433:5432 -e POSTGRES_PASSWORD=flowsage_dev -e POSTGRES_USER=flowsage -e POSTGRES_DB=flowsage postgres:16-alpine`, or the project's `infra/docker-compose.yml` `db` service):

```bash
cd backend
DATABASE_URL=postgresql+asyncpg://flowsage:flowsage_dev@localhost:5433/flowsage uv run alembic upgrade head
DATABASE_URL=postgresql+asyncpg://flowsage:flowsage_dev@localhost:5433/flowsage uv run alembic downgrade -1
DATABASE_URL=postgresql+asyncpg://flowsage:flowsage_dev@localhost:5433/flowsage uv run alembic upgrade head
```
Expected: no errors at any step (the enum-drop-on-downgrade fix from Task 1 is what makes the down→up cycle safe)

- [ ] **Step 4: Full `docker-compose up -d --build` pass**

```bash
cd infra
docker compose up -d --build
```
If PyPI downloads time out through the sandboxed proxy, retry with the proxy workaround from project memory:
```bash
HTTP_PROXY=http://172.17.0.1:11180 HTTPS_PROXY=http://172.17.0.1:11180 docker compose up -d --build
```

Then, against the running stack:
- Migrate: `docker compose exec backend /workspace/.venv/bin/python -m alembic -c /workspace/backend/alembic.ini upgrade head`
- Create a user: `docker compose exec backend /workspace/.venv/bin/flowsage-backend create-user verify@flowsage.dev supersecret123`
- `curl http://localhost:8000/billing/usage` unauthenticated → expect 401
- Log in via the browser (or curl with cookie jar) and `curl` `/billing/usage` authenticated → expect 200, `{"tier": "free", ...}`
- `curl -X POST http://localhost:8000/billing/checkout -d '{"tier":"pro"}' -H "Content-Type: application/json" --cookie <session>` → expect 400 (Stripe unconfigured, no live keys this session)
- `curl -X POST http://localhost:8000/billing/portal --cookie <session>` → expect 400 (no Stripe customer yet)
- Manually construct a signed webhook payload without any live Stripe account (no live keys needed — signature verification only needs the shared secret, which you control): set `STRIPE_WEBHOOK_SECRET=whsec_manual_test` in the backend's environment, then from a Python shell inside the container:
  ```python
  import stripe, json, time
  payload = json.dumps({"id": "evt_manual", "type": "checkout.session.completed", "data": {"object": {"customer": "cus_manual", "subscription": "sub_manual", "metadata": {"workspace_id": "<real-workspace-uuid>", "tier": "pro"}}}}).encode()
  header = stripe.WebhookSignature.sign_header(payload, "whsec_manual_test")  # or hmac-construct as in the unit tests
  ```
  POST that payload + signature header to `/billing/webhook`, confirm 200 and that `workspace_subscriptions.tier` flips to `pro` via `psql`.
- Log in via a real Playwright browser session, visit `/settings/billing`, confirm the usage bars render with the Free-tier defaults and the sidebar shows "Billing".
- Seed enough events/runs/members past the Free caps for a throwaway workspace and confirm `/settings/billing`'s usage bars turn red at 100%+, and that attempting one more simulation run or member invite in the UI surfaces the `UsageLimitBanner`.
- Tear down: `docker compose down -v`

- [ ] **Step 5: Push to main**

```bash
git push origin main
```

- [ ] **Step 6: Update project memory**

Update `project_build_status.md` (the FlowSage build-status memory file) with a new dated entry summarizing: Stripe billing (Phase 4 item 1) shipped — tiers/limits, `WorkspaceSubscription` model, on-demand usage counting, Checkout/Portal/webhook, enforcement on events/runs/seats, `/settings/billing` UI, and the test-isolation fix from Task 6 (worth remembering the same way the Phase 2 chunk 1 gotcha is recorded, since it's the same category of issue). Note remaining Phase 4 items: Figma plugin, deploy + landing/docs.
