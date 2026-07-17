"""Typed data models shared across the persona simulation pipeline.

These mirror the `Persona`, `SimulationRun`/`SimulationStep`, and `FrictionIssue`
entities described in the project plan's Postgres data model, so the Phase 1
backend can adopt the same shapes when it persists simulation results.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """Impact ranking for a detected friction issue."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DemographicAnchors(BaseModel):
    tech_affinity: str
    primary_device: str
    discovery_mode: str


class BehavioralSliders(BaseModel):
    """Each value is normalized to the 0.0-1.0 range, matching the prototype's percent sliders."""

    technical_literacy: float = Field(ge=0.0, le=1.0)
    anxiety: float = Field(ge=0.0, le=1.0)
    patience: float = Field(ge=0.0, le=1.0)
    curiosity: float = Field(ge=0.0, le=1.0)


class Persona(BaseModel):
    id: str
    name: str
    description: str
    baseline: bool = False
    demographic_anchors: DemographicAnchors
    contextual_triggers: list[str] = Field(default_factory=list)
    sliders: BehavioralSliders
    model: str = "claude-sonnet-4-5"

    def system_prompt(self) -> str:
        """Render the persona as a system prompt fragment for the vision model."""
        triggers = ", ".join(self.contextual_triggers) or "none"
        return (
            f"You are role-playing as the '{self.name}' user persona while evaluating a "
            f"product UI. {self.description}\n"
            f"Tech affinity: {self.demographic_anchors.tech_affinity}. "
            f"Primary device: {self.demographic_anchors.primary_device}. "
            f"Discovery mode: {self.demographic_anchors.discovery_mode}.\n"
            f"Contextual triggers active right now: {triggers}.\n"
            f"Behavioral calibration (0=low, 1=high): technical_literacy="
            f"{self.sliders.technical_literacy}, anxiety={self.sliders.anxiety}, "
            f"patience={self.sliders.patience}, curiosity={self.sliders.curiosity}."
        )


class FrictionIssue(BaseModel):
    screen: str
    severity: Severity
    title: str
    heuristic_violated: str
    persona_impact: str
    description: str
    suggested_fix: str


class ScreenEvaluation(BaseModel):
    """Structured output the vision model returns for a single screenshot."""

    action: str
    reasoning: str
    abandon: bool = False
    friction: FrictionIssue | None = None


class SimulationStep(BaseModel):
    screen: str
    action: str
    reasoning: str
    friction: FrictionIssue | None = None


class SimulationReport(BaseModel):
    persona: Persona
    goal: str
    flow_name: str
    steps: list[SimulationStep]
    issues: list[FrictionIssue]
    completed: bool
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def screenshots_visited(self) -> int:
        return len(self.steps)


class ScreenshotSequence(BaseModel):
    """An ordered set of screenshots making up one flow to walk through."""

    flow_name: str
    goal: str
    screenshots: list[Path]
