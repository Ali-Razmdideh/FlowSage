# Phase 3 chunk 1: Workspace Multi-Tenancy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn FlowSage from single-tenant into multi-tenant: a `Workspace`/`Membership` model with Admin/Researcher/Viewer roles, row-level scoping on all 8 tenant-owned tables, an active-workspace-in-JWT auth flow with a switch-workspace endpoint, and `/settings/general` + `/settings/team` UI, per `docs/superpowers/specs/2026-07-19-workspace-multitenancy-design.md`.

**Architecture:** New `Workspace`/`Membership` SQLAlchemy models + a `Role` enum ordinal (`admin` > `researcher` > `viewer`). JWT payload gains `workspace_id`; `deps.get_current_membership` replaces `get_current_user` everywhere and returns `(User, Membership)`; a `require_role(min_role)` dependency factory 403s on insufficient role. Every existing router adds a `.where(Model.workspace_id == membership.workspace_id)` clause and sets `workspace_id` on insert. A migration backfills one "Default" workspace + admin membership for all pre-existing data.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async ORM, Alembic, PyJWT, pytest + testcontainers (Postgres/Redis/Neo4j), React 18 + TypeScript + Vite, Vitest, Playwright.

## Global Constraints

- Python 3.12, `uv` for dependency management (no new dependencies needed in this chunk).
- Follow existing code conventions exactly: `from __future__ import annotations` at the top of every backend module; Pydantic `BaseModel` with `ConfigDict(from_attributes=True)` for ORM-backed response models; async SQLAlchemy 2.0 `Mapped[...]`/`mapped_column` style; routers use module-level `router = APIRouter(...)` with `dependencies=[Depends(...)]` for blanket auth.
- No new HTTP client library on the frontend — plain `fetch` via `frontend/src/lib/api.ts`'s `request<T>` helper.
- Every task ends with the relevant test suite green before moving to the next task.
- `mypy --strict` (backend) and `tsc` (frontend) must stay clean after every task — this repo runs both per the project's build process.
- Commit after each task (small, working increments — matches every prior chunk in this repo's history).

---

### Task 1: `Workspace`/`Membership` models + schema migration + backfill

**Files:**
- Create: `backend/src/flowsage_backend/models/workspace.py`
- Modify: `backend/src/flowsage_backend/models/__init__.py`
- Modify: `backend/src/flowsage_backend/models/persona.py` (add `workspace_id` to `Persona`, `PersonaMemory`)
- Modify: `backend/src/flowsage_backend/models/simulation.py` (add `workspace_id` to `SimulationRun`, `SimulationStep`, `FrictionIssue`)
- Modify: `backend/src/flowsage_backend/models/calibration.py` (add `workspace_id` to `RetrainingJob`)
- Modify: `backend/src/flowsage_backend/models/event.py` (add `workspace_id` to `Event`)
- Modify: `backend/src/flowsage_backend/models/settings.py` (add `workspace_id` to `CalibrationSettings`, drop singleton assumption)
- Create: `backend/migrations/versions/<rev1>_add_workspaces_and_memberships.py`
- Create: `backend/migrations/versions/<rev2>_backfill_default_workspace.py`
- Test: `backend/tests/test_workspace_migration.py`

**Interfaces:**
- Produces: `Workspace` (id, name, slug, description, avatar_url, privacy, region, retention_days, archived, created_at), `Membership` (id, user_id, workspace_id, role, created_at), `Role` enum (`ADMIN`, `RESEARCHER`, `VIEWER`) with `.value` `"admin"`/`"researcher"`/`"viewer"` and an `ordinal()` method. Every one of `Persona`, `PersonaMemory`, `SimulationRun`, `SimulationStep`, `FrictionIssue`, `RetrainingJob`, `Event`, `CalibrationSettings` gains a non-nullable `workspace_id: Mapped[uuid.UUID]` column (`ForeignKey("workspaces.id", ondelete="CASCADE")`).
- Consumes: nothing (foundational task).

- [ ] **Step 1: Write `models/workspace.py`**

```python
"""Workspace (tenant) and per-user role membership within it.

`Workspace` is the row-level scoping boundary for every tenant-owned table
(personas, simulation runs, events, etc.) added in Phase 3. `Membership` is
the join between a `User` and a `Workspace`, carrying that user's role in
that workspace -- a user can belong to more than one workspace, each with
its own role.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from flowsage_backend.models.base import Base


class WorkspacePrivacy(str, enum.Enum):
    PRIVATE = "private"
    RESTRICTED = "restricted"


class Role(str, enum.Enum):
    VIEWER = "viewer"
    RESEARCHER = "researcher"
    ADMIN = "admin"

    def ordinal(self) -> int:
        return {Role.VIEWER: 1, Role.RESEARCHER: 2, Role.ADMIN: 3}[self]


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    privacy: Mapped[WorkspacePrivacy] = mapped_column(
        SAEnum(WorkspacePrivacy, name="workspace_privacy"), default=WorkspacePrivacy.PRIVATE
    )
    region: Mapped[str] = mapped_column(String(64), default="us")
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list["Membership"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "workspace_id", name="uq_membership_user_workspace"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(SAEnum(Role, name="membership_role"), default=Role.VIEWER)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
```

- [ ] **Step 2: Register the new models in `models/__init__.py`**

```python
"""SQLAlchemy ORM models. Import submodules here so Alembic autogenerate sees them."""

from flowsage_backend.models.base import Base
from flowsage_backend.models.calibration import RetrainingJob, RetrainingStatus
from flowsage_backend.models.event import Event
from flowsage_backend.models.persona import Persona, PersonaMemory
from flowsage_backend.models.settings import CalibrationSettings, DigestFrequency
from flowsage_backend.models.simulation import (
    FrictionIssue,
    RunStatus,
    SimulationRun,
    SimulationStep,
)
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role, Workspace, WorkspacePrivacy

__all__ = [
    "Base",
    "User",
    "Workspace",
    "WorkspacePrivacy",
    "Membership",
    "Role",
    "Persona",
    "PersonaMemory",
    "SimulationRun",
    "SimulationStep",
    "FrictionIssue",
    "RunStatus",
    "Event",
    "RetrainingJob",
    "RetrainingStatus",
    "CalibrationSettings",
    "DigestFrequency",
]
```

- [ ] **Step 3: Add `workspace_id` to the 8 tenant-owned models**

In `models/persona.py`, add to both `Persona` and `PersonaMemory` (right after each class's `id` column):

```python
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
```

Needs `from sqlalchemy import ForeignKey` added to `persona.py`'s existing `from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text, func` import (already imports `ForeignKey` — no change needed there, `PersonaMemory` already imports it too).

In `models/simulation.py`, add the same `workspace_id` column to `SimulationRun`, `SimulationStep`, and `FrictionIssue` (each right after their `id` column). `ForeignKey` is already imported.

In `models/calibration.py`, add it to `RetrainingJob` (right after `id`). `ForeignKey` is already imported.

In `models/event.py`, add it to `Event` (right after `id`). Needs `from sqlalchemy import DateTime, ForeignKey, String` (add `ForeignKey` to the existing import).

In `models/settings.py`, add it to `CalibrationSettings` (right after `id`), and update the module docstring's first paragraph to remove "Single-tenant, so this is a **singleton row**" language:

```python
"""Per-workspace calibration/alerting settings (`/settings/model-calibration`).

One row per workspace (see `flowsage_backend.settings_store.get_or_create_calibration_settings`).
Values here override the hardcoded defaults in `flowsage_backend.calibration` /
`flowsage_backend.alerts` when present.
"""
```

Needs `from sqlalchemy import Boolean, DateTime, Float, ForeignKey, func` (add `ForeignKey`).

- [ ] **Step 4: Write the schema migration**

Run `cd backend && uv run alembic revision -m "add workspaces and memberships"` to get a real revision id (call it `<rev1>`, chained from the current head `d68463a9d9c1`), then replace its body:

```python
"""add workspaces and memberships

Revision ID: <rev1>
Revises: d68463a9d9c1
Create Date: 2026-07-19 ...

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '<rev1>'
down_revision: Union[str, Sequence[str], None] = 'd68463a9d9c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT_TABLES = [
    "personas",
    "persona_memories",
    "simulation_runs",
    "simulation_steps",
    "friction_issues",
    "retraining_jobs",
    "calibration_settings",
    "events",
]


def upgrade() -> None:
    op.create_table(
        'workspaces',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('slug', sa.String(length=64), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('avatar_url', sa.String(length=500), nullable=True),
        sa.Column(
            'privacy',
            sa.Enum('PRIVATE', 'RESTRICTED', name='workspace_privacy'),
            nullable=False,
        ),
        sa.Column('region', sa.String(length=64), nullable=False),
        sa.Column('retention_days', sa.Integer(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_workspaces_slug'), 'workspaces', ['slug'], unique=True)

    op.create_table(
        'memberships',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column(
            'role',
            sa.Enum('VIEWER', 'RESEARCHER', 'ADMIN', name='membership_role'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'workspace_id', name='uq_membership_user_workspace'),
    )
    op.create_index(op.f('ix_memberships_user_id'), 'memberships', ['user_id'], unique=False)
    op.create_index(op.f('ix_memberships_workspace_id'), 'memberships', ['workspace_id'], unique=False)

    for table in _TENANT_TABLES:
        op.add_column(table, sa.Column('workspace_id', sa.Uuid(), nullable=True))
        op.create_index(op.f(f'ix_{table}_workspace_id'), table, ['workspace_id'], unique=False)
        op.create_foreign_key(
            f'fk_{table}_workspace_id', table, 'workspaces', ['workspace_id'], ['id'], ondelete='CASCADE'
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_constraint(f'fk_{table}_workspace_id', table, type_='foreignkey')
        op.drop_index(op.f(f'ix_{table}_workspace_id'), table_name=table)
        op.drop_column(table, 'workspace_id')

    op.drop_index(op.f('ix_memberships_workspace_id'), table_name='memberships')
    op.drop_index(op.f('ix_memberships_user_id'), table_name='memberships')
    op.drop_table('memberships')
    sa.Enum(name='membership_role').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_workspaces_slug'), table_name='workspaces')
    op.drop_table('workspaces')
    sa.Enum(name='workspace_privacy').drop(op.get_bind(), checkfirst=True)
```

- [ ] **Step 5: Write the backfill + NOT NULL migration**

Run `uv run alembic revision -m "backfill default workspace"` chained after `<rev1>` (call it `<rev2>`):

```python
"""backfill default workspace

Revision ID: <rev2>
Revises: <rev1>
Create Date: 2026-07-19 ...

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '<rev2>'
down_revision: Union[str, Sequence[str], None] = '<rev1>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TENANT_TABLES = [
    "personas",
    "persona_memories",
    "simulation_runs",
    "simulation_steps",
    "friction_issues",
    "retraining_jobs",
    "calibration_settings",
    "events",
]


def upgrade() -> None:
    bind = op.get_bind()
    workspace_id = uuid.uuid4()

    workspaces = sa.table(
        'workspaces',
        sa.column('id', sa.Uuid()),
        sa.column('name', sa.String()),
        sa.column('slug', sa.String()),
        sa.column('description', sa.Text()),
        sa.column('privacy', sa.String()),
        sa.column('region', sa.String()),
        sa.column('retention_days', sa.Integer()),
        sa.column('archived', sa.Boolean()),
    )
    bind.execute(
        workspaces.insert().values(
            id=workspace_id,
            name="Default",
            slug="fs-default",
            description="",
            privacy="PRIVATE",
            region="us",
            retention_days=90,
            archived=False,
        )
    )

    memberships = sa.table(
        'memberships',
        sa.column('id', sa.Uuid()),
        sa.column('user_id', sa.Uuid()),
        sa.column('workspace_id', sa.Uuid()),
        sa.column('role', sa.String()),
    )
    users = sa.table('users', sa.column('id', sa.Uuid()))
    for (user_id,) in bind.execute(sa.select(users.c.id)).fetchall():
        bind.execute(
            memberships.insert().values(
                id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, role="ADMIN"
            )
        )

    for table_name in _TENANT_TABLES:
        table = sa.table(table_name, sa.column('workspace_id', sa.Uuid()))
        bind.execute(table.update().values(workspace_id=workspace_id))
        op.alter_column(table_name, 'workspace_id', nullable=False)


def downgrade() -> None:
    for table_name in _TENANT_TABLES:
        op.alter_column(table_name, 'workspace_id', nullable=True)
    op.execute("DELETE FROM memberships")
    op.execute("DELETE FROM workspaces")
```

- [ ] **Step 6: Write the migration test**

```python
"""backend/tests/test_workspace_migration.py"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="module")
def sync_postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container.get_connection_url()


def _alembic_config(sync_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


def test_backfill_migration_creates_default_workspace_and_scopes_existing_rows(
    sync_postgres_url: str,
) -> None:
    config = _alembic_config(sync_postgres_url)
    # Migrate to just before this chunk's changes, seed pre-existing rows, then upgrade.
    command.upgrade(config, "d68463a9d9c1")

    engine = create_engine(sync_postgres_url)
    user_id = uuid.uuid4()
    persona_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, created_at) "
                "VALUES (:id, 'pre-existing@example.com', 'x', now())"
            ),
            {"id": user_id},
        )
        conn.execute(
            text(
                "INSERT INTO personas (id, slug, name, description, baseline, tech_affinity, "
                "primary_device, discovery_mode, contextual_triggers, technical_literacy, "
                "anxiety, patience, curiosity, model, created_at) VALUES "
                "(:id, 'pre-existing', 'Pre-existing', 'd', false, 'Low', 'Desktop', 'Search', "
                "'[]', 0.5, 0.5, 0.5, 0.5, 'claude-sonnet-4-5', now())"
            ),
            {"id": persona_id},
        )

    command.upgrade(config, "head")

    with engine.begin() as conn:
        workspace_row = conn.execute(text("SELECT id FROM workspaces WHERE slug = 'fs-default'")).one()
        default_workspace_id = workspace_row[0]

        membership_role = conn.execute(
            text("SELECT role FROM memberships WHERE user_id = :uid"), {"uid": user_id}
        ).scalar_one()
        assert membership_role == "ADMIN"

        persona_workspace_id = conn.execute(
            text("SELECT workspace_id FROM personas WHERE id = :pid"), {"pid": persona_id}
        ).scalar_one()
        assert persona_workspace_id == default_workspace_id

        # workspace_id is NOT NULL after the backfill.
        result = conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'personas' AND column_name = 'workspace_id'"
            )
        ).scalar_one()
        assert result == "NO"

    engine.dispose()
```

- [ ] **Step 7: Run the migration test**

Run: `cd backend && uv run pytest tests/test_workspace_migration.py -v`
Expected: PASS. (Fill in `<rev1>`/`<rev2>` with the real IDs Alembic generated in Step 4/5 before running.)

- [ ] **Step 8: Run full backend test suite to confirm nothing else broke**

Run: `cd backend && uv run pytest -x -q`
Expected: existing tests fail here (they don't yet pass a workspace-scoped session) — that's expected and fixed across Tasks 2, 5, 6. Confirm failures are all about missing `workspace_id`/auth, not import/syntax errors.

- [ ] **Step 9: Commit**

```bash
git add backend/src/flowsage_backend/models backend/migrations/versions backend/tests/test_workspace_migration.py
git commit -m "feat: add Workspace/Membership models and backfill migration"
```

---

### Task 2: Auth layer — JWT workspace context, `require_role`, switch-workspace

**Files:**
- Modify: `backend/src/flowsage_backend/security.py`
- Modify: `backend/src/flowsage_backend/deps.py`
- Modify: `backend/src/flowsage_backend/api/auth.py`
- Modify: `backend/src/flowsage_backend/seed.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_auth.py`
- Modify: `backend/tests/test_security.py`
- Test: (same files above, extended)

**Interfaces:**
- Consumes: `Workspace`, `Membership`, `Role` from Task 1.
- Produces: `security.create_access_token(user_id, workspace_id, *, secret, algorithm, expires_minutes)`, `security.decode_access_token(token, *, secret, algorithm) -> tuple[uuid.UUID, uuid.UUID]` (now returns `(user_id, workspace_id)`), `deps.get_current_membership(request, session) -> tuple[User, Membership]`, `deps.require_role(min_role: Role)` (dependency factory), `POST /auth/switch-workspace`. Every other task's routers depend on `get_current_membership`/`require_role` exactly as defined here.

- [ ] **Step 1: Update `security.py`'s token functions**

```python
def create_access_token(
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    *,
    secret: str,
    algorithm: str,
    expires_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "workspace_id": str(workspace_id),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Raises `jwt.PyJWTError` (or a subclass) if the token is invalid, expired, or malformed."""
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    return uuid.UUID(payload["sub"]), uuid.UUID(payload["workspace_id"])
```

- [ ] **Step 2: Update `test_security.py`'s token round-trip test(s) to match the new signature**

Find the existing `create_access_token`/`decode_access_token` test(s) (they currently call with just `user_id`) and update both the call and assertion to the two-value tuple, e.g.:

```python
def test_create_and_decode_access_token_round_trips() -> None:
    user_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    token = create_access_token(
        user_id, workspace_id, secret="test-secret", algorithm="HS256", expires_minutes=5
    )
    decoded_user_id, decoded_workspace_id = decode_access_token(
        token, secret="test-secret", algorithm="HS256"
    )
    assert decoded_user_id == user_id
    assert decoded_workspace_id == workspace_id
```

(Locate the existing test by this name/shape in `test_security.py` and replace it in place rather than duplicating.)

- [ ] **Step 3: Rewrite `deps.py`**

```python
"""FastAPI dependency providers: DB session, the current authenticated membership
(user + their role in the active workspace), and the shared-secret API key check
used by the server-to-server ingestion endpoint."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role
from flowsage_backend.security import decode_access_token


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


async def get_current_membership(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> tuple[User, Membership]:
    settings = request.app.state.settings
    token = request.cookies.get(settings.cookie_name)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    try:
        user_id, workspace_id = decode_access_token(
            token, secret=settings.jwt_secret, algorithm=settings.jwt_algorithm
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session") from exc

    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists")

    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user_id, Membership.workspace_id == workspace_id
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No membership in the active workspace")

    return user, membership


def require_role(min_role: Role) -> Callable[..., "AsyncIterator[tuple[User, Membership]]"]:
    async def _dependency(
        membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    ) -> tuple[User, Membership]:
        _, membership = membership_pair
        if membership.role.ordinal() < min_role.ordinal():
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role for this action")
        return membership_pair

    return _dependency


async def require_api_key(request: Request) -> None:
    settings = request.app.state.settings
    provided = request.headers.get("X-API-Key")
    if provided is None or not secrets.compare_digest(provided, settings.events_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")
```

- [ ] **Step 4: Bootstrap a workspace for new users in `seed.py`**

```python
"""Seed data: the single-tenant admin user (now bootstrapped with a personal
workspace), and the 5 baseline personas.

There is no public registration endpoint. Accounts are seeded via the
`flowsage-backend create-user` CLI command, which calls `upsert_user` below --
a brand-new user gets a personal "Default" workspace and becomes its admin;
resetting an existing user's password leaves their workspaces untouched.
"""

from __future__ import annotations

import uuid

from flowsage_predict.personas import load_baseline_personas
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.persona import Persona
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.security import hash_password


async def upsert_user(session: AsyncSession, email: str, password: str) -> User:
    """Create the user (with a personal workspace) if it doesn't exist, or reset
    its password if it does."""
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(email=email, hashed_password=hash_password(password))
        session.add(user)
        await session.flush()

        workspace = Workspace(name="Default", slug=f"fs-{uuid.uuid4().hex[:8]}")
        session.add(workspace)
        await session.flush()

        session.add(Membership(user_id=user.id, workspace_id=workspace.id, role=Role.ADMIN))
    else:
        user.hashed_password = hash_password(password)
    await session.commit()
    await session.refresh(user)
    return user


async def seed_baseline_personas(session: AsyncSession, workspace_id: uuid.UUID) -> list[Persona]:
    """Load the 5 baseline personas from flowsage-predict into the `personas` table,
    scoped to `workspace_id`.

    Idempotent per workspace: existing rows (matched by slug + workspace_id) are
    left as-is, not overwritten.
    """
    rows: list[Persona] = []
    for persona in load_baseline_personas():
        result = await session.execute(
            select(Persona).where(
                Persona.slug == persona.id, Persona.workspace_id == workspace_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            rows.append(existing)
            continue

        row = Persona(
            workspace_id=workspace_id,
            slug=persona.id,
            name=persona.name,
            description=persona.description,
            baseline=persona.baseline,
            tech_affinity=persona.demographic_anchors.tech_affinity,
            primary_device=persona.demographic_anchors.primary_device,
            discovery_mode=persona.demographic_anchors.discovery_mode,
            contextual_triggers=list(persona.contextual_triggers),
            technical_literacy=persona.sliders.technical_literacy,
            anxiety=persona.sliders.anxiety,
            patience=persona.sliders.patience,
            curiosity=persona.sliders.curiosity,
            model=persona.model,
        )
        session.add(row)
        rows.append(row)

    await session.commit()
    for row in rows:
        await session.refresh(row)
    return rows
```

Note: `seed_baseline_personas` gains a required `workspace_id` param — its one caller, `__main__.py`'s `_seed_personas`, is updated in Step 6 below. The `Persona.slug` column's `unique=True` constraint (persona.py:26) must become a composite unique constraint on `(slug, workspace_id)` instead of a bare unique — add this to Task 1's Step 3 model edit for `Persona` (add `__table_args__ = (UniqueConstraint("slug", "workspace_id", name="uq_persona_slug_workspace"),)` and drop the column-level `unique=True`), and add the matching Alembic operation (`op.drop_constraint`/`op.create_unique_constraint`) to Task 1 Step 4's migration. Since Task 1 is already committed by this point, add a small follow-up edit now: reopen `models/persona.py` and Task 1's `<rev1>` migration file to make this change before continuing this task's remaining steps.

- [ ] **Step 5: Update `auth.py`**

```python
"""Auth endpoints: email+password login with a JWT httpOnly cookie carrying the
active workspace, plus switching between a user's workspaces."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role
from flowsage_backend.security import create_access_token, dummy_password_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SwitchWorkspaceRequest(BaseModel):
    workspace_id: uuid.UUID


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    name: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime
    workspace_id: uuid.UUID
    role: Role
    workspaces: list[WorkspaceSummary]


def _set_session_cookie(
    response: Response, request: Request, user_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    settings = request.app.state.settings
    token = create_access_token(
        user_id,
        workspace_id,
        secret=settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
        expires_minutes=settings.jwt_expires_minutes,
    )
    response.set_cookie(
        settings.cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expires_minutes * 60,
    )


async def _first_membership_or_401(session: AsyncSession, user_id: uuid.UUID) -> Membership:
    result = await session.execute(
        select(Membership).where(Membership.user_id == user_id).order_by(Membership.created_at)
    )
    membership = result.scalars().first()
    if membership is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User has no workspace membership")
    return membership


@router.post("/login", response_model=MeOut)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
) -> MeOut:
    result = await session.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    # Always hash-verify, even for an unknown email, so a wrong password and an unknown
    # email take about the same time -- see dummy_password_hash's docstring.
    password_ok = verify_password(
        payload.password, user.hashed_password if user is not None else dummy_password_hash()
    )
    if user is None or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    membership = await _first_membership_or_401(session, user.id)
    _set_session_cookie(response, request, user.id, membership.workspace_id)
    return await _build_me_out(session, user, membership)


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    settings = request.app.state.settings
    response.delete_cookie(
        settings.cookie_name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return {"status": "logged_out"}


async def _build_me_out(session: AsyncSession, user: User, membership: Membership) -> MeOut:
    result = await session.execute(
        select(Membership)
        .where(Membership.user_id == user.id)
        .options(selectinload(Membership.workspace))
    )
    memberships = result.scalars().all()
    return MeOut(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        workspace_id=membership.workspace_id,
        role=membership.role,
        workspaces=[
            WorkspaceSummary(id=m.workspace_id, name=m.workspace.name) for m in memberships
        ],
    )


@router.get("/me", response_model=MeOut)
async def me(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> MeOut:
    user, membership = membership_pair
    return await _build_me_out(session, user, membership)


@router.post("/switch-workspace", response_model=MeOut)
async def switch_workspace(
    payload: SwitchWorkspaceRequest,
    request: Request,
    response: Response,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> MeOut:
    user, _ = membership_pair
    result = await session.execute(
        select(Membership).where(
            Membership.user_id == user.id, Membership.workspace_id == payload.workspace_id
        )
    )
    target_membership = result.scalar_one_or_none()
    if target_membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not a member of that workspace")

    _set_session_cookie(response, request, user.id, target_membership.workspace_id)
    return await _build_me_out(session, user, target_membership)
```

- [ ] **Step 6: Update `__main__.py`'s `_seed_personas` to pass a workspace**

```python
async def _seed_personas() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(select(Workspace).order_by(Workspace.created_at).limit(1))
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise SystemExit("No workspace exists yet -- run `create-user` first.")
        personas = await seed_baseline_personas(session, workspace.id)
    await engine.dispose()
    print(f"{len(personas)} baseline persona(s) ready: {', '.join(p.slug for p in personas)}")
```

Add `from sqlalchemy import select` and `from flowsage_backend.models.workspace import Workspace` to `__main__.py`'s imports.

- [ ] **Step 7: Update `conftest.py`'s authed-client pattern**

Every test file's `_authed_client`/equivalent helper currently does `await upsert_user(...)` then logs in and only ever exercises one workspace. Since `upsert_user` (Step 4) now returns a user with a bootstrapped workspace automatically, no fixture change is needed for the single-workspace case — existing per-file `_authed_client` helpers keep working unchanged, because login now resolves `_first_membership_or_401` itself. Add one new shared fixture to `conftest.py` for tests that need a *second* workspace (used by Task 6's cross-tenant isolation tests):

```python
@pytest.fixture
async def second_workspace_membership(db_session: AsyncSession) -> tuple["User", "Membership"]:
    """A second user in a second workspace, for cross-tenant isolation tests."""
    from flowsage_backend.seed import upsert_user
    from flowsage_backend.models.workspace import Membership
    from sqlalchemy import select

    user = await upsert_user(db_session, "other-tenant@example.com", "hunter2")
    result = await db_session.execute(
        select(Membership).where(Membership.user_id == user.id)
    )
    return user, result.scalar_one()
```

(Local imports inside the fixture avoid a circular import between `conftest.py` and app modules at collection time — matches how other fixtures in this file already import lazily where needed; if `conftest.py` already imports these at module level without issue, hoist them to the top instead.)

- [ ] **Step 8: Update `test_auth.py` for the new response shape + add switch-workspace tests**

Update every assertion like `response.json()["email"]` to also work with the new `MeOut` shape (email is still a top-level field, so those assertions are unchanged). Add:

```python
async def test_switch_workspace_rejects_non_member(app: FastAPI, db_session: AsyncSession) -> None:
    await upsert_user(db_session, "switch-reject@example.com", "hunter2")
    async with await _client(app) as client:
        await client.post(
            "/auth/login", json={"email": "switch-reject@example.com", "password": "hunter2"}
        )
        response = await client.post("/auth/switch-workspace", json={"workspace_id": str(uuid.uuid4())})

    assert response.status_code == 403


async def test_switch_workspace_succeeds_for_member(app: FastAPI, db_session: AsyncSession) -> None:
    from flowsage_backend.models.workspace import Membership, Role, Workspace

    user = await upsert_user(db_session, "switch-ok@example.com", "hunter2")
    second_workspace = Workspace(name="Second", slug=f"fs-{uuid.uuid4().hex[:8]}")
    db_session.add(second_workspace)
    await db_session.flush()
    db_session.add(Membership(user_id=user.id, workspace_id=second_workspace.id, role=Role.VIEWER))
    await db_session.commit()

    async with await _client(app) as client:
        await client.post("/auth/login", json={"email": "switch-ok@example.com", "password": "hunter2"})
        response = await client.post(
            "/auth/switch-workspace", json={"workspace_id": str(second_workspace.id)}
        )

    assert response.status_code == 200
    assert response.json()["workspace_id"] == str(second_workspace.id)
    assert response.json()["role"] == "viewer"
```

Add `import uuid` to `test_auth.py`'s imports if not already present.

- [ ] **Step 9: Run auth + security tests**

Run: `cd backend && uv run pytest tests/test_auth.py tests/test_security.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add backend/src/flowsage_backend/security.py backend/src/flowsage_backend/deps.py \
  backend/src/flowsage_backend/api/auth.py backend/src/flowsage_backend/seed.py \
  backend/src/flowsage_backend/__main__.py backend/src/flowsage_backend/models/persona.py \
  backend/migrations/versions backend/tests/conftest.py backend/tests/test_auth.py \
  backend/tests/test_security.py
git commit -m "feat: workspace-aware JWT auth, get_current_membership, switch-workspace"
```

---

### Task 3: `workspaces.py` — general settings endpoints

**Files:**
- Create: `backend/src/flowsage_backend/api/workspaces.py`
- Modify: `backend/src/flowsage_backend/main.py` (register the router)
- Test: `backend/tests/test_workspaces_api.py`

**Interfaces:**
- Consumes: `get_current_membership`, `require_role` from Task 2; `Workspace`, `Membership`, `Role` from Task 1.
- Produces: `GET /workspaces`, `POST /workspaces`, `GET /workspaces/current`, `PATCH /workspaces/current`, `POST /workspaces/current/archive` — consumed by Task 4's member endpoints (same router/file) and Task 8's frontend `GeneralSettingsPage`.

- [ ] **Step 1: Write `api/workspaces.py` (general-settings portion)**

```python
"""Workspace CRUD (`/settings/general`) and member management (`/settings/team`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session, require_role
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership, Role, Workspace, WorkspacePrivacy

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


class WorkspaceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str
    avatar_url: str | None
    privacy: WorkspacePrivacy
    region: str
    retention_days: int
    archived: bool
    created_at: datetime


class WorkspaceSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: Role


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str
    avatar_url: str | None = None
    privacy: WorkspacePrivacy
    region: str = Field(min_length=1, max_length=64)
    retention_days: int = Field(ge=1, le=3650)


@router.get("", response_model=list[WorkspaceSummaryOut])
async def list_workspaces(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[WorkspaceSummaryOut]:
    user, _ = membership_pair
    result = await session.execute(
        select(Membership, Workspace)
        .join(Workspace, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user.id)
    )
    return [
        WorkspaceSummaryOut(id=workspace.id, name=workspace.name, role=membership.role)
        for membership, workspace in result.all()
    ]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceCreate,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    user, _ = membership_pair
    workspace = Workspace(name=payload.name, slug=f"fs-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.flush()
    session.add(Membership(user_id=user.id, workspace_id=workspace.id, role=Role.ADMIN))
    await session.commit()
    await session.refresh(workspace)
    return workspace


@router.get("/current", response_model=WorkspaceOut)
async def get_current_workspace(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None  # guaranteed by the FK + get_current_membership's lookup
    return workspace


@router.patch("/current", response_model=WorkspaceOut)
async def update_current_workspace(
    payload: WorkspaceUpdate,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    workspace.name = payload.name
    workspace.description = payload.description
    workspace.avatar_url = payload.avatar_url
    workspace.privacy = payload.privacy
    workspace.region = payload.region
    workspace.retention_days = payload.retention_days
    await session.commit()
    await session.refresh(workspace)
    return workspace


@router.post("/current/archive", response_model=WorkspaceOut)
async def archive_current_workspace(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    _, membership = membership_pair
    workspace = await session.get(Workspace, membership.workspace_id)
    assert workspace is not None
    workspace.archived = True
    await session.commit()
    await session.refresh(workspace)
    return workspace
```

- [ ] **Step 2: Register the router in `main.py`**

Add `from flowsage_backend.api.workspaces import router as workspaces_router` to the imports and `app.include_router(workspaces_router)` alongside the other `include_router` calls.

- [ ] **Step 3: Write `test_workspaces_api.py`**

```python
"""backend/tests/test_workspaces_api.py"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, db_session: AsyncSession, email: str) -> AsyncIterator[AsyncClient]:
    await upsert_user(db_session, email, "hunter2")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def test_get_current_workspace(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session, f"ws-get-{uuid.uuid4().hex[:8]}@example.com") as client:
        response = await client.get("/workspaces/current")

    assert response.status_code == 200
    assert response.json()["name"] == "Default"


async def test_update_current_workspace(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session, f"ws-patch-{uuid.uuid4().hex[:8]}@example.com") as client:
        response = await client.patch(
            "/workspaces/current",
            json={
                "name": "Acme Corp",
                "description": "Our workspace",
                "avatar_url": None,
                "privacy": "restricted",
                "region": "eu",
                "retention_days": 30,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Acme Corp"
    assert body["privacy"] == "restricted"
    assert body["retention_days"] == 30


async def test_archive_current_workspace(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session, f"ws-archive-{uuid.uuid4().hex[:8]}@example.com") as client:
        response = await client.post("/workspaces/current/archive")

    assert response.status_code == 200
    assert response.json()["archived"] is True


async def test_list_workspaces_shows_only_memberships(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session, f"ws-list-{uuid.uuid4().hex[:8]}@example.com") as client:
        response = await client.get("/workspaces")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["role"] == "admin"


async def test_create_workspace_makes_caller_admin(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session, f"ws-create-{uuid.uuid4().hex[:8]}@example.com") as client:
        create_response = await client.post("/workspaces", json={"name": "New Co"})
        assert create_response.status_code == 201

        list_response = await client.get("/workspaces")

    assert len(list_response.json()) == 2
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/test_workspaces_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/api/workspaces.py backend/src/flowsage_backend/main.py \
  backend/tests/test_workspaces_api.py
git commit -m "feat: add /workspaces general-settings endpoints"
```

---

### Task 4: `workspaces.py` — member management endpoints

**Files:**
- Modify: `backend/src/flowsage_backend/api/workspaces.py`
- Test: `backend/tests/test_workspaces_api.py`

**Interfaces:**
- Consumes: same as Task 3.
- Produces: `GET /workspaces/current/members`, `POST /workspaces/current/members`, `PATCH /workspaces/current/members/{membership_id}`, `DELETE /workspaces/current/members/{membership_id}` — consumed by Task 9's `TeamSettingsPage`.

- [ ] **Step 1: Add member endpoints to `api/workspaces.py`**

Append to the file (after `archive_current_workspace`):

```python
class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: Role
    created_at: datetime


class MemberAdd(BaseModel):
    email: str = Field(min_length=1)
    role: Role


class MemberRoleUpdate(BaseModel):
    role: Role


async def _admin_count(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    result = await session.execute(
        select(Membership).where(
            Membership.workspace_id == workspace_id, Membership.role == Role.ADMIN
        )
    )
    return len(result.scalars().all())


async def _get_membership_in_workspace(
    session: AsyncSession, membership_id: uuid.UUID, workspace_id: uuid.UUID
) -> Membership:
    membership = await session.get(Membership, membership_id)
    if membership is None or membership.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Member not found in this workspace")
    return membership


@router.get("/current/members", response_model=list[MemberOut])
async def list_members(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[MemberOut]:
    _, membership = membership_pair
    result = await session.execute(
        select(Membership, User)
        .join(User, Membership.user_id == User.id)
        .where(Membership.workspace_id == membership.workspace_id)
        .order_by(User.email)
    )
    return [
        MemberOut(id=m.id, user_id=m.user_id, email=user.email, role=m.role, created_at=m.created_at)
        for m, user in result.all()
    ]


@router.post("/current/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    payload: MemberAdd,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> MemberOut:
    _, membership = membership_pair
    result = await session.execute(select(User).where(User.email == payload.email))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No account with that email")

    existing = await session.execute(
        select(Membership).where(
            Membership.user_id == target_user.id, Membership.workspace_id == membership.workspace_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That user is already a member")

    new_membership = Membership(
        user_id=target_user.id, workspace_id=membership.workspace_id, role=payload.role
    )
    session.add(new_membership)
    await session.commit()
    await session.refresh(new_membership)
    return MemberOut(
        id=new_membership.id,
        user_id=target_user.id,
        email=target_user.email,
        role=new_membership.role,
        created_at=new_membership.created_at,
    )


@router.patch("/current/members/{membership_id}", response_model=MemberOut)
async def update_member_role(
    membership_id: uuid.UUID,
    payload: MemberRoleUpdate,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> MemberOut:
    _, membership = membership_pair
    target = await _get_membership_in_workspace(session, membership_id, membership.workspace_id)

    if target.role == Role.ADMIN and payload.role != Role.ADMIN:
        if await _admin_count(session, membership.workspace_id) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workspace must keep at least one admin")

    target.role = payload.role
    await session.commit()
    await session.refresh(target)
    user = await session.get(User, target.user_id)
    assert user is not None
    return MemberOut(
        id=target.id, user_id=target.user_id, email=user.email, role=target.role,
        created_at=target.created_at,
    )


@router.delete("/current/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def remove_member(
    membership_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    target = await _get_membership_in_workspace(session, membership_id, membership.workspace_id)

    if target.role == Role.ADMIN and await _admin_count(session, membership.workspace_id) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Workspace must keep at least one admin")

    await session.delete(target)
    await session.commit()
```

- [ ] **Step 2: Add member-endpoint tests to `test_workspaces_api.py`**

```python
async def test_add_member_by_email(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-member-admin-{uuid.uuid4().hex[:8]}@example.com"
    invitee_email = f"ws-member-invitee-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, invitee_email, "hunter2")

    async with _authed_client(app, db_session, admin_email) as client:
        response = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "researcher"}
        )

    assert response.status_code == 201
    assert response.json()["email"] == invitee_email
    assert response.json()["role"] == "researcher"


async def test_add_member_rejects_unknown_email(app: FastAPI, db_session: AsyncSession) -> None:
    async with _authed_client(app, db_session, f"ws-member-404-{uuid.uuid4().hex[:8]}@example.com") as client:
        response = await client.post(
            "/workspaces/current/members",
            json={"email": "nobody-registered@example.com", "role": "viewer"},
        )

    assert response.status_code == 404


async def test_add_member_rejects_duplicate(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-member-dup-admin-{uuid.uuid4().hex[:8]}@example.com"
    invitee_email = f"ws-member-dup-invitee-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, invitee_email, "hunter2")

    async with _authed_client(app, db_session, admin_email) as client:
        first = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "viewer"}
        )
        assert first.status_code == 201
        second = await client.post(
            "/workspaces/current/members", json={"email": invitee_email, "role": "viewer"}
        )

    assert second.status_code == 409


async def test_cannot_remove_last_admin(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-last-admin-{uuid.uuid4().hex[:8]}@example.com"
    async with _authed_client(app, db_session, admin_email) as client:
        me = await client.get("/auth/me")
        members = await client.get("/workspaces/current/members")
        own_membership_id = next(
            m["id"] for m in members.json() if m["email"] == me.json()["email"]
        )
        response = await client.delete(f"/workspaces/current/members/{own_membership_id}")

    assert response.status_code == 400


async def test_non_admin_cannot_add_member(app: FastAPI, db_session: AsyncSession) -> None:
    admin_email = f"ws-role-admin-{uuid.uuid4().hex[:8]}@example.com"
    viewer_email = f"ws-role-viewer-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, viewer_email, "hunter2")

    async with _authed_client(app, db_session, admin_email) as admin_client:
        await admin_client.post(
            "/workspaces/current/members", json={"email": viewer_email, "role": "viewer"}
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as viewer_client:
        await viewer_client.post("/auth/login", json={"email": viewer_email, "password": "hunter2"})
        # The viewer's own workspace (their personal one from upsert_user) has no admin
        # co-member yet to add -- switch into the shared workspace first.
        me = await viewer_client.get("/auth/me")
        shared_workspace_id = next(
            w["id"] for w in me.json()["workspaces"] if w["id"] != me.json()["workspace_id"]
        )
        await viewer_client.post("/auth/switch-workspace", json={"workspace_id": shared_workspace_id})
        response = await viewer_client.post(
            "/workspaces/current/members",
            json={"email": f"irrelevant-{uuid.uuid4().hex[:8]}@example.com", "role": "viewer"},
        )

    assert response.status_code == 403
```

- [ ] **Step 3: Run the tests**

Run: `cd backend && uv run pytest tests/test_workspaces_api.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/src/flowsage_backend/api/workspaces.py backend/tests/test_workspaces_api.py
git commit -m "feat: add workspace member management (invite/role/remove)"
```

---

### Task 5: Thread `workspace_id` through business-logic modules

**Files:**
- Modify: `backend/src/flowsage_backend/events.py`
- Modify: `backend/src/flowsage_backend/churn.py`
- Modify: `backend/src/flowsage_backend/calibration.py`
- Modify: `backend/src/flowsage_backend/alerts.py`
- Modify: `backend/src/flowsage_backend/settings_store.py`
- Modify: `backend/tests/test_events.py`, `test_churn.py`, `test_calibration.py`, `test_alerts.py`

**Interfaces:**
- Consumes: nothing new (pure functions + a `workspace_id: uuid.UUID` param threaded through each).
- Produces: `ingest_events(session, workspace_id, events)`, `query_events(session, workspace_id, ...)`, `distinct_cohorts(session, workspace_id, ...)`, `build_funnel_report(session, workspace_id, ...)`, `compare_cohorts(session, workspace_id, ...)`, `build_churn_risk_segments(session, workspace_id, ...)`, `get_node_intelligence(session, workspace_id, ...)`, `latest_completed_runs_by_persona(session, workspace_id)`, `latest_completed_run_for_persona(session, workspace_id, persona_id)`, `build_calibration_report(session, workspace_id, funnel, ...)`, `build_alerts_report(session, workspace_id)`, `get_or_create_calibration_settings(session, workspace_id)` — every one of these gains `workspace_id` as its second positional parameter, right after `session`. Task 6's routers pass `membership.workspace_id` into all of them.

- [ ] **Step 1: `events.py` — add `workspace_id` to every function**

```python
async def ingest_events(
    session: AsyncSession, workspace_id: "uuid.UUID", events: list[GraphEvent]
) -> list[Event]:
    rows = [
        Event(
            workspace_id=workspace_id,
            session_id=e.session_id,
            screen=e.screen,
            event=e.event,
            timestamp=e.timestamp,
            device=e.device,
            cohort=e.cohort,
        )
        for e in events
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def query_events(
    session: AsyncSession,
    workspace_id: "uuid.UUID",
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
) -> list[GraphEvent]:
    query = select(Event).where(Event.workspace_id == workspace_id)
    if cohort is not None:
        query = query.where(Event.cohort == cohort)
    if device is not None:
        query = query.where(Event.device == device)
    if since is not None:
        query = query.where(Event.timestamp >= since)

    result = await session.execute(query.order_by(Event.timestamp))
    return [row.to_graph_event() for row in result.scalars().all()]


async def distinct_cohorts(
    session: AsyncSession,
    workspace_id: "uuid.UUID",
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> list[str]:
    query = select(Event.cohort).distinct().where(Event.workspace_id == workspace_id)
    if device is not None:
        query = query.where(Event.device == device)
    if since is not None:
        query = query.where(Event.timestamp >= since)

    result = await session.execute(query)
    return sorted(result.scalars().all())


async def build_funnel_report(
    session: AsyncSession,
    workspace_id: "uuid.UUID",
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
) -> FunnelReport:
    events = await query_events(session, workspace_id, cohort=cohort, device=device, since=since)
    funnel = discover_funnel(events)
    friction = detect_friction(events, funnel)
    return FunnelReport(
        funnel=funnel,
        friction_nodes=friction,
        total_sessions=len({e.session_id for e in events}),
        total_events=len(events),
    )
```

Add `import uuid` to the top of `events.py`.

- [ ] **Step 2: `churn.py` — add `workspace_id`**

Update `compare_cohorts`, `build_churn_risk_segments`, and `get_node_intelligence` to accept `workspace_id: uuid.UUID` as their second positional param and pass it through to every `distinct_cohorts`/`build_funnel_report`/`query_events` call inside them:

```python
async def compare_cohorts(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    cohorts: list[str],
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> CohortComparisonReport:
    if not cohorts:
        cohorts = await distinct_cohorts(session, workspace_id, device=device, since=since)

    reports = {
        cohort: await build_funnel_report(
            session, workspace_id, cohort=cohort, device=device, since=since
        )
        for cohort in cohorts
    }
    return build_cohort_comparison(reports)


async def build_churn_risk_segments(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    device: str | None = None,
    since: datetime | None = None,
) -> list[ChurnRiskSegment]:
    cohorts = await distinct_cohorts(session, workspace_id, device=device, since=since)
    segments = [
        score_churn_risk(
            cohort,
            await build_funnel_report(session, workspace_id, cohort=cohort, device=device, since=since),
        )
        for cohort in cohorts
    ]
    segments.sort(key=lambda s: s.risk_score, reverse=True)
    return segments


async def get_node_intelligence(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    screen: str,
    *,
    cohort: str | None = None,
    device: str | None = None,
    since: datetime | None = None,
) -> NodeIntelligence | None:
    events = await query_events(session, workspace_id, cohort=cohort, device=device, since=since)
    funnel = discover_funnel(events)
    if screen not in {step.screen for step in funnel}:
        return None

    friction = detect_friction(events, funnel)
    report = FunnelReport(
        funnel=funnel,
        friction_nodes=friction,
        total_sessions=len({e.session_id for e in events}),
        total_events=len(events),
    )
    return build_node_intelligence(screen, report, events)
```

Add `import uuid` to the top of `churn.py`.

- [ ] **Step 3: `calibration.py` — add `workspace_id`**

```python
async def latest_completed_runs_by_persona(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[SimulationRun]:
    """One row per persona: their most recent COMPLETED run, if any."""
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id, SimulationRun.status == RunStatus.COMPLETED
        )
        .options(selectinload(SimulationRun.issues), selectinload(SimulationRun.persona))
        .order_by(SimulationRun.persona_id, SimulationRun.finished_at.desc())
    )
    latest_by_persona: dict[uuid.UUID, SimulationRun] = {}
    for run in result.scalars().all():
        latest_by_persona.setdefault(run.persona_id, run)
    return list(latest_by_persona.values())


async def latest_completed_run_for_persona(
    session: AsyncSession, workspace_id: uuid.UUID, persona_id: uuid.UUID
) -> SimulationRun | None:
    result = await session.execute(
        select(SimulationRun)
        .where(
            SimulationRun.workspace_id == workspace_id,
            SimulationRun.persona_id == persona_id,
            SimulationRun.status == RunStatus.COMPLETED,
        )
        .options(selectinload(SimulationRun.issues))
        .order_by(SimulationRun.finished_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def build_calibration_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    funnel: list[FunnelStep],
    anomaly_threshold: float = ANOMALY_THRESHOLD,
) -> CalibrationReport:
    runs = await latest_completed_runs_by_persona(session, workspace_id)
    # ... rest of the function body is unchanged from here.
```

(Only the signature and the one internal call site change — the loop body below `runs = ...` stays exactly as it is today.)

- [ ] **Step 4: `alerts.py` — add `workspace_id`**

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
    )
```

Add `import uuid` to the top of `alerts.py`.

- [ ] **Step 5: `settings_store.py` — one row per workspace**

```python
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
```

- [ ] **Step 6: Update the 4 modules' existing unit tests to pass a fixture `workspace_id`**

In each of `test_events.py`, `test_churn.py`, `test_calibration.py`, `test_alerts.py`: add `workspace_id = uuid.uuid4()` at the top of each test function (or a shared `@pytest.fixture def workspace_id() -> uuid.UUID: return uuid.uuid4()` if the file has many tests), and pass it as the second positional argument to every call to the functions changed above. These are pure-function/DB tests already using a real `db_session` fixture, so a fresh random `workspace_id` per test is enough to prove scoping — no cross-test collision since each test's events/rows are tagged with its own `workspace_id` and queries filter by it.

- [ ] **Step 7: Run the four test files**

Run: `cd backend && uv run pytest tests/test_events.py tests/test_churn.py tests/test_calibration.py tests/test_alerts.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/flowsage_backend/events.py backend/src/flowsage_backend/churn.py \
  backend/src/flowsage_backend/calibration.py backend/src/flowsage_backend/alerts.py \
  backend/src/flowsage_backend/settings_store.py backend/tests/test_events.py \
  backend/tests/test_churn.py backend/tests/test_calibration.py backend/tests/test_alerts.py
git commit -m "feat: thread workspace_id through events/churn/calibration/alerts modules"
```

---

### Task 6: Scope every router by workspace + cross-tenant isolation tests

**Files:**
- Modify: `backend/src/flowsage_backend/api/personas.py`
- Modify: `backend/src/flowsage_backend/api/simulations.py`
- Modify: `backend/src/flowsage_backend/api/events.py`
- Modify: `backend/src/flowsage_backend/api/calibration.py`
- Modify: `backend/src/flowsage_backend/api/alerts.py`
- Modify: `backend/src/flowsage_backend/api/exports.py`
- Modify: `backend/src/flowsage_backend/api/settings.py`
- Modify: `backend/src/flowsage_backend/config.py` (add `default_workspace_slug` note — see Step 3)
- Modify: `backend/tests/test_personas_api.py`, `test_simulations_api.py`, `test_events.py` (API parts), `test_calibration_api.py`, `test_alerts_api.py`, `test_exports_api.py`, `test_settings_api.py`, `test_node_export_api.py`
- Create: `backend/tests/test_workspace_isolation.py`

**Interfaces:**
- Consumes: `get_current_membership`, `require_role` (Task 2); workspace-aware business-logic functions (Task 5).
- Produces: every existing endpoint now scoped; `test_workspace_isolation.py` is the authoritative cross-tenant regression suite.

- [ ] **Step 1: `personas.py`**

Swap the router's blanket dependency and every query:

```python
router = APIRouter(
    prefix="/personas", tags=["personas"], dependencies=[Depends(get_current_membership)]
)
```

(`get_current_membership` replaces `get_current_user` in the import too.) Then:

```python
async def _get_persona_or_404(
    session: AsyncSession, workspace_id: uuid.UUID, persona_id: uuid.UUID
) -> Persona:
    result = await session.execute(
        select(Persona)
        .where(Persona.id == persona_id, Persona.workspace_id == workspace_id)
        .options(selectinload(Persona.memories))
    )
    persona = result.scalar_one_or_none()
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona not found")
    return persona


@router.get("", response_model=list[PersonaOut])
async def list_personas(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[Persona]:
    _, membership = membership_pair
    result = await session.execute(
        select(Persona).where(Persona.workspace_id == membership.workspace_id).order_by(Persona.name)
    )
    return list(result.scalars().all())


@router.get("/{persona_id}", response_model=PersonaDetailOut)
async def get_persona(
    persona_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> Persona:
    _, membership = membership_pair
    return await _get_persona_or_404(session, membership.workspace_id, persona_id)


@router.post("", response_model=PersonaOut, status_code=status.HTTP_201_CREATED)
async def create_persona(
    payload: PersonaCreate,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> Persona:
    _, membership = membership_pair
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "slug must be lowercase alphanumeric words separated by hyphens",
        )

    persona = Persona(
        workspace_id=membership.workspace_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        baseline=False,
        tech_affinity=payload.tech_affinity,
        primary_device=payload.primary_device,
        discovery_mode=payload.discovery_mode,
        contextual_triggers=payload.contextual_triggers,
        technical_literacy=payload.technical_literacy,
        anxiety=payload.anxiety,
        patience=payload.patience,
        curiosity=payload.curiosity,
    )
    session.add(persona)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A persona with slug {payload.slug!r} already exists"
        ) from exc
    await session.refresh(persona)
    return persona
```

Apply the same `membership.workspace_id`-scoped-lookup pattern to `update_persona`, `reset_persona`, and `delete_persona` (each already calls `_get_persona_or_404`; just add the `membership_pair` param and pass `membership.workspace_id` through).

- [ ] **Step 2: `simulations.py`**

Swap `get_current_user` → `get_current_membership` on the router. Scope `_load_run_with_children` and the two handlers:

```python
async def _load_run_with_children(
    session: AsyncSession, workspace_id: uuid.UUID, run_id: uuid.UUID
) -> SimulationRun | None:
    result = await session.execute(
        select(SimulationRun)
        .where(SimulationRun.id == run_id, SimulationRun.workspace_id == workspace_id)
        .options(selectinload(SimulationRun.steps), selectinload(SimulationRun.issues))
    )
    return result.scalar_one_or_none()


@router.post("", response_model=SimulationRunOut, status_code=status.HTTP_201_CREATED)
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
    settings = request.app.state.settings
    run_id = uuid.uuid4()
    screenshots_dir = Path(settings.upload_dir) / str(run_id)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        filename = Path(upload.filename or "").name
        if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unsupported file type: {filename!r}"
            )
        (screenshots_dir / filename).write_bytes(await upload.read())

    try:
        run = await create_run(
            session,
            workspace_id=membership.workspace_id,
            run_id=run_id,
            persona_id=persona_id,
            flow_name=flow_name,
            goal=goal,
            screenshots_dir=screenshots_dir,
        )
    except SimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await request.app.state.arq_pool.enqueue_job("run_simulation_job", str(run.id))
    return run


@router.get("/{run_id}", response_model=SimulationRunDetailOut)
async def get_simulation(
    run_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> SimulationRun:
    _, membership = membership_pair
    run = await _load_run_with_children(session, membership.workspace_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation run not found")
    return run
```

`create_run` in `flowsage_backend/simulations.py` gains a `workspace_id: uuid.UUID` keyword param and sets it on the `SimulationRun` it constructs — locate that function (it's imported at the top of `simulations.py` router) and add the same one-line `workspace_id=workspace_id` to its `SimulationRun(...)` construction, threading the new param through its signature.

The `/{run_id}/stream` SSE endpoint (`stream_simulation`/`stream_simulation_events`) is trickier: it's a `StreamingResponse` whose generator opens its own DB sessions in a loop, decoupled from the request's `Depends`. Capture the workspace at request time and pass it into the generator:

```python
@router.get("/{run_id}/stream")
async def stream_simulation(
    run_id: uuid.UUID,
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
) -> StreamingResponse:
    _, membership = membership_pair
    session_factory = request.app.state.session_factory
    return StreamingResponse(
        stream_simulation_events(session_factory, membership.workspace_id, run_id),
        media_type="text/event-stream",
    )
```

And update `stream_simulation_events`'s signature to accept `workspace_id: uuid.UUID` as its second positional param, passing it to every `_load_run_with_children` call inside the polling loop.

- [ ] **Step 3: `api/events.py`**

The API-key-gated `events_router` has no user session and thus no `membership.workspace_id` to scope by. Per the design spec, this chunk hardcodes ingestion to the one "Default" workspace (proper per-workspace API keys are chunk 2 scope). Add a small resolver:

```python
async def _default_workspace_id(session: AsyncSession) -> uuid.UUID:
    result = await session.execute(select(Workspace.id).where(Workspace.slug == "fs-default"))
    workspace_id = result.scalar_one_or_none()
    if workspace_id is None:
        raise HTTPException(500, "No default workspace configured")
    return workspace_id
```

Add `from flowsage_backend.models.workspace import Membership, Workspace` and `from sqlalchemy import select` (already imported) to `events.py`'s imports. Update `ingest`:

```python
@events_router.post("", response_model=IngestResult, status_code=201)
async def ingest(
    payload: list[EventIn],
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> IngestResult:
    workspace_id = await _default_workspace_id(session)
    graph_events = [GraphEvent.model_validate(e.model_dump()) for e in payload]
    rows = await ingest_events(session, workspace_id, graph_events)

    graph_sink = request.app.state.graph_sink
    try:
        await asyncio.to_thread(graph_sink.ingest, graph_events)
    except Exception:  # noqa: BLE001 - Neo4j being unreachable shouldn't fail ingestion
        logger.warning(
            "Neo4j ingestion failed; events were still stored in Postgres", exc_info=True
        )

    return IngestResult(ingested=len(rows))
```

Swap `graph_router`'s dependency to `get_current_membership` and thread `membership.workspace_id` into every one of `funnel`, `cohorts_compare`, `churn_risk`, `node_intelligence`, `export_node_to_slack`, `export_node_to_jira` — each gains a `membership_pair: tuple[User, Membership] = Depends(get_current_membership)` param and passes `membership.workspace_id` as the second positional arg into `build_funnel_report`/`compare_cohorts`/`build_churn_risk_segments`/`get_node_intelligence`.

- [ ] **Step 4: `api/calibration.py`**

Swap dependency to `get_current_membership`. Update `get_calibration_report`:

```python
@router.get("/report", response_model=CalibrationReport)
async def get_calibration_report(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CalibrationReport:
    _, membership = membership_pair
    events = await query_events(session, membership.workspace_id)
    funnel = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, membership.workspace_id)
    return await build_calibration_report(session, membership.workspace_id, funnel, settings.anomaly_threshold)
```

`start_retraining` and `get_retraining_job` need `RetrainingJob` scoped by workspace too — `RetrainingJob` doesn't currently join through anything workspace-aware in `create_retraining_job` (in `retraining.py`); give it a `workspace_id` kwarg the same way `create_run` got one in Step 2, and scope `get_retraining_job`'s direct `session.get(RetrainingJob, job_id)` with an explicit workspace check:

```python
@router.get("/retrain/{job_id}", response_model=RetrainingJobOut)
async def get_retraining_job(
    job_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> RetrainingJobOut:
    _, membership = membership_pair
    job = await session.get(RetrainingJob, job_id)
    if job is None or job.workspace_id != membership.workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Retraining job not found")
    return RetrainingJobOut.from_row(job)
```

Apply the same workspace-check pattern to `stream_retraining`/`stream_retraining_events` (capture `membership.workspace_id` at request time in the endpoint, pass into the SSE generator, and check it on every poll iteration the same way `get_retraining_job` does above — return the `"error"` SSE event instead of raising if the workspace doesn't match).

- [ ] **Step 5: `api/alerts.py` and `api/exports.py`**

`alerts.py`: swap dependency, pass `membership.workspace_id` into `build_alerts_report`; the digest endpoint additionally passes it into `build_alerts_report` the same way.

`exports.py`: swap dependency, and scope `_get_issue`:

```python
async def _get_issue(
    session: AsyncSession, workspace_id: uuid.UUID, issue_id: uuid.UUID
) -> FrictionIssue:
    issue = await session.get(FrictionIssue, issue_id)
    if issue is None or issue.workspace_id != workspace_id:
        raise HTTPException(404, "Friction issue not found")
    return issue
```

Both `export_issue_to_slack`/`export_issue_to_jira` gain a `membership_pair` param and pass `membership.workspace_id` into `_get_issue`.

- [ ] **Step 6: `api/settings.py`**

Swap dependency and thread `membership.workspace_id` into both `get_or_create_calibration_settings` calls:

```python
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
```

- [ ] **Step 7: Update every existing router test file's fixtures**

Each of `test_personas_api.py`, `test_simulations_api.py`, `test_events.py` (API portion), `test_calibration_api.py`, `test_alerts_api.py`, `test_exports_api.py`, `test_settings_api.py`, `test_node_export_api.py` needs no fixture changes for the single-workspace case — their existing `_authed_client`-style helper already logs in via `/auth/login`, and Task 2's `upsert_user` bootstraps a workspace automatically, so a fresh unique-email user in each test still gets exactly one workspace and every existing assertion continues to hold (the *data* each test creates now happens to carry that workspace's id, invisibly). Run the full suite once to confirm — if any test constructs a model row directly (bypassing the API, e.g. via `db_session.add(Persona(...))`) rather than through `upsert_user`/the API, it now needs an explicit `workspace_id=` kwarg; fix any such construction site the test run below surfaces.

- [ ] **Step 8: Write `test_workspace_isolation.py`**

```python
"""Cross-tenant isolation regression tests: a workspace must never see another
workspace's personas, simulation runs, events, or friction issues. The single
most important test file in this chunk."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def test_persona_created_in_one_workspace_is_invisible_in_another(
    app: FastAPI, db_session: AsyncSession
) -> None:
    tenant_a_email = f"isolation-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-b-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    slug = f"isolation-persona-{uuid.uuid4().hex[:8]}"
    async with _authed_client(app, tenant_a_email) as client_a:
        create_response = await client_a.post(
            "/personas",
            json={
                "slug": slug,
                "name": "Tenant A Persona",
                "description": "d",
                "tech_affinity": "Low",
                "primary_device": "Desktop",
                "discovery_mode": "Search",
                "contextual_triggers": [],
                "technical_literacy": 0.5,
                "anxiety": 0.5,
                "patience": 0.5,
                "curiosity": 0.5,
            },
        )
        assert create_response.status_code == 201
        persona_id = create_response.json()["id"]

    async with _authed_client(app, tenant_b_email) as client_b:
        list_response = await client_b.get("/personas")
        get_response = await client_b.get(f"/personas/{persona_id}")

    assert all(p["slug"] != slug for p in list_response.json())
    assert get_response.status_code == 404


async def test_friction_issue_export_is_workspace_scoped(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """A FrictionIssue id from workspace A must 404 for a workspace B caller,
    not leak via the direct-by-id export endpoints."""
    import datetime

    from flowsage_backend.models.persona import Persona
    from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
    from flowsage_backend.models.workspace import Membership

    tenant_a_email = f"isolation-fi-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-fi-b-{uuid.uuid4().hex[:8]}@example.com"
    user_a = await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    from sqlalchemy import select

    membership_a = (
        await db_session.execute(select(Membership).where(Membership.user_id == user_a.id))
    ).scalar_one()

    persona = Persona(
        workspace_id=membership_a.workspace_id,
        slug=f"fi-persona-{uuid.uuid4().hex[:8]}",
        name="P",
        description="d",
        baseline=False,
        tech_affinity="Low",
        primary_device="Desktop",
        discovery_mode="Search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.flush()

    run = SimulationRun(
        workspace_id=membership_a.workspace_id,
        flow_name="f",
        goal="g",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.datetime.now(datetime.timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()

    issue = FrictionIssue(
        workspace_id=membership_a.workspace_id,
        run_id=run.id,
        screen="checkout",
        severity="high",
        title="t",
        heuristic_violated="h",
        persona_impact="p",
        description="d",
        suggested_fix="f",
    )
    db_session.add(issue)
    await db_session.commit()
    await db_session.refresh(issue)

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.post(f"/friction-issues/{issue.id}/export/slack")

    assert response.status_code == 404
```

- [ ] **Step 9: Run the full backend suite**

Run: `cd backend && uv run pytest -x -q`
Expected: PASS (all files from Tasks 1-6 combined).

- [ ] **Step 10: `mypy --strict` check**

Run: `cd backend && uv run mypy --strict src/`
Expected: no errors. Fix any type issues surfaced by the `tuple[User, Membership]` dependency pattern (e.g. missing imports of `Membership`/`User` in a router file).

- [ ] **Step 11: Commit**

```bash
git add backend/src/flowsage_backend/api backend/tests
git commit -m "feat: scope all routers by workspace, add cross-tenant isolation tests"
```

---

### Task 7: Frontend types + API client for workspaces

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/api.test.ts`

**Interfaces:**
- Produces: `Workspace`, `WorkspaceSummary`, `Member`, `Role` TS types; `api.getWorkspaces`, `api.createWorkspace`, `api.getCurrentWorkspace`, `api.updateCurrentWorkspace`, `api.archiveCurrentWorkspace`, `api.getMembers`, `api.addMember`, `api.updateMemberRole`, `api.removeMember`, `api.switchWorkspace` — consumed by Tasks 8-10.

- [ ] **Step 1: Add types to `types.ts`**

```typescript
export type Role = "admin" | "researcher" | "viewer";

export interface User {
  id: string;
  email: string;
  created_at: string;
  workspace_id: string;
  role: Role;
  workspaces: { id: string; name: string }[];
}

export type WorkspacePrivacy = "private" | "restricted";

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description: string;
  avatar_url: string | null;
  privacy: WorkspacePrivacy;
  region: string;
  retention_days: number;
  archived: boolean;
  created_at: string;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  role: Role;
}

export interface WorkspaceUpdatePayload {
  name: string;
  description: string;
  avatar_url: string | null;
  privacy: WorkspacePrivacy;
  region: string;
  retention_days: number;
}

export interface Member {
  id: string;
  user_id: string;
  email: string;
  role: Role;
  created_at: string;
}

export interface MemberAddPayload {
  email: string;
  role: Role;
}
```

(This replaces the existing narrower `User` interface at the top of `types.ts` — every field it had is kept, four are added.)

- [ ] **Step 2: Add API client functions to `api.ts`**

```typescript
  getWorkspaces: (): Promise<WorkspaceSummary[]> => request<WorkspaceSummary[]>("/workspaces"),

  createWorkspace: (name: string): Promise<Workspace> =>
    request<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name }) }),

  getCurrentWorkspace: (): Promise<Workspace> => request<Workspace>("/workspaces/current"),

  updateCurrentWorkspace: (payload: WorkspaceUpdatePayload): Promise<Workspace> =>
    request<Workspace>("/workspaces/current", { method: "PATCH", body: JSON.stringify(payload) }),

  archiveCurrentWorkspace: (): Promise<Workspace> =>
    request<Workspace>("/workspaces/current/archive", { method: "POST" }),

  getMembers: (): Promise<Member[]> => request<Member[]>("/workspaces/current/members"),

  addMember: (payload: MemberAddPayload): Promise<Member> =>
    request<Member>("/workspaces/current/members", { method: "POST", body: JSON.stringify(payload) }),

  updateMemberRole: (membershipId: string, role: Role): Promise<Member> =>
    request<Member>(`/workspaces/current/members/${membershipId}`, {
      method: "PATCH",
      body: JSON.stringify({ role }),
    }),

  removeMember: (membershipId: string): Promise<void> =>
    request<void>(`/workspaces/current/members/${membershipId}`, { method: "DELETE" }),

  switchWorkspace: (workspaceId: string): Promise<User> =>
    request<User>("/auth/switch-workspace", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId }),
    }),
```

Add `Member`, `MemberAddPayload`, `Role`, `Workspace`, `WorkspaceSummary`, `WorkspaceUpdatePayload` to the `import type { ... } from "./types"` block at the top of `api.ts`.

- [ ] **Step 3: Add tests to `api.test.ts`**

Follow the existing file's pattern exactly (read `api.test.ts` first to match its mock-`fetch` style precisely), adding one test per new `api.*` function that asserts the request URL, method, and body shape — mirroring however the file currently tests e.g. `api.listPersonas`/`api.createPersona`.

- [ ] **Step 4: Run frontend type-check + tests**

Run: `cd frontend && npm run typecheck && npm run test -- api.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/lib/api.test.ts
git commit -m "feat: add workspace/member types and API client functions"
```

---

### Task 8: `GeneralSettingsPage`

**Files:**
- Create: `frontend/src/routes/settings/GeneralSettingsPage.tsx`
- Create: `frontend/src/routes/settings/GeneralSettingsPage.test.tsx`

**Interfaces:**
- Consumes: `api.getCurrentWorkspace`, `api.updateCurrentWorkspace`, `api.archiveCurrentWorkspace` (Task 7).
- Produces: `GeneralSettingsPage` component, wired into routing by Task 10.

- [ ] **Step 1: Write `GeneralSettingsPage.tsx`**

Follow `ModelCalibrationSettingsPage.tsx`'s exact structure (load-on-mount `useState`/`useEffect`, `update<K>` helper, save button with `saving`/`saved`/`error` state, `bg-surface-container-lowest rounded-xl p-6` section cards) — Workspace Identity section (name, description, read-only `slug`/`created_at` display), Configuration Parameters section (privacy select, region text input, retention_days number input), and a Danger Zone section with a confirm-before-archive button:

```tsx
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { Workspace, WorkspacePrivacy } from "../../lib/types";

const PRIVACY_OPTIONS: { value: WorkspacePrivacy; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "restricted", label: "Restricted" },
];

export function GeneralSettingsPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);

  useEffect(() => {
    api
      .getCurrentWorkspace()
      .then(setWorkspace)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load workspace.");
      });
  }, []);

  function update<K extends keyof Workspace>(key: K, value: Workspace[K]) {
    setWorkspace((prev) => (prev ? { ...prev, [key]: value } : prev));
    setSaved(false);
  }

  async function handleSave() {
    if (!workspace) return;
    setError(null);
    setSaving(true);
    try {
      const updated = await api.updateCurrentWorkspace({
        name: workspace.name,
        description: workspace.description,
        avatar_url: workspace.avatar_url,
        privacy: workspace.privacy,
        region: workspace.region,
        retention_days: workspace.retention_days,
      });
      setWorkspace(updated);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save workspace.");
    } finally {
      setSaving(false);
    }
  }

  async function handleArchive() {
    setError(null);
    setArchiving(true);
    try {
      const updated = await api.archiveCurrentWorkspace();
      setWorkspace(updated);
      setConfirmArchive(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to archive workspace.");
    } finally {
      setArchiving(false);
    }
  }

  if (error !== null && workspace === null) {
    return <p className="text-error text-sm">{error}</p>;
  }

  if (workspace === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="font-headline text-3xl">General Settings</h1>
          <p className="text-on-surface-variant mt-1">
            Workspace identity, privacy, and data retention.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={saving}
          className="rounded-lg bg-primary py-2.5 px-6 text-on-primary font-medium hover:opacity-90 transition disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save Changes"}
        </button>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}
      {saved ? <p className="text-sm text-primary">Workspace saved.</p> : null}

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Workspace Identity</h2>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Project Name</span>
          <input
            type="text"
            value={workspace.name}
            onChange={(event) => update("name", event.target.value)}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Description</span>
          <textarea
            value={workspace.description}
            onChange={(event) => update("description", event.target.value)}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            rows={3}
          />
        </label>
        <div className="flex gap-8 text-sm text-on-surface-variant">
          <span>Workspace ID: {workspace.slug}</span>
          <span>Established: {new Date(workspace.created_at).toLocaleDateString()}</span>
        </div>
      </section>

      <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl">Configuration Parameters</h2>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Workspace Privacy</span>
          <div className="grid grid-cols-2 gap-3">
            {PRIVACY_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                onClick={() => update("privacy", option.value)}
                className={`rounded-lg py-3 text-center font-medium transition ${
                  workspace.privacy === option.value
                    ? "bg-primary text-on-primary"
                    : "ghost-border text-on-surface-variant hover:bg-surface-container"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Regional Compliance</span>
          <input
            type="text"
            value={workspace.region}
            onChange={(event) => update("region", event.target.value)}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-on-surface-variant">Retention Policy (days)</span>
          <input
            type="number"
            min={1}
            max={3650}
            value={workspace.retention_days}
            onChange={(event) => update("retention_days", Number(event.target.value))}
            className="ghost-border rounded-lg px-3 py-2 bg-transparent"
          />
        </label>
      </section>

      <section className="bg-error-container/10 rounded-xl p-6 flex flex-col gap-4">
        <h2 className="font-headline text-xl text-on-error-container">Archive Workspace</h2>
        <p className="text-sm text-on-surface-variant">
          Archiving disables new simulations and event ingestion for this workspace. This can't be
          undone from this screen.
        </p>
        {workspace.archived ? (
          <p className="text-sm font-medium text-error">This workspace is archived.</p>
        ) : confirmArchive ? (
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleArchive()}
              disabled={archiving}
              className="rounded-lg bg-error py-2 px-4 text-on-error font-medium disabled:opacity-50"
            >
              {archiving ? "Archiving…" : "Confirm Archive"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmArchive(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmArchive(true)}
            className="self-start rounded-lg ghost-border py-2 px-4 font-medium text-error"
          >
            Archive Workspace
          </button>
        )}
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Write `GeneralSettingsPage.test.tsx`**

Mirror `ModelCalibrationSettingsPage`'s existing test file structure (mock `api.getCurrentWorkspace`/`api.updateCurrentWorkspace`/`api.archiveCurrentWorkspace`, render, assert loading → loaded, edit name, click save, assert `api.updateCurrentWorkspace` called with expected payload; click Archive Workspace → Confirm Archive, assert `api.archiveCurrentWorkspace` called and "This workspace is archived." renders).

- [ ] **Step 3: Run frontend tests**

Run: `cd frontend && npm run test -- GeneralSettingsPage`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/settings/GeneralSettingsPage.tsx frontend/src/routes/settings/GeneralSettingsPage.test.tsx
git commit -m "feat: add General Settings page (workspace identity, privacy, archive)"
```

---

### Task 9: `TeamSettingsPage`

**Files:**
- Create: `frontend/src/routes/settings/TeamSettingsPage.tsx`
- Create: `frontend/src/routes/settings/TeamSettingsPage.test.tsx`

**Interfaces:**
- Consumes: `api.getMembers`, `api.addMember`, `api.updateMemberRole`, `api.removeMember` (Task 7).
- Produces: `TeamSettingsPage` component, wired into routing by Task 10.

- [ ] **Step 1: Write `TeamSettingsPage.tsx`**

```tsx
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { Member, Role } from "../../lib/types";

const ROLE_OPTIONS: Role[] = ["admin", "researcher", "viewer"];

export function TeamSettingsPage() {
  const [members, setMembers] = useState<Member[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<Role>("viewer");
  const [inviting, setInviting] = useState(false);

  function load() {
    api
      .getMembers()
      .then(setMembers)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load members.");
      });
  }

  useEffect(load, []);

  async function handleInvite() {
    setError(null);
    setInviting(true);
    try {
      await api.addMember({ email: inviteEmail, role: inviteRole });
      setInviteEmail("");
      setShowInvite(false);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add member.");
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(member: Member, role: Role) {
    setError(null);
    try {
      await api.updateMemberRole(member.id, role);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update role.");
    }
  }

  async function handleRemove(member: Member) {
    setError(null);
    try {
      await api.removeMember(member.id);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to remove member.");
    }
  }

  if (error !== null && members === null) {
    return <p className="text-error text-sm">{error}</p>;
  }

  if (members === null) {
    return <p className="text-on-surface-variant text-sm">Loading…</p>;
  }

  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div className="flex items-start justify-between flex-wrap gap-4">
        <div>
          <h1 className="font-headline text-3xl">Team Access</h1>
          <p className="text-on-surface-variant mt-1">
            {members.length} team member{members.length === 1 ? "" : "s"}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowInvite(true)}
          className="rounded-lg bg-primary py-2.5 px-6 text-on-primary font-medium hover:opacity-90 transition"
        >
          Invite Member
        </button>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      {showInvite ? (
        <section className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-4">
          <h2 className="font-headline text-xl">Invite Member</h2>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Email</span>
            <input
              type="email"
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Role</span>
            <select
              value={inviteRole}
              onChange={(event) => setInviteRole(event.target.value as Role)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            >
              {ROLE_OPTIONS.map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleInvite()}
              disabled={inviting || inviteEmail.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              {inviting ? "Adding…" : "Add to Workspace"}
            </button>
            <button
              type="button"
              onClick={() => setShowInvite(false)}
              className="rounded-lg ghost-border py-2 px-4 font-medium"
            >
              Cancel
            </button>
          </div>
        </section>
      ) : null}

      <section className="bg-surface-container-lowest rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="text-left text-on-surface-variant border-b border-outline-variant">
            <tr>
              <th className="px-6 py-3 font-medium">Team Member</th>
              <th className="px-6 py-3 font-medium">Role</th>
              <th className="px-6 py-3 font-medium">Joined</th>
              <th className="px-6 py-3 font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {members.map((member) => {
              const isLastAdmin =
                member.role === "admin" && members.filter((m) => m.role === "admin").length === 1;
              return (
                <tr key={member.id} className="border-b border-outline-variant last:border-0">
                  <td className="px-6 py-3">{member.email}</td>
                  <td className="px-6 py-3">
                    <select
                      value={member.role}
                      onChange={(event) => void handleRoleChange(member, event.target.value as Role)}
                      disabled={isLastAdmin}
                      className="ghost-border rounded-lg px-2 py-1 bg-transparent disabled:opacity-50"
                    >
                      {ROLE_OPTIONS.map((role) => (
                        <option key={role} value={role}>
                          {role}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-6 py-3 text-on-surface-variant">
                    {new Date(member.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-6 py-3 text-right">
                    <button
                      type="button"
                      onClick={() => void handleRemove(member)}
                      disabled={isLastAdmin}
                      className="text-error text-xs font-medium hover:underline disabled:opacity-50 disabled:no-underline"
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Write `TeamSettingsPage.test.tsx`**

Mirror the existing settings test file's mocking pattern: mock `api.getMembers` (returns 2 fixture members, one admin one viewer), render, assert both rows show; click Invite Member, fill email + role, click Add to Workspace, assert `api.addMember` called with the right payload and `api.getMembers` called again; assert the sole admin row's role `<select>` and Remove button are `disabled`.

- [ ] **Step 3: Run frontend tests**

Run: `cd frontend && npm run test -- TeamSettingsPage`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/settings/TeamSettingsPage.tsx frontend/src/routes/settings/TeamSettingsPage.test.tsx
git commit -m "feat: add Team Access settings page (members, invite, roles)"
```

---

### Task 10: Workspace switcher, nav entries, routing

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/auth/AuthContext.ts`
- Modify: `frontend/src/auth/AuthProvider.tsx`

**Interfaces:**
- Consumes: `api.switchWorkspace` (Task 7), `GeneralSettingsPage`/`TeamSettingsPage` (Tasks 8-9).
- Produces: `/settings/general` and `/settings/team` routes; a workspace switcher in the sidebar; `useAuth().switchWorkspace(workspaceId)`.

- [ ] **Step 1: Add `switchWorkspace` to `AuthContext.ts`**

```typescript
import { createContext, useContext } from "react";
import type { User } from "../lib/types";

export interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  switchWorkspace: (workspaceId: string) => Promise<void>;
}

export const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
```

- [ ] **Step 2: Implement it in `AuthProvider.tsx`**

```tsx
  const switchWorkspace = useCallback(async (workspaceId: string) => {
    const updatedUser = await api.switchWorkspace(workspaceId);
    setUser(updatedUser);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, loading, login, logout, switchWorkspace }),
    [user, loading, login, logout, switchWorkspace],
  );
```

(Add the `switchWorkspace` `useCallback` right after the existing `logout` one, and add it to the `useMemo` dependency array/object.)

- [ ] **Step 3: Add the switcher + Sign out area to `Sidebar.tsx`**

Replace the existing `<div className="mt-auto ...">` block:

```tsx
        <div className="mt-auto flex flex-col gap-3">
          {user && user.workspaces.length > 1 ? (
            <select
              value={user.workspace_id}
              onChange={(event) => void switchWorkspace(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent text-sm"
              aria-label="Switch workspace"
            >
              {user.workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </select>
          ) : null}
          <div className="ghost-border rounded-lg p-3">
            <p className="text-xs text-on-surface-variant truncate">{user?.email}</p>
            <button
              type="button"
              onClick={() => void logout()}
              className="mt-1 text-xs font-medium text-primary hover:underline"
            >
              Sign out
            </button>
          </div>
        </div>
```

Add `switchWorkspace` to the `const { user, logout } = useAuth();` destructure (→ `const { user, logout, switchWorkspace } = useAuth();`), and add the two new nav entries to `NAV_ITEMS`:

```typescript
const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { to: "/predictive", label: "Predictive Engine", icon: "psychology" },
  { to: "/journey", label: "Journey Graph", icon: "timeline" },
  { to: "/calibration", label: "Calibration", icon: "tune" },
  { to: "/settings/general", label: "General Settings", icon: "settings" },
  { to: "/settings/team", label: "Team Access", icon: "group" },
  { to: "/settings/model-calibration", label: "Model Calibration", icon: "tune" },
] as const;
```

- [ ] **Step 4: Wire the two new routes in `App.tsx`**

```tsx
import { GeneralSettingsPage } from "./routes/settings/GeneralSettingsPage";
import { TeamSettingsPage } from "./routes/settings/TeamSettingsPage";
```

Add inside the existing `<Route element={<RequireAuth><Shell /></RequireAuth>}>` block, alongside the existing `/settings/model-calibration` route:

```tsx
          <Route path="/settings/general" element={<GeneralSettingsPage />} />
          <Route path="/settings/team" element={<TeamSettingsPage />} />
```

- [ ] **Step 5: Update `RequireAuth.test.tsx` and `Sidebar`-adjacent tests if they hardcode the old `User` shape**

Any existing test fixture building a `User` object (e.g. in `RequireAuth.test.tsx`, `DashboardPage.test.tsx`, `LoginPage.test.tsx`) needs the four new required fields (`workspace_id`, `role`, `workspaces`) added to its fixture object or it will fail TypeScript compilation. Grep for `id: "` / `email: "` object literals typed as `User` across `frontend/src/**/*.test.tsx` and add e.g. `workspace_id: "test-workspace", role: "admin", workspaces: [{ id: "test-workspace", name: "Test" }]` to each.

- [ ] **Step 6: Run frontend typecheck + full test suite**

Run: `cd frontend && npm run typecheck && npm run test`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Sidebar.tsx frontend/src/auth
git add frontend/src -u
git commit -m "feat: add workspace switcher, General/Team nav entries and routes"
```

---

### Task 11: Full verification pass

**Files:** none (verification only, plus whatever small fixes verification surfaces).

- [ ] **Step 1: Full backend test suite + strict typecheck**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/`
Expected: all green.

- [ ] **Step 2: Full frontend test suite + typecheck**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all green.

- [ ] **Step 3: `docker compose up -d --build`, run the migration for real**

Run:
```bash
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend uv run alembic upgrade head
docker compose -f infra/docker-compose.yml exec backend flowsage-backend create-user demo@example.com hunter2
docker compose -f infra/docker-compose.yml exec backend flowsage-backend seed-personas
```
Expected: migration applies cleanly against the real seeded Postgres from prior chunks; `create-user` bootstraps a workspace for `demo@example.com`.

- [ ] **Step 4: Manual cross-tenant curl check**

```bash
# Log in as demo@example.com, capture the cookie.
curl -c /tmp/cookie.txt -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"demo@example.com","password":"hunter2"}'

# Create a second workspace as the same user.
curl -b /tmp/cookie.txt -X POST http://localhost:8000/workspaces \
  -H 'Content-Type: application/json' -d '{"name":"Second Workspace"}'

# Confirm /personas is empty in the second workspace (created baseline personas
# belong only to the Default workspace).
SECOND_ID=$(curl -b /tmp/cookie.txt http://localhost:8000/workspaces | jq -r '.[1].id')
curl -b /tmp/cookie.txt -X POST http://localhost:8000/auth/switch-workspace \
  -H 'Content-Type: application/json' -d "{\"workspace_id\":\"$SECOND_ID\"}"
curl -b /tmp/cookie.txt http://localhost:8000/personas   # expect: []
```
Expected: `[]` — confirms row-level isolation for real against the live stack, not just the test suite.

- [ ] **Step 5: Playwright e2e — General/Team settings + workspace switch**

Run: `cd frontend && npx playwright test` (or the project's existing e2e npm script — check `package.json` for the exact command other chunks used). Drive: log in, navigate to `/settings/general`, edit name, save, confirm persisted on reload; navigate to `/settings/team`, invite a second pre-existing user, change their role, confirm the table updates; create a second workspace via the API directly (or via a `POST /workspaces` call in the test setup) and confirm the sidebar switcher appears and switching updates `/dashboard`'s data.
Expected: PASS.

- [ ] **Step 6: Tear down and final commit/push**

```bash
docker compose -f infra/docker-compose.yml down
git status  # confirm clean tree, everything already committed per-task
git push origin main  # or the chunk's feature branch, per however this repo's worktree/branch was set up
```
