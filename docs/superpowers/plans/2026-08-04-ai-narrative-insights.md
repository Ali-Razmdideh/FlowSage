# AI Narrative Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deterministic template text in Node Intelligence (`ai_insight`), calibration anomaly reports, and retraining rationale with real Claude-generated narrative, cached with input-hash invalidation and gated by a soft usage cap, without adding synchronous LLM latency to any HTTP GET.

**Architecture:** A new `flowsage_predict.narrative` module (sibling to `vision.py`, same forced-tool-call pattern) does the actual Claude calls, but **only from the arq worker process** — matching this codebase's existing rule that every Claude call happens in a background job, never inline in a FastAPI handler. GET endpoints read from a new `GeneratedInsight` cache table (keyed by input hash) and fall back to today's deterministic templates on a cache miss, while enqueueing a background job (deduped via arq's `_job_id`) to warm the cache for next time. Retraining, which is already an async job, calls the narrative client directly inside `execute_retraining` — no extra enqueue hop needed there.

**Tech Stack:** FastAPI, SQLAlchemy async/Postgres, arq (Redis job queue), Anthropic Python SDK (forced tool-call JSON), Pydantic.

## Global Constraints

- `mypy --strict` clean on both `backend/` and `scripts/flowsage-predict/` after every task (this repo's CI gate).
- `flowsage-predict` must not depend on `flowsage-backend` or `flowsage-graph` (it's a standalone workspace package — verified via `scripts/flowsage-predict/pyproject.toml` having zero `flowsage-*` dependencies). `flowsage_predict.narrative` therefore defines its own small input types (`FrictionSignal`, `ScreenSignal`) instead of importing `flowsage_graph.models.FrictionNode` or `flowsage_backend.calibration.ScreenCalibration`.
- No `anthropic.Anthropic()` (or any class wrapping it) may be constructed anywhere in the FastAPI app process (`create_app`/`app.state`) — this repo's test suite never sets `ANTHROPIC_API_KEY`, and the `app` fixture backs ~290 existing tests. Every real Claude call is constructed lazily, only inside arq's `_startup` (mirrors `AnthropicVisionClient` in `worker.py`), never touched by `pytest` except via an injected fake.
- A `GeneratedInsight` row is written **only** after a successful Claude call — this makes "rows created this month" exactly equal to the new usage-cap counter, with no separate accounting.
- A narrative-generation failure (network error, malformed tool response, exhausted budget) must never raise out of a GET endpoint or flip a `RetrainingJob` to `FAILED` — always fall back to the existing deterministic text.
- New DB columns use plain `String` for `kind` (not a Postgres `Enum`) — sidesteps the enum-drop-on-downgrade migration gotcha this project has hit twice before (`run_status`, `schedule_interval`).

---

## Task 1: `flowsage_predict.narrative` — Claude client + result types

**Files:**
- Create: `scripts/flowsage-predict/src/flowsage_predict/narrative.py`
- Create: `scripts/flowsage-predict/tests/test_narrative.py`

**Interfaces:**
- Produces: `NARRATIVE_MODEL: str`, `FrictionSignal(BaseModel)` with fields `kind: str, sessions_affected: int`, `ScreenSignal(BaseModel)` with fields `screen: str, predicted_score: float, observed_score: float, delta: float`, `NarrativeRecommendation(BaseModel)` with fields `title: str, description: str, expected_lift_pct: float | None`, `NodeInsightResult(BaseModel)` with fields `insight: str, recommendations: list[NarrativeRecommendation]`, `NarrativeClient(Protocol)` with methods `generate_node_insight(screen: str, drop_off_rate: float, friction: list[FrictionSignal]) -> NodeInsightResult`, `generate_calibration_narrative(persona_name: str, anomalies: list[ScreenSignal]) -> str`, `generate_retraining_rationale(persona_name: str, anomalies: list[ScreenSignal], new_technical_literacy: float, new_anxiety: float, new_patience: float) -> str`, `AnthropicNarrativeClient` (real implementation of the Protocol), `parse_node_insight_tool_input(tool_input: dict[str, object]) -> NodeInsightResult`, `parse_narrative_text_tool_input(tool_input: dict[str, object], field: str) -> str`.

- [ ] **Step 1: Write the failing tests for the parsing helpers**

```python
# scripts/flowsage-predict/tests/test_narrative.py
import pytest

from flowsage_predict.narrative import (
    parse_narrative_text_tool_input,
    parse_node_insight_tool_input,
)


def test_parse_node_insight_tool_input() -> None:
    result = parse_node_insight_tool_input(
        {
            "insight": "Users abandon checkout because the total price is unclear.",
            "recommendations": [
                {
                    "title": "Show total upfront",
                    "description": "Display all fees before the final step.",
                    "expected_lift_pct": 12.0,
                }
            ],
        }
    )
    assert result.insight == "Users abandon checkout because the total price is unclear."
    assert result.recommendations[0].title == "Show total upfront"
    assert result.recommendations[0].expected_lift_pct == 12.0


def test_parse_node_insight_tool_input_allows_null_lift() -> None:
    result = parse_node_insight_tool_input(
        {
            "insight": "No abnormal signal.",
            "recommendations": [
                {"title": "t", "description": "d", "expected_lift_pct": None},
            ],
        }
    )
    assert result.recommendations[0].expected_lift_pct is None


def test_parse_narrative_text_tool_input() -> None:
    value = parse_narrative_text_tool_input(
        {"narrative": "Real users hesitated more than predicted."}, "narrative"
    )
    assert value == "Real users hesitated more than predicted."


def test_parse_narrative_text_tool_input_raises_on_missing_field() -> None:
    with pytest.raises(ValueError, match="Expected a string"):
        parse_narrative_text_tool_input({}, "narrative")


def test_parse_narrative_text_tool_input_raises_on_wrong_type() -> None:
    with pytest.raises(ValueError, match="Expected a string"):
        parse_narrative_text_tool_input({"rationale": 5}, "rationale")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd scripts/flowsage-predict && uv run pytest tests/test_narrative.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_predict.narrative'`

- [ ] **Step 3: Write the module**

```python
# scripts/flowsage-predict/src/flowsage_predict/narrative.py
"""Text-only narrative client: turns funnel/calibration/retraining signal into
short AI-written analysis, via a forced tool call so parsing is deterministic.

Sibling to vision.py: same Protocol + real-Anthropic-implementation + fake-in-
tests split, just no image content block since these calls are text-only.
This module has zero flowsage-graph/flowsage-backend dependencies on purpose
(flowsage-predict is a standalone workspace package) -- FrictionSignal/
ScreenSignal are narrow local mirrors of flowsage_graph.models.FrictionNode
and flowsage_backend.calibration.ScreenCalibration, built by the caller.
"""

from __future__ import annotations

from typing import Protocol

import anthropic
from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam
from pydantic import BaseModel

NARRATIVE_MODEL = "claude-haiku-4-5-20251001"


class FrictionSignal(BaseModel):
    kind: str
    sessions_affected: int


class ScreenSignal(BaseModel):
    screen: str
    predicted_score: float
    observed_score: float
    delta: float


class NarrativeRecommendation(BaseModel):
    title: str
    description: str
    expected_lift_pct: float | None


class NodeInsightResult(BaseModel):
    insight: str
    recommendations: list[NarrativeRecommendation]


class NarrativeClient(Protocol):
    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction: list[FrictionSignal]
    ) -> NodeInsightResult: ...

    def generate_calibration_narrative(
        self, persona_name: str, anomalies: list[ScreenSignal]
    ) -> str: ...

    def generate_retraining_rationale(
        self,
        persona_name: str,
        anomalies: list[ScreenSignal],
        new_technical_literacy: float,
        new_anxiety: float,
        new_patience: float,
    ) -> str: ...


_NODE_INSIGHT_TOOL_NAME = "report_node_insight"
_NODE_INSIGHT_TOOL_SCHEMA: ToolParam = {
    "name": _NODE_INSIGHT_TOOL_NAME,
    "description": (
        "Report a usability insight and up to 3 re-engagement recommendations "
        "for a funnel screen."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "insight": {
                "type": "string",
                "description": (
                    "1-2 sentence plain-language explanation of the friction on "
                    "this screen."
                ),
            },
            "recommendations": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "expected_lift_pct": {"type": ["number", "null"]},
                    },
                    "required": ["title", "description", "expected_lift_pct"],
                },
            },
        },
        "required": ["insight", "recommendations"],
    },
}
_NODE_INSIGHT_TOOL_CHOICE: ToolChoiceToolParam = {"type": "tool", "name": _NODE_INSIGHT_TOOL_NAME}

_CALIBRATION_NARRATIVE_TOOL_NAME = "report_calibration_narrative"
_CALIBRATION_NARRATIVE_TOOL_SCHEMA: ToolParam = {
    "name": _CALIBRATION_NARRATIVE_TOOL_NAME,
    "description": (
        "Explain in plain language why a persona's predicted friction diverged "
        "from what real users experienced."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": "1-3 sentence explanation of the divergence.",
            },
        },
        "required": ["narrative"],
    },
}
_CALIBRATION_NARRATIVE_TOOL_CHOICE: ToolChoiceToolParam = {
    "type": "tool",
    "name": _CALIBRATION_NARRATIVE_TOOL_NAME,
}

_RETRAINING_RATIONALE_TOOL_NAME = "report_retraining_rationale"
_RETRAINING_RATIONALE_TOOL_SCHEMA: ToolParam = {
    "name": _RETRAINING_RATIONALE_TOOL_NAME,
    "description": "Explain in plain language why a persona's sliders were adjusted.",
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {
                "type": "string",
                "description": "1-3 sentence explanation of the slider adjustment.",
            },
        },
        "required": ["rationale"],
    },
}
_RETRAINING_RATIONALE_TOOL_CHOICE: ToolChoiceToolParam = {
    "type": "tool",
    "name": _RETRAINING_RATIONALE_TOOL_NAME,
}


def parse_node_insight_tool_input(tool_input: dict[str, object]) -> NodeInsightResult:
    """Validate a tool-call payload from Claude into a `NodeInsightResult`.

    Extracted so unit tests can exercise parsing/validation without a network call.
    """
    return NodeInsightResult.model_validate(tool_input)


def parse_narrative_text_tool_input(tool_input: dict[str, object], field: str) -> str:
    """Validate a single-string-field tool-call payload (calibration narrative,
    retraining rationale both use this shape)."""
    value = tool_input.get(field)
    if not isinstance(value, str):
        raise ValueError(f"Expected a string {field!r} field, got: {tool_input!r}")
    return value


def _screens_summary(anomalies: list[ScreenSignal]) -> str:
    return "\n".join(
        f"- {a.screen}: predicted {a.predicted_score:.2f}, observed "
        f"{a.observed_score:.2f} (delta {a.delta:+.2f})"
        for a in anomalies
    )


class AnthropicNarrativeClient:
    """Calls the Anthropic Messages API with a forced tool call, text-only (no
    image content block, unlike `vision.AnthropicVisionClient`)."""

    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self._client = client or anthropic.Anthropic()

    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction: list[FrictionSignal]
    ) -> NodeInsightResult:
        friction_summary = (
            "\n".join(f"- {f.kind} affecting {f.sessions_affected} sessions" for f in friction)
            or "- no specific friction pattern recorded"
        )
        message: MessageParam = {
            "role": "user",
            "content": (
                f"Screen '{screen}' has a {drop_off_rate * 100:.0f}% drop-off rate. "
                f"Detected friction patterns:\n{friction_summary}\n\n"
                "Explain the likely usability problem in plain language and suggest "
                "up to 3 concrete fixes ranked by expected impact."
            ),
        }
        response = self._client.messages.create(
            model=NARRATIVE_MODEL,
            max_tokens=512,
            tools=[_NODE_INSIGHT_TOOL_SCHEMA],
            tool_choice=_NODE_INSIGHT_TOOL_CHOICE,
            messages=[message],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == _NODE_INSIGHT_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_node_insight_tool_input(tool_input)
        raise RuntimeError(f"Claude did not call {_NODE_INSIGHT_TOOL_NAME!r}")

    def generate_calibration_narrative(
        self, persona_name: str, anomalies: list[ScreenSignal]
    ) -> str:
        message: MessageParam = {
            "role": "user",
            "content": (
                f"Persona '{persona_name}' predicted friction that diverged from real "
                f"user behavior on these screens:\n{_screens_summary(anomalies)}\n\n"
                "In 1-3 sentences, explain the likely reason predicted and observed "
                "friction diverged."
            ),
        }
        response = self._client.messages.create(
            model=NARRATIVE_MODEL,
            max_tokens=256,
            tools=[_CALIBRATION_NARRATIVE_TOOL_SCHEMA],
            tool_choice=_CALIBRATION_NARRATIVE_TOOL_CHOICE,
            messages=[message],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == _CALIBRATION_NARRATIVE_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_narrative_text_tool_input(tool_input, "narrative")
        raise RuntimeError(f"Claude did not call {_CALIBRATION_NARRATIVE_TOOL_NAME!r}")

    def generate_retraining_rationale(
        self,
        persona_name: str,
        anomalies: list[ScreenSignal],
        new_technical_literacy: float,
        new_anxiety: float,
        new_patience: float,
    ) -> str:
        message: MessageParam = {
            "role": "user",
            "content": (
                f"Persona '{persona_name}' was just retrained from observed behavioral "
                f"evidence on these anomalous screens:\n{_screens_summary(anomalies)}\n\n"
                f"New sliders -- technical_literacy={new_technical_literacy:.2f}, "
                f"anxiety={new_anxiety:.2f}, patience={new_patience:.2f}. In 1-3 "
                "sentences, explain in plain language why this adjustment makes sense."
            ),
        }
        response = self._client.messages.create(
            model=NARRATIVE_MODEL,
            max_tokens=256,
            tools=[_RETRAINING_RATIONALE_TOOL_SCHEMA],
            tool_choice=_RETRAINING_RATIONALE_TOOL_CHOICE,
            messages=[message],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == _RETRAINING_RATIONALE_TOOL_NAME:
                tool_input = block.input
                assert isinstance(tool_input, dict)
                return parse_narrative_text_tool_input(tool_input, "rationale")
        raise RuntimeError(f"Claude did not call {_RETRAINING_RATIONALE_TOOL_NAME!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd scripts/flowsage-predict && uv run pytest tests/test_narrative.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Type-check and lint**

Run: `cd scripts/flowsage-predict && uv run mypy --strict src && uv run autoflake8 --check src tests`
Expected: both clean

- [ ] **Step 6: Commit**

```bash
git add scripts/flowsage-predict/src/flowsage_predict/narrative.py scripts/flowsage-predict/tests/test_narrative.py
git commit -m "feat: add flowsage_predict.narrative text-only Claude client"
```

---

## Task 2: `GeneratedInsight` model + migration

**Files:**
- Create: `backend/src/flowsage_backend/models/generated_insight.py`
- Modify: `backend/src/flowsage_backend/models/__init__.py`
- Create: `backend/migrations/versions/<new_revision>_add_generated_insights_table.py`

**Interfaces:**
- Produces: `GeneratedInsight` ORM model with columns `id: uuid.UUID`, `workspace_id: uuid.UUID`, `kind: str`, `cache_key: str`, `input_hash: str`, `payload: dict[str, object]`, `model: str`, `created_at: datetime`; unique constraint on `(workspace_id, kind, cache_key)`.

- [ ] **Step 1: Write the model**

```python
# backend/src/flowsage_backend/models/generated_insight.py
"""Cache of Claude-generated narrative text for report surfaces that were
previously deterministic templates: churn.py's Node Intelligence `ai_insight`,
calibration.py's `PersonaCalibration.narrative`, and retraining.py's
`PersonaMemory.note`. Rows are only ever written after a successful
`flowsage_predict.narrative.NarrativeClient` call (see worker.py's
generate_node_insight_job/generate_calibration_narrative_job and
retraining.execute_retraining) -- this also makes "rows created this month"
exactly equal to the insight_generations usage-cap counter in billing.py, with
no separate accounting needed. See
docs/superpowers/specs/2026-08-04-ai-narrative-insights-design.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class GeneratedInsight(Base):
    __tablename__ = "generated_insights"
    __table_args__ = (
        UniqueConstraint("workspace_id", "kind", "cache_key", name="uq_generated_insight_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    cache_key: Mapped[str] = mapped_column(String(200))
    input_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: Register it in `models/__init__.py`**

Add the import and `__all__` entry, following the existing pattern for every other model:

```python
# backend/src/flowsage_backend/models/__init__.py
from flowsage_backend.models.event import Event
from flowsage_backend.models.generated_insight import GeneratedInsight
from flowsage_backend.models.integration import JiraIntegration, SlackIntegration
```

(insert the `generated_insight` import alphabetically between `event` and `integration`, matching the file's existing import ordering) and add `"GeneratedInsight",` to `__all__` (insert after `"Event",`).

- [ ] **Step 3: Generate and hand-verify the migration**

Run: `cd backend && uv run alembic revision --autogenerate -m "add generated_insights table"`

This produces a new file under `backend/migrations/versions/`. Open it and confirm it only adds the `generated_insights` table (drop any unrelated autogenerate noise). Rewrite it to match this exact shape (substitute the real generated `revision` id and current head as `down_revision` — run `cd backend && uv run alembic heads` to confirm the current head first):

```python
"""add generated_insights table

Revision ID: <generated>
Revises: 7c2f9a4d18be
Create Date: <generated>

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "<generated>"
down_revision: Union[str, Sequence[str], None] = "7c2f9a4d18be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "generated_insights",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("cache_key", sa.String(length=200), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "kind", "cache_key", name="uq_generated_insight_key"
        ),
    )
    op.create_index(
        op.f("ix_generated_insights_workspace_id"), "generated_insights", ["workspace_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generated_insights_workspace_id"), table_name="generated_insights")
    op.drop_table("generated_insights")
    # No native Enum column on this table (kind is a plain String, deliberately --
    # see this plan's Global Constraints), so no enum-drop-on-downgrade step is
    # needed here, unlike run_status/schedule_interval.
```

- [ ] **Step 4: Verify the upgrade -> downgrade -> upgrade cycle against a live Postgres**

Run (against a real local Postgres, e.g. via `infra/docker-compose.yml`'s `postgres` service, or any scratch Postgres reachable via `DATABASE_URL`):
```bash
cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: all three commands exit 0, no "already exists" / "does not exist" errors.

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/models/generated_insight.py backend/src/flowsage_backend/models/__init__.py backend/migrations/versions/
git commit -m "feat: add GeneratedInsight cache table"
```

---

## Task 3: `insight_cache` module — generic cache read/write

**Files:**
- Create: `backend/src/flowsage_backend/insight_cache.py`
- Create: `backend/tests/test_insight_cache.py`

**Interfaces:**
- Consumes: `GeneratedInsight` (Task 2).
- Produces: `compute_input_hash(signal: dict[str, object]) -> str`, `get_cached(session, workspace_id, kind, cache_key) -> GeneratedInsight | None`, `is_fresh(session, workspace_id, kind, cache_key, input_hash) -> bool`, `upsert_cached(session, workspace_id, kind, cache_key, input_hash, payload, model) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_insight_cache.py
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.insight_cache import compute_input_hash, get_cached, is_fresh, upsert_cached
from flowsage_backend.models.workspace import Workspace


async def _workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Insight Cache Test", slug=f"insight-cache-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


def test_compute_input_hash_is_deterministic_and_order_independent() -> None:
    a = compute_input_hash({"x": 1, "y": 2})
    b = compute_input_hash({"y": 2, "x": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex digest


def test_compute_input_hash_differs_for_different_signals() -> None:
    assert compute_input_hash({"x": 1}) != compute_input_hash({"x": 2})


async def test_get_cached_returns_none_when_absent(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    assert await get_cached(db_session, workspace_id, "node_intelligence", "checkout") is None


async def test_upsert_then_get_cached_round_trips(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    await upsert_cached(
        db_session,
        workspace_id,
        "node_intelligence",
        "checkout",
        "hash1",
        {"insight": "text", "recommendations": []},
        "claude-haiku-4-5-20251001",
    )

    cached = await get_cached(db_session, workspace_id, "node_intelligence", "checkout")
    assert cached is not None
    assert cached.input_hash == "hash1"
    assert cached.payload == {"insight": "text", "recommendations": []}
    assert cached.model == "claude-haiku-4-5-20251001"


async def test_upsert_cached_replaces_stale_row_on_conflict(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    await upsert_cached(
        db_session, workspace_id, "node_intelligence", "checkout", "hash1", {"a": 1}, "m1"
    )
    await upsert_cached(
        db_session, workspace_id, "node_intelligence", "checkout", "hash2", {"a": 2}, "m2"
    )

    cached = await get_cached(db_session, workspace_id, "node_intelligence", "checkout")
    assert cached is not None
    assert cached.input_hash == "hash2"
    assert cached.payload == {"a": 2}


async def test_is_fresh_true_only_when_hash_matches(db_session: AsyncSession) -> None:
    workspace_id = await _workspace(db_session)
    assert await is_fresh(db_session, workspace_id, "node_intelligence", "checkout", "hash1") is False

    await upsert_cached(
        db_session, workspace_id, "node_intelligence", "checkout", "hash1", {"a": 1}, "m1"
    )
    assert await is_fresh(db_session, workspace_id, "node_intelligence", "checkout", "hash1") is True
    assert await is_fresh(db_session, workspace_id, "node_intelligence", "checkout", "stale") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_insight_cache.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.insight_cache'`

- [ ] **Step 3: Write the module**

```python
# backend/src/flowsage_backend/insight_cache.py
"""Generic on-demand cache for Claude-generated narrative text (see
flowsage_predict.narrative), keyed by an input hash so a GET only pays for a
fresh Claude call when the underlying signal has actually changed since the
last successful generation. Written to exclusively by the arq job functions
in worker.py and retraining.execute_retraining -- read from synchronously by
churn.py/calibration.py, which never call Claude themselves.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.generated_insight import GeneratedInsight


def compute_input_hash(signal: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(signal, sort_keys=True).encode()).hexdigest()


async def get_cached(
    session: AsyncSession, workspace_id: uuid.UUID, kind: str, cache_key: str
) -> GeneratedInsight | None:
    result = await session.execute(
        select(GeneratedInsight).where(
            GeneratedInsight.workspace_id == workspace_id,
            GeneratedInsight.kind == kind,
            GeneratedInsight.cache_key == cache_key,
        )
    )
    return result.scalar_one_or_none()


async def is_fresh(
    session: AsyncSession, workspace_id: uuid.UUID, kind: str, cache_key: str, input_hash: str
) -> bool:
    cached = await get_cached(session, workspace_id, kind, cache_key)
    return cached is not None and cached.input_hash == input_hash


async def upsert_cached(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    kind: str,
    cache_key: str,
    input_hash: str,
    payload: dict[str, object],
    model: str,
) -> None:
    """Insert-or-replace on the (workspace_id, kind, cache_key) unique
    constraint -- an upsert rather than a plain insert because a screen's
    second-ever generation (after the underlying signal changes again) must
    replace the stale row, not violate the unique constraint."""
    stmt = pg_insert(GeneratedInsight).values(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind=kind,
        cache_key=cache_key,
        input_hash=input_hash,
        payload=payload,
        model=model,
        created_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["workspace_id", "kind", "cache_key"],
        set_={
            "input_hash": stmt.excluded.input_hash,
            "payload": stmt.excluded.payload,
            "model": stmt.excluded.model,
            "created_at": stmt.excluded.created_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_insight_cache.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/insight_cache.py backend/tests/test_insight_cache.py
git commit -m "feat: add insight_cache generic narrative-text cache"
```

---

## Task 4: Narrative usage cap in `billing.py`

**Files:**
- Modify: `backend/src/flowsage_backend/billing.py`
- Modify: `backend/tests/test_billing.py`

**Interfaces:**
- Consumes: `GeneratedInsight` (Task 2).
- Produces: `TierLimits.insight_generations_per_month: int`, `UsageSnapshot.insight_generations_used: int`, `UsageSnapshot.insight_generations_limit: int`, `has_narrative_budget(session, workspace_id) -> bool`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_billing.py`:

```python
# add to imports at top of backend/tests/test_billing.py
from flowsage_backend.billing import TIER_LIMITS, check_within_limits, get_usage, has_narrative_budget
from flowsage_backend.models.generated_insight import GeneratedInsight

# add these test functions
async def test_get_usage_counts_insight_generations_this_month(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Insight Usage Test", slug=f"insight-usage-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()

    this_month = _month_start_utc() + timedelta(days=1)
    last_month = _month_start_utc() - timedelta(days=1)
    db_session.add_all(
        [
            GeneratedInsight(
                workspace_id=workspace.id,
                kind="node_intelligence",
                cache_key="checkout",
                input_hash="h1",
                payload={"insight": "a"},
                model="claude-haiku-4-5-20251001",
                created_at=this_month,
            ),
            GeneratedInsight(
                workspace_id=workspace.id,
                kind="node_intelligence",
                cache_key="cart",
                input_hash="h2",
                payload={"insight": "b"},
                model="claude-haiku-4-5-20251001",
                created_at=last_month,
            ),
        ]
    )
    await db_session.commit()

    usage = await get_usage(db_session, workspace.id)
    assert usage.insight_generations_used == 1
    assert usage.insight_generations_limit == TIER_LIMITS[usage.tier].insight_generations_per_month


async def test_has_narrative_budget_false_once_free_tier_cap_hit(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Narrative Cap Test", slug=f"narrative-cap-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceSubscription(workspace_id=workspace.id, tier=SubscriptionTier.FREE)
    )
    await db_session.commit()

    limit = TIER_LIMITS[SubscriptionTier.FREE].insight_generations_per_month
    now = _month_start_utc() + timedelta(days=1)
    db_session.add_all(
        [
            GeneratedInsight(
                workspace_id=workspace.id,
                kind="node_intelligence",
                cache_key=f"screen-{i}",
                input_hash=f"h{i}",
                payload={},
                model="claude-haiku-4-5-20251001",
                created_at=now,
            )
            for i in range(limit)
        ]
    )
    await db_session.commit()

    assert await has_narrative_budget(db_session, workspace.id) is False


async def test_has_narrative_budget_true_when_unlimited(db_session: AsyncSession) -> None:
    workspace = Workspace(name="Team Narrative Test", slug=f"team-narrative-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(WorkspaceSubscription(workspace_id=workspace.id, tier=SubscriptionTier.TEAM))
    await db_session.commit()

    assert await has_narrative_budget(db_session, workspace.id) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_billing.py -v -k "insight_generations or narrative_budget"`
Expected: FAIL (`insight_generations_per_month`/`has_narrative_budget` don't exist yet)

- [ ] **Step 3: Implement in `billing.py`**

```python
# add import near the top, alongside the other model imports
from flowsage_backend.models.generated_insight import GeneratedInsight

# TierLimits gains a field
class TierLimits(BaseModel):
    events_per_month: int
    runs_per_month: int
    seats: int  # -1 means unlimited
    insight_generations_per_month: int  # -1 means unlimited


TIER_LIMITS: dict[SubscriptionTier, TierLimits] = {
    SubscriptionTier.FREE: TierLimits(
        events_per_month=1_000, runs_per_month=5, seats=1, insight_generations_per_month=20
    ),
    SubscriptionTier.PRO: TierLimits(
        events_per_month=50_000, runs_per_month=100, seats=10, insight_generations_per_month=1_000
    ),
    SubscriptionTier.TEAM: TierLimits(
        events_per_month=500_000, runs_per_month=1_000, seats=-1, insight_generations_per_month=-1
    ),
}


class UsageSnapshot(BaseModel):
    tier: SubscriptionTier
    events_used: int
    events_limit: int
    runs_used: int
    runs_limit: int
    seats_used: int
    seats_limit: int
    insight_generations_used: int
    insight_generations_limit: int
```

In `get_usage`, add the count query (same shape as `runs_used`) and pass it into the returned `UsageSnapshot`:

```python
    insight_generations_used = (
        await session.execute(
            select(func.count())
            .select_from(GeneratedInsight)
            .where(
                GeneratedInsight.workspace_id == workspace_id,
                GeneratedInsight.created_at >= month_start,
            )
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
        insight_generations_used=insight_generations_used,
        insight_generations_limit=limits.insight_generations_per_month,
    )
```

Add the soft-gate function after `check_within_limits`:

```python
async def has_narrative_budget(session: AsyncSession, workspace_id: uuid.UUID) -> bool:
    """Soft gate for narrative generation (Node Intelligence AI Insight,
    calibration anomaly narrative, retraining rationale) -- unlike
    `check_within_limits`, this never raises. A report page must never break
    because a text-generation budget ran out; callers fall back to the
    existing deterministic template text instead."""
    usage = await get_usage(session, workspace_id)
    if usage.insight_generations_limit == -1:
        return True
    return usage.insight_generations_used < usage.insight_generations_limit
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_billing.py -v`
Expected: PASS (all tests in the file, including the 3 new ones)

- [ ] **Step 5: Run the full backend suite once to confirm no other test asserts an exact `UsageSnapshot`/`TierLimits` shape that just broke**

Run: `cd backend && uv run pytest -x -q`
Expected: PASS. If `test_billing_api.py`/`test_billing_enforcement_api.py` assert on the JSON shape of `GET /billing/usage`, update those fixtures/assertions to include the two new fields.

- [ ] **Step 6: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add backend/src/flowsage_backend/billing.py backend/tests/test_billing.py
git commit -m "feat: add insight-generation usage cap to billing"
```

---

## Task 5: Node Intelligence cache wiring in `churn.py`

**Files:**
- Modify: `backend/src/flowsage_backend/churn.py`
- Modify: `backend/tests/test_churn.py`

**Interfaces:**
- Consumes: `insight_cache.{compute_input_hash,get_cached,upsert_cached}` (Task 3), `flowsage_predict.narrative.{NarrativeClient,FrictionSignal,NARRATIVE_MODEL}` (Task 1).
- Produces: `NODE_INSIGHT_KIND: str`, `node_insight_input_hash(drop_off_rate: float, friction_nodes: list[FrictionNode]) -> str`, `generate_and_cache_node_insight(session, workspace_id, screen, narrative_client) -> None`. `get_node_intelligence`'s existing signature/return type is unchanged, but it now overlays a fresh cache hit onto `ai_insight`/`recommendations`.

- [ ] **Step 1: Write the failing tests**

`test_churn.py` currently has no async/DB-touching tests at all (only pure-function tests of `build_node_intelligence`/`build_cohort_comparison`/`score_churn_risk`/`_avg_seconds_on_node`) and no `uuid`/`pytest`/`AsyncSession` imports -- this task adds the first ones. Replace the file's existing import block with:

```python
import logging
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_graph.models import Event as GraphEvent
from flowsage_graph.models import FrictionKind, FrictionNode, FunnelReport, FunnelStep

from flowsage_backend import insight_cache
from flowsage_backend.churn import (
    NODE_INSIGHT_KIND,
    _avg_seconds_on_node,
    build_cohort_comparison,
    build_node_intelligence,
    generate_and_cache_node_insight,
    get_node_intelligence,
    node_insight_input_hash,
    score_churn_risk,
)
from flowsage_backend.events import ingest_events
from flowsage_backend.models.workspace import Workspace
from flowsage_predict.narrative import NarrativeRecommendation, NodeInsightResult


async def _workspace_with_checkout_dropoff(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Node Insight Test", slug=f"node-insight-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.flush()
    now = datetime.now(timezone.utc)
    events = [
        GraphEvent(
            session_id=f"nit-{i}",
            screen="landing",
            event="view",
            timestamp=now,
            device="desktop",
            cohort="default",
        )
        for i in range(5)
    ] + [
        GraphEvent(
            session_id="nit-0",
            screen="checkout",
            event="view",
            timestamp=now,
            device="desktop",
            cohort="default",
        )
    ]
    await ingest_events(session, workspace.id, events)
    await session.commit()
    return workspace.id


async def test_get_node_intelligence_uses_cached_narrative_when_fresh(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    baseline = await get_node_intelligence(db_session, workspace_id, "landing")
    assert baseline is not None
    input_hash = node_insight_input_hash(baseline.drop_off_rate, baseline.friction_nodes)

    await insight_cache.upsert_cached(
        db_session,
        workspace_id,
        NODE_INSIGHT_KIND,
        "landing",
        input_hash,
        {
            "insight": "Cached AI insight.",
            "recommendations": [
                {"title": "Do X", "description": "Because Y.", "expected_lift_pct": 5.0}
            ],
        },
        "claude-haiku-4-5-20251001",
    )

    result = await get_node_intelligence(db_session, workspace_id, "landing")
    assert result is not None
    assert result.ai_insight == "Cached AI insight."
    assert result.recommendations[0].title == "Do X"
    assert result.recommendations[0].rank == 1


async def test_get_node_intelligence_falls_back_to_template_on_stale_cache(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    await insight_cache.upsert_cached(
        db_session,
        workspace_id,
        NODE_INSIGHT_KIND,
        "landing",
        "a-stale-hash-that-will-never-match",
        {"insight": "Stale.", "recommendations": []},
        "claude-haiku-4-5-20251001",
    )

    result = await get_node_intelligence(db_session, workspace_id, "landing")
    assert result is not None
    assert result.ai_insight != "Stale."


class _FakeNarrativeClient:
    def generate_node_insight(
        self, screen: str, drop_off_rate: float, friction: list[object]
    ) -> NodeInsightResult:
        return NodeInsightResult(
            insight=f"Generated insight for {screen}",
            recommendations=[
                NarrativeRecommendation(title="Fix it", description="Just fix it.", expected_lift_pct=9.0)
            ],
        )

    def generate_calibration_narrative(self, *args: object, **kwargs: object) -> str:
        raise NotImplementedError

    def generate_retraining_rationale(self, *args: object, **kwargs: object) -> str:
        raise NotImplementedError


async def test_generate_and_cache_node_insight_writes_cache_row(db_session: AsyncSession) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    await generate_and_cache_node_insight(
        db_session, workspace_id, "landing", _FakeNarrativeClient()
    )

    cached = await insight_cache.get_cached(db_session, workspace_id, NODE_INSIGHT_KIND, "landing")
    assert cached is not None
    assert cached.payload["insight"] == "Generated insight for landing"


async def test_generate_and_cache_node_insight_swallows_client_errors(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _workspace_with_checkout_dropoff(db_session)
    failing_client = Mock()
    failing_client.generate_node_insight.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.WARNING):
        await generate_and_cache_node_insight(db_session, workspace_id, "landing", failing_client)

    assert await insight_cache.get_cached(db_session, workspace_id, NODE_INSIGHT_KIND, "landing") is None
    assert "Node insight generation failed" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_churn.py -v -k "node_insight or cached_narrative"`
Expected: FAIL (`node_insight_input_hash`/`generate_and_cache_node_insight`/`NODE_INSIGHT_KIND` don't exist; cache overlay not wired in)

- [ ] **Step 3: Implement in `churn.py`**

Add imports at the top:

```python
import logging

from flowsage_predict.narrative import FrictionSignal, NARRATIVE_MODEL, NarrativeClient

from flowsage_backend import insight_cache

logger = logging.getLogger(__name__)

NODE_INSIGHT_KIND = "node_intelligence"
```

Add the hash function (place near `MAX_RECOMMENDATIONS`):

```python
def node_insight_input_hash(drop_off_rate: float, friction_nodes: list[FrictionNode]) -> str:
    signal = {
        "drop_off_rate": round(drop_off_rate, 4),
        "friction": sorted(
            (
                {"kind": n.kind.value, "sessions_affected": n.sessions_affected}
                for n in friction_nodes
            ),
            key=lambda d: (d["kind"], d["sessions_affected"]),
        ),
    }
    return insight_cache.compute_input_hash(signal)
```

Modify `get_node_intelligence`'s final block to overlay a fresh cache hit:

```python
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
    intelligence = build_node_intelligence(screen, report, events)

    input_hash = node_insight_input_hash(intelligence.drop_off_rate, intelligence.friction_nodes)
    cached = await insight_cache.get_cached(session, workspace_id, NODE_INSIGHT_KIND, screen)
    if cached is not None and cached.input_hash == input_hash:
        intelligence.ai_insight = str(cached.payload["insight"])
        intelligence.recommendations = [
            Recommendation(rank=i + 1, **rec)  # type: ignore[arg-type]
            for i, rec in enumerate(cached.payload["recommendations"])
        ]
    return intelligence
```

Add the job-facing generation function at the end of the file:

```python
async def generate_and_cache_node_insight(
    session: AsyncSession, workspace_id: uuid.UUID, screen: str, narrative_client: NarrativeClient
) -> None:
    """Runs inside the arq worker (`generate_node_insight_job`) -- recomputes
    the current deterministic signal fresh (it may have drifted since the GET
    that triggered this job was served) and caches a real narrative under
    whatever hash that signal hashes to *now*. A stale trigger (data changed
    again before this job ran) just means the cache gets warmed under today's
    hash instead of the slightly older one that triggered it -- still
    correct, no wasted call."""
    events = await query_events(session, workspace_id)
    funnel = discover_funnel(events)
    if screen not in {step.screen for step in funnel}:
        return

    friction = detect_friction(events, funnel)
    nodes_here = [n for n in friction if n.screen == screen]
    step = next((s for s in funnel if s.screen == screen), None)
    drop_off_rate = step.drop_off_rate if step is not None else 0.0

    try:
        result = narrative_client.generate_node_insight(
            screen,
            drop_off_rate,
            [
                FrictionSignal(kind=n.kind.value, sessions_affected=n.sessions_affected)
                for n in nodes_here
            ],
        )
    except Exception:  # noqa: BLE001 - a narrative-generation failure must not
        # fail the whole background job; the GET path already has a template
        # fallback and simply won't see a fresh cache row.
        logger.warning("Node insight generation failed for screen %s", screen, exc_info=True)
        return

    input_hash = node_insight_input_hash(drop_off_rate, nodes_here)
    await insight_cache.upsert_cached(
        session,
        workspace_id,
        NODE_INSIGHT_KIND,
        screen,
        input_hash,
        {
            "insight": result.insight,
            "recommendations": [r.model_dump() for r in result.recommendations],
        },
        NARRATIVE_MODEL,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_churn.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean (the `# type: ignore[arg-type]` on the `Recommendation(**rec)` unpack is because `cached.payload["recommendations"]` is typed `object` from the JSONB column -- narrower than mypy can verify without a runtime check, matching this codebase's existing tolerance for one narrow `type: ignore` at a JSON-boundary, e.g. `worker.py`'s arq-protocol ignore)

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/churn.py backend/tests/test_churn.py
git commit -m "feat: wire Node Intelligence AI Insight to the narrative cache"
```

---

## Task 6: Calibration anomaly narrative wiring in `calibration.py`

**Files:**
- Modify: `backend/src/flowsage_backend/calibration.py`
- Modify: `backend/tests/test_calibration.py`

**Interfaces:**
- Consumes: `insight_cache` (Task 3), `flowsage_predict.narrative.{NarrativeClient,ScreenSignal,NARRATIVE_MODEL}` (Task 1).
- Produces: `CALIBRATION_NARRATIVE_KIND: str`, `calibration_input_hash(anomalies: list[ScreenCalibration]) -> str`, `generate_and_cache_calibration_narrative(session, workspace_id, persona_id, narrative_client) -> None`. `PersonaCalibration` gains `narrative: str | None = None`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_calibration.py` (inspect the existing file first for its exact fixture-building helpers and reuse them rather than duplicating -- it already builds a `SimulationRun` + `FrictionIssue` + `Event` set for calibration tests):

Add `CALIBRATION_NARRATIVE_KIND`, `calibration_input_hash`, `generate_and_cache_calibration_narrative`, and `ScreenCalibration` to the existing `from flowsage_backend.calibration import (...)` block (it currently imports `bucket_severity, build_calibration_report, build_screen_calibrations, latest_completed_run_for_persona, latest_completed_runs_by_persona, predicted_scores_by_screen` but not `ScreenCalibration`, which the new hash tests below construct directly), and add two new import lines:

```python
from flowsage_backend import insight_cache
from flowsage_predict.narrative import ScreenSignal


def test_calibration_input_hash_is_deterministic() -> None:
    anomalies = [
        ScreenCalibration(
            screen="checkout", predicted_score=0.2, observed_score=0.9, delta=0.7, anomaly=True
        )
    ]
    assert calibration_input_hash(anomalies) == calibration_input_hash(anomalies)


def test_calibration_input_hash_differs_for_different_scores() -> None:
    a = [
        ScreenCalibration(
            screen="checkout", predicted_score=0.2, observed_score=0.9, delta=0.7, anomaly=True
        )
    ]
    b = [
        ScreenCalibration(
            screen="checkout", predicted_score=0.2, observed_score=0.5, delta=0.3, anomaly=True
        )
    ]
    assert calibration_input_hash(a) != calibration_input_hash(b)


class _FakeCalibrationNarrativeClient:
    def generate_node_insight(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError

    def generate_calibration_narrative(
        self, persona_name: str, anomalies: list[ScreenSignal]
    ) -> str:
        return f"Narrative for {persona_name} across {len(anomalies)} screen(s)."

    def generate_retraining_rationale(self, *args: object, **kwargs: object) -> str:
        raise NotImplementedError
```

This file's `build_calibration_report` tests pass a synthetic `funnel: list[FunnelStep]` directly and never touch real `Event` rows -- but `generate_and_cache_calibration_narrative` re-derives its funnel from real ingested events itself (same reasoning as `retraining.execute_retraining`, which does the same thing), so this test needs real `Event` rows, following `test_retraining.py`'s event-seeding shape. Add these two extra imports at the top of the file: `from sqlalchemy import delete` and `from flowsage_backend.models.event import Event`.

```python
async def test_generate_and_cache_calibration_narrative_writes_cache_row(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    await _completed_run_with_issue(
        db_session, workspace_id, persona, screen="cal_narrative_checkout", severity="low"
    )

    session_ids = [f"cal-narrative-gen-{i}" for i in range(10)]
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen="cal_narrative_checkout",
                event="view",
                timestamp=now,
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen="cal_narrative_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
        )
    )
    await db_session.commit()

    try:
        await generate_and_cache_calibration_narrative(
            db_session, workspace_id, persona.id, _FakeCalibrationNarrativeClient()
        )
        cached = await insight_cache.get_cached(
            db_session, workspace_id, CALIBRATION_NARRATIVE_KIND, str(persona.id)
        )
        assert cached is not None
        assert "Narrative for" in cached.payload["narrative"]
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_calibration.py -v -k "calibration_input_hash or calibration_narrative"`
Expected: FAIL (names don't exist yet)

- [ ] **Step 3: Implement in `calibration.py`**

Add imports:

```python
import logging

from flowsage_graph.models import FunnelStep
from flowsage_predict.narrative import NARRATIVE_MODEL, NarrativeClient, ScreenSignal

from flowsage_backend import insight_cache
from flowsage_backend.events import query_events
from flowsage_backend.models.persona import Persona
from flowsage_backend.settings_store import get_or_create_calibration_settings

logger = logging.getLogger(__name__)

CALIBRATION_NARRATIVE_KIND = "calibration_anomaly"
```

(`flowsage_graph.funnel.discover_funnel` is already imported elsewhere in the file's tests but not in `calibration.py` itself yet -- add `from flowsage_graph.funnel import discover_funnel` too.)

Add `narrative` to `PersonaCalibration`:

```python
class PersonaCalibration(BaseModel):
    persona_id: str
    persona_name: str
    run_id: str
    screens: list[ScreenCalibration]
    narrative: str | None = None
```

Add the hash function near `_complexity`:

```python
def calibration_input_hash(anomalies: list[ScreenCalibration]) -> str:
    signal = {
        "screens": sorted(
            (
                {
                    "screen": a.screen,
                    "predicted_score": round(a.predicted_score, 4),
                    "observed_score": round(a.observed_score, 4),
                }
                for a in anomalies
            ),
            key=lambda d: str(d["screen"]),
        )
    }
    return insight_cache.compute_input_hash(signal)
```

Modify the loop body in `build_calibration_report` to overlay a fresh cache hit:

```python
    for run in runs:
        predicted = predicted_scores_by_screen(run.issues)
        if not predicted:
            continue

        screens = build_screen_calibrations(predicted, funnel, anomaly_threshold)
        anomalies = [s for s in screens if s.anomaly]
        if anomalies:
            has_anomaly = True

        narrative: str | None = None
        if anomalies:
            input_hash = calibration_input_hash(anomalies)
            cached = await insight_cache.get_cached(
                session, workspace_id, CALIBRATION_NARRATIVE_KIND, str(run.persona_id)
            )
            if cached is not None and cached.input_hash == input_hash:
                narrative = str(cached.payload["narrative"])

        personas.append(
            PersonaCalibration(
                persona_id=str(run.persona_id),
                persona_name=run.persona.name,
                run_id=str(run.id),
                screens=screens,
                narrative=narrative,
            )
        )
```

Add the job-facing generation function at the end of the file:

```python
async def generate_and_cache_calibration_narrative(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    persona_id: uuid.UUID,
    narrative_client: NarrativeClient,
) -> None:
    """Runs inside the arq worker (`generate_calibration_narrative_job`) --
    re-derives the persona's current anomalous screens fresh, same reasoning
    as `churn.generate_and_cache_node_insight`."""
    persona = await session.get(Persona, persona_id)
    run = await latest_completed_run_for_persona(session, workspace_id, persona_id)
    if persona is None or run is None:
        return

    predicted = predicted_scores_by_screen(run.issues)
    if not predicted:
        return

    events = await query_events(session, workspace_id)
    funnel: list[FunnelStep] = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, workspace_id)
    screens = build_screen_calibrations(predicted, funnel, settings.anomaly_threshold)
    anomalies = [s for s in screens if s.anomaly]
    if not anomalies:
        return

    try:
        narrative = narrative_client.generate_calibration_narrative(
            persona.name,
            [
                ScreenSignal(
                    screen=a.screen,
                    predicted_score=a.predicted_score,
                    observed_score=a.observed_score,
                    delta=a.delta,
                )
                for a in anomalies
            ],
        )
    except Exception:  # noqa: BLE001 - see generate_and_cache_node_insight
        logger.warning(
            "Calibration narrative generation failed for persona %s", persona_id, exc_info=True
        )
        return

    input_hash = calibration_input_hash(anomalies)
    await insight_cache.upsert_cached(
        session,
        workspace_id,
        CALIBRATION_NARRATIVE_KIND,
        str(persona_id),
        input_hash,
        {"narrative": narrative},
        NARRATIVE_MODEL,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_calibration.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 5: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/calibration.py backend/tests/test_calibration.py
git commit -m "feat: wire calibration anomaly narrative to the narrative cache"
```

---

## Task 7: Retraining rationale via narrative client

**Files:**
- Modify: `backend/src/flowsage_backend/retraining.py`
- Modify: `backend/tests/test_retraining.py`

**Interfaces:**
- Consumes: `flowsage_predict.narrative.{NarrativeClient,ScreenSignal,NARRATIVE_MODEL}` (Task 1), `insight_cache.upsert_cached` (Task 3), `billing.has_narrative_budget` (Task 4), `calibration.calibration_input_hash` (Task 6).
- Produces: `RETRAINING_RATIONALE_KIND: str` (distinct from `calibration.CALIBRATION_NARRATIVE_KIND` -- retraining rationale and calibration anomaly narrative are cached under different `kind`s, per the design spec's 3-way split), `execute_retraining(session, job_id, narrative_client: NarrativeClient | None = None) -> None` (new optional third parameter; existing 2-arg call sites keep working unchanged and always use the deterministic note).

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_retraining.py`:

```python
# add to imports
import logging
from unittest.mock import Mock

from flowsage_backend import insight_cache
from flowsage_backend.retraining import RETRAINING_RATIONALE_KIND
from flowsage_predict.narrative import ScreenSignal


class _FakeRetrainingNarrativeClient:
    def generate_node_insight(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError

    def generate_calibration_narrative(self, *args: object, **kwargs: object) -> str:
        raise NotImplementedError

    def generate_retraining_rationale(
        self,
        persona_name: str,
        anomalies: list[ScreenSignal],
        new_technical_literacy: float,
        new_anxiety: float,
        new_patience: float,
    ) -> str:
        return f"AI rationale for {persona_name}."
```

Add a test reusing this file's existing `test_execute_retraining_nudges_sliders_and_writes_memory` setup (same workspace/persona/events shape, but pass the fake client and assert the `PersonaMemory.note` is the AI-generated one):

```python
async def test_execute_retraining_uses_narrative_client_when_available(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cal_retrain_narrative_checkout",
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    session_ids = [f"cal-retrain-narrative-{i}" for i in range(10)]
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen="cal_retrain_narrative_checkout",
                event="view",
                timestamp=now,
                device="desktop",
                cohort="default",
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen="cal_retrain_narrative_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
            device="desktop",
            cohort="default",
        )
    )
    await db_session.commit()

    try:
        job = await create_retraining_job(db_session, persona.id, workspace_id=workspace_id)
        await execute_retraining(db_session, job.id, _FakeRetrainingNarrativeClient())

        result = await db_session.execute(
            select(PersonaMemory).where(PersonaMemory.persona_id == persona.id)
        )
        memory = result.scalars().one()
        assert memory.note == f"AI rationale for {persona.name}."

        cached = await insight_cache.get_cached(
            db_session, workspace_id, RETRAINING_RATIONALE_KIND, str(job.id)
        )
        assert cached is not None
        assert cached.payload["rationale"] == f"AI rationale for {persona.name}."
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()


async def test_execute_retraining_falls_back_to_deterministic_note_on_client_error(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cal_retrain_fail_checkout",
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    session_ids = [f"cal-retrain-fail-{i}" for i in range(10)]
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen="cal_retrain_fail_checkout",
                event="view",
                timestamp=now,
                device="desktop",
                cohort="default",
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen="cal_retrain_fail_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
            device="desktop",
            cohort="default",
        )
    )
    await db_session.commit()

    failing_client = Mock()
    failing_client.generate_retraining_rationale.side_effect = RuntimeError("boom")

    try:
        job = await create_retraining_job(db_session, persona.id, workspace_id=workspace_id)
        with caplog.at_level(logging.WARNING):
            await execute_retraining(db_session, job.id, failing_client)

        result = await db_session.execute(
            select(PersonaMemory).where(PersonaMemory.persona_id == persona.id)
        )
        memory = result.scalars().one()
        assert "Adjusted sliders after" in memory.note
        assert "Retraining rationale generation failed" in caplog.text
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_retraining.py -v -k "narrative_client"`
Expected: FAIL (`execute_retraining` doesn't accept a third argument yet)

- [ ] **Step 3: Implement in `retraining.py`**

Add imports:

```python
import logging

from flowsage_predict.narrative import NARRATIVE_MODEL, NarrativeClient, ScreenSignal

from flowsage_backend import billing, insight_cache
from flowsage_backend.calibration import calibration_input_hash

logger = logging.getLogger(__name__)

RETRAINING_RATIONALE_KIND = "retraining_rationale"
```

(this file already has `from flowsage_backend.calibration import (ScreenCalibration, build_screen_calibrations, latest_completed_run_for_persona, predicted_scores_by_screen,)` at the top -- add `calibration_input_hash` into that existing import line rather than a separate one.)

Change `execute_retraining`'s signature and the `if anomalies:` branch:

```python
async def execute_retraining(
    session: AsyncSession, job_id: uuid.UUID, narrative_client: NarrativeClient | None = None
) -> None:
    ...  # unchanged up through building `anomalies`
        if anomalies:
            new_literacy, new_anxiety, new_patience = nudge_sliders(persona, anomalies)
            persona.technical_literacy = new_literacy
            persona.anxiety = new_anxiety
            persona.patience = new_patience

            screens_summary = ", ".join(f"{a.screen} (delta {a.delta:+.2f})" for a in anomalies)
            note = (
                f"Adjusted sliders after {len(anomalies)} anomalous screen(s): "
                f"{screens_summary}. New sliders -- technical_literacy={new_literacy:.2f}, "
                f"anxiety={new_anxiety:.2f}, patience={new_patience:.2f}."
            )
            if narrative_client is not None and await billing.has_narrative_budget(
                session, job.workspace_id
            ):
                try:
                    generated_note = narrative_client.generate_retraining_rationale(
                        persona.name,
                        [
                            ScreenSignal(
                                screen=a.screen,
                                predicted_score=a.predicted_score,
                                observed_score=a.observed_score,
                                delta=a.delta,
                            )
                            for a in anomalies
                        ],
                        new_literacy,
                        new_anxiety,
                        new_patience,
                    )
                except Exception:  # noqa: BLE001 - a narrative failure must not
                    # flip this retraining job to FAILED; keep the deterministic
                    # note computed above.
                    logger.warning(
                        "Retraining rationale generation failed for job %s", job_id, exc_info=True
                    )
                else:
                    note = generated_note
                    await insight_cache.upsert_cached(
                        session,
                        job.workspace_id,
                        RETRAINING_RATIONALE_KIND,
                        str(job.id),
                        calibration_input_hash(anomalies),
                        {"rationale": note},
                        NARRATIVE_MODEL,
                    )
        else:
            note = "No screens exceeded the calibration anomaly threshold; sliders unchanged."
    ...  # unchanged: PersonaMemory(...) build, job COMPLETED, etc.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_retraining.py -v`
Expected: PASS (all existing + new tests, including the pre-existing 2-arg `execute_retraining(db_session, job.id)` call in `test_execute_retraining_nudges_sliders_and_writes_memory`, unchanged and still passing)

- [ ] **Step 5: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/retraining.py backend/tests/test_retraining.py
git commit -m "feat: use narrative client for retraining rationale"
```

---

## Task 8: Worker wiring — narrative client + 2 new background jobs

**Files:**
- Modify: `backend/src/flowsage_backend/worker.py`

**Interfaces:**
- Consumes: `flowsage_predict.narrative.AnthropicNarrativeClient` (Task 1), `churn.generate_and_cache_node_insight` (Task 5), `calibration.generate_and_cache_calibration_narrative` (Task 6).
- Produces: arq job functions `generate_node_insight_job(ctx, workspace_id: str, screen: str) -> None` and `generate_calibration_narrative_job(ctx, workspace_id: str, persona_id: str) -> None`, both registered in `WorkerSettings.functions`. `ctx["narrative_client"]` available to `run_retraining_job` too.

There is no isolated unit test for this task -- `_startup`/job dispatch wiring is exercised the same way `run_simulation_job`'s `ctx["vision_client"]` wiring already is in this codebase: never directly, only indirectly via Task 9's route-level tests (which enqueue but never execute the job in-process) and via the full-stack Docker smoke test in Task 10. The business logic these jobs call (`generate_and_cache_node_insight`, `generate_and_cache_calibration_narrative`, `execute_retraining`) already has direct unit test coverage from Tasks 5-7.

- [ ] **Step 1: Add the narrative client to worker imports and startup**

```python
from flowsage_predict.narrative import AnthropicNarrativeClient, NarrativeClient
from flowsage_predict.vision import AnthropicVisionClient, VisionClient

from flowsage_backend import churn
from flowsage_backend.calibration import generate_and_cache_calibration_narrative
```

(insert `from flowsage_predict.narrative import ...` alphabetically before the existing `from flowsage_predict.vision import ...` line; insert the two `flowsage_backend` imports alphabetically among the existing `from flowsage_backend...` import block)

```python
async def _startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    ctx["engine"] = engine
    ctx["session_factory"] = create_session_factory(engine)
    ctx["vision_client"] = AnthropicVisionClient()
    ctx["narrative_client"] = AnthropicNarrativeClient()
```

- [ ] **Step 2: Thread the narrative client into `run_retraining_job`**

```python
async def run_retraining_job(ctx: dict[str, Any], job_id: str) -> None:
    session_factory = ctx["session_factory"]
    narrative_client: NarrativeClient = ctx["narrative_client"]
    async with session_factory() as session:
        await execute_retraining(session, uuid.UUID(job_id), narrative_client)
```

- [ ] **Step 3: Add the two new job functions**

Place these near `run_retraining_job`:

```python
async def generate_node_insight_job(ctx: dict[str, Any], workspace_id: str, screen: str) -> None:
    session_factory = ctx["session_factory"]
    narrative_client: NarrativeClient = ctx["narrative_client"]
    async with session_factory() as session:
        await churn.generate_and_cache_node_insight(
            session, uuid.UUID(workspace_id), screen, narrative_client
        )


async def generate_calibration_narrative_job(
    ctx: dict[str, Any], workspace_id: str, persona_id: str
) -> None:
    session_factory = ctx["session_factory"]
    narrative_client: NarrativeClient = ctx["narrative_client"]
    async with session_factory() as session:
        await generate_and_cache_calibration_narrative(
            session, uuid.UUID(workspace_id), uuid.UUID(persona_id), narrative_client
        )
```

- [ ] **Step 4: Register both jobs in `WorkerSettings`**

```python
class WorkerSettings:
    functions = [
        run_simulation_job,
        run_retraining_job,
        generate_node_insight_job,
        generate_calibration_narrative_job,
    ]
    cron_jobs = [
        cron(run_digest_job, hour=9, minute=0),
        cron(run_retention_purge_job, hour=3, minute=0),
        cron(run_scheduled_simulations_job, minute=0),
    ]
    on_startup = _startup
    on_shutdown = _shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 5: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean

- [ ] **Step 6: Run the full backend test suite** (confirms nothing about the worker module's import graph broke; `worker.py` itself has no dedicated test file in this codebase, matching the existing pattern)

Run: `cd backend && uv run pytest -x -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/src/flowsage_backend/worker.py
git commit -m "feat: register narrative-generation background jobs in the worker"
```

---

## Task 9: Route wiring — enqueue background generation from the GET endpoints

**Files:**
- Modify: `backend/src/flowsage_backend/api/events.py`
- Modify: `backend/src/flowsage_backend/api/calibration.py`
- Modify: `backend/tests/test_churn_api.py`
- Modify: `backend/tests/test_calibration_api.py`

**Interfaces:**
- Consumes: `churn.node_insight_input_hash`, `churn.NODE_INSIGHT_KIND` (Task 5), `calibration.calibration_input_hash`, `calibration.CALIBRATION_NARRATIVE_KIND` (Task 6), `insight_cache.is_fresh` (Task 3), `billing.has_narrative_budget` (Task 4).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_churn_api.py`, following this file's existing `test_node_intelligence_returns_recommendations_for_friction_screen` pattern exactly (`_event`/`_authed_client` helpers, `ensure_default_workspace`/`create_api_key_for` from `.conftest`, all already imported at the top of this file):

```python
async def test_node_intelligence_response_includes_ai_insight_field(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)
    session_ids = [f"node-intel-narrative-{i}" for i in range(4)]
    events = [
        *[_event(session_ids[i], "landing", 0, "paid") for i in range(4)],
        *[_event(session_ids[i], "narrative_checkout", 1, "paid") for i in range(4)],
        _event(session_ids[0], "narrative_confirmation", 2, "paid"),
    ]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post(
                "/v1/events", json=events, headers={"X-API-Key": api_key}
            )
            assert ingest_response.status_code == 201

        async with _authed_client(app, db_session) as client:
            response = await client.get("/graph/nodes/narrative_checkout")

        assert response.status_code == 200
        body = response.json()
        # ai_insight is always populated (template fallback, since no cache
        # row exists yet) -- confirms the cache-lookup wiring didn't break
        # the existing deterministic path.
        assert body["ai_insight"]
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
```

Add to `backend/tests/test_calibration_api.py`, following this file's existing `test_get_calibration_report_flags_anomaly` pattern exactly (`_cal_api_workspace_id`/`_authed_client` helpers, all already imported at the top of this file):

```python
async def test_calibration_report_narrative_is_none_before_any_generation(
    app: FastAPI, db_session: AsyncSession
) -> None:
    workspace_id = await _cal_api_workspace_id(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    session_ids = [f"cal-narrative-report-{i}" for i in range(10)]

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="Checkout",
        goal="Complete purchase",
        persona_id=persona.id,
        screenshots_dir="/tmp/unused",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(
        FrictionIssue(
            workspace_id=workspace_id,
            run_id=run.id,
            screen="cal_narrative_report_checkout",
            severity="low",
            title="issue",
            heuristic_violated="",
            persona_impact="",
            description="",
            suggested_fix="",
        )
    )
    now = datetime.now(timezone.utc)
    for session_id in session_ids:
        db_session.add(
            Event(
                workspace_id=workspace_id,
                session_id=session_id,
                screen="cal_narrative_report_checkout",
                event="view",
                timestamp=now,
            )
        )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id=session_ids[0],
            screen="cal_narrative_report_confirmation",
            event="view",
            timestamp=datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc),
        )
    )
    await db_session.commit()

    try:
        async with _authed_client(app, db_session) as client:
            response = await client.get("/calibration/report")

        assert response.status_code == 200
        body = response.json()
        persona_body = next(p for p in body["personas"] if p["persona_id"] == str(persona.id))
        assert persona_body["narrative"] is None
    finally:
        await db_session.execute(delete(Event).where(Event.session_id.in_(session_ids)))
        await db_session.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_churn_api.py tests/test_calibration_api.py -v -k "narrative"`
Expected: FAIL (`narrative` key doesn't exist in the calibration response yet -- `ai_insight` test may already pass since that field predates this plan; the calibration one is the real signal here)

- [ ] **Step 3: Wire `api/events.py`**

Add `node_insight_input_hash`, `NODE_INSIGHT_KIND` to the existing `from flowsage_backend.churn import (...)` block, and add two new imports:

```python
from flowsage_backend import billing, insight_cache
from flowsage_backend.churn import (
    NODE_INSIGHT_KIND,
    ChurnRiskSegment,
    CohortComparisonReport,
    NodeIntelligence,
    build_churn_risk_segments,
    compare_cohorts,
    get_node_intelligence,
    node_insight_input_hash,
)
```

Modify the `node_intelligence` route to add a `request: Request` parameter and the enqueue check (`Request` is already imported in this file for `ingest`):

```python
@graph_router.get("/nodes/{screen}", response_model=NodeIntelligence)
async def node_intelligence(
    screen: str,
    request: Request,
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> NodeIntelligence:
    _, membership = membership_pair
    result = await get_node_intelligence(
        session, membership.workspace_id, screen, cohort=cohort, device=device, since=since
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"No funnel data for screen '{screen}'")

    input_hash = node_insight_input_hash(result.drop_off_rate, result.friction_nodes)
    is_fresh = await insight_cache.is_fresh(
        session, membership.workspace_id, NODE_INSIGHT_KIND, screen, input_hash
    )
    if not is_fresh and await billing.has_narrative_budget(session, membership.workspace_id):
        await request.app.state.arq_pool.enqueue_job(
            "generate_node_insight_job",
            str(membership.workspace_id),
            screen,
            _job_id=f"node-insight:{membership.workspace_id}:{screen}:{input_hash}",
        )
    return result
```

- [ ] **Step 4: Wire `api/calibration.py`**

Add imports:

```python
from flowsage_backend import billing, insight_cache
from flowsage_backend.calibration import (
    CALIBRATION_NARRATIVE_KIND,
    CalibrationReport,
    build_calibration_report,
    calibration_input_hash,
)
```

Modify `get_calibration_report`:

```python
@router.get("/report", response_model=CalibrationReport)
async def get_calibration_report(
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> CalibrationReport:
    _, membership = membership_pair
    events = await query_events(session, membership.workspace_id)
    funnel = discover_funnel(events)
    settings = await get_or_create_calibration_settings(session, membership.workspace_id)
    report = await build_calibration_report(
        session, membership.workspace_id, funnel, settings.anomaly_threshold
    )

    for persona in report.personas:
        anomalies = [s for s in persona.screens if s.anomaly]
        if not anomalies or persona.narrative is not None:
            continue
        input_hash = calibration_input_hash(anomalies)
        is_fresh = await insight_cache.is_fresh(
            session, membership.workspace_id, CALIBRATION_NARRATIVE_KIND, persona.persona_id, input_hash
        )
        if not is_fresh and await billing.has_narrative_budget(session, membership.workspace_id):
            await request.app.state.arq_pool.enqueue_job(
                "generate_calibration_narrative_job",
                str(membership.workspace_id),
                persona.persona_id,
                _job_id=f"cal-narrative:{membership.workspace_id}:{persona.persona_id}:{input_hash}",
            )
    return report
```

(`Request` is already imported in this file for `start_retraining`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_churn_api.py tests/test_calibration_api.py -v`
Expected: PASS (all existing + new tests)

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest -x -q`
Expected: PASS. Also update the exports-to-Slack/Jira tests in `test_churn_api.py` if they assert on exact `ai_insight` text that's unaffected by this change (they shouldn't be, since `get_node_intelligence`'s deterministic path is unchanged when there's no cache row).

- [ ] **Step 7: Type-check**

Run: `cd backend && uv run mypy --strict src`
Expected: clean

- [ ] **Step 8: Commit**

```bash
git add backend/src/flowsage_backend/api/events.py backend/src/flowsage_backend/api/calibration.py backend/tests/test_churn_api.py backend/tests/test_calibration_api.py
git commit -m "feat: enqueue background narrative generation from report GET endpoints"
```

---

## Task 10: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend suite + mypy + formatting**

Run:
```bash
cd backend && uv run pytest -q
cd backend && uv run mypy --strict src
cd backend && uv run autoflake8 --check src tests
cd backend && uv run black --check src tests
```
Expected: all clean

- [ ] **Step 2: Full flowsage-predict suite + mypy + formatting**

Run:
```bash
cd scripts/flowsage-predict && uv run pytest -q
cd scripts/flowsage-predict && uv run mypy --strict src
cd scripts/flowsage-predict && uv run autoflake8 --check src tests
cd scripts/flowsage-predict && uv run black --check src tests
```
Expected: all clean

- [ ] **Step 3: Migration upgrade -> downgrade -> upgrade cycle against a live Postgres** (re-verify after all model/route changes, not just Task 2's isolated check)

Run: `cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head`
Expected: exit 0, no errors

- [ ] **Step 4: Docker smoke test**

Run: `docker compose -f infra/docker-compose.yml up -d --build` (from repo root), then confirm the `backend` and `worker` containers both start cleanly (`docker compose logs worker` shows no import errors from the new `generate_node_insight_job`/`generate_calibration_narrative_job`/`ctx["narrative_client"]` wiring -- `AnthropicNarrativeClient()` construction only needs *some* `ANTHROPIC_API_KEY` string to succeed, which the compose stack's `backend_env` anchor already supplies via `.env`). Tear down with `docker compose -f infra/docker-compose.yml down` afterward.

- [ ] **Step 5: Update the design spec's status line**

In `docs/superpowers/specs/2026-08-04-ai-narrative-insights-design.md`, the "Architecture" section's synchronous `asyncio.to_thread`-inline description for Node Intelligence/calibration anomaly no longer matches what was built (background-enrichment was chosen instead, see this plan's Architecture section). Add a short note at the top of the spec pointing to this plan as the as-built source of truth, e.g.:

```markdown
**Note (implementation):** Node Intelligence and calibration-anomaly narrative generation ended up running via background arq jobs triggered from the GET endpoints (cache-miss enqueues, next GET picks up the result), not inline via `asyncio.to_thread` as originally drafted above -- this codebase never makes a live Claude call from a synchronous FastAPI handler. Retraining rationale is unchanged from the original design (already an async job). See `docs/superpowers/plans/2026-08-04-ai-narrative-insights.md` for the as-built architecture.
```

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/specs/2026-08-04-ai-narrative-insights-design.md
git commit -m "docs: note as-built background-enrichment architecture in the narrative insights spec"
```
