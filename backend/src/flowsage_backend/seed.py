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
            select(Persona).where(Persona.slug == persona.id, Persona.workspace_id == workspace_id)
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
