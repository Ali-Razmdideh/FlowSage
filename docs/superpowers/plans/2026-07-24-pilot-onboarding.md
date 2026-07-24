# Phase 3 Chunk 4 — Pilot Onboarding Tooling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a pilot user go from an empty workspace to a populated Journey Graph and Predictive Engine in one click, and give them a permanent `/getting-started` checklist page.

**Architecture:** A pure-function module (`onboarding.py`, no new tables — same "compute on demand" shape as `calibration.py`/`churn.py`) backs two new endpoints: `GET /onboarding/status` (4 cheap workspace-scoped existence checks) and `POST /onboarding/import-sample-data` (ingests the bundled `events.jsonl` via the existing `ingest_events()`, then creates + enqueues a `SimulationRun` via the existing `create_run()` + `run_simulation_job`, so onboarding reuses the exact same ingestion and simulation pipelines real users go through — no second code path). The sample dataset (44 events + 3 checkout screenshots), previously only reachable by the `flowsage-graph`/`flowsage-predict` CLIs, is copied into the backend package as `importlib.resources` data, mirroring how `flowsage_predict.baseline_personas` already ships YAML files the same way. On the frontend, one shared `ImportSampleDataButton` component is used both by a new `/getting-started` page and by the Journey Graph's existing empty state.

**Tech Stack:** FastAPI + SQLAlchemy async (backend), React + TypeScript + Vite (frontend), pytest + testcontainers (backend tests), Vitest + Testing Library (frontend tests), Playwright (e2e).

## Global Constraints

- The baseline persona used for the demo run is the one whose `slug` is **`novice`** (loaded from `flowsage_predict.baseline_personas`, seeded per-workspace by `seed_baseline_personas`) — the design spec calls it "novice_user" but the actual seeded slug in this codebase is `novice`; verify with `find_baseline_persona("novice")` (`scripts/flowsage-predict/tests/test_personas.py`) before assuming otherwise.
- Demo run uses the fixed goal `"Complete purchase"` and flow name `"Checkout Flow"` — matching `scripts/sample_data/README.md`'s own example invocation, so the demo run's copy in the UI reads consistently with the CLI's documented demo.
- No new database table for onboarding status — it is computed on demand from `ApiKey`, `Event`, `SimulationRun`, `Membership`, scoped to `membership.workspace_id`, matching `calibration.py`'s and `churn.py`'s "everything computed on demand, nothing persisted" philosophy.
- No idempotency guard on import — importing sample data twice ingests the same 44 events twice and queues a second demo run; this is intentionally left alone (see spec's "Out of scope").
- Events are ingested via the existing `ingest_events()` (Postgres) only — **not** mirrored into Neo4j. The Journey Graph UI's data (`GET /graph/funnel`) is built from `build_funnel_report()`, which reads Postgres `Event` rows directly (see `backend/src/flowsage_backend/events.py`), so a Neo4j mirror isn't needed for the empty state to populate. This narrows the spec's "reads bundled events.jsonl, calls ingest_events()" line — no additional Neo4j sink call.
- `/getting-started` is a permanent reference page, not a dismissible one-time wizard — no logic to hide it once all 4 checklist items are complete.
- Bundling the sample data needs **no `pyproject.toml` or Dockerfile changes**. `backend/Dockerfile` already does `COPY backend ./backend` before `uv sync --frozen --no-dev --package flowsage-backend` — a uv workspace member is installed pointing at its source tree, not copied into a separate wheel-only layout, so any file placed under `backend/src/flowsage_backend/` is on disk and importable at runtime exactly like `flowsage_predict/baseline_personas/*.yaml` already is today with zero special packaging config. Verified in Task 10's Docker pass, not assumed.

## File Structure

| File | Purpose |
|---|---|
| `backend/src/flowsage_backend/resources/__init__.py` | New empty package marker |
| `backend/src/flowsage_backend/resources/sample_data/__init__.py` | New empty package marker |
| `backend/src/flowsage_backend/resources/sample_data/events.jsonl` | Copy of `scripts/sample_data/events.jsonl` |
| `backend/src/flowsage_backend/resources/sample_data/screenshots/{01_cart,02_shipping,03_confirm}.png` | Copy of `scripts/sample_data/screenshots/*.png` |
| `backend/src/flowsage_backend/onboarding.py` | New — `OnboardingStatus`, `ImportSampleDataResult`, `get_onboarding_status()`, `import_sample_data()` |
| `backend/src/flowsage_backend/api/onboarding.py` | New — `GET /onboarding/status`, `POST /onboarding/import-sample-data` |
| `backend/src/flowsage_backend/main.py` | Modify — register the new router |
| `backend/tests/test_onboarding.py` | New — unit tests for `onboarding.py` |
| `backend/tests/test_onboarding_api.py` | New — endpoint + cross-tenant isolation tests |
| `frontend/src/lib/types.ts` | Modify — add `OnboardingStatus`, `ImportSampleDataResult` |
| `frontend/src/lib/api.ts` | Modify — add `getOnboardingStatus`, `importSampleData` |
| `frontend/src/components/ImportSampleDataButton.tsx` | New — shared button |
| `frontend/src/components/ImportSampleDataButton.test.tsx` | New |
| `frontend/src/routes/GettingStartedPage.tsx` | New — `/getting-started` checklist page |
| `frontend/src/routes/GettingStartedPage.test.tsx` | New |
| `frontend/src/routes/journey/JourneyGraphPage.tsx` | Modify — empty state gains the shared button |
| `frontend/src/App.tsx` | Modify — add `/getting-started` route |
| `frontend/src/components/Sidebar.tsx` | Modify — add nav entry |
| `frontend/e2e/getting-started.spec.ts` | New — Playwright e2e |

---

### Task 1: Bundle the sample dataset as backend package resources

**Files:**
- Create: `backend/src/flowsage_backend/resources/__init__.py`
- Create: `backend/src/flowsage_backend/resources/sample_data/__init__.py`
- Create: `backend/src/flowsage_backend/resources/sample_data/events.jsonl` (copy of `scripts/sample_data/events.jsonl`)
- Create: `backend/src/flowsage_backend/resources/sample_data/screenshots/01_cart.png`, `02_shipping.png`, `03_confirm.png` (copies of `scripts/sample_data/screenshots/*.png`)
- Test: `backend/tests/test_onboarding.py` (new file, first test only in this task)

**Interfaces:**
- Produces: an importable resource package `flowsage_backend.resources.sample_data` containing `events.jsonl` (44 lines, one JSON object per line, each parseable by `flowsage_graph.ingest.load_events`) and a `screenshots/` directory with exactly 3 `.png` files. Later tasks resolve it via `importlib.resources.as_file(importlib.resources.files("flowsage_backend.resources.sample_data"))`.

- [ ] **Step 1: Copy the files**

```bash
mkdir -p backend/src/flowsage_backend/resources/sample_data/screenshots
touch backend/src/flowsage_backend/resources/__init__.py
touch backend/src/flowsage_backend/resources/sample_data/__init__.py
cp scripts/sample_data/events.jsonl backend/src/flowsage_backend/resources/sample_data/events.jsonl
cp scripts/sample_data/screenshots/*.png backend/src/flowsage_backend/resources/sample_data/screenshots/
```

Add a one-line docstring to each new `__init__.py` (matching `scripts/flowsage-predict/src/flowsage_predict/baseline_personas/__init__.py`'s style):

`backend/src/flowsage_backend/resources/__init__.py`:
```python
"""Bundled, non-Python resources shipped inside the backend package."""
```

`backend/src/flowsage_backend/resources/sample_data/__init__.py`:
```python
"""Bundled copy of `scripts/sample_data/` (single source of truth stays there) --
powers the `/onboarding/import-sample-data` pilot onboarding action."""
```

- [ ] **Step 2: Write a failing sanity test that the bundle is importable and complete**

```python
# backend/tests/test_onboarding.py
from __future__ import annotations

from importlib import resources

from flowsage_graph.ingest import load_events


def test_bundled_sample_data_is_complete() -> None:
    with resources.as_file(
        resources.files("flowsage_backend.resources.sample_data")
    ) as sample_dir:
        events = load_events(sample_dir / "events.jsonl")
        screenshots = sorted(p.name for p in (sample_dir / "screenshots").glob("*.png"))

    assert len(events) == 44
    assert screenshots == ["01_cart.png", "02_shipping.png", "03_confirm.png"]
```

- [ ] **Step 3: Run the test**

Run: `cd backend && uv run pytest tests/test_onboarding.py -v`
Expected: PASS (the files already exist on disk from Step 1 — this step just proves they're reachable through `importlib.resources`, which is the mechanism the rest of the plan depends on)

- [ ] **Step 4: Commit**

```bash
git add backend/src/flowsage_backend/resources backend/tests/test_onboarding.py
git commit -m "feat: bundle sample dataset as backend package resources"
```

---

### Task 2: `onboarding.py` — `get_onboarding_status()`

**Files:**
- Modify: `backend/src/flowsage_backend/onboarding.py` (create — this task adds the status half only)
- Test: `backend/tests/test_onboarding.py` (append)

**Interfaces:**
- Consumes: `flowsage_backend.models.api_key.ApiKey`, `flowsage_backend.models.event.Event`, `flowsage_backend.models.simulation.SimulationRun`, `RunStatus`, `flowsage_backend.models.workspace.Membership`.
- Produces: `class OnboardingStatus(BaseModel)` with fields `has_api_key: bool`, `has_events: bool`, `has_completed_simulation: bool`, `has_multiple_members: bool`; `async def get_onboarding_status(session: AsyncSession, workspace_id: uuid.UUID) -> OnboardingStatus`. Task 4's router calls this directly.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_onboarding.py (append below the Task 1 test)
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership, Role, Workspace
from flowsage_backend.onboarding import get_onboarding_status
from flowsage_backend.security import generate_api_key, hash_api_key
from flowsage_backend.seed import seed_baseline_personas, upsert_user


async def _create_workspace(session: AsyncSession) -> uuid.UUID:
    workspace = Workspace(name="Test", slug=f"test-{uuid.uuid4().hex[:8]}")
    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)
    return workspace.id


async def test_get_onboarding_status_all_false_for_fresh_workspace(
    db_session: AsyncSession,
) -> None:
    workspace_id = await _create_workspace(db_session)

    status = await get_onboarding_status(db_session, workspace_id)

    assert status.has_api_key is False
    assert status.has_events is False
    assert status.has_completed_simulation is False
    assert status.has_multiple_members is False


async def test_get_onboarding_status_reflects_workspace_state(db_session: AsyncSession) -> None:
    workspace_id = await _create_workspace(db_session)

    user_a = await upsert_user(db_session, "onb-status-a@example.com", "hunter2")
    db_session.add(Membership(user_id=user_a.id, workspace_id=workspace_id, role=Role.ADMIN))
    user_b = await upsert_user(db_session, "onb-status-b@example.com", "hunter2")
    db_session.add(Membership(user_id=user_b.id, workspace_id=workspace_id, role=Role.VIEWER))

    raw_key = generate_api_key()
    db_session.add(
        ApiKey(
            workspace_id=workspace_id,
            name="k",
            key_prefix=raw_key[:12],
            key_hash=hash_api_key(raw_key),
        )
    )
    db_session.add(
        Event(
            workspace_id=workspace_id,
            session_id="s1",
            screen="Landing",
            event="screen_view",
            timestamp=datetime.now(timezone.utc),
        )
    )
    personas = await seed_baseline_personas(db_session, workspace_id)
    db_session.add(
        SimulationRun(
            workspace_id=workspace_id,
            flow_name="Checkout",
            goal="goal",
            persona_id=personas[0].id,
            screenshots_dir="/tmp/x",
            status=RunStatus.COMPLETED,
        )
    )
    await db_session.commit()

    status = await get_onboarding_status(db_session, workspace_id)

    assert status.has_api_key is True
    assert status.has_events is True
    assert status.has_completed_simulation is True
    assert status.has_multiple_members is True


async def test_get_onboarding_status_ignores_revoked_api_keys(db_session: AsyncSession) -> None:
    workspace_id = await _create_workspace(db_session)
    raw_key = generate_api_key()
    db_session.add(
        ApiKey(
            workspace_id=workspace_id,
            name="k",
            key_prefix=raw_key[:12],
            key_hash=hash_api_key(raw_key),
            revoked_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    status = await get_onboarding_status(db_session, workspace_id)

    assert status.has_api_key is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_onboarding.py -v -k get_onboarding_status`
Expected: FAIL with `ModuleNotFoundError` / `ImportError: cannot import name 'get_onboarding_status'`

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/onboarding.py
"""Pilot onboarding tooling: a compute-on-demand checklist (`GET /onboarding/status`,
no new table -- same pattern as `calibration.py`/`churn.py`) and a one-click sample
data importer (`POST /onboarding/import-sample-data`) that reuses the exact same
`ingest_events()` and simulation pipeline (`create_run()` + `run_simulation_job`) a
real user's upload goes through -- see the Phase 3 chunk 4 design spec.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.api_key import ApiKey
from flowsage_backend.models.event import Event
from flowsage_backend.models.simulation import RunStatus, SimulationRun
from flowsage_backend.models.workspace import Membership


class OnboardingStatus(BaseModel):
    has_api_key: bool
    has_events: bool
    has_completed_simulation: bool
    has_multiple_members: bool


async def get_onboarding_status(session: AsyncSession, workspace_id: uuid.UUID) -> OnboardingStatus:
    has_api_key = (
        await session.execute(
            select(ApiKey.id)
            .where(ApiKey.workspace_id == workspace_id, ApiKey.revoked_at.is_(None))
            .limit(1)
        )
    ).first() is not None

    has_events = (
        await session.execute(select(Event.id).where(Event.workspace_id == workspace_id).limit(1))
    ).first() is not None

    has_completed_simulation = (
        await session.execute(
            select(SimulationRun.id)
            .where(
                SimulationRun.workspace_id == workspace_id,
                SimulationRun.status == RunStatus.COMPLETED,
            )
            .limit(1)
        )
    ).first() is not None

    member_count = (
        await session.execute(
            select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace_id)
        )
    ).scalar_one()

    return OnboardingStatus(
        has_api_key=has_api_key,
        has_events=has_events,
        has_completed_simulation=has_completed_simulation,
        has_multiple_members=member_count >= 2,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_onboarding.py -v -k get_onboarding_status`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/onboarding.py backend/tests/test_onboarding.py
git commit -m "feat: add get_onboarding_status()"
```

---

### Task 3: `onboarding.py` — `import_sample_data()`

**Files:**
- Modify: `backend/src/flowsage_backend/onboarding.py` (append)
- Test: `backend/tests/test_onboarding.py` (append)

**Interfaces:**
- Consumes: `create_run()` and `SimulationError` from `flowsage_backend.simulations` (`backend/src/flowsage_backend/simulations.py:44` / `:34`); `ingest_events()` from `flowsage_backend.events` (`backend/src/flowsage_backend/events.py:22`); `load_events(path: Path) -> list[Event]` from `flowsage_graph.ingest`.
- Produces: `async def import_sample_data(session, *, workspace_id: uuid.UUID, screenshots_dest_dir: Path, run_id: uuid.UUID | None = None) -> ImportSampleDataResult`. Task 5's router calls this, passing a `screenshots_dest_dir` it builds from `settings.upload_dir` (mirroring how `api/simulations.py:create_simulation` builds its own upload directory) and a `run_id` it picks upfront so the directory name and the `SimulationRun.id` match — exactly the pattern `create_run`'s own `run_id` parameter already documents.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_onboarding.py (append)
from pathlib import Path

import pytest
from sqlalchemy import select

from flowsage_backend.onboarding import import_sample_data
from flowsage_backend.simulations import SimulationError


async def test_import_sample_data_ingests_events_and_creates_run(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    await seed_baseline_personas(db_session, workspace_id)
    screenshots_dir = tmp_path / "run-screens"

    result = await import_sample_data(
        db_session, workspace_id=workspace_id, screenshots_dest_dir=screenshots_dir
    )

    assert result.events_ingested == 44

    events = (
        await db_session.execute(select(Event).where(Event.workspace_id == workspace_id))
    ).scalars().all()
    assert len(events) == 44

    run = await db_session.get(SimulationRun, result.run_id)
    assert run is not None
    assert run.workspace_id == workspace_id
    assert run.flow_name == "Checkout Flow"
    assert run.goal == "Complete purchase"
    assert run.status == RunStatus.QUEUED

    assert sorted(p.name for p in screenshots_dir.iterdir()) == [
        "01_cart.png",
        "02_shipping.png",
        "03_confirm.png",
    ]


async def test_import_sample_data_uses_the_novice_baseline_persona(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    personas = await seed_baseline_personas(db_session, workspace_id)
    novice = next(p for p in personas if p.slug == "novice")

    result = await import_sample_data(
        db_session, workspace_id=workspace_id, screenshots_dest_dir=tmp_path / "screens"
    )

    run = await db_session.get(SimulationRun, result.run_id)
    assert run is not None
    assert run.persona_id == novice.id


async def test_import_sample_data_raises_without_seeded_personas(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    workspace_id = await _create_workspace(db_session)
    # no seed_baseline_personas call -- this workspace has no "novice" persona

    with pytest.raises(SimulationError):
        await import_sample_data(
            db_session, workspace_id=workspace_id, screenshots_dest_dir=tmp_path / "screens"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_onboarding.py -v -k import_sample_data`
Expected: FAIL with `ImportError: cannot import name 'import_sample_data'`

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/onboarding.py (append)
# Add these imports to the existing import block at the top of the file
# (Task 2 left it with only uuid / pydantic / sqlalchemy / the 4 models it
# queries -- this task's function needs the rest):
#   import shutil
#   from importlib import resources
#   from pathlib import Path
#   from flowsage_graph.ingest import load_events
#   from flowsage_backend.events import ingest_events
#   from flowsage_backend.models.persona import Persona
#   from flowsage_backend.simulations import SimulationError, create_run

SAMPLE_DATA_PACKAGE = "flowsage_backend.resources.sample_data"
SAMPLE_PERSONA_SLUG = "novice"
SAMPLE_GOAL = "Complete purchase"
SAMPLE_FLOW_NAME = "Checkout Flow"


class ImportSampleDataResult(BaseModel):
    events_ingested: int
    run_id: uuid.UUID


async def import_sample_data(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    screenshots_dest_dir: Path,
    run_id: uuid.UUID | None = None,
) -> ImportSampleDataResult:
    persona = (
        await session.execute(
            select(Persona).where(
                Persona.workspace_id == workspace_id, Persona.slug == SAMPLE_PERSONA_SLUG
            )
        )
    ).scalar_one_or_none()
    if persona is None:
        raise SimulationError(
            f"No {SAMPLE_PERSONA_SLUG!r} baseline persona in this workspace -- "
            "seed_baseline_personas() should have created it at signup"
        )

    with resources.as_file(resources.files(SAMPLE_DATA_PACKAGE)) as sample_dir:
        graph_events = load_events(sample_dir / "events.jsonl")
        rows = await ingest_events(session, workspace_id, graph_events)

        screenshots_dest_dir.mkdir(parents=True, exist_ok=True)
        for screenshot in sorted((sample_dir / "screenshots").glob("*.png")):
            shutil.copy(screenshot, screenshots_dest_dir / screenshot.name)

    run = await create_run(
        session,
        workspace_id=workspace_id,
        run_id=run_id,
        persona_id=persona.id,
        flow_name=SAMPLE_FLOW_NAME,
        goal=SAMPLE_GOAL,
        screenshots_dir=screenshots_dest_dir,
    )
    return ImportSampleDataResult(events_ingested=len(rows), run_id=run.id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_onboarding.py -v`
Expected: PASS (all tests in the file, 7 total)

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/onboarding.py backend/tests/test_onboarding.py
git commit -m "feat: add import_sample_data()"
```

---

### Task 4: `api/onboarding.py` — `GET /onboarding/status` + router wiring

**Files:**
- Create: `backend/src/flowsage_backend/api/onboarding.py`
- Modify: `backend/src/flowsage_backend/main.py:10-21` (imports), `:44-55` (`include_router` calls)
- Test: `backend/tests/test_onboarding_api.py` (new file)

**Interfaces:**
- Consumes: `get_current_membership`, `get_db_session` from `flowsage_backend.deps` (`backend/src/flowsage_backend/deps.py:29`, `:24`); `get_onboarding_status` from Task 2.
- Produces: `router = APIRouter(prefix="/onboarding", ...)` with `router.onboarding_router` name for `main.py`'s import (`from flowsage_backend.api.onboarding import router as onboarding_router`). Task 5 appends the import endpoint to this same router.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_onboarding_api.py
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.models.workspace import Membership
from flowsage_backend.seed import upsert_user


@asynccontextmanager
async def _authed_client(app: FastAPI, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/auth/login", json={"email": email, "password": "hunter2"})
        yield client


async def _onboarding_workspace_id(db_session: AsyncSession, email: str) -> uuid.UUID:
    user = await upsert_user(db_session, email, "hunter2")
    membership = (
        await db_session.execute(select(Membership).where(Membership.user_id == user.id))
    ).scalar_one()
    return membership.workspace_id


async def test_onboarding_status_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/onboarding/status")

    assert response.status_code == 401


async def test_onboarding_status_all_false_for_fresh_workspace(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-api-{uuid.uuid4().hex[:8]}@example.com"
    await _onboarding_workspace_id(db_session, email)

    async with _authed_client(app, email) as client:
        response = await client.get("/onboarding/status")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "has_api_key": False,
        "has_events": False,
        "has_completed_simulation": False,
        "has_multiple_members": False,
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_onboarding_api.py -v`
Expected: FAIL with 404 (route doesn't exist yet) on both tests

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/api/onboarding.py
"""Pilot onboarding endpoints: `GET /onboarding/status` (checklist) and
`POST /onboarding/import-sample-data` (Task 5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_membership, get_db_session
from flowsage_backend.models.user import User
from flowsage_backend.models.workspace import Membership
from flowsage_backend.onboarding import OnboardingStatus, get_onboarding_status

router = APIRouter(
    prefix="/onboarding", tags=["onboarding"], dependencies=[Depends(get_current_membership)]
)


@router.get("/status", response_model=OnboardingStatus)
async def onboarding_status(
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> OnboardingStatus:
    _, membership = membership_pair
    return await get_onboarding_status(session, membership.workspace_id)
```

Wire it into `main.py`. Add the import next to the other `api.*` imports (`backend/src/flowsage_backend/main.py:19`, right after the `integrations` import):

```python
from flowsage_backend.api.integrations import router as integrations_router
from flowsage_backend.api.onboarding import router as onboarding_router
```

Add the include next to the other `include_router` calls (`backend/src/flowsage_backend/main.py:55`, right after `integrations_router`):

```python
    app.include_router(integrations_router)
    app.include_router(onboarding_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_onboarding_api.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/flowsage_backend/api/onboarding.py backend/src/flowsage_backend/main.py backend/tests/test_onboarding_api.py
git commit -m "feat: add GET /onboarding/status"
```

---

### Task 5: `POST /onboarding/import-sample-data` + cross-tenant isolation test

**Files:**
- Modify: `backend/src/flowsage_backend/api/onboarding.py` (append endpoint)
- Test: `backend/tests/test_onboarding_api.py` (append)

**Interfaces:**
- Consumes: `import_sample_data` from Task 3; `request.app.state.settings.upload_dir` and `request.app.state.arq_pool.enqueue_job("run_simulation_job", str(run_id))` — the exact same two calls `api/simulations.py:create_simulation` (`backend/src/flowsage_backend/api/simulations.py:96-124`) already makes.
- Produces: `POST /onboarding/import-sample-data` → `ImportSampleDataResult` (`{events_ingested: int, run_id: str}`), status 201.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_onboarding_api.py (append)
from flowsage_backend.onboarding import get_onboarding_status
from flowsage_backend.seed import seed_baseline_personas


async def test_import_sample_data_endpoint_ingests_and_creates_run(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-import-{uuid.uuid4().hex[:8]}@example.com"
    workspace_id = await _onboarding_workspace_id(db_session, email)
    await seed_baseline_personas(db_session, workspace_id)

    async with _authed_client(app, email) as client:
        response = await client.post("/onboarding/import-sample-data")

    assert response.status_code == 201
    body = response.json()
    assert body["events_ingested"] == 44
    assert uuid.UUID(body["run_id"])


async def test_import_sample_data_requires_authentication(app: FastAPI) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/onboarding/import-sample-data")

    assert response.status_code == 401


async def test_import_sample_data_rejects_workspace_without_novice_persona(
    app: FastAPI, db_session: AsyncSession
) -> None:
    email = f"onb-nopersona-{uuid.uuid4().hex[:8]}@example.com"
    await _onboarding_workspace_id(db_session, email)
    # deliberately skip seed_baseline_personas

    async with _authed_client(app, email) as client:
        response = await client.post("/onboarding/import-sample-data")

    assert response.status_code == 422


async def test_import_sample_data_is_workspace_scoped(
    app: FastAPI, db_session: AsyncSession
) -> None:
    """Cross-tenant isolation: importing into workspace A must not affect
    workspace B's onboarding status or event count."""
    email_a = f"onb-isolation-a-{uuid.uuid4().hex[:8]}@example.com"
    email_b = f"onb-isolation-b-{uuid.uuid4().hex[:8]}@example.com"
    workspace_a = await _onboarding_workspace_id(db_session, email_a)
    workspace_b = await _onboarding_workspace_id(db_session, email_b)
    await seed_baseline_personas(db_session, workspace_a)

    async with _authed_client(app, email_a) as client_a:
        import_response = await client_a.post("/onboarding/import-sample-data")
    assert import_response.status_code == 201

    async with _authed_client(app, email_b) as client_b:
        status_response = await client_b.get("/onboarding/status")

    assert status_response.json()["has_events"] is False
    other_status = await get_onboarding_status(db_session, workspace_b)
    assert other_status.has_events is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_onboarding_api.py -v -k import_sample_data`
Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Implement**

```python
# backend/src/flowsage_backend/api/onboarding.py (append)
import uuid
from pathlib import Path

from fastapi import HTTPException, Request, status

from flowsage_backend.onboarding import ImportSampleDataResult, import_sample_data
from flowsage_backend.simulations import SimulationError


@router.post(
    "/import-sample-data", response_model=ImportSampleDataResult, status_code=status.HTTP_201_CREATED
)
async def import_sample_data_endpoint(
    request: Request,
    membership_pair: tuple[User, Membership] = Depends(get_current_membership),
    session: AsyncSession = Depends(get_db_session),
) -> ImportSampleDataResult:
    _, membership = membership_pair
    settings = request.app.state.settings
    run_id = uuid.uuid4()
    screenshots_dir = Path(settings.upload_dir) / str(run_id)

    try:
        result = await import_sample_data(
            session,
            workspace_id=membership.workspace_id,
            run_id=run_id,
            screenshots_dest_dir=screenshots_dir,
        )
    except SimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await request.app.state.arq_pool.enqueue_job("run_simulation_job", str(result.run_id))
    return result
```

Move the `import uuid` and `from pathlib import Path` lines to the top of the file alongside the existing imports (Python style — no mid-file imports); the block above shows them inline only to mark what's new for this step.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_onboarding_api.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Full backend suite + lint/type check**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/ && uv run autoflake8 --check -r src/`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add backend/src/flowsage_backend/api/onboarding.py backend/tests/test_onboarding_api.py
git commit -m "feat: add POST /onboarding/import-sample-data"
```

---

### Task 6: Frontend — types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts` (append, near `AuditLogEntry`/`AuditLogPage` at the end of the file)
- Modify: `frontend/src/lib/api.ts:1-37` (type import list), append to the `api` object (near `getAuditLogs`, `frontend/src/lib/api.ts:307-313`)

**Interfaces:**
- Produces: `OnboardingStatus`, `ImportSampleDataResult` types; `api.getOnboardingStatus()`, `api.importSampleData()`. Tasks 7-9 consume these.

- [ ] **Step 1: Add the types**

Append to the end of `frontend/src/lib/types.ts`:

```typescript
export interface OnboardingStatus {
  has_api_key: boolean;
  has_events: boolean;
  has_completed_simulation: boolean;
  has_multiple_members: boolean;
}

export interface ImportSampleDataResult {
  events_ingested: number;
  run_id: string;
}
```

- [ ] **Step 2: Add the types to `api.ts`'s import list**

In `frontend/src/lib/api.ts`, add to the `import type { ... } from "./types";` block (alphabetically, so between `NodeIntelligence` and `Persona`):

```typescript
  ImportSampleDataResult,
```

and (alphabetically, after `NodeIntelligence`, before `Persona`):

```typescript
  NodeIntelligence,
  OnboardingStatus,
  Persona,
```

- [ ] **Step 3: Add the API methods**

Append to the `api` object in `frontend/src/lib/api.ts`, after `getAuditLogs` (`frontend/src/lib/api.ts:307-313`, before the closing `};`):

```typescript

  getOnboardingStatus: (): Promise<OnboardingStatus> =>
    request<OnboardingStatus>("/onboarding/status"),

  importSampleData: (): Promise<ImportSampleDataResult> =>
    request<ImportSampleDataResult>("/onboarding/import-sample-data", { method: "POST" }),
```

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat: add onboarding types and API client methods"
```

---

### Task 7: `ImportSampleDataButton` shared component

**Files:**
- Create: `frontend/src/components/ImportSampleDataButton.tsx`
- Test: `frontend/src/components/ImportSampleDataButton.test.tsx`

**Interfaces:**
- Consumes: `api.importSampleData()` from Task 6.
- Produces: `export function ImportSampleDataButton({ onImported }: { onImported?: () => void }): JSX.Element`. Tasks 8 and 9 both render this.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/ImportSampleDataButton.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { ImportSampleDataButton } from "./ImportSampleDataButton";

vi.mock("../lib/api", () => ({
  api: { importSampleData: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

describe("ImportSampleDataButton", () => {
  it("calls importSampleData and onImported on click", async () => {
    mockApi.importSampleData.mockResolvedValue({ events_ingested: 44, run_id: "run-1" });
    const onImported = vi.fn();

    render(<ImportSampleDataButton onImported={onImported} />);
    fireEvent.click(screen.getByRole("button", { name: /import sample data/i }));

    await waitFor(() => expect(onImported).toHaveBeenCalledTimes(1));
    expect(mockApi.importSampleData).toHaveBeenCalledTimes(1);
  });

  it("shows an error message when the import fails", async () => {
    mockApi.importSampleData.mockRejectedValue(new Error("boom"));

    render(<ImportSampleDataButton />);
    fireEvent.click(screen.getByRole("button", { name: /import sample data/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- ImportSampleDataButton`
Expected: FAIL — module not found

- [ ] **Step 3: Implement**

```tsx
// frontend/src/components/ImportSampleDataButton.tsx
import { useState } from "react";
import { api, ApiError } from "../lib/api";

export function ImportSampleDataButton({ onImported }: { onImported?: () => void }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setLoading(true);
    setError(null);
    try {
      await api.importSampleData();
      onImported?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to import sample data.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col items-center gap-2">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={loading}
        className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-medium text-on-primary hover:opacity-90 transition disabled:opacity-50"
      >
        <span className="material-symbols-outlined text-lg">download</span>
        {loading ? "Importing…" : "Import Sample Data"}
      </button>
      {error !== null ? (
        <p role="alert" className="text-xs text-error">
          {error}
        </p>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- ImportSampleDataButton`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ImportSampleDataButton.tsx frontend/src/components/ImportSampleDataButton.test.tsx
git commit -m "feat: add shared ImportSampleDataButton component"
```

---

### Task 8: `/getting-started` page + route + sidebar entry

**Files:**
- Create: `frontend/src/routes/GettingStartedPage.tsx`
- Test: `frontend/src/routes/GettingStartedPage.test.tsx`
- Modify: `frontend/src/App.tsx` (add import + route)
- Modify: `frontend/src/components/Sidebar.tsx:4-14` (add nav entry)

**Interfaces:**
- Consumes: `api.getOnboardingStatus()` (Task 6), `ImportSampleDataButton` (Task 7).
- Produces: route `/getting-started`, exported `GettingStartedPage` component.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/routes/GettingStartedPage.test.tsx
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../lib/api";
import { GettingStartedPage } from "./GettingStartedPage";

vi.mock("../lib/api", () => ({
  api: { getOnboardingStatus: vi.fn(), importSampleData: vi.fn() },
  ApiError: class ApiError extends Error {},
}));

const mockApi = vi.mocked(api);

beforeEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  render(
    <MemoryRouter>
      <GettingStartedPage />
    </MemoryRouter>,
  );
}

describe("GettingStartedPage", () => {
  it("renders all 4 checklist items reflecting status", async () => {
    mockApi.getOnboardingStatus.mockResolvedValue({
      has_api_key: true,
      has_events: false,
      has_completed_simulation: false,
      has_multiple_members: false,
    });

    renderPage();

    await waitFor(() => expect(screen.getByText("Create an API key")).toBeInTheDocument());
    expect(screen.getByText("Ingest your first event")).toBeInTheDocument();
    expect(screen.getByText("Run your first simulation")).toBeInTheDocument();
    expect(screen.getByText("Invite a teammate")).toBeInTheDocument();
  });

  it("refetches status after a successful sample data import", async () => {
    mockApi.getOnboardingStatus
      .mockResolvedValueOnce({
        has_api_key: false,
        has_events: false,
        has_completed_simulation: false,
        has_multiple_members: false,
      })
      .mockResolvedValueOnce({
        has_api_key: false,
        has_events: true,
        has_completed_simulation: false,
        has_multiple_members: false,
      });
    mockApi.importSampleData.mockResolvedValue({ events_ingested: 44, run_id: "run-1" });

    renderPage();
    await waitFor(() => expect(screen.getByRole("button", { name: /import sample data/i })).toBeInTheDocument());

    screen.getByRole("button", { name: /import sample data/i }).click();

    await waitFor(() => expect(mockApi.getOnboardingStatus).toHaveBeenCalledTimes(2));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- GettingStartedPage`
Expected: FAIL — module not found

- [ ] **Step 3: Implement the page**

```tsx
// frontend/src/routes/GettingStartedPage.tsx
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ImportSampleDataButton } from "../components/ImportSampleDataButton";
import { api, ApiError } from "../lib/api";
import type { OnboardingStatus } from "../lib/types";

const CHECKLIST: { key: keyof OnboardingStatus; label: string; to: string }[] = [
  { key: "has_api_key", label: "Create an API key", to: "/settings/integrations" },
  { key: "has_events", label: "Ingest your first event", to: "/getting-started" },
  { key: "has_completed_simulation", label: "Run your first simulation", to: "/predictive" },
  { key: "has_multiple_members", label: "Invite a teammate", to: "/settings/team" },
];

export function GettingStartedPage() {
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(() => {
    api
      .getOnboardingStatus()
      .then(setStatus)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load onboarding status.");
      });
  }, []);

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  return (
    <div className="flex flex-col gap-6 p-8 max-w-2xl">
      <div>
        <h1 className="font-headline text-2xl">Getting Started</h1>
        <p className="text-sm text-on-surface-variant mt-1">
          Four steps to get FlowSage fully wired up for your team.
        </p>
      </div>

      {error !== null ? (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      ) : null}

      {status === null ? (
        <p className="text-on-surface-variant text-sm">Loading…</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {CHECKLIST.map((item) => (
            <li key={item.key}>
              <Link
                to={item.to}
                className="flex items-center gap-3 ghost-border rounded-lg p-4 hover:bg-surface-container transition"
              >
                <span className="material-symbols-outlined text-lg text-primary">
                  {status[item.key] ? "check_circle" : "radio_button_unchecked"}
                </span>
                <span className={status[item.key] ? "text-on-surface-variant line-through" : ""}>
                  {item.label}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      <div className="bg-surface-container-lowest rounded-xl p-6 flex flex-col items-center gap-3">
        <p className="text-sm text-on-surface-variant text-center max-w-sm">
          Not ready to connect real data yet? Load a demo checkout flow to see the Journey Graph
          and Predictive Engine populated in one click.
        </p>
        <ImportSampleDataButton onImported={loadStatus} />
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire the route into `App.tsx`**

Add the import to `frontend/src/App.tsx` (after the `SecurityLogsPage` import):

```typescript
import { SecurityLogsPage } from "./routes/settings/SecurityLogsPage";
import { GettingStartedPage } from "./routes/GettingStartedPage";
```

Add the route (after `/settings/security`):

```tsx
          <Route path="/settings/security" element={<SecurityLogsPage />} />
          <Route path="/getting-started" element={<GettingStartedPage />} />
```

- [ ] **Step 5: Wire the sidebar entry**

In `frontend/src/components/Sidebar.tsx`, add to `NAV_ITEMS` (`frontend/src/components/Sidebar.tsx:4-14`), as the first entry so it's the most visible for a new pilot:

```typescript
const NAV_ITEMS = [
  { to: "/getting-started", label: "Getting Started", icon: "flag" },
  { to: "/dashboard", label: "Dashboard", icon: "dashboard" },
  { to: "/predictive", label: "Predictive Engine", icon: "psychology" },
  { to: "/journey", label: "Journey Graph", icon: "timeline" },
  { to: "/calibration", label: "Calibration", icon: "tune" },
  { to: "/settings/general", label: "General Settings", icon: "settings" },
  { to: "/settings/team", label: "Team Access", icon: "group" },
  { to: "/settings/model-calibration", label: "Model Calibration", icon: "tune" },
  { to: "/settings/integrations", label: "Integrations", icon: "hub" },
  { to: "/settings/security", label: "Security", icon: "shield" },
] as const;
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npm run test -- GettingStartedPage && npm run typecheck`
Expected: PASS, no type errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/GettingStartedPage.tsx frontend/src/routes/GettingStartedPage.test.tsx frontend/src/App.tsx frontend/src/components/Sidebar.tsx
git commit -m "feat: add /getting-started page, route, and sidebar entry"
```

---

### Task 9: Wire `ImportSampleDataButton` into the Journey Graph empty state

**Files:**
- Modify: `frontend/src/routes/journey/JourneyGraphPage.tsx`
- Modify: `frontend/src/routes/journey/JourneyGraphPage.test.tsx` (append a test — read the existing file first to match its mocking style before writing this step)

**Interfaces:**
- Consumes: `ImportSampleDataButton` from Task 7.

- [ ] **Step 1: Read the existing test file to match conventions**

Run: `cat frontend/src/routes/journey/JourneyGraphPage.test.tsx`

(No command output shown here — inspect the file's existing `vi.mock("../../lib/api", ...)` shape before writing Step 2, since this task adds `importSampleData` to that mock and the exact mock shape must match what's already there to avoid breaking existing tests.)

- [ ] **Step 2: Write the failing test**

Append to `frontend/src/routes/journey/JourneyGraphPage.test.tsx`, extending the existing `vi.mock("../../lib/api", ...)` factory to also include `importSampleData: vi.fn()` (alongside whatever `getFunnel`/`getChurnRisk`/etc. mocks already exist there), then add:

```typescript
it("shows an Import Sample Data button in the empty state and refetches the funnel on success", async () => {
  mockApi.getFunnel.mockResolvedValue({
    funnel: [],
    friction_nodes: [],
    total_sessions: 0,
    total_events: 0,
  });
  mockApi.importSampleData.mockResolvedValue({ events_ingested: 44, run_id: "run-1" });

  render(<JourneyGraphPage />);

  const importButton = await screen.findByRole("button", { name: /import sample data/i });
  fireEvent.click(importButton);

  await waitFor(() => expect(mockApi.getFunnel).toHaveBeenCalledTimes(2));
});
```

(Add `fireEvent` to the existing `@testing-library/react` import at the top of the file if it isn't already imported there.)

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npm run test -- JourneyGraphPage`
Expected: FAIL — no "Import Sample Data" button in the empty state yet

- [ ] **Step 4: Implement**

Add the import at the top of `frontend/src/routes/journey/JourneyGraphPage.tsx` (`frontend/src/routes/journey/JourneyGraphPage.tsx:1-10`, after the `api`/`ApiError` import):

```typescript
import { api, ApiError } from "../../lib/api";
import { ImportSampleDataButton } from "../../components/ImportSampleDataButton";
```

Extract the funnel-loading effect body into a reusable callback so both the mount effect and the import button's success handler can trigger it. Replace the existing effect (`frontend/src/routes/journey/JourneyGraphPage.tsx:30-41`):

```typescript
  useEffect(() => {
    const filters = {
      ...(cohort && { cohort }),
      ...(device && { device }),
    };
    api
      .getFunnel(filters)
      .then(setReport)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load journey graph.");
      });
  }, [cohort, device]);
```

with:

```typescript
  const loadFunnel = useCallback(() => {
    const filters = {
      ...(cohort && { cohort }),
      ...(device && { device }),
    };
    api
      .getFunnel(filters)
      .then(setReport)
      .catch((err: unknown) => {
        setError(err instanceof ApiError ? err.message : "Failed to load journey graph.");
      });
  }, [cohort, device]);

  useEffect(() => {
    loadFunnel();
  }, [loadFunnel]);
```

Add `useCallback` to the existing `import { useEffect, useState } from "react";` line (`frontend/src/routes/journey/JourneyGraphPage.tsx:1`):

```typescript
import { useCallback, useEffect, useState } from "react";
```

Update the call site that renders `<EmptyState />` (`frontend/src/routes/journey/JourneyGraphPage.tsx:96`):

```tsx
        {report !== null && report.funnel.length === 0 ? (
          <EmptyState onImported={loadFunnel} />
        ) : (
```

Update the `EmptyState` function itself (`frontend/src/routes/journey/JourneyGraphPage.tsx:371-381`):

```tsx
function EmptyState({ onImported }: { onImported: () => void }) {
  return (
    <div className="bg-surface-container-lowest rounded-xl p-16 text-center flex flex-col items-center gap-4">
      <h2 className="font-headline text-2xl mb-2">Awaiting Event Ingestion</h2>
      <p className="text-on-surface-variant max-w-md mx-auto">
        The journey graph will materialize once events start arriving via{" "}
        <code className="font-mono text-sm">POST /v1/events</code>.
      </p>
      <ImportSampleDataButton onImported={onImported} />
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm run test -- JourneyGraphPage`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 6: Full frontend suite**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all green

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/journey/JourneyGraphPage.tsx frontend/src/routes/journey/JourneyGraphPage.test.tsx
git commit -m "feat: wire Import Sample Data button into Journey Graph empty state"
```

---

### Task 10: Playwright e2e + full verification pass

**Files:**
- Create: `frontend/e2e/getting-started.spec.ts`
- No other files (verification only beyond the new e2e spec)

**Interfaces:** none new — exercises the full stack built in Tasks 1-9.

- [ ] **Step 1: Write the e2e spec**

```typescript
// frontend/e2e/getting-started.spec.ts
import { test, expect } from "@playwright/test";

const EMAIL = "e2e@flowsage.dev";
const PASSWORD = "supersecret123";

test("Getting Started: import sample data populates the Journey Graph", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(EMAIL);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);

  await page.getByRole("link", { name: "Journey Graph" }).click();
  await expect(page).toHaveURL(/\/journey/);

  const importButton = page.getByRole("button", { name: /import sample data/i });
  if (await importButton.isVisible()) {
    await importButton.click();
    await expect(page.getByText("Discovered Funnel")).toBeVisible({ timeout: 15_000 });
  } else {
    // A prior test run in this environment already ingested events -- the
    // empty state (and its button) won't render, which is expected reuse,
    // not a failure.
    await expect(page.getByText("Discovered Funnel")).toBeVisible();
  }

  await page.getByRole("link", { name: "Getting Started" }).click();
  await expect(page).toHaveURL(/\/getting-started/);
  await expect(page.getByText("Run your first simulation")).toBeVisible();
});
```

- [ ] **Step 2: Backend full suite**

Run: `cd backend && uv run pytest -q && uv run mypy --strict src/ && uv run autoflake8 --check -r src/`
Expected: all green

- [ ] **Step 3: Frontend full suite**

Run: `cd frontend && npm run typecheck && npm run test && npm run lint`
Expected: all green

- [ ] **Step 4: Full docker-compose pass**

```bash
docker compose -f infra/docker-compose.yml up -d --build
```

Then, against the running stack:
1. Confirm the bundled sample data actually shipped inside the built image (this is what validates the Global Constraints claim that no packaging config was needed):
   ```bash
   docker compose -f infra/docker-compose.yml exec backend python -c "from importlib import resources; print(list(resources.files('flowsage_backend.resources.sample_data').iterdir()))"
   ```
   Expected: lists `events.jsonl` and `screenshots`.
2. Create a user (`flowsage-backend create-user`), log in via the browser.
3. Visit `/getting-started` — confirm all 4 checklist items render, all unchecked.
4. Visit `/journey` — confirm the empty state shows the "Import Sample Data" button. Click it.
5. Confirm the Journey Graph populates with the discovered funnel and friction nodes shortly after.
6. Return to `/getting-started` — confirm "Ingest your first event" now shows checked, and "Run your first simulation" checks once the enqueued demo run finishes (poll `/predictive` or wait ~30s for the worker).
7. `docker compose down`.

- [ ] **Step 5: Update project memory**

Update the `project-build-status` memory noting Phase 3 chunk 4 (pilot onboarding tooling) complete, and that this closes out Phase 3 (Beta: multi-tenant) entirely per the design spec's header.

- [ ] **Step 6: Final commit (if Step 4 uncovered any fixes)**

```bash
git add frontend/e2e/getting-started.spec.ts
git commit -m "test: add getting-started e2e spec"
```
