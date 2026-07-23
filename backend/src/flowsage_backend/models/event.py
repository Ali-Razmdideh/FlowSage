"""Raw event log rows.

The plan's Neo4j schema (`(:Screen)-[:TRANSITION]->(:Screen)`, one edge per
session-pair) only records genuine screen-to-screen transitions -- same-screen
repeats are deliberately collapsed before ever reaching Neo4j (see
`flowsage_graph.ingest.session_transitions`), which is exactly the signal rage-loop
detection needs. So the full raw log is kept here in Postgres too:
`to_graph_event()` converts a row back into `flowsage_graph.models.Event`, letting
the graph API reuse that package's tested `discover_funnel`/`detect_friction`
directly instead of re-deriving them from Neo4j's more lossy aggregated edges.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from flowsage_graph.models import Event as GraphEvent
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    session_id: Mapped[str] = mapped_column(String(200), index=True)
    screen: Mapped[str] = mapped_column(String(200))
    event: Mapped[str] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    device: Mapped[str] = mapped_column(String(64), default="unknown")
    cohort: Mapped[str] = mapped_column(String(64), default="unknown", index=True)

    def to_graph_event(self) -> GraphEvent:
        return GraphEvent(
            session_id=self.session_id,
            screen=self.screen,
            event=self.event,
            timestamp=self.timestamp,
            device=self.device,
            cohort=self.cohort,
        )
