# Insights API + Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a public, API-key-authenticated, OpenAPI-documented read API (`/v1/insights/funnel`, `/v1/insights/friction-issues`) plus a manual load-test tool for `POST /v1/events`, closing Phase 4 items 2 and 4 of `plans/full-project-coding-plan.md`.

**Architecture:** New `flowsage_backend/insights.py` compute module (no new tables) backs a new `api/insights.py` router mounted under `/v1/insights`, reusing the existing `require_workspace_api_key` dependency unchanged and the existing `build_funnel_report` unchanged. A docs-only `APIKeyHeader` security scheme is added purely so Swagger UI shows the Authorize control. A standalone asyncio+httpx script under `scripts/load_test/` load-tests ingestion; it is not a workspace package and is not wired into CI.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, pytest + testcontainers (existing backend stack — no new dependencies).

## Global Constraints

- Strict typing: `mypy --strict` must stay clean on every backend file touched or added.
- `autoflake8` (remove unused imports/vars) then `black` formatting before every commit, per the project's standing process rules.
- No new tables — this chunk is entirely compute-on-demand, matching `calibration.py`/`churn.py`/`alerts.py`.
- No changes to `POST /v1/events` or any existing `/graph/*` route's behavior — additive only.
- Work happens in git worktree `.claude/worktrees/phase4-insights-hardening` (branch `worktree-phase4-insights-hardening`), one task per commit, final task merges to `main` and removes the worktree.
- Full backend test suite (currently 196 tests) + mypy --strict + autoflake8 must stay green after every task that touches backend code.

---

### Task 0: Create the worktree

**Files:** none (setup only)

- [ ] **Step 1: Create the worktree and branch**

```bash
cd /home/asus/Projects/personal/FlowSage
git worktree add .claude/worktrees/phase4-insights-hardening -b worktree-phase4-insights-hardening
```

- [ ] **Step 2: Verify the worktree builds**

```bash
cd .claude/worktrees/phase4-insights-hardening
uv sync --all-extras
cd backend && uv run pytest -q
```

Expected: all 196 existing tests pass. All subsequent steps in this plan run inside `.claude/worktrees/phase4-insights-hardening` unless noted otherwise.

---

### Task 1: `insights.py` compute module — `list_friction_issues`

**Files:**
- Create: `backend/src/flowsage_backend/insights.py`
- Test: `backend/tests/test_insights.py`

**Interfaces:**
- Consumes: `flowsage_backend.models.simulation.FrictionIssue` (fields: `id`, `workspace_id`, `run_id`, `screen`, `severity`, `title`, `heuristic_violated`, `persona_impact`, `description`, `suggested_fix`, `created_at`).
- Produces: `list_friction_issues(session: AsyncSession, workspace_id: uuid.UUID, *, severity: str | None = None, screen: str | None = None, since: datetime | None = None, cursor: str | None = None, limit: int = 50) -> tuple[list[FrictionIssue], str | None]` — used by Task 2's router.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_insights.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.insights import list_friction_issues
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from tests.conftest import create_workspace_and_admin

_T0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


async def _make_run(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    persona = Persona(
        workspace_id=workspace_id,
        slug=f"insights-persona-{uuid.uuid4().hex[:8]}",
        name="Insights Test Persona",
        description="d",
        baseline=False,
        tech_affinity="medium",
        primary_device="desktop",
        discovery_mode="search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    session.add(persona)
    await session.flush()

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="checkout",
        goal="buy",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    return run.id


async def _make_issue(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    screen: str = "checkout",
    severity: str = "high",
    created_at: datetime | None = None,
) -> FrictionIssue:
    issue = FrictionIssue(
        workspace_id=workspace_id,
        run_id=run_id,
        screen=screen,
        severity=severity,
        title="Confusing CTA",
        heuristic_violated="Visibility of system status",
        persona_impact="Anxious users abandon.",
        description="The primary button is unlabeled.",
        suggested_fix="Add a clear label.",
    )
    session.add(issue)
    await session.flush()
    if created_at is not None:
        issue.created_at = created_at
        await session.flush()
    return issue


async def test_list_friction_issues_scopes_to_workspace(db_session: AsyncSession) -> None:
    _, membership_a = await create_workspace_and_admin(
        db_session, f"insights-a-{uuid.uuid4().hex[:8]}@example.com"
    )
    _, membership_b = await create_workspace_and_admin(
        db_session, f"insights-b-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_a = await _make_run(db_session, membership_a.workspace_id)
    run_b = await _make_run(db_session, membership_b.workspace_id)
    await _make_issue(db_session, membership_a.workspace_id, run_a)
    await _make_issue(db_session, membership_b.workspace_id, run_b)
    await db_session.commit()

    issues, cursor = await list_friction_issues(db_session, membership_a.workspace_id)
    assert len(issues) == 1
    assert issues[0].workspace_id == membership_a.workspace_id
    assert cursor is None


async def test_list_friction_issues_filters_by_severity_and_screen(db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-filter-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_id = await _make_run(db_session, membership.workspace_id)
    await _make_issue(db_session, membership.workspace_id, run_id, screen="checkout", severity="high")
    await _make_issue(db_session, membership.workspace_id, run_id, screen="landing", severity="low")
    await db_session.commit()

    high_only, _ = await list_friction_issues(db_session, membership.workspace_id, severity="high")
    assert {i.screen for i in high_only} == {"checkout"}

    checkout_only, _ = await list_friction_issues(db_session, membership.workspace_id, screen="checkout")
    assert {i.severity for i in checkout_only} == {"high"}


async def test_list_friction_issues_filters_by_since(db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-since-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_id = await _make_run(db_session, membership.workspace_id)
    await _make_issue(db_session, membership.workspace_id, run_id, created_at=_T0)
    await _make_issue(db_session, membership.workspace_id, run_id, created_at=_T0 + timedelta(days=1))
    await db_session.commit()

    recent, _ = await list_friction_issues(
        db_session, membership.workspace_id, since=_T0 + timedelta(hours=12)
    )
    assert len(recent) == 1


async def test_list_friction_issues_paginates_with_cursor(db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-page-{uuid.uuid4().hex[:8]}@example.com"
    )
    run_id = await _make_run(db_session, membership.workspace_id)
    for i in range(3):
        await _make_issue(db_session, membership.workspace_id, run_id, created_at=_T0 + timedelta(minutes=i))
    await db_session.commit()

    page_one, cursor = await list_friction_issues(db_session, membership.workspace_id, limit=2)
    assert len(page_one) == 2
    assert cursor is not None

    page_two, cursor_two = await list_friction_issues(
        db_session, membership.workspace_id, limit=2, cursor=cursor
    )
    assert len(page_two) == 1
    assert cursor_two is None
    assert {i.id for i in page_one} & {i.id for i in page_two} == set()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_insights.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'flowsage_backend.insights'`.

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/insights.py`:

```python
"""Compute-on-demand queries backing the public `/v1/insights/*` API
(`api/insights.py`). No new tables -- mirrors `calibration.py`/`churn.py`."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.simulation import FrictionIssue


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at_str, id_str = cursor.split("|", 1)
    return datetime.fromisoformat(created_at_str), uuid.UUID(id_str)


def _encode_cursor(issue: FrictionIssue) -> str:
    return f"{issue.created_at.isoformat()}|{issue.id}"


async def list_friction_issues(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    severity: str | None = None,
    screen: str | None = None,
    since: datetime | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[FrictionIssue], str | None]:
    query = select(FrictionIssue).where(FrictionIssue.workspace_id == workspace_id)
    if severity is not None:
        query = query.where(FrictionIssue.severity == severity)
    if screen is not None:
        query = query.where(FrictionIssue.screen == screen)
    if since is not None:
        query = query.where(FrictionIssue.created_at >= since)
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_cursor(cursor)
        query = query.where(
            or_(
                FrictionIssue.created_at < cursor_created_at,
                and_(
                    FrictionIssue.created_at == cursor_created_at,
                    FrictionIssue.id < cursor_id,
                ),
            )
        )
    query = query.order_by(FrictionIssue.created_at.desc(), FrictionIssue.id.desc()).limit(limit + 1)

    result = await session.execute(query)
    rows = list(result.scalars().all())
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more else None
    return page, next_cursor
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_insights.py -v
```

Expected: PASS, all 4 tests.

- [ ] **Step 5: Type-check and format**

```bash
cd backend
uv run autoflake8 --in-place --remove-all-unused-imports src/flowsage_backend/insights.py tests/test_insights.py
uv run black src/flowsage_backend/insights.py tests/test_insights.py
uv run mypy --strict src
```

Expected: mypy reports no issues.

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/insights.py backend/tests/test_insights.py
git commit -m "feat: add list_friction_issues compute-on-demand query for insights API"
```

---

### Task 2: `api/insights.py` router — funnel + friction-issues endpoints

**Files:**
- Create: `backend/src/flowsage_backend/api/insights.py`
- Modify: `backend/src/flowsage_backend/main.py`
- Test: `backend/tests/test_insights_api.py`

**Interfaces:**
- Consumes: `flowsage_backend.insights.list_friction_issues` (Task 1), `flowsage_backend.events.build_funnel_report` (existing, unchanged), `flowsage_backend.deps.require_workspace_api_key` (existing, unchanged: `async def require_workspace_api_key(request: Request, session: AsyncSession = Depends(get_db_session)) -> uuid.UUID`).
- Produces: `insights_router: APIRouter` importable from `flowsage_backend.api.insights`, mounted in `main.py`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_insights_api.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.event import Event
from flowsage_backend.models.persona import Persona
from flowsage_backend.models.simulation import FrictionIssue, RunStatus, SimulationRun
from tests.conftest import create_api_key_for, create_workspace_and_admin

_T0 = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


async def _make_run(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    """Duplicated from `test_insights.py` on purpose -- this codebase's
    convention (see `test_calibration_api.py` vs `test_calibration.py`) is each
    test file keeps its own local fixtures rather than importing across
    `test_*.py` modules."""
    persona = Persona(
        workspace_id=workspace_id,
        slug=f"insights-api-persona-{uuid.uuid4().hex[:8]}",
        name="Insights API Test Persona",
        description="d",
        baseline=False,
        tech_affinity="medium",
        primary_device="desktop",
        discovery_mode="search",
        contextual_triggers=[],
        technical_literacy=0.5,
        anxiety=0.5,
        patience=0.5,
        curiosity=0.5,
    )
    session.add(persona)
    await session.flush()

    run = SimulationRun(
        workspace_id=workspace_id,
        flow_name="checkout",
        goal="buy",
        persona_id=persona.id,
        screenshots_dir="/tmp/x",
        status=RunStatus.COMPLETED,
        finished_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()
    return run.id


async def _make_issue(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    created_at: datetime | None = None,
) -> FrictionIssue:
    issue = FrictionIssue(
        workspace_id=workspace_id,
        run_id=run_id,
        screen="checkout",
        severity="high",
        title="Confusing CTA",
        heuristic_violated="Visibility of system status",
        persona_impact="Anxious users abandon.",
        description="The primary button is unlabeled.",
        suggested_fix="Add a clear label.",
    )
    session.add(issue)
    await session.flush()
    if created_at is not None:
        issue.created_at = created_at
        await session.flush()
    return issue


async def test_insights_funnel_requires_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/insights/funnel")

    assert response.status_code == 401


async def test_insights_friction_issues_requires_api_key(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/insights/friction-issues")

    assert response.status_code == 401


async def test_insights_funnel_returns_workspace_scoped_data(
    app: FastAPI, db_session: AsyncSession
) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-api-funnel-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key = await create_api_key_for(db_session, membership.workspace_id)
    db_session.add_all(
        [
            Event(
                workspace_id=membership.workspace_id,
                session_id="s1",
                screen="landing",
                event="screen_view",
                timestamp=_T0,
                device="mobile",
                cohort="paid_users",
            ),
            Event(
                workspace_id=membership.workspace_id,
                session_id="s1",
                screen="checkout",
                event="screen_view",
                timestamp=_T0 + timedelta(minutes=1),
                device="mobile",
                cohort="paid_users",
            ),
        ]
    )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/insights/funnel", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    body = response.json()
    assert body["total_sessions"] == 1
    assert {step["screen"] for step in body["funnel"]} == {"landing", "checkout"}


async def test_insights_friction_issues_returns_workspace_scoped_data(
    app: FastAPI, db_session: AsyncSession
) -> None:
    _, membership_a = await create_workspace_and_admin(
        db_session, f"insights-api-issues-a-{uuid.uuid4().hex[:8]}@example.com"
    )
    _, membership_b = await create_workspace_and_admin(
        db_session, f"insights-api-issues-b-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key_a = await create_api_key_for(db_session, membership_a.workspace_id)
    run_a = await _make_run(db_session, membership_a.workspace_id)
    run_b = await _make_run(db_session, membership_b.workspace_id)
    await _make_issue(db_session, membership_a.workspace_id, run_a)
    await _make_issue(db_session, membership_b.workspace_id, run_b)
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/v1/insights/friction-issues", headers={"X-API-Key": api_key_a}
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["issues"]) == 1
    assert body["next_cursor"] is None


async def test_insights_friction_issues_paginates(app: FastAPI, db_session: AsyncSession) -> None:
    _, membership = await create_workspace_and_admin(
        db_session, f"insights-api-page-{uuid.uuid4().hex[:8]}@example.com"
    )
    api_key = await create_api_key_for(db_session, membership.workspace_id)
    run_id = await _make_run(db_session, membership.workspace_id)
    for i in range(3):
        await _make_issue(
            db_session, membership.workspace_id, run_id, created_at=_T0 + timedelta(minutes=i)
        )
    await db_session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get(
            "/v1/insights/friction-issues",
            params={"limit": 2},
            headers={"X-API-Key": api_key},
        )
        assert first.status_code == 200
        first_body = first.json()
        assert len(first_body["issues"]) == 2
        assert first_body["next_cursor"] is not None

        second = await client.get(
            "/v1/insights/friction-issues",
            params={"limit": 2, "cursor": first_body["next_cursor"]},
            headers={"X-API-Key": api_key},
        )
        assert second.status_code == 200
        second_body = second.json()
        assert len(second_body["issues"]) == 1
        assert second_body["next_cursor"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_insights_api.py -v
```

Expected: FAIL — `404 Not Found` on both routes (router not yet registered), plus `ModuleNotFoundError` if `flowsage_backend.api.insights` doesn't exist yet (import happens inside the test module indirectly via `main` once registered — first run will fail at collection or with 404s from the unmodified app).

- [ ] **Step 3: Write the implementation**

Create `backend/src/flowsage_backend/api/insights.py`:

```python
"""Public, API-key-authenticated read endpoints for external integrations
(`/v1/insights/...`). Reuses the same `require_workspace_api_key` dependency
`POST /v1/events` already uses -- no new auth mechanism. The `APIKeyHeader`
security scheme below is purely additive documentation: it makes Swagger UI
show an Authorize control for this router, but `require_workspace_api_key`
still independently reads and validates the header itself."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import APIKeyHeader
from flowsage_graph.models import FunnelReport
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_db_session, require_workspace_api_key
from flowsage_backend.events import build_funnel_report
from flowsage_backend.insights import list_friction_issues

_api_key_header_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

insights_router = APIRouter(
    prefix="/v1/insights",
    tags=["insights"],
    dependencies=[Security(_api_key_header_scheme)],
)


class FrictionIssueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    screen: str
    severity: str
    title: str
    heuristic_violated: str
    persona_impact: str
    description: str
    suggested_fix: str
    created_at: datetime


class FrictionIssuePageOut(BaseModel):
    issues: list[FrictionIssueOut]
    next_cursor: str | None


@insights_router.get("/funnel", response_model=FunnelReport)
async def insights_funnel(
    cohort: str | None = Query(default=None),
    device: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    workspace_id: uuid.UUID = Depends(require_workspace_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> FunnelReport:
    return await build_funnel_report(session, workspace_id, cohort=cohort, device=device, since=since)


@insights_router.get("/friction-issues", response_model=FrictionIssuePageOut)
async def insights_friction_issues(
    severity: str | None = Query(default=None),
    screen: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    workspace_id: uuid.UUID = Depends(require_workspace_api_key),
    session: AsyncSession = Depends(get_db_session),
) -> FrictionIssuePageOut:
    issues, next_cursor = await list_friction_issues(
        session,
        workspace_id,
        severity=severity,
        screen=screen,
        since=since,
        cursor=cursor,
        limit=limit,
    )
    return FrictionIssuePageOut(
        issues=[FrictionIssueOut.model_validate(i) for i in issues], next_cursor=next_cursor
    )
```

Modify `backend/src/flowsage_backend/main.py` — add the import and registration, and the OpenAPI metadata:

```python
from flowsage_backend.api.integrations import router as integrations_router
from flowsage_backend.api.insights import insights_router
from flowsage_backend.api.onboarding import router as onboarding_router
```

(insert the `insights_router` import alphabetically between `integrations` and `onboarding` imports)

```python
    app = FastAPI(
        title="FlowSage API",
        description=(
            "FlowSage's predictive & observed UX intelligence platform. "
            "`/v1/insights/*` endpoints are public, API-key-authenticated "
            "(`X-API-Key` header) read endpoints for external integrations; "
            "everything else requires a browser session."
        ),
        version="0.1.0",
        openapi_tags=[
            {
                "name": "insights",
                "description": "Public, API-key-authenticated read endpoints for external integrations.",
            }
        ],
        lifespan=_lifespan,
    )
```

(replace the existing `app = FastAPI(title="FlowSage API", lifespan=_lifespan)` line)

```python
    app.include_router(onboarding_router)
    app.include_router(insights_router)
```

(add `app.include_router(insights_router)` after the existing `app.include_router(onboarding_router)` line)

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_insights_api.py -v
```

Expected: PASS, all 5 tests.

- [ ] **Step 5: Run the full backend suite**

```bash
cd backend && uv run pytest -q
```

Expected: all tests pass (196 existing + 4 from Task 1 + 5 from this task = 205).

- [ ] **Step 6: Type-check and format**

```bash
cd backend
uv run autoflake8 --in-place --remove-all-unused-imports \
  src/flowsage_backend/api/insights.py src/flowsage_backend/main.py tests/test_insights_api.py
uv run black src/flowsage_backend/api/insights.py src/flowsage_backend/main.py tests/test_insights_api.py
uv run mypy --strict src
```

Expected: mypy reports no issues.

- [ ] **Step 7: Verify OpenAPI docs render**

```bash
cd backend && uv run python -c "
from flowsage_backend.config import Settings
from flowsage_backend.main import create_app
app = create_app(Settings(database_url='postgresql+asyncpg://x/x', redis_url='redis://x', jwt_secret='dev-only-not-a-real-secret', environment='development'))
schema = app.openapi()
assert 'insights' in [t['name'] for t in schema['tags']]
assert '/v1/insights/funnel' in schema['paths']
assert '/v1/insights/friction-issues' in schema['paths']
print('OpenAPI schema OK:', schema['info']['version'])
"
```

Expected: prints `OpenAPI schema OK: 0.1.0` with no assertion errors.

- [ ] **Step 8: Commit**

```bash
git add backend/src/flowsage_backend/api/insights.py backend/src/flowsage_backend/main.py backend/tests/test_insights_api.py
git commit -m "feat: add public /v1/insights funnel and friction-issues endpoints"
```

---

### Task 3: Load-test script for `POST /v1/events`

**Files:**
- Create: `scripts/load_test/ingest_load_test.py`
- Create: `scripts/load_test/README.md`

**Interfaces:**
- Consumes: nothing from earlier tasks (standalone script, hits the live HTTP API).
- Produces: nothing consumed by later tasks — this is the final deliverable of the "load test ingestion" hardening bullet.

- [ ] **Step 1: Write the script**

Create `scripts/load_test/ingest_load_test.py`:

```python
"""Manual load-test tool for `POST /v1/events`. Not part of the pytest suite or
CI -- run by hand against a live stack (see README.md in this directory).

Usage:
    uv run --project backend python scripts/load_test/ingest_load_test.py \\
        --url http://localhost:8000 --api-key fs_... --concurrency 20 --total 2000
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx


@dataclass
class Result:
    latencies_ms: list[float] = field(default_factory=list)
    errors: int = 0


async def _send_one(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    url: str,
    api_key: str,
    batch_size: int,
    result: Result,
) -> None:
    session_id = str(uuid.uuid4())
    payload = [
        {
            "session_id": session_id,
            "screen": "load-test-screen",
            "event": "screen_view",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "device": "load-test",
            "cohort": "load-test",
        }
        for _ in range(batch_size)
    ]
    async with semaphore:
        start = time.perf_counter()
        try:
            response = await client.post(url, json=payload, headers={"X-API-Key": api_key})
            elapsed_ms = (time.perf_counter() - start) * 1000
            if response.status_code != 201:
                result.errors += 1
            result.latencies_ms.append(elapsed_ms)
        except httpx.HTTPError:
            result.errors += 1


async def run_load_test(
    base_url: str, api_key: str, concurrency: int, total: int, batch_size: int
) -> Result:
    url = f"{base_url.rstrip('/')}/v1/events"
    result = Result()
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=30.0) as client:
        await asyncio.gather(
            *(_send_one(client, semaphore, url, api_key, batch_size, result) for _ in range(total))
        )
    return result


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * pct), len(ordered) - 1)
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load-test POST /v1/events")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--total", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=1)
    args = parser.parse_args()

    start = time.perf_counter()
    result = asyncio.run(
        run_load_test(args.url, args.api_key, args.concurrency, args.total, args.batch_size)
    )
    wall_seconds = time.perf_counter() - start

    print(f"requests: {args.total}  errors: {result.errors}  wall: {wall_seconds:.2f}s")
    print(f"throughput: {args.total / wall_seconds:.1f} req/s")
    if result.latencies_ms:
        print(f"latency p50: {_percentile(result.latencies_ms, 0.50):.1f}ms")
        print(f"latency p95: {_percentile(result.latencies_ms, 0.95):.1f}ms")
        print(f"latency p99: {_percentile(result.latencies_ms, 0.99):.1f}ms")
        print(f"latency mean: {statistics.mean(result.latencies_ms):.1f}ms")
    error_rate = result.errors / args.total if args.total else 0.0
    print(f"error rate: {error_rate:.1%}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the README**

Create `scripts/load_test/README.md`:

```markdown
# Load test: `POST /v1/events`

Manual tool, not part of CI or the pytest suite. Hits a live, already-running
backend the same way `frontend/e2e/README.md`'s tests do.

## Setup

1. Bring up the stack: `docker compose -f infra/docker-compose.yml up -d --build`
2. Migrate + create a user + create an API key via `/settings/integrations` in
   the running frontend (or `flowsage-backend create-user` + the API for a key).
3. Run:

```bash
uv run --project backend python scripts/load_test/ingest_load_test.py \
  --url http://localhost:8000 --api-key <your-key> --concurrency 20 --total 2000
```

## Reading the output

- `throughput`: requests/second sustained across the whole run.
- `latency p50/p95/p99`: per-request wall-clock time including the semaphore wait.
- `error rate`: fraction of requests that didn't return `201`.

There's no pass/fail threshold baked in — this is a single-container dev-compose
stack, not a production sizing target. The point is having a repeatable number to
compare against after future changes to the ingestion path, not a gate.

## Baseline run (recorded 2026-07-25)

Record actual numbers here after running Task 4's closeout verification pass.
```

- [ ] **Step 3: Verify the script runs (syntax/import check, no live server needed yet)**

```bash
cd /home/asus/Projects/personal/FlowSage
uv run --project backend python -c "import ast; ast.parse(open('scripts/load_test/ingest_load_test.py').read())"
uv run --project backend python scripts/load_test/ingest_load_test.py --help
```

Expected: argparse help text prints with no errors.

- [ ] **Step 4: Commit**

```bash
git add scripts/load_test/ingest_load_test.py scripts/load_test/README.md
git commit -m "test: add manual load-test script for POST /v1/events"
```

---

### Task 4: Full verification and merge

**Files:** none (verification + merge only)

- [ ] **Step 1: Full backend verification**

```bash
cd .claude/worktrees/phase4-insights-hardening/backend
uv run autoflake8 --check --remove-all-unused-imports -r src tests
uv run black --check src tests
uv run mypy --strict src
uv run pytest -q
```

Expected: all clean, all tests pass (205 total per Task 2's count).

- [ ] **Step 2: Bring up the full live stack**

```bash
cd .claude/worktrees/phase4-insights-hardening
docker compose -f infra/docker-compose.yml up -d --build
docker compose -f infra/docker-compose.yml exec backend \
  python -m alembic -c /workspace/backend/alembic.ini upgrade head
docker compose -f infra/docker-compose.yml exec backend \
  flowsage-backend create-user loadtest@flowsage.dev supersecret123
```

- [ ] **Step 3: Create a real API key and curl both new endpoints**

Log in via the frontend (or curl `/auth/login`) as `loadtest@flowsage.dev`, create an API key via `/settings/integrations`, then:

```bash
curl -s http://localhost:8000/v1/insights/funnel -H "X-API-Key: <key>" | head -c 300
curl -s http://localhost:8000/v1/insights/friction-issues -H "X-API-Key: <key>" | head -c 300
curl -s http://localhost:8000/v1/insights/funnel  # no key
```

Expected: first two return `200` with JSON bodies; the last returns `401`.

- [ ] **Step 4: Confirm OpenAPI docs render in a browser**

Visit `http://localhost:8000/docs`, confirm an "insights" tag section exists with both endpoints and an Authorize button that accepts `X-API-Key`.

- [ ] **Step 5: Run the load test against the live stack and record results**

```bash
uv run --project backend python scripts/load_test/ingest_load_test.py \
  --url http://localhost:8000 --api-key <key> --concurrency 20 --total 2000
```

Copy the printed output into `scripts/load_test/README.md`'s "Baseline run" section, then:

```bash
git add scripts/load_test/README.md
git commit -m "docs: record load-test baseline run"
```

- [ ] **Step 6: Tear down the stack**

```bash
docker compose -f infra/docker-compose.yml down
```

- [ ] **Step 7: Merge to main**

```bash
cd /home/asus/Projects/personal/FlowSage
git checkout main
git pull --ff-only
git merge --no-ff worktree-phase4-insights-hardening -m "feat: public Insights API + load-test tooling (Phase 4 chunk 1)"
git push origin main
```

- [ ] **Step 8: Remove the worktree**

```bash
git worktree remove .claude/worktrees/phase4-insights-hardening
git branch -d worktree-phase4-insights-hardening
```
