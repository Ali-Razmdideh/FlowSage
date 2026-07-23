"""Per-workspace Slack/Jira config, replacing the global `Settings.slack_webhook_url`/
`jira_*` env vars. One row per workspace per provider; presence of a row means
"connected" (see `integrations_store.py`)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class SlackIntegration(Base):
    __tablename__ = "slack_integrations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    webhook_url: Mapped[str] = mapped_column(String(500))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JiraIntegration(Base):
    __tablename__ = "jira_integrations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, index=True
    )
    base_url: Mapped[str] = mapped_column(String(500))
    email: Mapped[str] = mapped_column(String(320))
    api_token: Mapped[str] = mapped_column(String(500))
    project_key: Mapped[str] = mapped_column(String(64))
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
