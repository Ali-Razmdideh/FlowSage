"""Read-only persona listing, for the Predictive Engine's persona panel."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_user, get_db_session
from flowsage_backend.models.persona import Persona

router = APIRouter(prefix="/personas", tags=["personas"], dependencies=[Depends(get_current_user)])


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


@router.get("", response_model=list[PersonaOut])
async def list_personas(session: AsyncSession = Depends(get_db_session)) -> list[Persona]:
    result = await session.execute(select(Persona).order_by(Persona.name))
    return list(result.scalars().all())
