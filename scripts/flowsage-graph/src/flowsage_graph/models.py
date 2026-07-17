"""Typed data models for the event log -> journey graph pipeline.

`Event` mirrors one row of the CSV/JSONL event log described in the project plan
(session_id, event, screen, ts, device, cohort). `FunnelStep` and `FrictionNode`
are the funnel-discovery output consumed by `viz.py` to render the static HTML report.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, computed_field


class Event(BaseModel):
    session_id: str
    screen: str
    event: str
    timestamp: datetime
    device: str = "unknown"
    cohort: str = "unknown"


class FrictionKind(str, Enum):
    ABNORMAL_DROP_OFF = "abnormal_drop_off"
    RAGE_LOOP = "rage_loop"
    BACKTRACK = "backtrack"


class FunnelStep(BaseModel):
    screen: str
    sessions_entered: int
    sessions_continued: int

    @computed_field  # type: ignore[prop-decorator]
    @property
    def drop_off_rate(self) -> float:
        if self.sessions_entered == 0:
            return 0.0
        return 1 - (self.sessions_continued / self.sessions_entered)


class FrictionNode(BaseModel):
    screen: str
    kind: FrictionKind
    detail: str
    sessions_affected: int


class FunnelReport(BaseModel):
    funnel: list[FunnelStep] = Field(default_factory=list)
    friction_nodes: list[FrictionNode] = Field(default_factory=list)
    total_sessions: int = 0
    total_events: int = 0
