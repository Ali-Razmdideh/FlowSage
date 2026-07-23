# Phase 3 chunk 2: Integrations (API keys, Slack/Jira, webhooks) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared `events_api_key`/global Slack-Jira env vars with per-workspace
API keys and per-workspace Slack/Jira config, make the digest job iterate every workspace
instead of just `fs-default`, and add webhook subscriptions + a delivery log, all surfaced on
one new `/settings/integrations` page.

**Architecture:** Five new SQLAlchemy models (`ApiKey`, `SlackIntegration`,
`JiraIntegration`, `Webhook`, `WebhookDelivery`) behind one new FastAPI router
(`api/integrations.py`). `deps.require_workspace_api_key` replaces `deps.require_api_key`,
resolving `workspace_id` from a hashed key lookup instead of a shared secret. A new
`integrations_store.py` (mirrors `settings_store.py`) centralizes per-workspace Slack/Jira
config reads for the three existing export endpoints and the digest job.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async, Alembic, Argon2/sha256 (`security.py`), httpx
(webhook delivery, same idiom as `integrations/slack.py`), Postgres `ARRAY`, React 19 +
TypeScript, Vitest, Playwright.

## Global Constraints

- No backwards-compat shim for the old shared `events_api_key` — delete it and the global
  Slack/Jira env fields from `config.py` outright (pre-launch product, no external
  consumers yet; matches this repo's "change code directly, no compat branches" convention).
- Mutating `/settings/integrations/*` endpoints require `Role.ADMIN`
  (`Depends(require_role(Role.ADMIN))`); `GET`s only require membership.
- API keys: format `fs_live_<32 url-safe random chars>` via `secrets.token_urlsafe(32)`,
  hashed with sha256 (not Argon2 — see design spec's rationale), raw key shown exactly once.
- Webhook signing: `X-FlowSage-Signature: sha256=<hex hmac-sha256 of the raw JSON body>`.
- Every new table gets a `workspace_id` (or, for `WebhookDelivery`, scopes via
  `webhook_id` → `Webhook.workspace_id`) and must be covered by a cross-tenant isolation test.
- Backend: `cd backend && uv run pytest -q && uv run mypy --strict src/` must stay green
  after every task. Frontend: `cd frontend && npm run typecheck && npm run test && npm run
  lint` must stay green after every frontend task.
- Full spec: `docs/superpowers/specs/2026-07-23-integrations-design.md`.

---

## File Structure

**Backend — create:**
- `backend/src/flowsage_backend/models/api_key.py` — `ApiKey`
- `backend/src/flowsage_backend/models/integration.py` — `SlackIntegration`, `JiraIntegration`
- `backend/src/flowsage_backend/models/webhook.py` — `Webhook`, `WebhookDelivery`
- `backend/migrations/versions/<rev>_add_integrations_api_keys_webhooks.py`
- `backend/src/flowsage_backend/integrations_store.py` — per-workspace Slack/Jira config get/set/delete
- `backend/src/flowsage_backend/webhooks_store.py` — Webhook/WebhookDelivery CRUD + fan-out helper
- `backend/src/flowsage_backend/integrations/webhooks.py` — `deliver_webhook` (HMAC POST)
- `backend/src/flowsage_backend/api/integrations.py` — the `/settings/integrations` router
- `backend/tests/test_integrations_models.py`
- `backend/tests/test_integrations_api.py`
- `backend/tests/test_webhooks.py`

**Backend — modify:**
- `backend/src/flowsage_backend/models/__init__.py` — export the 5 new models
- `backend/src/flowsage_backend/security.py` — `generate_api_key`, `hash_api_key`
- `backend/src/flowsage_backend/deps.py` — `require_workspace_api_key` replaces `require_api_key`
- `backend/src/flowsage_backend/config.py` — delete `events_api_key`/`slack_webhook_url`/`jira_*`
- `backend/src/flowsage_backend/api/events.py` — ingest endpoint uses the new dependency; export-to-slack/jira reads `integrations_store`
- `backend/src/flowsage_backend/api/exports.py` — export-to-slack/jira reads `integrations_store`
- `backend/src/flowsage_backend/api/alerts.py` — digest-run reads `integrations_store`
- `backend/src/flowsage_backend/worker.py` — `run_digest_job` iterates every non-archived workspace + delivers to webhooks
- `backend/src/flowsage_backend/__main__.py` — `create-api-key` CLI command
- `backend/src/flowsage_backend/main.py` — register `integrations_router`
- `backend/tests/conftest.py` — `create_api_key_for` helper
- `backend/tests/test_events.py`, `test_alerts_api.py`, `test_churn_api.py`, `test_node_export_api.py` — use `create_api_key_for` instead of `app.state.settings.events_api_key`
- `backend/tests/test_workspace_isolation.py` — add API key / integration / webhook isolation cases

**Frontend — create:**
- `frontend/src/routes/settings/IntegrationsSettingsPage.tsx`
- `frontend/src/routes/settings/IntegrationsSettingsPage.test.tsx`
- `frontend/e2e/integrations-settings.spec.ts`

**Frontend — modify:**
- `frontend/src/lib/types.ts` — `ApiKey`, `Webhook`, `WebhookDelivery`, `SlackIntegrationStatus`, `JiraIntegrationStatus`
- `frontend/src/lib/api.ts` — client functions for all `/settings/integrations/*` endpoints
- `frontend/src/components/Sidebar.tsx` — nav entry
- `frontend/src/App.tsx` — route

---

### Task 1: Models + migration

**Files:**
- Create: `backend/src/flowsage_backend/models/api_key.py`
- Create: `backend/src/flowsage_backend/models/integration.py`
- Create: `backend/src/flowsage_backend/models/webhook.py`
- Modify: `backend/src/flowsage_backend/models/__init__.py`
- Create: `backend/migrations/versions/<rev>_add_integrations_api_keys_webhooks.py`
- Test: `backend/tests/test_integrations_models.py`

**Interfaces:**
- Produces: `ApiKey(id, workspace_id, name, key_prefix, key_hash, created_at, last_used_at, revoked_at)`, `SlackIntegration(id, workspace_id, webhook_url, connected_at)`, `JiraIntegration(id, workspace_id, base_url, email, api_token, project_key, connected_at)`, `Webhook(id, workspace_id, url, secret, event_types: list[str], enabled, created_at)`, `WebhookDelivery(id, webhook_id, event_type, payload, status_code, success, created_at)`. All importable from `flowsage_backend.models`.

- [ ] **Step 1: Write the failing model test**

```python
# backend/tests/test_integrations_models.py
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models import (
    ApiKey,
    JiraIntegration,
    SlackIntegration,
    Webhook,
    WebhookDelivery,
)
from flowsage_backend.models.workspace import Workspace


async def _make_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Models Test", slug=f"models-test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


async def test_api_key_round_trips(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    key = ApiKey(
        workspace_id=workspace_id,
        name="CI key",
        key_prefix="fs_live_ab12",
        key_hash="a" * 64,
    )
    db_session.add(key)
    await db_session.commit()

    result = await db_session.execute(select(ApiKey).where(ApiKey.workspace_id == workspace_id))
    fetched = result.scalar_one()
    assert fetched.name == "CI key"
    assert fetched.revoked_at is None
    assert fetched.last_used_at is None


async def test_slack_and_jira_integration_round_trip(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    db_session.add(SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/x"))
    db_session.add(
        JiraIntegration(
            workspace_id=workspace_id,
            base_url="https://acme.atlassian.net",
            email="bot@acme.test",
            api_token="tok",
            project_key="FS",
        )
    )
    await db_session.commit()

    slack = (
        await db_session.execute(select(SlackIntegration).where(SlackIntegration.workspace_id == workspace_id))
    ).scalar_one()
    jira = (
        await db_session.execute(select(JiraIntegration).where(JiraIntegration.workspace_id == workspace_id))
    ).scalar_one()
    assert slack.webhook_url == "https://hooks.slack.test/x"
    assert jira.project_key == "FS"


async def test_webhook_delivery_cascades_on_webhook_delete(db_session: AsyncSession) -> None:
    workspace_id = await _make_workspace(db_session)
    webhook = Webhook(
        workspace_id=workspace_id,
        url="https://example.test/hook",
        secret="s3cr3t",
        event_types=["alert.triggered"],
    )
    db_session.add(webhook)
    await db_session.commit()
    await db_session.refresh(webhook)

    db_session.add(
        WebhookDelivery(
            webhook_id=webhook.id,
            event_type="alert.triggered",
            payload="{}",
            status_code=200,
            success=True,
        )
    )
    await db_session.commit()

    await db_session.delete(webhook)
    await db_session.commit()

    remaining = await db_session.execute(select(WebhookDelivery))
    assert remaining.scalars().all() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest -q tests/test_integrations_models.py`
Expected: FAIL with `ImportError: cannot import name 'ApiKey'`.

- [ ] **Step 3: Write the models**

```python
# backend/src/flowsage_backend/models/api_key.py
"""Per-workspace API keys (`/settings/integrations`), replacing the single shared
`Settings.events_api_key` -- see the Phase 3 chunk 2 design spec. The raw key is
never stored; only its sha256 hash and a display prefix are."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

```python
# backend/src/flowsage_backend/models/integration.py
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
```

```python
# backend/src/flowsage_backend/models/webhook.py
"""Custom outbound webhook subscriptions + their delivery log (`/settings/integrations`).
v1 has exactly one event type, `"alert.triggered"`, fired by `worker.py`'s digest job --
see the design spec for why retries/backoff are explicitly out of scope."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ARRAY, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from flowsage_backend.models.base import Base


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(64))
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

Register in `models/__init__.py`: add the imports and append `"ApiKey"`,
`"SlackIntegration"`, `"JiraIntegration"`, `"Webhook"`, `"WebhookDelivery"` to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest -q tests/test_integrations_models.py`
Expected: `3 passed` (tables are created from ORM metadata by the `_tables_ready` fixture,
same as every other model — no migration needed for this test file to pass).

- [ ] **Step 5: Write the Alembic migration**

Run: `cd backend && uv run alembic revision -m "add integrations api keys webhooks"`. This
generates a file from `backend/migrations/script.py.mako` with the header (docstring,
`revision`/`down_revision`/`branch_labels`/`depends_on` using `typing.Union`, matching every
existing migration in `backend/migrations/versions/` — do not change that header style to
`from __future__ import annotations`/PEP 604 `|` unions, it must match the other migration
files) already filled in correctly. Leave that header exactly as generated, and replace only
the `upgrade()`/`downgrade()` bodies (currently `pass`) with:

```python
def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.create_index("ix_api_keys_workspace_id", "api_keys", ["workspace_id"])

    op.create_table(
        "slack_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_url", sa.String(length=500), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )

    op.create_table(
        "jira_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("api_token", sa.String(length=500), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )

    op.create_table(
        "webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("event_types", sa.ARRAY(sa.String()), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhooks_workspace_id", "webhooks", ["workspace_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("webhook_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["webhook_id"], ["webhooks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_table("jira_integrations")
    op.drop_table("slack_integrations")
    op.drop_table("api_keys")
```

Leave the auto-filled `revision`/`down_revision`/`Revision ID`/`Revises`/`Create Date`
values exactly as Alembic generated them — only the body (`upgrade`/`downgrade`) is
hand-written above; this migration is verified for real in Task 10's docker step, same as
every prior chunk's migrations (test fixtures build tables straight from ORM metadata, not
via Alembic — see `conftest.py`'s module docstring).

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/models/api_key.py \
        backend/src/flowsage_backend/models/integration.py \
        backend/src/flowsage_backend/models/webhook.py \
        backend/src/flowsage_backend/models/__init__.py \
        backend/migrations/versions/*_add_integrations_api_keys_webhooks.py \
        backend/tests/test_integrations_models.py
git commit -m "feat: add ApiKey, SlackIntegration, JiraIntegration, Webhook models + migration"
```

---

### Task 2: Per-workspace API keys replace the shared `events_api_key`

**Files:**
- Modify: `backend/src/flowsage_backend/security.py`
- Modify: `backend/src/flowsage_backend/deps.py`
- Modify: `backend/src/flowsage_backend/config.py`
- Modify: `backend/src/flowsage_backend/api/events.py`
- Modify: `backend/src/flowsage_backend/__main__.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/test_events.py`, `test_alerts_api.py`, `test_churn_api.py`, `test_node_export_api.py`

**Interfaces:**
- Consumes: `ApiKey` (Task 1).
- Produces: `security.generate_api_key() -> str`, `security.hash_api_key(raw: str) -> str`,
  `deps.require_workspace_api_key(request, session) -> uuid.UUID` (a FastAPI dependency),
  `conftest.create_api_key_for(session, workspace_id, name="test-key") -> str` (raw key).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_security.py`:

```python
from flowsage_backend.security import generate_api_key, hash_api_key


def test_generate_api_key_has_expected_prefix_and_is_random() -> None:
    key_a = generate_api_key()
    key_b = generate_api_key()
    assert key_a.startswith("fs_live_")
    assert key_a != key_b


def test_hash_api_key_is_deterministic_and_not_reversible() -> None:
    raw = generate_api_key()
    assert hash_api_key(raw) == hash_api_key(raw)
    assert hash_api_key(raw) != raw
```

Replace the whole body of `backend/tests/test_events.py`'s API-key-dependent tests (keep
everything else -- imports, `_event`, `test_funnel_*` -- as-is except where noted) with:

```python
# add to the imports at the top of test_events.py
from .conftest import create_api_key_for, ensure_default_workspace


async def test_ingest_rejects_wrong_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events",
            json=[_event("s1", "landing", 0)],
            headers={"X-API-Key": "wrong-key"},
        )

    assert response.status_code == 401


async def test_ingest_stores_events_in_postgres(app: FastAPI, db_session: AsyncSession) -> None:
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)
    events = [_event("s1", "landing", 0), _event("s1", "cart", 1)]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/v1/events", json=events, headers={"X-API-Key": api_key})

    assert response.status_code == 201
    assert response.json() == {"ingested": 2}

    result = await db_session.execute(select(Event).where(Event.session_id == "s1"))
    rows = result.scalars().all()
    assert {r.screen for r in rows} == {"landing", "cart"}


async def test_ingest_continues_when_neo4j_unreachable(app: FastAPI, db_session: AsyncSession) -> None:
    """The default `app` fixture points at an unreachable Neo4j -- ingestion into
    Postgres must still succeed (best-effort mirroring, matching flowsage-graph's
    own CLI resilience pattern)."""
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events", json=[_event("s1", "landing", 0)], headers={"X-API-Key": api_key}
        )

    assert response.status_code == 201


async def test_ingest_actually_writes_to_neo4j(
    app_with_real_neo4j: FastAPI, db_session: AsyncSession, neo4j_credentials: tuple[str, str, str]
) -> None:
    workspace_id = await ensure_default_workspace(db_session)
    api_key = await create_api_key_for(db_session, workspace_id)
    events = [_event("s1", "landing", 0), _event("s1", "cart", 1)]

    async with AsyncClient(
        transport=ASGITransport(app=app_with_real_neo4j), base_url="http://test"
    ) as client:
        response = await client.post("/v1/events", json=events, headers={"X-API-Key": api_key})

    assert response.status_code == 201

    uri, user, password = neo4j_credentials
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session() as session:
        record = session.run(
            "MATCH (a:Screen {name: 'landing'})-[t:TRANSITION]->(b:Screen {name: 'cart'}) "
            "RETURN t.session_id AS session_id"
        ).single()
    driver.close()

    assert record is not None
    assert record["session_id"] == "s1"
```

Also delete the old `test_ingest_requires_api_key` test (a missing `X-API-Key` header still
401s under the new dependency too, but there's no `app.state.settings.events_api_key` left
to reference — `test_ingest_rejects_wrong_api_key` above already covers "no valid key").

For `test_funnel_discovers_path_and_friction` and `test_funnel_filters_by_cohort` (further
down in the same file), replace their `api_key = app.state.settings.events_api_key` line
with `workspace_id = await ensure_default_workspace(db_session); api_key = await
create_api_key_for(db_session, workspace_id)`.

Apply the identical replacement (`workspace_id = await ensure_default_workspace(db_session)`
then `api_key = await create_api_key_for(db_session, workspace_id)`, adding
`from .conftest import create_api_key_for` to the existing `from .conftest import
ensure_default_workspace, login_to_default_workspace` import line) to the API-key-minting
lines in `test_alerts_api.py`, `test_churn_api.py` (3 call sites), and
`test_node_export_api.py` (2 call sites) — every one of those already imports
`ensure_default_workspace` or can call it fresh (it's idempotent: get-or-create).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest -q tests/test_security.py tests/test_events.py`
Expected: FAIL — `generate_api_key`/`hash_api_key`/`create_api_key_for` don't exist yet.

- [ ] **Step 3: Implement**

Add to `backend/src/flowsage_backend/security.py` (below the existing password-hashing
functions, above `create_access_token`):

```python
import hashlib
import secrets as _secrets


def generate_api_key() -> str:
    return f"fs_live_{_secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
```

(Note: `security.py` already has `import uuid` etc. at the top; add `import hashlib` and
`import secrets as _secrets` there instead of inline — `_secrets` avoids shadowing
`deps.py`'s own `import secrets`, which is a separate module-level name so no collision
actually exists across files, but keep the aliased import for clarity that this key
generation is independent of `deps.py`'s now-unused `secrets.compare_digest` call.)

Replace `deps.py`'s `require_api_key` function entirely with:

```python
from datetime import datetime, timezone

from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.security import hash_api_key


async def require_workspace_api_key(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> uuid.UUID:
    provided = request.headers.get("X-API-Key")
    if provided is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")

    result = await session.execute(
        select(ApiKey).where(ApiKey.key_hash == hash_api_key(provided))
    )
    api_key = result.scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid X-API-Key")

    api_key.last_used_at = datetime.now(timezone.utc)
    await session.commit()
    return api_key.workspace_id
```

Add `import uuid` to `deps.py`'s imports if not already present (it is not currently
imported there), and drop the now-unused `import secrets` line from `deps.py`.

In `backend/src/flowsage_backend/api/events.py`:
- Delete the `_default_workspace_id` function entirely.
- Change the import line `from flowsage_backend.deps import get_current_membership,
  get_db_session, require_api_key` to `from flowsage_backend.deps import
  get_current_membership, get_db_session, require_workspace_api_key`.
- Change `events_router = APIRouter(prefix="/v1/events", tags=["events"],
  dependencies=[Depends(require_api_key)])` to `events_router = APIRouter(prefix="/v1/events",
  tags=["events"])` (dependency moves to the endpoint itself, since it now needs to return a
  value, not just gate access).
- Change the `ingest` endpoint to:

```python
@events_router.post("", response_model=IngestResult, status_code=201)
async def ingest(
    payload: list[EventIn],
    request: Request,
    workspace_id: uuid.UUID = Depends(require_workspace_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> IngestResult:
    graph_events = [GraphEvent.model_validate(e.model_dump()) for e in payload]
    rows = await ingest_events(session, workspace_id, graph_events)

    graph_sink = request.app.state.graph_sink
    try:
        await asyncio.to_thread(graph_sink.ingest, graph_events, str(workspace_id))
    except Exception:  # noqa: BLE001 - Neo4j being unreachable shouldn't fail ingestion
        logger.warning(
            "Neo4j ingestion failed; events were still stored in Postgres", exc_info=True
        )

    return IngestResult(ingested=len(rows))
```
- Remove the now-unused `from sqlalchemy import select` and `Workspace` (keep `Membership`)
  imports at the top of the file — both were only used by `_default_workspace_id`.

In `backend/src/flowsage_backend/config.py`, delete the `events_api_key`,
`slack_webhook_url`, `jira_base_url`, `jira_email`, `jira_api_token`, `jira_project_key`
fields and the `_PLACEHOLDER_EVENTS_API_KEY` constant, and remove
`"EVENTS_API_KEY": self.events_api_key == _PLACEHOLDER_EVENTS_API_KEY,` from
`_reject_placeholder_secret_outside_dev`'s `placeholders` dict (leaving just `"JWT_SECRET"`).

In `backend/tests/conftest.py`, add (near `ensure_default_workspace`):

```python
from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.security import generate_api_key, hash_api_key


async def create_api_key_for(
    session: AsyncSession, workspace_id: uuid.UUID, name: str = "test-key"
) -> str:
    """Mints a real `ApiKey` row and returns the raw key, for tests that need to
    authenticate `POST /v1/events` -- replaces the old shared `events_api_key`."""
    raw_key = generate_api_key()
    session.add(
        ApiKey(
            workspace_id=workspace_id,
            name=name,
            key_prefix=raw_key[:12],
            key_hash=hash_api_key(raw_key),
        )
    )
    await session.commit()
    return raw_key
```

In `backend/src/flowsage_backend/__main__.py`, add a `create-api-key` subcommand:

```python
from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.security import generate_api_key, hash_api_key


async def _create_api_key(workspace_slug: str, name: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    async with session_factory() as session:
        result = await session.execute(select(Workspace).where(Workspace.slug == workspace_slug))
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise SystemExit(f"No workspace with slug {workspace_slug!r}.")
        raw_key = generate_api_key()
        session.add(
            ApiKey(workspace_id=workspace.id, name=name, key_prefix=raw_key[:12], key_hash=hash_api_key(raw_key))
        )
        await session.commit()
    await engine.dispose()
    print(f"API key created for workspace {workspace_slug!r}: {raw_key}")
    print("Store it now -- it will not be shown again.")
```

Wire it into `main()`:

```python
create_api_key_parser = subparsers.add_parser(
    "create-api-key", help="Create a POST /v1/events API key for a workspace"
)
create_api_key_parser.add_argument("workspace_slug")
create_api_key_parser.add_argument("name")
```

```python
if args.command == "create-api-key":
    asyncio.run(_create_api_key(args.workspace_slug, args.name))
    return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass (145 pre-existing + the new security tests). If a test still fails on
`app.state.settings.events_api_key`, grep for it — `grep -rn events_api_key backend/` must
return nothing anywhere in `backend/` once this task is done.

- [ ] **Step 5: Typecheck**

Run: `cd backend && uv run mypy --strict src/`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/security.py backend/src/flowsage_backend/deps.py \
        backend/src/flowsage_backend/config.py backend/src/flowsage_backend/api/events.py \
        backend/src/flowsage_backend/__main__.py backend/tests/conftest.py \
        backend/tests/test_security.py backend/tests/test_events.py \
        backend/tests/test_alerts_api.py backend/tests/test_churn_api.py \
        backend/tests/test_node_export_api.py
git commit -m "feat: per-workspace API keys replace the shared events_api_key"
```

---

### Task 3: Per-workspace Slack/Jira config replaces global env vars

**Files:**
- Create: `backend/src/flowsage_backend/integrations_store.py`
- Modify: `backend/src/flowsage_backend/api/events.py` (export-to-slack/jira endpoints)
- Modify: `backend/src/flowsage_backend/api/exports.py`
- Modify: `backend/src/flowsage_backend/api/alerts.py`
- Test: extend `backend/tests/test_node_export_api.py`, `test_exports_api.py`, `test_alerts_api.py`

**Interfaces:**
- Consumes: `SlackIntegration`, `JiraIntegration` (Task 1).
- Produces: `integrations_store.get_slack_integration(session, workspace_id) ->
  SlackIntegration | None`, `integrations_store.get_jira_integration(session, workspace_id)
  -> JiraIntegration | None`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_node_export_api.py` (which already has
`test_export_node_to_slack_returns_400_when_not_configured` — keep that test as-is, it
still passes unmodified since no `SlackIntegration` row exists by default):

```python
async def test_export_node_to_slack_succeeds_with_workspace_integration(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Plants a `SlackIntegration` row on the shared "fs-default" workspace --
    same workspace every other test in this file uses via `login_to_default_workspace`
    -- so it MUST delete that row again afterward (`finally`), same "mutate shared
    state, restore in finally" convention `test_worker.py`'s `_force_due` already
    uses for `CalibrationSettings`. Leaving it in place would make "fs-default" look
    permanently Slack-connected to every later test in the suite, including
    `test_worker.py`'s "skips silently when not configured" digest test."""
    from sqlalchemy import select

    from flowsage_backend.models.integration import SlackIntegration

    workspace_id = await ensure_default_workspace(db_session)
    integration = SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/x")
    db_session.add(integration)
    await db_session.commit()

    try:
        api_key = await create_api_key_for(db_session, workspace_id)
        session_ids = [f"node-export-slack-{i}" for i in range(4)]
        events = [
            *[_event(session_ids[i], "landing", 0) for i in range(4)],
            *[_event(session_ids[i], "checkout", 1) for i in range(4)],
        ]

        import respx
        from httpx import Response

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            ingest_response = await client.post("/v1/events", json=events, headers={"X-API-Key": api_key})
            assert ingest_response.status_code == 201

        with respx.mock:
            respx.post("https://hooks.slack.test/x").mock(return_value=Response(200, json={"ok": True}))
            async with _authed_client(app, db_session) as client:
                response = await client.post("/graph/nodes/checkout/export/slack")

        assert response.status_code == 200
    finally:
        result = await db_session.execute(
            select(SlackIntegration).where(SlackIntegration.workspace_id == workspace_id)
        )
        stale = result.scalar_one_or_none()
        if stale is not None:
            await db_session.delete(stale)
            await db_session.commit()
```

This introduces `respx` as a test-only HTTP mock for `httpx.AsyncClient` calls made without
an explicit `transport=` (the production code path). Add it to
`backend/pyproject.toml`'s dev dependency group (`respx>=0.21`) instead of threading a
`transport` parameter through three call sites and the digest job — the existing
`integrations/slack.py`/`jira.py` already accept an optional `transport` for direct unit
tests, but the endpoint/job code that calls them doesn't expose one, and adding that plumbing
purely for this one test isn't worth it when `respx` intercepts at the transport layer
globally for the duration of the `with respx.mock:` block.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv add --dev respx && uv run pytest -q tests/test_node_export_api.py::test_export_node_to_slack_succeeds_with_workspace_integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowsage_backend.models.integration'`.

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/integrations_store.py
"""Per-workspace Slack/Jira config lookup, replacing the global `Settings.slack_webhook_url`/
`jira_*` env vars. Mirrors `settings_store.py`'s shape, but returns `None` rather than
lazily creating a row -- "not configured" is a real, common state here (most workspaces
won't connect Slack/Jira), unlike `CalibrationSettings` which every workspace has."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.integration import JiraIntegration, SlackIntegration


async def get_slack_integration(
    session: AsyncSession, workspace_id: uuid.UUID
) -> SlackIntegration | None:
    result = await session.execute(
        select(SlackIntegration).where(SlackIntegration.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def get_jira_integration(
    session: AsyncSession, workspace_id: uuid.UUID
) -> JiraIntegration | None:
    result = await session.execute(
        select(JiraIntegration).where(JiraIntegration.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()
```

In `api/events.py`'s `export_node_to_slack`, replace:

```python
    settings = request.app.state.settings
    text = f"Friction node `{screen}`: {intel.ai_insight}"
    try:
        await post_slack_message(settings.slack_webhook_url, text=text)
```

with:

```python
    integration = await get_slack_integration(session, membership.workspace_id)
    text = f"Friction node `{screen}`: {intel.ai_insight}"
    try:
        await post_slack_message(integration.webhook_url if integration else None, text=text)
```

and add `from flowsage_backend.integrations_store import get_jira_integration,
get_slack_integration` to its imports. Apply the analogous change to `export_node_to_jira`
in the same file:

```python
    integration = await get_jira_integration(session, membership.workspace_id)
    try:
        issue_key = await create_jira_issue(
            base_url=integration.base_url if integration else None,
            email=integration.email if integration else None,
            api_token=integration.api_token if integration else None,
            project_key=integration.project_key if integration else None,
            summary=f"[FlowSage] Friction node: {screen}",
            description=intel.ai_insight,
        )
```

Check `integrations/jira.py`'s `create_jira_issue` signature accepts `str | None` for these
four parameters and raises `JiraNotConfiguredError` when any is `None` (it already does --
this is exactly the same shape `_get_settings`-based calls used before, just sourced from
`integration` instead of `settings`). Both endpoints can now drop `request: Request` from
their signature if `request.app.state.settings` was its only use — check each function body
before removing the parameter.

In `backend/src/flowsage_backend/api/exports.py`, add `from
flowsage_backend.integrations_store import get_jira_integration, get_slack_integration` to
the imports, then replace `export_issue_to_slack`'s body from `settings =
request.app.state.settings` onward with:

```python
    integration = await get_slack_integration(session, membership.workspace_id)
    text = f"*{issue.severity.upper()}* friction on `{issue.screen}`: {issue.title}"
    try:
        await post_slack_message(integration.webhook_url if integration else None, text=text)
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return SlackExportResult()
```

and drop that endpoint's now-unused `request: Request` parameter. Replace
`export_issue_to_jira`'s body from `settings = request.app.state.settings` onward with:

```python
    integration = await get_jira_integration(session, membership.workspace_id)
    try:
        issue_key = await create_jira_issue(
            base_url=integration.base_url if integration else None,
            email=integration.email if integration else None,
            api_token=integration.api_token if integration else None,
            project_key=integration.project_key if integration else None,
            summary=f"[FlowSage] {issue.title}",
            description=f"{issue.description}\n\nSuggested fix: {issue.suggested_fix}",
        )
    except JiraNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except JiraDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return JiraExportResult(issue_key=issue_key)
```

and drop that endpoint's `request: Request` parameter too.

In `backend/src/flowsage_backend/api/alerts.py`, add the same `integrations_store` import,
then replace `run_digest_now`'s body from `settings = request.app.state.settings` onward
with:

```python
    integration = await get_slack_integration(session, membership.workspace_id)
    report = await build_alerts_report(session, membership.workspace_id)
    try:
        await post_slack_message(
            integration.webhook_url if integration else None,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except SlackNotConfiguredError as exc:
        raise HTTPException(400, str(exc)) from exc
    except SlackDeliveryError as exc:
        raise HTTPException(502, str(exc)) from exc
    return DigestResult()
```

drop its `request: Request` parameter (only reorder if `build_alerts_report` was called
before the `settings` line originally — confirm the report is still built before the
try/except in your edit, matching the order shown above).

In `backend/tests/test_exports_api.py`, delete the line `assert app.state.settings
.slack_webhook_url is None` from `test_export_issue_to_slack_returns_400_when_not_configured`
— "not configured" is now simply the absence of a `SlackIntegration` row, which is already
the default state for a fresh test workspace, so there is nothing left to assert about a
deleted `Settings` field. Make the same deletion in `test_digest_run_returns_400_when_slack
_not_configured` in `backend/tests/test_alerts_api.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass, including the new respx-backed test.

- [ ] **Step 5: Typecheck**

Run: `cd backend && uv run mypy --strict src/`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src/flowsage_backend/integrations_store.py \
        backend/src/flowsage_backend/api/events.py backend/src/flowsage_backend/api/exports.py \
        backend/src/flowsage_backend/api/alerts.py backend/tests/test_node_export_api.py \
        backend/tests/test_exports_api.py backend/tests/test_alerts_api.py
git commit -m "feat: per-workspace Slack/Jira config replaces global env vars"
```

---

### Task 4: Webhook delivery mechanics

**Files:**
- Create: `backend/src/flowsage_backend/integrations/webhooks.py`
- Create: `backend/src/flowsage_backend/webhooks_store.py`
- Test: `backend/tests/test_webhooks.py`

**Interfaces:**
- Consumes: `Webhook`, `WebhookDelivery` (Task 1).
- Produces: `integrations.webhooks.deliver_webhook(url, secret, event_type, payload:
  dict[str, object], *, transport=None) -> tuple[int | None, bool]`,
  `webhooks_store.record_delivery(session, webhook_id, event_type, payload_dict,
  status_code, success) -> WebhookDelivery`, `webhooks_store.list_deliveries(session,
  webhook_id, limit=50) -> list[WebhookDelivery]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_webhooks.py
from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.integrations.webhooks import deliver_webhook
from flowsage_backend.models.webhook import Webhook
from flowsage_backend.webhooks_store import list_deliveries, record_delivery


async def test_deliver_webhook_signs_body_and_returns_success() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["signature"] = request.headers["X-FlowSage-Signature"]
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    status_code, success = await deliver_webhook(
        "https://example.test/hook",
        secret="s3cr3t",
        event_type="alert.triggered",
        payload={"calibration_alerts": [], "churn_alerts": []},
        transport=transport,
    )

    assert status_code == 200
    assert success is True
    expected_signature = "sha256=" + hmac.new(
        b"s3cr3t", captured["body"], hashlib.sha256
    ).hexdigest()
    assert captured["signature"] == expected_signature


async def test_deliver_webhook_reports_failure_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    transport = httpx.MockTransport(handler)
    status_code, success = await deliver_webhook(
        "https://example.test/hook",
        secret="s3cr3t",
        event_type="alert.triggered",
        payload={},
        transport=transport,
    )

    assert status_code == 500
    assert success is False


async def test_deliver_webhook_handles_connection_error_without_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    transport = httpx.MockTransport(handler)
    status_code, success = await deliver_webhook(
        "https://example.test/hook",
        secret="s3cr3t",
        event_type="alert.triggered",
        payload={},
        transport=transport,
    )

    assert status_code is None
    assert success is False


async def test_record_and_list_deliveries(db_session: AsyncSession) -> None:
    import uuid

    from flowsage_backend.models.workspace import Workspace

    workspace = Workspace(name="Webhook Store Test", slug=f"webhook-store-{uuid.uuid4().hex[:8]}")
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)

    webhook = Webhook(
        workspace_id=workspace.id,
        url="https://example.test/hook",
        secret="s3cr3t",
        event_types=["alert.triggered"],
    )
    db_session.add(webhook)
    await db_session.commit()
    await db_session.refresh(webhook)

    await record_delivery(db_session, webhook.id, "alert.triggered", {"x": 1}, 200, True)
    await record_delivery(db_session, webhook.id, "alert.triggered", {"x": 2}, 500, False)

    deliveries = await list_deliveries(db_session, webhook.id)
    assert len(deliveries) == 2
    assert deliveries[0].success is False  # newest first
    assert json.loads(deliveries[0].payload) == {"x": 2}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest -q tests/test_webhooks.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flowsage_backend.integrations.webhooks'`.

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/integrations/webhooks.py
"""Outbound delivery for custom webhook subscriptions (`/settings/integrations`).
Same "never raise, let the caller log a delivery row either way" contract as
`slack.py`'s `post_slack_message` almost has -- except here failure is an expected,
routine outcome (a user's endpoint being down shouldn't be an exception the digest
job has to catch), so this returns a `(status_code, success)` tuple instead of raising."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx


async def deliver_webhook(
    url: str,
    *,
    secret: str,
    event_type: str,
    payload: dict[str, object],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[int | None, bool]:
    body = json.dumps({"event": event_type, "data": payload}).encode("utf-8")
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    try:
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post(
                url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-FlowSage-Signature": signature,
                },
            )
    except httpx.HTTPError:
        return None, False

    success = 200 <= response.status_code < 300
    return response.status_code, success
```

```python
# backend/src/flowsage_backend/webhooks_store.py
"""CRUD + delivery-log helpers for `Webhook`/`WebhookDelivery`."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.webhook import Webhook, WebhookDelivery


async def record_delivery(
    session: AsyncSession,
    webhook_id: uuid.UUID,
    event_type: str,
    payload: dict[str, object],
    status_code: int | None,
    success: bool,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        webhook_id=webhook_id,
        event_type=event_type,
        payload=json.dumps(payload),
        status_code=status_code,
        success=success,
    )
    session.add(delivery)
    await session.commit()
    await session.refresh(delivery)
    return delivery


async def list_deliveries(
    session: AsyncSession, webhook_id: uuid.UUID, limit: int = 50
) -> list[WebhookDelivery]:
    result = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def list_enabled_webhooks_for_event(
    session: AsyncSession, workspace_id: uuid.UUID, event_type: str
) -> list[Webhook]:
    result = await session.execute(
        select(Webhook).where(
            Webhook.workspace_id == workspace_id,
            Webhook.enabled.is_(True),
        )
    )
    return [w for w in result.scalars().all() if event_type in w.event_types]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest -q tests/test_webhooks.py`
Expected: `4 passed`.

- [ ] **Step 5: Typecheck**

Run: `cd backend && uv run mypy --strict src/`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/integrations/webhooks.py \
        backend/src/flowsage_backend/webhooks_store.py backend/tests/test_webhooks.py
git commit -m "feat: HMAC-signed webhook delivery + delivery-log store"
```

---

### Task 5: `run_digest_job` iterates every workspace + delivers to webhooks

**Files:**
- Modify: `backend/src/flowsage_backend/worker.py`
- Test: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `integrations_store.get_slack_integration` (Task 3),
  `webhooks_store.list_enabled_webhooks_for_event`, `webhooks_store.record_delivery`
  (Task 4), `integrations.webhooks.deliver_webhook` (Task 4).

**Context the implementer needs before touching `test_worker.py`:** its four existing tests
(`test_run_digest_job_skips_silently_when_slack_not_configured`,
`test_run_digest_job_posts_when_slack_configured`, `test_run_digest_job_skips_send_when_not_due`,
`test_run_digest_job_auto_retrains_anomalous_personas`) all monkeypatch
`worker_module.get_settings` and/or read `settings.slack_webhook_url` — both go away in this
task, since Slack config now comes from `integrations_store.get_slack_integration`, not
`Settings`. They also all pass `ctx = {"session_factory": lambda: db_session, "redis":
_FakeRedis()}` — one single shared session reused across calls — and only ever seed
`ensure_default_workspace`'s one workspace, implicitly assuming it's the *only* workspace
`run_digest_job` will touch. Since Postgres is a **session-scoped** testcontainer (see
`conftest.py`'s `postgres_url` fixture), by the time `test_worker.py` runs, dozens of other
workspaces already exist from every other test file that ran earlier in the same `pytest`
invocation — and the new per-workspace-iterating `run_digest_job` will process every one of
them (any without a `CalibrationSettings` row gets one lazily created, due-by-default).
That's fine and correct in production, but it means these tests can no longer assert "the
`_fake_post`/`_fake_deliver` was called with exactly these arguments" (some other leftover
workspace could be processed too) — they must assert "our workspace's expected call is
present among however many calls happened", by having fakes append to a list instead of
overwrite a single dict.

- [ ] **Step 1: Rewrite the existing tests for per-workspace config + list-based capture**

Replace `test_worker.py`'s four existing test functions with:

```python
async def test_run_digest_job_skips_silently_when_slack_not_configured(
    db_session: AsyncSession,
) -> None:
    """No SlackIntegration row for fs-default is the default state -- nothing to
    configure here. Must not raise even though other leftover workspaces from
    earlier test files may or may not have Slack configured either."""
    calibration_settings, original_last_sent = await _force_due(db_session)
    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)  # must not raise
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()


async def test_run_digest_job_posts_when_slack_configured(db_session: AsyncSession) -> None:
    from flowsage_backend.models.integration import SlackIntegration

    workspace_id = await ensure_default_workspace(db_session)
    integration = SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/x")
    db_session.add(integration)
    await db_session.commit()

    posted: list[tuple[str | None, str]] = []

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        posted.append((webhook_url, text))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    calibration_settings, original_last_sent = await _force_due(db_session)
    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)

        assert ("https://hooks.slack.test/x", None) not in [(url, None) for url, _ in posted]
        matches = [text for url, text in posted if url == "https://hooks.slack.test/x"]
        assert len(matches) == 1
        assert "FlowSage Digest" in matches[0]
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()
        await db_session.delete(integration)
        await db_session.commit()
        monkeypatch.undo()


async def test_run_digest_job_skips_send_when_not_due(db_session: AsyncSession) -> None:
    """A weekly-frequency settings row with a `digest_last_sent_at` from moments ago
    is not due yet -- must not have posted for *our* workspace specifically (other
    leftover workspaces being due or not is out of scope for this assertion)."""
    from flowsage_backend.models.integration import SlackIntegration

    workspace_id = await ensure_default_workspace(db_session)
    integration = SlackIntegration(workspace_id=workspace_id, webhook_url="https://hooks.slack.test/not-due")
    db_session.add(integration)
    await db_session.commit()

    calibration_settings = await get_or_create_calibration_settings(db_session, workspace_id)
    original_last_sent = calibration_settings.digest_last_sent_at
    calibration_settings.digest_last_sent_at = datetime.now(timezone.utc)
    await db_session.commit()

    posted: list[tuple[str | None, str]] = []

    async def _fake_post(webhook_url: str | None, *, text: str, blocks: object = None) -> None:
        posted.append((webhook_url, text))

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "post_slack_message", _fake_post)

    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)
        assert all(url != "https://hooks.slack.test/not-due" for url, _ in posted)
    finally:
        calibration_settings.digest_last_sent_at = original_last_sent
        await db_session.commit()
        await db_session.delete(integration)
        await db_session.commit()
        monkeypatch.undo()


async def test_run_digest_job_auto_retrains_anomalous_personas(db_session: AsyncSession) -> None:
    workspace_id = await ensure_default_workspace(db_session)
    calibration_settings = await get_or_create_calibration_settings(db_session, workspace_id)
    original_auto_retrain = calibration_settings.auto_retrain_on_anomaly
    calibration_settings.auto_retrain_on_anomaly = True
    await db_session.commit()

    persona = Persona(
        workspace_id=workspace_id,
        slug=f"worker-autoretrain-{uuid.uuid4().hex[:8]}",
        name="Worker Autoretrain Persona",
        description="d",
        tech_affinity="low",
        primary_device="mobile",
        discovery_mode="search",
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    db_session.add(persona)
    await db_session.commit()
    await db_session.refresh(persona)

    async def _fake_alerts_report(session: AsyncSession, ws_id: uuid.UUID) -> AlertsReport:
        if ws_id != workspace_id:
            return AlertsReport(calibration_alerts=[], churn_alerts=[])
        return AlertsReport(
            calibration_alerts=[
                CalibrationAlert(persona_name=persona.name, screen="checkout", delta=0.9)
            ],
            churn_alerts=[],
        )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "build_alerts_report", _fake_alerts_report)

    try:
        fake_redis = _FakeRedis()
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": fake_redis}
        await run_digest_job(ctx)

        retraining_calls = [args for name, args in fake_redis.enqueued if name == "run_retraining_job"]
        assert len(retraining_calls) >= 1

        result = await db_session.execute(
            select(RetrainingJob).where(RetrainingJob.persona_id == persona.id)
        )
        assert result.scalar_one_or_none() is not None
    finally:
        calibration_settings.auto_retrain_on_anomaly = original_auto_retrain
        await db_session.commit()
        monkeypatch.undo()
```

(`_force_due` and the module-level imports/`_FakeRedis` stay as-is; only the four test
bodies and the file's `Settings` import — no longer used — are replaced. Remove `from
flowsage_backend.config import Settings` from the imports if nothing else in the file uses
it, and remove the `settings: Settings` / `monkeypatch: pytest.MonkeyPatch` fixture
parameters these tests used to take as pytest fixtures, since they now construct their own
`pytest.MonkeyPatch()` instances explicitly to control `.undo()` timing relative to the
`finally` cleanup.)

Then add the new cross-workspace test:

```python
async def test_run_digest_job_delivers_to_two_workspaces_independently(
    db_session: AsyncSession,
) -> None:
    """Two fresh workspaces (never touched by any other test, so no shared-state
    dance needed), each with its own Slack webhook and its own enabled Webhook,
    each with its own churn-risk-triggering events. After one `run_digest_job`
    call, each workspace's webhook has exactly one delivery -- scoped assertions
    via `list_deliveries(webhook_id)`, so however many *other* leftover workspaces
    also get processed in the same run is irrelevant."""
    import json

    from flowsage_backend.models.event import Event
    from flowsage_backend.models.integration import SlackIntegration
    from flowsage_backend.models.webhook import Webhook
    from flowsage_backend.webhooks_store import list_deliveries

    async def _make_workspace_with_alerts(cohort: str) -> tuple[uuid.UUID, Webhook]:
        workspace = Workspace(name=f"Digest Test {cohort}", slug=f"digest-{cohort}-{uuid.uuid4().hex[:8]}")
        db_session.add(workspace)
        await db_session.commit()
        await db_session.refresh(workspace)

        db_session.add(SlackIntegration(workspace_id=workspace.id, webhook_url=f"https://hooks.slack.test/{cohort}"))
        webhook = Webhook(
            workspace_id=workspace.id,
            url=f"https://example.test/{cohort}",
            secret="s3cr3t",
            event_types=["alert.triggered"],
        )
        db_session.add(webhook)

        base = datetime(2026, 7, 23, 12, 0, 0, tzinfo=timezone.utc)
        session_ids = [f"{cohort}-{i}" for i in range(8)]
        for i, sid in enumerate(session_ids):
            db_session.add(
                Event(
                    workspace_id=workspace.id, session_id=sid, screen="landing", event="screen_view",
                    timestamp=base, device="mobile", cohort=cohort,
                )
            )
        for sid in session_ids[:2]:
            db_session.add(
                Event(
                    workspace_id=workspace.id, session_id=sid, screen="checkout", event="screen_view",
                    timestamp=base, device="mobile", cohort=cohort,
                )
            )
        db_session.add(
            Event(
                workspace_id=workspace.id, session_id=session_ids[0], screen="confirmation",
                event="screen_view", timestamp=base, device="mobile", cohort=cohort,
            )
        )
        await db_session.commit()
        await db_session.refresh(webhook)
        return workspace.id, webhook

    async def _fake_deliver(url: str, *, secret: str, event_type: str, payload: dict[str, object]) -> tuple[int, bool]:
        return 200, True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(worker_module, "deliver_webhook", _fake_deliver)

    workspace_a_id, webhook_a = await _make_workspace_with_alerts("digestcohorta")
    workspace_b_id, webhook_b = await _make_workspace_with_alerts("digestcohortb")

    try:
        ctx: dict[str, Any] = {"session_factory": lambda: db_session, "redis": _FakeRedis()}
        await run_digest_job(ctx)

        deliveries_a = await list_deliveries(db_session, webhook_a.id)
        deliveries_b = await list_deliveries(db_session, webhook_b.id)
        assert len(deliveries_a) == 1
        assert len(deliveries_b) == 1
        assert json.loads(deliveries_a[0].payload)["churn_alerts"][0]["cohort"] == "digestcohorta"
        assert json.loads(deliveries_b[0].payload)["churn_alerts"][0]["cohort"] == "digestcohortb"
    finally:
        monkeypatch.undo()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest -q tests/test_worker.py`
Expected: FAIL — `AttributeError`/`ImportError` (e.g. `worker_module` has no attribute
`deliver_webhook` yet, `flowsage_backend.models.integration` doesn't exist), since
`worker.py` hasn't been changed yet.

- [ ] **Step 3: Implement**

Replace `run_digest_job`'s body in `worker.py`:

```python
async def run_digest_job(ctx: dict[str, Any]) -> None:
    """Fires daily off the cron schedule below, but only actually sends when due
    per each workspace's own `CalibrationSettings.digest_frequency`. Iterates every
    non-archived workspace independently: one workspace's Slack failure/missing
    config doesn't stop the others (broad except -- a bad webhook URL in one
    workspace must never abort digests for every other workspace in the loop).
    Also delivers to each workspace's enabled `Webhook` rows when
    `has_alerts(report)` is true."""
    session_factory = ctx["session_factory"]
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        result = await session.execute(select(Workspace.id).where(Workspace.archived.is_(False)))
        workspace_ids = list(result.scalars().all())

    for workspace_id in workspace_ids:
        await _run_digest_for_workspace(ctx, workspace_id, now)


async def _run_digest_for_workspace(
    ctx: dict[str, Any], workspace_id: uuid.UUID, now: datetime
) -> None:
    session_factory = ctx["session_factory"]

    async with session_factory() as session:
        calibration_settings = await get_or_create_calibration_settings(session, workspace_id)
        report = await build_alerts_report(session, workspace_id)

        if calibration_settings.auto_retrain_on_anomaly:
            await _auto_retrain_anomalous_personas(session, workspace_id, report, ctx["redis"])

        interval = _DIGEST_INTERVALS[calibration_settings.digest_frequency]
        last_sent = calibration_settings.digest_last_sent_at
        due = last_sent is None or now - last_sent >= interval
        if not due:
            return

        calibration_settings.digest_last_sent_at = now
        await session.commit()

        integration = await get_slack_integration(session, workspace_id)

    try:
        await post_slack_message(
            integration.webhook_url if integration else None,
            text=build_digest_text(report),
            blocks=build_digest_blocks(report),
        )
    except Exception:  # noqa: BLE001 - one workspace's broken/unreachable Slack
        # config must not abort the loop over every other workspace.
        logger.warning("Digest Slack delivery failed for workspace %s", workspace_id, exc_info=True)

    if not has_alerts(report):
        return

    async with session_factory() as session:
        webhooks = await list_enabled_webhooks_for_event(session, workspace_id, "alert.triggered")
        payload = report.model_dump(mode="json")
        for webhook in webhooks:
            status_code, success = await deliver_webhook(
                webhook.url, secret=webhook.secret, event_type="alert.triggered", payload=payload
            )
            await record_delivery(session, webhook.id, "alert.triggered", payload, status_code, success)
```

Add imports to `worker.py`: `import logging` (top of file, with the other stdlib imports),
`logger = logging.getLogger(__name__)` (module level, same as `api/events.py`'s pattern),
`has_alerts` added to the existing `from flowsage_backend.alerts import (AlertsReport,
build_alerts_report, build_digest_blocks, build_digest_text)` import,
`from flowsage_backend.integrations.webhooks import deliver_webhook`,
`from flowsage_backend.integrations_store import get_slack_integration`,
`from flowsage_backend.webhooks_store import list_enabled_webhooks_for_event, record_delivery`.
Remove `SlackNotConfiguredError` from the `flowsage_backend.integrations.slack` import (no
longer referenced — the broad `except Exception` above replaces it) and remove `app_settings
= get_settings()` along with the now-unused `from flowsage_backend.config import
get_settings` import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest -q`
Expected: all pass. If `test_run_digest_job_posts_when_slack_configured` or the two-workspace
test fail with a delivery *not* found among captured calls, re-check that
`_run_digest_for_workspace`'s `get_slack_integration` call happens inside the same `async
with session_factory()` block that just committed `digest_last_sent_at` — reading it in a
*new* session afterward would work too against a real Postgres connection, but since these
tests all pass the identical `db_session` object as their "factory", any accidental
early-`return` before that read would silently skip the integration lookup for a workspace
that has one.

- [ ] **Step 5: Typecheck**

Run: `cd backend && uv run mypy --strict src/`
Expected: `Success: no issues found`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/worker.py backend/tests/test_worker.py
git commit -m "feat: digest job iterates every workspace, delivers to webhooks"
```

---

### Task 6: `/settings/integrations` router

**Files:**
- Create: `backend/src/flowsage_backend/api/integrations.py`
- Modify: `backend/src/flowsage_backend/main.py`
- Test: `backend/tests/test_integrations_api.py`

**Interfaces:**
- Consumes: everything from Tasks 1-4 (`integrations_store`, `webhooks_store`,
  `integrations.webhooks.deliver_webhook`, `ApiKey`/`SlackIntegration`/`JiraIntegration`/
  `Webhook`/`WebhookDelivery`, `security.generate_api_key`/`hash_api_key`,
  `deps.get_current_membership`/`require_role`).
- Produces: `integrations_router` (importable from `flowsage_backend.api.integrations`),
  registered under `main.py`'s `create_app`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_integrations_api.py
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


async def test_slack_integration_starts_disconnected(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-slack-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        response = await client.get("/settings/integrations/slack")

    assert response.status_code == 200
    assert response.json() == {"connected": False, "webhook_url_preview": None}


async def test_connect_and_disconnect_slack(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-slack-connect-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        connect = await client.put(
            "/settings/integrations/slack", json={"webhook_url": "https://hooks.slack.test/abc"}
        )
        assert connect.status_code == 200
        assert connect.json()["connected"] is True

        disconnect = await client.delete("/settings/integrations/slack")
        assert disconnect.status_code == 204

        status = await client.get("/settings/integrations/slack")
        assert status.json()["connected"] is False


async def test_create_api_key_reveals_raw_key_once(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-key-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post("/settings/integrations/api-keys", json={"name": "CI"})
        assert create.status_code == 201
        body = create.json()
        assert body["key"].startswith("fs_live_")

        listing = await client.get("/settings/integrations/api-keys")
        assert listing.status_code == 200
        assert "key" not in listing.json()[0]
        assert listing.json()[0]["key_prefix"] == body["key_prefix"]


async def test_revoke_api_key(app: FastAPI, db_session: AsyncSession) -> None:
    email = f"int-key-revoke-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post("/settings/integrations/api-keys", json={"name": "CI"})
        key_id = create.json()["id"]

        revoke = await client.delete(f"/settings/integrations/api-keys/{key_id}")
        assert revoke.status_code == 204

        listing = await client.get("/settings/integrations/api-keys")
        assert listing.json()[0]["revoked"] is True


async def test_create_webhook_reveals_secret_once_and_lists_it_without_secret(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"int-webhook-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/hook", "event_types": ["alert.triggered"]},
        )
        assert create.status_code == 201
        assert "secret" in create.json()
        webhook_id = create.json()["id"]

        listing = await client.get("/settings/integrations/webhooks")
        assert "secret" not in listing.json()[0]


async def test_test_webhook_endpoint_records_a_delivery(app: FastAPI, db_session: AsyncSession) -> None:
    import respx
    from httpx import Response

    email = f"int-webhook-test-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, email, "hunter2")
    async with _authed_client(app, email) as client:
        create = await client.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/hook", "event_types": ["alert.triggered"]},
        )
        webhook_id = create.json()["id"]

        with respx.mock:
            respx.post("https://example.test/hook").mock(return_value=Response(200))
            test_response = await client.post(f"/settings/integrations/webhooks/{webhook_id}/test")
        assert test_response.status_code == 200
        assert test_response.json()["success"] is True

        deliveries = await client.get(f"/settings/integrations/webhooks/{webhook_id}/deliveries")
        assert len(deliveries.json()) == 1
        assert deliveries.json()[0]["event_type"] == "test"


async def test_viewer_cannot_create_api_key(app: FastAPI, db_session: AsyncSession) -> None:
    """Mutating endpoints require Role.ADMIN -- a fresh user is ADMIN of their own
    workspace (see `seed.upsert_user`), so this test demotes them first."""
    from sqlalchemy import select

    from flowsage_backend.models.workspace import Membership, Role

    email = f"int-viewer-{uuid.uuid4().hex[:8]}@example.com"
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    membership.role = Role.VIEWER
    await db_session.commit()

    async with _authed_client(app, email) as client:
        response = await client.post("/settings/integrations/api-keys", json={"name": "CI"})

    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest -q tests/test_integrations_api.py`
Expected: FAIL — `404 Not Found` for every request (router doesn't exist yet).

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/api/integrations.py
"""`/settings/integrations`: Slack/Jira connect-disconnect, API key issue/revoke,
webhook CRUD + delivery log + test-send. See the Phase 3 chunk 2 design spec."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session, require_role
from flowsage_backend.integrations.webhooks import deliver_webhook
from flowsage_backend.integrations_store import get_jira_integration, get_slack_integration
from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.integration import JiraIntegration, SlackIntegration
from flowsage_backend.models.user import User
from flowsage_backend.models.webhook import Webhook, WebhookDelivery
from flowsage_backend.models.workspace import Membership, Role
from flowsage_backend.security import generate_api_key, hash_api_key
from flowsage_backend.webhooks_store import list_deliveries, record_delivery

router = APIRouter(prefix="/settings/integrations", tags=["integrations"])


def _mask(value: str, keep: int = 4) -> str:
    return f"...{value[-keep:]}" if len(value) > keep else "..."


class SlackStatusOut(BaseModel):
    connected: bool
    webhook_url_preview: str | None


class SlackConnectIn(BaseModel):
    webhook_url: str = Field(min_length=1, max_length=500)


class JiraStatusOut(BaseModel):
    connected: bool
    base_url: str | None
    email: str | None
    project_key: str | None


class JiraConnectIn(BaseModel):
    base_url: str = Field(min_length=1, max_length=500)
    email: str = Field(min_length=1, max_length=320)
    api_token: str = Field(min_length=1, max_length=500)
    project_key: str = Field(min_length=1, max_length=64)


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None

    @property
    def revoked(self) -> bool:
        raise NotImplementedError  # replaced below -- see note


class ApiKeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ApiKeyCreateOut(BaseModel):
    id: uuid.UUID
    name: str
    key: str
    key_prefix: str
    created_at: datetime


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    event_types: list[str]
    enabled: bool
    created_at: datetime


class WebhookCreateIn(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    event_types: list[str] = Field(min_length=1)


class WebhookCreateOut(WebhookOut):
    secret: str


class WebhookUpdateIn(BaseModel):
    url: str | None = None
    event_types: list[str] | None = None
    enabled: bool | None = None


class WebhookDeliveryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    status_code: int | None
    success: bool
    created_at: datetime


class WebhookTestOut(BaseModel):
    status_code: int | None
    success: bool


@router.get("/slack", response_model=SlackStatusOut)
async def get_slack_status(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> SlackStatusOut:
    _, membership = membership_pair
    integration = await get_slack_integration(session, membership.workspace_id)
    if integration is None:
        return SlackStatusOut(connected=False, webhook_url_preview=None)
    return SlackStatusOut(connected=True, webhook_url_preview=_mask(integration.webhook_url))


@router.put("/slack", response_model=SlackStatusOut)
async def connect_slack(
    payload: SlackConnectIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> SlackStatusOut:
    _, membership = membership_pair
    existing = await get_slack_integration(session, membership.workspace_id)
    if existing is not None:
        existing.webhook_url = payload.webhook_url
    else:
        session.add(SlackIntegration(workspace_id=membership.workspace_id, webhook_url=payload.webhook_url))
    await session.commit()
    return SlackStatusOut(connected=True, webhook_url_preview=_mask(payload.webhook_url))


@router.delete("/slack", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_slack(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    integration = await get_slack_integration(session, membership.workspace_id)
    if integration is not None:
        await session.delete(integration)
        await session.commit()


@router.get("/jira", response_model=JiraStatusOut)
async def get_jira_status(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> JiraStatusOut:
    _, membership = membership_pair
    integration = await get_jira_integration(session, membership.workspace_id)
    if integration is None:
        return JiraStatusOut(connected=False, base_url=None, email=None, project_key=None)
    return JiraStatusOut(
        connected=True,
        base_url=integration.base_url,
        email=integration.email,
        project_key=integration.project_key,
    )


@router.put("/jira", response_model=JiraStatusOut)
async def connect_jira(
    payload: JiraConnectIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> JiraStatusOut:
    _, membership = membership_pair
    existing = await get_jira_integration(session, membership.workspace_id)
    if existing is not None:
        existing.base_url = payload.base_url
        existing.email = payload.email
        existing.api_token = payload.api_token
        existing.project_key = payload.project_key
    else:
        session.add(
            JiraIntegration(
                workspace_id=membership.workspace_id,
                base_url=payload.base_url,
                email=payload.email,
                api_token=payload.api_token,
                project_key=payload.project_key,
            )
        )
    await session.commit()
    return JiraStatusOut(
        connected=True, base_url=payload.base_url, email=payload.email, project_key=payload.project_key
    )


@router.delete("/jira", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_jira(
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    integration = await get_jira_integration(session, membership.workspace_id)
    if integration is not None:
        await session.delete(integration)
        await session.commit()


class ApiKeyListOut(BaseModel):
    id: uuid.UUID
    name: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


@router.get("/api-keys", response_model=list[ApiKeyListOut])
async def list_api_keys(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[ApiKeyListOut]:
    _, membership = membership_pair
    result = await session.execute(
        select(ApiKey).where(ApiKey.workspace_id == membership.workspace_id).order_by(ApiKey.created_at.desc())
    )
    return [
        ApiKeyListOut(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            created_at=k.created_at,
            last_used_at=k.last_used_at,
            revoked=k.revoked_at is not None,
        )
        for k in result.scalars().all()
    ]


@router.post("/api-keys", response_model=ApiKeyCreateOut, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreateIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> ApiKeyCreateOut:
    _, membership = membership_pair
    raw_key = generate_api_key()
    key = ApiKey(
        workspace_id=membership.workspace_id,
        name=payload.name,
        key_prefix=raw_key[:12],
        key_hash=hash_api_key(raw_key),
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return ApiKeyCreateOut(id=key.id, name=key.name, key=raw_key, key_prefix=key.key_prefix, created_at=key.created_at)


async def _get_owned_api_key(session: AsyncSession, workspace_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey:
    key = await session.get(ApiKey, key_id)
    if key is None or key.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    return key


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    key = await _get_owned_api_key(session, membership.workspace_id, key_id)
    key.revoked_at = datetime.now(tz=key.created_at.tzinfo)
    await session.commit()


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[WebhookOut]:
    _, membership = membership_pair
    result = await session.execute(
        select(Webhook).where(Webhook.workspace_id == membership.workspace_id).order_by(Webhook.created_at.desc())
    )
    return [
        WebhookOut(id=w.id, url=w.url, event_types=w.event_types, enabled=w.enabled, created_at=w.created_at)
        for w in result.scalars().all()
    ]


@router.post("/webhooks", response_model=WebhookCreateOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreateIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookCreateOut:
    _, membership = membership_pair
    secret = generate_api_key()  # same high-entropy generator; format doesn't matter here
    webhook = Webhook(
        workspace_id=membership.workspace_id,
        url=payload.url,
        secret=secret,
        event_types=payload.event_types,
    )
    session.add(webhook)
    await session.commit()
    await session.refresh(webhook)
    return WebhookCreateOut(
        id=webhook.id,
        url=webhook.url,
        event_types=webhook.event_types,
        enabled=webhook.enabled,
        created_at=webhook.created_at,
        secret=secret,
    )


async def _get_owned_webhook(session: AsyncSession, workspace_id: uuid.UUID, webhook_id: uuid.UUID) -> Webhook:
    webhook = await session.get(Webhook, webhook_id)
    if webhook is None or webhook.workspace_id != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Webhook not found")
    return webhook


@router.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdateIn,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookOut:
    _, membership = membership_pair
    webhook = await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    if payload.url is not None:
        webhook.url = payload.url
    if payload.event_types is not None:
        webhook.event_types = payload.event_types
    if payload.enabled is not None:
        webhook.enabled = payload.enabled
    await session.commit()
    return WebhookOut(
        id=webhook.id, url=webhook.url, event_types=webhook.event_types, enabled=webhook.enabled,
        created_at=webhook.created_at,
    )


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    _, membership = membership_pair
    webhook = await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    await session.delete(webhook)
    await session.commit()


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def get_webhook_deliveries(
    webhook_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> list[WebhookDelivery]:
    _, membership = membership_pair
    await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    return await list_deliveries(session, webhook_id)


@router.post("/webhooks/{webhook_id}/test", response_model=WebhookTestOut)
async def test_webhook(
    webhook_id: uuid.UUID,
    membership_pair: tuple[User, Membership] = Depends(require_role(Role.ADMIN)),
    session: AsyncSession = Depends(get_db_session),
) -> WebhookTestOut:
    _, membership = membership_pair
    webhook = await _get_owned_webhook(session, membership.workspace_id, webhook_id)
    status_code, success = await deliver_webhook(
        webhook.url, secret=webhook.secret, event_type="test", payload={"message": "FlowSage test delivery"}
    )
    await record_delivery(
        session, webhook.id, "test", {"message": "FlowSage test delivery"}, status_code, success
    )
    return WebhookTestOut(status_code=status_code, success=success)
```

Delete the stray `ApiKeyOut` class defined near the top (with the `raise
NotImplementedError` property) -- it was superseded by `ApiKeyListOut` further down and is
dead code; this note exists so the implementer removes it rather than leaving two
similarly-named unused response models in the file.

Register in `main.py`: add `from flowsage_backend.api.integrations import router as
integrations_router` and `app.include_router(integrations_router)` alongside the other
`include_router` calls.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest -q tests/test_integrations_api.py`
Expected: all pass.

- [ ] **Step 5: Full backend suite + typecheck**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/`
Expected: all green. (This is the point where the dead `ApiKeyOut` class, if not removed,
would surface as an unused-class situation — mypy won't flag it, but re-read the file once
to confirm it's gone before committing.)

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/api/integrations.py backend/src/flowsage_backend/main.py \
        backend/tests/test_integrations_api.py
git commit -m "feat: /settings/integrations router (Slack/Jira/API keys/webhooks)"
```

---

### Task 7: Cross-tenant isolation tests

**Files:**
- Modify: `backend/tests/test_workspace_isolation.py`

**Interfaces:**
- Consumes: everything from Tasks 1-6.

- [ ] **Step 1: Write the failing tests**

Add to `test_workspace_isolation.py`, following its existing `_authed_client`/`upsert_user`
pattern exactly:

```python
async def test_api_key_created_in_one_workspace_does_not_authenticate_for_another(
    app: FastAPI, db_session: AsyncSession
) -> None:
    from flowsage_backend.seed import upsert_user
    from flowsage_backend.models.workspace import Membership

    tenant_a_email = f"isolation-key-a-{uuid.uuid4().hex[:8]}@example.com"
    user_a = await upsert_user(db_session, tenant_a_email, "hunter2")
    membership_a = (
        await db_session.execute(select(Membership).where(Membership.user_id == user_a.id))
    ).scalar_one()

    async with _authed_client(app, tenant_a_email) as client_a:
        create = await client_a.post("/settings/integrations/api-keys", json={"name": "tenant-a-key"})
    raw_key = create.json()["key"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/v1/events",
            json=[{
                "session_id": "isolation-key-check",
                "screen": "landing",
                "event": "screen_view",
                "timestamp": "2026-07-23T00:00:00Z",
            }],
            headers={"X-API-Key": raw_key},
        )

    assert response.status_code == 201
    # The event landed in tenant A's workspace specifically -- confirm via a second
    # tenant's authenticated session never seeing it.
    tenant_b_email = f"isolation-key-b-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_b_email, "hunter2")
    async with _authed_client(app, tenant_b_email) as client_b:
        funnel = await client_b.get("/graph/funnel")
    assert funnel.json()["total_sessions"] == 0


async def test_webhook_deliveries_do_not_leak_across_workspaces(
    app: FastAPI, db_session: AsyncSession
) -> None:
    from flowsage_backend.webhooks_store import record_delivery
    from flowsage_backend.models.webhook import Webhook

    tenant_a_email = f"isolation-webhook-a-{uuid.uuid4().hex[:8]}@example.com"
    tenant_b_email = f"isolation-webhook-b-{uuid.uuid4().hex[:8]}@example.com"
    await upsert_user(db_session, tenant_a_email, "hunter2")
    await upsert_user(db_session, tenant_b_email, "hunter2")

    async with _authed_client(app, tenant_a_email) as client_a:
        create_a = await client_a.post(
            "/settings/integrations/webhooks",
            json={"url": "https://example.test/a", "event_types": ["alert.triggered"]},
        )
    webhook_a_id = create_a.json()["id"]

    async with _authed_client(app, tenant_b_email) as client_b:
        response = await client_b.get(f"/settings/integrations/webhooks/{webhook_a_id}/deliveries")

    assert response.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail (or pass by luck) then confirm real coverage**

Run: `cd backend && uv run pytest -q tests/test_workspace_isolation.py`
Expected: PASS if Tasks 1-6 were implemented correctly (this task is regression coverage,
not new behavior) -- if either test fails, it means a workspace-scoping check was missed in
an earlier task; go fix that task's endpoint, don't weaken this test.

- [ ] **Step 3: Full backend verification**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_workspace_isolation.py
git commit -m "test: cross-tenant isolation coverage for API keys and webhooks"
```

---

### Task 8: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces (consumed by Task 9): `ApiKey`, `ApiKeyCreated`, `Webhook`, `WebhookCreated`,
  `WebhookDelivery`, `SlackStatus`, `JiraStatus` types; `api.getSlackStatus`,
  `api.connectSlack`, `api.disconnectSlack`, `api.getJiraStatus`, `api.connectJira`,
  `api.disconnectJira`, `api.getApiKeys`, `api.createApiKey`, `api.revokeApiKey`,
  `api.getWebhooks`, `api.createWebhook`, `api.updateWebhook`, `api.deleteWebhook`,
  `api.getWebhookDeliveries`, `api.testWebhook`.

- [ ] **Step 1: Add types**

Append to `frontend/src/lib/types.ts`:

```typescript
export interface SlackStatus {
  connected: boolean;
  webhook_url_preview: string | null;
}

export interface JiraStatus {
  connected: boolean;
  base_url: string | null;
  email: string | null;
  project_key: string | null;
}

export interface JiraConnectPayload {
  base_url: string;
  email: string;
  api_token: string;
  project_key: string;
}

export interface ApiKey {
  id: string;
  name: string;
  key_prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

export interface ApiKeyCreated {
  id: string;
  name: string;
  key: string;
  key_prefix: string;
  created_at: string;
}

export interface Webhook {
  id: string;
  url: string;
  event_types: string[];
  enabled: boolean;
  created_at: string;
}

export interface WebhookCreated extends Webhook {
  secret: string;
}

export interface WebhookUpdatePayload {
  url?: string;
  event_types?: string[];
  enabled?: boolean;
}

export interface WebhookDelivery {
  id: string;
  event_type: string;
  status_code: number | null;
  success: boolean;
  created_at: string;
}

export interface WebhookTestResult {
  status_code: number | null;
  success: boolean;
}
```

- [ ] **Step 2: Add API client functions**

Add the new type names to the `import type { ... } from "./types"` block at the top of
`api.ts`, then append to the `api` object (matching the existing `getWorkspaces`/
`createWorkspace` style exactly):

```typescript
  getSlackStatus: (): Promise<SlackStatus> => request<SlackStatus>("/settings/integrations/slack"),

  connectSlack: (webhookUrl: string): Promise<SlackStatus> =>
    request<SlackStatus>("/settings/integrations/slack", {
      method: "PUT",
      body: JSON.stringify({ webhook_url: webhookUrl }),
    }),

  disconnectSlack: (): Promise<void> =>
    request<void>("/settings/integrations/slack", { method: "DELETE" }),

  getJiraStatus: (): Promise<JiraStatus> => request<JiraStatus>("/settings/integrations/jira"),

  connectJira: (payload: JiraConnectPayload): Promise<JiraStatus> =>
    request<JiraStatus>("/settings/integrations/jira", { method: "PUT", body: JSON.stringify(payload) }),

  disconnectJira: (): Promise<void> =>
    request<void>("/settings/integrations/jira", { method: "DELETE" }),

  getApiKeys: (): Promise<ApiKey[]> => request<ApiKey[]>("/settings/integrations/api-keys"),

  createApiKey: (name: string): Promise<ApiKeyCreated> =>
    request<ApiKeyCreated>("/settings/integrations/api-keys", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),

  revokeApiKey: (id: string): Promise<void> =>
    request<void>(`/settings/integrations/api-keys/${id}`, { method: "DELETE" }),

  getWebhooks: (): Promise<Webhook[]> => request<Webhook[]>("/settings/integrations/webhooks"),

  createWebhook: (url: string, eventTypes: string[]): Promise<WebhookCreated> =>
    request<WebhookCreated>("/settings/integrations/webhooks", {
      method: "POST",
      body: JSON.stringify({ url, event_types: eventTypes }),
    }),

  updateWebhook: (id: string, payload: WebhookUpdatePayload): Promise<Webhook> =>
    request<Webhook>(`/settings/integrations/webhooks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteWebhook: (id: string): Promise<void> =>
    request<void>(`/settings/integrations/webhooks/${id}`, { method: "DELETE" }),

  getWebhookDeliveries: (id: string): Promise<WebhookDelivery[]> =>
    request<WebhookDelivery[]>(`/settings/integrations/webhooks/${id}/deliveries`),

  testWebhook: (id: string): Promise<WebhookTestResult> =>
    request<WebhookTestResult>(`/settings/integrations/webhooks/${id}/test`, { method: "POST" }),
```

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: `ok` (no errors -- these are pure additions, nothing consumes them yet so nothing
can be broken, but this confirms the types themselves are well-formed).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat: add integrations types and API client functions"
```

---

### Task 9: `IntegrationsSettingsPage` + nav/routing

**Files:**
- Create: `frontend/src/routes/settings/IntegrationsSettingsPage.tsx`
- Create: `frontend/src/routes/settings/IntegrationsSettingsPage.test.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.tsx`

**Interfaces:**
- Consumes: all of Task 8's `api.*` functions and types.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/routes/settings/IntegrationsSettingsPage.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { IntegrationsSettingsPage } from "./IntegrationsSettingsPage";

vi.mock("../../lib/api", () => ({
  api: {
    getSlackStatus: vi.fn(),
    connectSlack: vi.fn(),
    disconnectSlack: vi.fn(),
    getJiraStatus: vi.fn(),
    getApiKeys: vi.fn(),
    createApiKey: vi.fn(),
    revokeApiKey: vi.fn(),
    getWebhooks: vi.fn(),
    createWebhook: vi.fn(),
    deleteWebhook: vi.fn(),
  },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.getSlackStatus.mockResolvedValue({ connected: false, webhook_url_preview: null });
  mockApi.getJiraStatus.mockResolvedValue({ connected: false, base_url: null, email: null, project_key: null });
  mockApi.getApiKeys.mockResolvedValue([]);
  mockApi.getWebhooks.mockResolvedValue([]);
});

describe("IntegrationsSettingsPage", () => {
  it("connects Slack via the marketplace card form", async () => {
    mockApi.connectSlack.mockResolvedValue({ connected: true, webhook_url_preview: "...abcd" });
    render(<IntegrationsSettingsPage />);

    await waitFor(() => expect(screen.getByText(/not connected/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /connect/i }));
    await userEvent.type(screen.getByLabelText(/webhook url/i), "https://hooks.slack.test/abc");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(mockApi.connectSlack).toHaveBeenCalledWith("https://hooks.slack.test/abc"));
  });

  it("creates an API key and shows the raw key once", async () => {
    mockApi.createApiKey.mockResolvedValue({
      id: "key-1", name: "CI", key: "fs_live_abc123", key_prefix: "fs_live_abc1", created_at: "2026-07-23T00:00:00Z",
    });
    render(<IntegrationsSettingsPage />);

    await waitFor(() => expect(screen.getByText(/api keys/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /create key/i }));
    await userEvent.type(screen.getByLabelText(/key name/i), "CI");
    await userEvent.click(screen.getByRole("button", { name: /^generate$/i }));

    await waitFor(() => expect(screen.getByText("fs_live_abc123")).toBeInTheDocument());
  });

  it("adds a webhook and lists it in the table", async () => {
    mockApi.createWebhook.mockResolvedValue({
      id: "hook-1", url: "https://example.test/hook", event_types: ["alert.triggered"],
      enabled: true, created_at: "2026-07-23T00:00:00Z", secret: "s3cr3t",
    });
    render(<IntegrationsSettingsPage />);

    await waitFor(() => expect(screen.getByText(/webhooks/i)).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: /add webhook/i }));
    await userEvent.type(screen.getByLabelText(/webhook url/i, { selector: "input" }), "https://example.test/hook");
    await userEvent.click(screen.getByRole("button", { name: /^add$/i }));

    await waitFor(() => expect(screen.getByText("https://example.test/hook")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- IntegrationsSettingsPage`
Expected: FAIL — the module doesn't exist yet.

- [ ] **Step 3: Implement**

```tsx
// frontend/src/routes/settings/IntegrationsSettingsPage.tsx
import { useEffect, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type {
  ApiKey,
  ApiKeyCreated,
  JiraStatus,
  SlackStatus,
  Webhook,
  WebhookCreated,
} from "../../lib/types";

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

function SlackCard() {
  const [status, setStatus] = useState<SlackStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [webhookUrl, setWebhookUrl] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getSlackStatus().then(setStatus).catch((err: unknown) => setError(errorMessage(err, "Failed to load Slack status.")));
  }, []);

  async function handleConnect() {
    setError(null);
    try {
      const updated = await api.connectSlack(webhookUrl);
      setStatus(updated);
      setConnecting(false);
      setWebhookUrl("");
    } catch (err) {
      setError(errorMessage(err, "Failed to connect Slack."));
    }
  }

  async function handleDisconnect() {
    setError(null);
    try {
      await api.disconnectSlack();
      setStatus({ connected: false, webhook_url_preview: null });
    } catch (err) {
      setError(errorMessage(err, "Failed to disconnect Slack."));
    }
  }

  if (status === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
      <h3 className="font-headline text-lg">Slack</h3>
      {error !== null ? <p role="alert" className="text-sm text-error">{error}</p> : null}
      {status.connected ? (
        <>
          <p className="text-sm text-on-surface-variant">Connected ({status.webhook_url_preview})</p>
          <button
            type="button"
            onClick={() => void handleDisconnect()}
            className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium"
          >
            Disconnect
          </button>
        </>
      ) : connecting ? (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Webhook URL</span>
            <input
              value={webhookUrl}
              onChange={(event) => setWebhookUrl(event.target.value)}
              className="ghost-border rounded-lg px-3 py-2 bg-transparent"
            />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleConnect()}
              disabled={webhookUrl.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Save
            </button>
            <button type="button" onClick={() => setConnecting(false)} className="rounded-lg ghost-border py-2 px-4 font-medium">
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-on-surface-variant">Not connected.</p>
          <button
            type="button"
            onClick={() => setConnecting(true)}
            className="self-start rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium"
          >
            Connect
          </button>
        </>
      )}
    </div>
  );
}

function JiraCard() {
  const [status, setStatus] = useState<JiraStatus | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [baseUrl, setBaseUrl] = useState("");
  const [email, setEmail] = useState("");
  const [apiToken, setApiToken] = useState("");
  const [projectKey, setProjectKey] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getJiraStatus().then(setStatus).catch((err: unknown) => setError(errorMessage(err, "Failed to load Jira status.")));
  }, []);

  async function handleConnect() {
    setError(null);
    try {
      const updated = await api.connectJira({ base_url: baseUrl, email, api_token: apiToken, project_key: projectKey });
      setStatus(updated);
      setConnecting(false);
    } catch (err) {
      setError(errorMessage(err, "Failed to connect Jira."));
    }
  }

  async function handleDisconnect() {
    setError(null);
    try {
      await api.disconnectJira();
      setStatus({ connected: false, base_url: null, email: null, project_key: null });
    } catch (err) {
      setError(errorMessage(err, "Failed to disconnect Jira."));
    }
  }

  if (status === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
      <h3 className="font-headline text-lg">Jira</h3>
      {error !== null ? <p role="alert" className="text-sm text-error">{error}</p> : null}
      {status.connected ? (
        <>
          <p className="text-sm text-on-surface-variant">
            Connected — {status.project_key} ({status.email})
          </p>
          <button type="button" onClick={() => void handleDisconnect()} className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium">
            Disconnect
          </button>
        </>
      ) : connecting ? (
        <>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Base URL</span>
            <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} className="ghost-border rounded-lg px-3 py-2 bg-transparent" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Email</span>
            <input value={email} onChange={(e) => setEmail(e.target.value)} className="ghost-border rounded-lg px-3 py-2 bg-transparent" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">API Token</span>
            <input type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)} className="ghost-border rounded-lg px-3 py-2 bg-transparent" />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Project Key</span>
            <input value={projectKey} onChange={(e) => setProjectKey(e.target.value)} className="ghost-border rounded-lg px-3 py-2 bg-transparent" />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleConnect()}
              disabled={!baseUrl || !email || !apiToken || !projectKey}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Save
            </button>
            <button type="button" onClick={() => setConnecting(false)} className="rounded-lg ghost-border py-2 px-4 font-medium">
              Cancel
            </button>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-on-surface-variant">Not connected.</p>
          <button type="button" onClick={() => setConnecting(true)} className="self-start rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium">
            Connect
          </button>
        </>
      )}
    </div>
  );
}

function ApiKeysSection() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [revealed, setRevealed] = useState<ApiKeyCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.getApiKeys().then(setKeys).catch((err: unknown) => setError(errorMessage(err, "Failed to load API keys.")));
  }

  useEffect(load, []);

  async function handleCreate() {
    setError(null);
    try {
      const created = await api.createApiKey(name);
      setRevealed(created);
      setCreating(false);
      setName("");
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to create API key."));
    }
  }

  async function handleRevoke(id: string) {
    setError(null);
    try {
      await api.revokeApiKey(id);
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to revoke API key."));
    }
  }

  if (keys === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-headline text-xl">API Keys</h2>
        <button type="button" onClick={() => setCreating(true)} className="rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium">
          Create key
        </button>
      </div>
      {error !== null ? <p role="alert" className="text-sm text-error">{error}</p> : null}

      {revealed !== null ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-2">
          <p className="text-sm text-on-surface-variant">
            Copy this key now — you won&apos;t be able to see it again.
          </p>
          <code className="bg-surface-container rounded-lg px-3 py-2 text-sm break-all">{revealed.key}</code>
          <button type="button" onClick={() => setRevealed(null)} className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium">
            Done
          </button>
        </div>
      ) : null}

      {creating ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Key name</span>
            <input value={name} onChange={(e) => setName(e.target.value)} className="ghost-border rounded-lg px-3 py-2 bg-transparent" />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={name.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Generate
            </button>
            <button type="button" onClick={() => setCreating(false)} className="rounded-lg ghost-border py-2 px-4 font-medium">
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <table className="w-full text-sm bg-surface-container-lowest rounded-xl overflow-hidden">
        <thead className="text-left text-on-surface-variant border-b border-outline-variant">
          <tr>
            <th className="px-6 py-3 font-medium">Name</th>
            <th className="px-6 py-3 font-medium">Prefix</th>
            <th className="px-6 py-3 font-medium">Created</th>
            <th className="px-6 py-3 font-medium">Last used</th>
            <th className="px-6 py-3 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.id} className="border-b border-outline-variant last:border-0">
              <td className="px-6 py-3">{key.name}</td>
              <td className="px-6 py-3 font-mono text-xs">{key.key_prefix}…</td>
              <td className="px-6 py-3 text-on-surface-variant">{new Date(key.created_at).toLocaleDateString()}</td>
              <td className="px-6 py-3 text-on-surface-variant">
                {key.last_used_at ? new Date(key.last_used_at).toLocaleDateString() : "Never"}
              </td>
              <td className="px-6 py-3 text-right">
                {key.revoked ? (
                  <span className="text-on-surface-variant text-xs">Revoked</span>
                ) : (
                  <button type="button" onClick={() => void handleRevoke(key.id)} className="text-error text-xs font-medium hover:underline">
                    Revoke
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function WebhooksSection() {
  const [webhooks, setWebhooks] = useState<Webhook[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [url, setUrl] = useState("");
  const [revealed, setRevealed] = useState<WebhookCreated | null>(null);
  const [error, setError] = useState<string | null>(null);

  function load() {
    api.getWebhooks().then(setWebhooks).catch((err: unknown) => setError(errorMessage(err, "Failed to load webhooks.")));
  }

  useEffect(load, []);

  async function handleCreate() {
    setError(null);
    try {
      const created = await api.createWebhook(url, ["alert.triggered"]);
      setRevealed(created);
      setCreating(false);
      setUrl("");
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to create webhook."));
    }
  }

  async function handleDelete(id: string) {
    setError(null);
    try {
      await api.deleteWebhook(id);
      load();
    } catch (err) {
      setError(errorMessage(err, "Failed to delete webhook."));
    }
  }

  if (webhooks === null) return <p className="text-on-surface-variant text-sm">Loading…</p>;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="font-headline text-xl">Webhooks</h2>
        <button type="button" onClick={() => setCreating(true)} className="rounded-lg bg-primary py-2 px-4 text-on-primary text-sm font-medium">
          Add webhook
        </button>
      </div>
      {error !== null ? <p role="alert" className="text-sm text-error">{error}</p> : null}

      {revealed !== null ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-2">
          <p className="text-sm text-on-surface-variant">
            Copy this signing secret now — you won&apos;t be able to see it again.
          </p>
          <code className="bg-surface-container rounded-lg px-3 py-2 text-sm break-all">{revealed.secret}</code>
          <button type="button" onClick={() => setRevealed(null)} className="self-start rounded-lg ghost-border py-2 px-4 text-sm font-medium">
            Done
          </button>
        </div>
      ) : null}

      {creating ? (
        <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-sm text-on-surface-variant">Webhook URL</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} className="ghost-border rounded-lg px-3 py-2 bg-transparent" />
          </label>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => void handleCreate()}
              disabled={url.length === 0}
              className="rounded-lg bg-primary py-2 px-4 text-on-primary font-medium disabled:opacity-50"
            >
              Add
            </button>
            <button type="button" onClick={() => setCreating(false)} className="rounded-lg ghost-border py-2 px-4 font-medium">
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <table className="w-full text-sm bg-surface-container-lowest rounded-xl overflow-hidden">
        <thead className="text-left text-on-surface-variant border-b border-outline-variant">
          <tr>
            <th className="px-6 py-3 font-medium">URL</th>
            <th className="px-6 py-3 font-medium">Events</th>
            <th className="px-6 py-3 font-medium">Created</th>
            <th className="px-6 py-3 font-medium"></th>
          </tr>
        </thead>
        <tbody>
          {webhooks.map((webhook) => (
            <tr key={webhook.id} className="border-b border-outline-variant last:border-0">
              <td className="px-6 py-3 break-all">{webhook.url}</td>
              <td className="px-6 py-3 text-on-surface-variant">{webhook.event_types.join(", ")}</td>
              <td className="px-6 py-3 text-on-surface-variant">{new Date(webhook.created_at).toLocaleDateString()}</td>
              <td className="px-6 py-3 text-right">
                <button type="button" onClick={() => void handleDelete(webhook.id)} className="text-error text-xs font-medium hover:underline">
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function IntegrationsSettingsPage() {
  return (
    <div className="flex flex-col gap-8 max-w-3xl">
      <div>
        <h1 className="font-headline text-3xl">Integrations</h1>
        <p className="text-on-surface-variant mt-1">Connect Slack/Jira, manage API keys and webhooks.</p>
      </div>

      <section className="flex flex-col gap-4">
        <h2 className="font-headline text-xl">Marketplace</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <SlackCard />
          <JiraCard />
        </div>
      </section>

      <ApiKeysSection />
      <WebhooksSection />
    </div>
  );
}
```

Note the `<label>` elements above wrap their `<input>` without an explicit `htmlFor`/`id`
pairing, relying on implicit label association (input nested inside label) — this already
matches `TeamSettingsPage`'s existing pattern, so `getByLabelText` in the test works the same
way it does for that page's tests.

- [ ] **Step 4: Add nav entry and route**

In `Sidebar.tsx`, add after the `/settings/model-calibration` entry:

```typescript
  { to: "/settings/integrations", label: "Integrations", icon: "hub" },
```

In `App.tsx`, add the import `import { IntegrationsSettingsPage } from
"./routes/settings/IntegrationsSettingsPage";` and the route
`<Route path="/settings/integrations" element={<IntegrationsSettingsPage />} />` next to the
other `/settings/*` routes.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd frontend && npm run test -- IntegrationsSettingsPage`
Expected: `3 passed`.

- [ ] **Step 6: Full frontend verification**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/settings/IntegrationsSettingsPage.tsx \
        frontend/src/routes/settings/IntegrationsSettingsPage.test.tsx \
        frontend/src/components/Sidebar.tsx frontend/src/App.tsx
git commit -m "feat: add IntegrationsSettingsPage (Slack/Jira/API keys/webhooks)"
```

---

### Task 10: Full verification pass

**Files:** none (verification only, plus whatever small fixes verification surfaces — same
shape as chunk 1's Task 11).

- [ ] **Step 1: Full backend test suite + strict typecheck**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/`
Expected: all green.

- [ ] **Step 2: Full frontend test suite + typecheck**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all green.

- [ ] **Step 3: `docker compose up -d --build`, run the migration for real**

```bash
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend /workspace/.venv/bin/alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f infra/docker-compose.yml exec backend /workspace/.venv/bin/flowsage-backend create-user demo@example.com hunter2
docker compose -f infra/docker-compose.yml exec backend /workspace/.venv/bin/flowsage-backend seed-personas
docker compose -f infra/docker-compose.yml exec backend /workspace/.venv/bin/flowsage-backend create-api-key fs-default "demo key"
```

(Use the direct `.venv/bin/` paths, not `uv run` — the backend image is built with `uv sync
--frozen --no-dev`, so `uv run` tries to resync the dev dependency group as a non-root user
and fails with a permission error; this bit chunk 1's Task 11 too.)
Expected: migration applies cleanly; `create-api-key` prints a raw `fs_live_...` key.

- [ ] **Step 4: Manual cross-tenant + webhook curl check**

```bash
API_KEY=<the fs_live_... key printed above>
curl -c /tmp/cookie.txt -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"demo@example.com","password":"hunter2"}'

curl -b /tmp/cookie.txt -X PUT http://localhost:8000/settings/integrations/slack \
  -H 'Content-Type: application/json' -d '{"webhook_url":"https://hooks.slack.test/demo"}'
curl -b /tmp/cookie.txt http://localhost:8000/settings/integrations/slack   # expect connected: true

curl -X POST http://localhost:8000/v1/events -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '[{"session_id":"verify-1","screen":"landing","event":"screen_view","timestamp":"2026-07-23T00:00:00Z"}]'
# expect: {"ingested":1}

curl -b /tmp/cookie.txt http://localhost:8000/graph/funnel   # expect total_sessions: 1, confirming the API key resolved to demo@example.com's own workspace
```
Expected: each response matches the comment above.

- [ ] **Step 5: Playwright e2e**

Run: `cd frontend && npx playwright test`
Expected: PASS, including the new `integrations-settings.spec.ts` (write it following
`workspace-settings.spec.ts`'s existing shape: log in, navigate to `/settings/integrations`,
connect Slack via the form and assert the connected state persists on reload, create an API
key and assert the raw-key-reveal panel appears, add a webhook, hit its "Send test" —
skip if no test-server plumbing exists yet, in which case just assert the webhook row
appears in the table after creation).

- [ ] **Step 6: Tear down and final commit/push**

```bash
docker compose -f infra/docker-compose.yml down
git status  # confirm clean tree, everything already committed per-task
git push origin main
```
