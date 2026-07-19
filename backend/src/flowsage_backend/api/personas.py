"""Persona library: read-only listing (Predictive Engine's persona panel) plus
full CRUD + baseline reset (Phase 2 chunk 4: Persona Configuration screen).
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from flowsage_predict.personas import find_baseline_persona
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from flowsage_backend.deps import get_current_user, get_db_session
from flowsage_backend.models.persona import Persona

router = APIRouter(prefix="/personas", tags=["personas"], dependencies=[Depends(get_current_user)])

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class PersonaMemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    note: str
    kind: str
    created_at: datetime


class PersonaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    description: str
    baseline: bool
    tech_affinity: str
    primary_device: str
    discovery_mode: str
    contextual_triggers: list[str]
    technical_literacy: float
    anxiety: float
    patience: float
    curiosity: float


class PersonaDetailOut(PersonaOut):
    memories: list[PersonaMemoryOut]


class PersonaCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    tech_affinity: str = Field(min_length=1, max_length=64)
    primary_device: str = Field(min_length=1, max_length=64)
    discovery_mode: str = Field(min_length=1, max_length=64)
    contextual_triggers: list[str] = Field(default_factory=list)
    technical_literacy: float = Field(ge=0.0, le=1.0)
    anxiety: float = Field(ge=0.0, le=1.0)
    patience: float = Field(ge=0.0, le=1.0)
    curiosity: float = Field(ge=0.0, le=1.0)


class PersonaUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    tech_affinity: str = Field(min_length=1, max_length=64)
    primary_device: str = Field(min_length=1, max_length=64)
    discovery_mode: str = Field(min_length=1, max_length=64)
    contextual_triggers: list[str]
    technical_literacy: float = Field(ge=0.0, le=1.0)
    anxiety: float = Field(ge=0.0, le=1.0)
    patience: float = Field(ge=0.0, le=1.0)
    curiosity: float = Field(ge=0.0, le=1.0)


async def _get_persona_or_404(session: AsyncSession, persona_id: uuid.UUID) -> Persona:
    result = await session.execute(
        select(Persona).where(Persona.id == persona_id).options(selectinload(Persona.memories))
    )
    persona = result.scalar_one_or_none()
    if persona is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona not found")
    return persona


@router.get("", response_model=list[PersonaOut])
async def list_personas(session: AsyncSession = Depends(get_db_session)) -> list[Persona]:
    result = await session.execute(select(Persona).order_by(Persona.name))
    return list(result.scalars().all())


@router.get("/{persona_id}", response_model=PersonaDetailOut)
async def get_persona(
    persona_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> Persona:
    return await _get_persona_or_404(session, persona_id)


@router.post("", response_model=PersonaOut, status_code=status.HTTP_201_CREATED)
async def create_persona(
    payload: PersonaCreate, session: AsyncSession = Depends(get_db_session)
) -> Persona:
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "slug must be lowercase alphanumeric words separated by hyphens",
        )

    persona = Persona(
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


@router.patch("/{persona_id}", response_model=PersonaOut)
async def update_persona(
    persona_id: uuid.UUID,
    payload: PersonaUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> Persona:
    persona = await _get_persona_or_404(session, persona_id)
    persona.name = payload.name
    persona.description = payload.description
    persona.tech_affinity = payload.tech_affinity
    persona.primary_device = payload.primary_device
    persona.discovery_mode = payload.discovery_mode
    persona.contextual_triggers = payload.contextual_triggers
    persona.technical_literacy = payload.technical_literacy
    persona.anxiety = payload.anxiety
    persona.patience = payload.patience
    persona.curiosity = payload.curiosity
    await session.commit()
    await session.refresh(persona)
    return persona


@router.post("/{persona_id}/reset", response_model=PersonaOut)
async def reset_persona(
    persona_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> Persona:
    """Baseline personas only -- re-reads the shipped YAML definition
    (`flowsage_predict.baseline_personas`) and overwrites the row's editable
    fields, undoing any manual edits or retraining-job slider nudges."""
    persona = await _get_persona_or_404(session, persona_id)
    if not persona.baseline:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only baseline personas can be reset to their default"
        )

    try:
        baseline = find_baseline_persona(persona.slug)
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No shipped baseline definition for this persona's slug"
        ) from exc

    persona.name = baseline.name
    persona.description = baseline.description
    persona.tech_affinity = baseline.demographic_anchors.tech_affinity
    persona.primary_device = baseline.demographic_anchors.primary_device
    persona.discovery_mode = baseline.demographic_anchors.discovery_mode
    persona.contextual_triggers = list(baseline.contextual_triggers)
    persona.technical_literacy = baseline.sliders.technical_literacy
    persona.anxiety = baseline.sliders.anxiety
    persona.patience = baseline.sliders.patience
    persona.curiosity = baseline.sliders.curiosity
    await session.commit()
    await session.refresh(persona)
    return persona


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_persona(
    persona_id: uuid.UUID, session: AsyncSession = Depends(get_db_session)
) -> None:
    persona = await _get_persona_or_404(session, persona_id)
    if persona.baseline:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Baseline personas can't be deleted, only reset"
        )

    await session.delete(persona)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Persona has simulation run history and can't be deleted",
        ) from exc
