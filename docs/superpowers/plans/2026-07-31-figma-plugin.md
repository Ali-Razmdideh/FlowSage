# Figma Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a sideloaded Figma plugin (`figma-plugin/`) that lets a user select frames, run a real FlowSage persona simulation against them, and get friction findings placed back on the canvas as annotation cards — per `docs/superpowers/specs/2026-07-31-figma-plugin-design.md`.

**Architecture:** A small backend auth extension (`get_current_actor`, accepting either the existing session cookie or `X-API-Key`) applied to `GET /personas`, `POST /simulations`, `GET /simulations/{run_id}`, so a non-browser client can call them. Then a standalone `figma-plugin/` package following Figma's required two-context model: `code.ts` (main/sandbox thread — Scene API, no network) and a React UI iframe (`ui/` — network access, no Scene API), talking to each other over `postMessage`.

**Tech Stack:** Backend: existing FastAPI/SQLAlchemy/pytest stack, no new dependencies. Plugin: TypeScript strict, esbuild (bundling only, no bundler framework), React 19 for the UI iframe, Vitest for pure-logic and UI-component tests, oxlint, `@figma/plugin-typings` for the `figma` global's types.

## Global Constraints

- Every payload shape the plugin sends/reads (`PersonaOut`, `SimulationRunOut`/`SimulationRunDetailOut`, `FrictionIssueOut`) must match the real backend `response_model`s exactly — re-check `backend/src/flowsage_backend/api/personas.py` and `api/simulations.py` at implementation time, not just this plan's copy.
- `figma-plugin/` is a fully standalone package (own `package.json`, own `node_modules`) — not part of the uv workspace, not part of `frontend/`'s npm project.
- Screen matching is purely index-based (`001.png`, `002.png`, …) — no frame-name sanitization, no name-parsing on the way back. See spec's "Screen-matching" section.
- `figma.clientStorage` is only callable from `code.ts` (the main thread) — the UI iframe has no `figma` global at all. Any settings persistence must be proxied through a `postMessage` round trip, not called directly from the UI.
- Annotation output is native Figma nodes (frames/text), never Figma's separate Comments/REST API.
- The plugin is sideloaded (dev-mode `manifest.json`), not published to Figma Community — `networkAccess.allowedDomains` can be `"*"` since the backend URL is user-configured at runtime, not fixed at publish time.
- No automated tool in this environment can sideload or drive a real Figma session — every task that touches `code.ts`'s actual `figma.*` calls says so explicitly and defers that piece of verification to a manual checklist (Task 7).
- `npx oxlint`, `npx tsc --noEmit`, and `npx vitest run` must all stay clean inside `figma-plugin/`; `pytest`, `mypy --strict`, `autoflake8` must all stay clean inside `backend/`.

---

### Task 1: Backend — dual auth (`get_current_actor`) on personas/simulations routes

**Files:**
- Modify: `backend/src/flowsage_backend/deps.py`
- Modify: `backend/src/flowsage_backend/api/personas.py`
- Modify: `backend/src/flowsage_backend/api/simulations.py`
- Test: `backend/tests/test_deps.py` (new)
- Modify: `backend/tests/test_personas_api.py`
- Modify: `backend/tests/test_simulations_api.py`

**Interfaces:**
- Produces: `get_current_actor(request, session) -> tuple[uuid.UUID, uuid.UUID | None]` in `flowsage_backend.deps`, importable by any future route that needs to accept both auth styles.

- [ ] **Step 1: Write the failing test for `get_current_actor`**

```python
# backend/tests/test_deps.py
import uuid

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_actor
from .conftest import create_api_key_for, login_to_default_workspace


async def test_get_current_actor_rejects_unauthenticated_request(app: FastAPI) -> None:
    @app.get("/_test/actor")
    async def _actor_route(
        actor: tuple[uuid.UUID, uuid.UUID | None] = pytest.importorskip(
            "fastapi"
        ).Depends(get_current_actor),
    ) -> dict[str, str]:
        return {"workspace_id": str(actor[0])}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor")

    assert response.status_code == 401


async def test_get_current_actor_accepts_valid_api_key(
    app: FastAPI, db_session: AsyncSession
) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_id = await login_to_default_workspace(client, db_session, "actor@example.com")
        api_key = await create_api_key_for(db_session, workspace_id)

        @app.get("/_test/actor")
        async def _actor_route(
            request: Request,
        ) -> dict[str, str]:
            from fastapi import Depends

            actor = await get_current_actor(request, db_session)
            return {"workspace_id": str(actor[0]), "user_id": str(actor[1])}

        response = await client.get("/_test/actor", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert response.json()["workspace_id"] == str(workspace_id)
    assert response.json()["user_id"] == "None"
```

This inline-route test approach is awkward (defining a route inside a test function fights FastAPI's dependency-injection style). Replace it with the simpler, correct version below before running anything.

- [ ] **Step 1 (corrected): Write the failing test against a tiny dedicated test router**

```python
# backend/tests/test_deps.py
import uuid

from fastapi import APIRouter, Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from flowsage_backend.deps import get_current_actor
from .conftest import create_api_key_for, login_to_default_workspace

_test_router = APIRouter()


@_test_router.get("/_test/actor")
async def _actor_probe(
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
) -> dict[str, str | None]:
    workspace_id, user_id = actor
    return {"workspace_id": str(workspace_id), "user_id": str(user_id) if user_id else None}


async def test_get_current_actor_rejects_unauthenticated_request(app: FastAPI) -> None:
    app.include_router(_test_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor")

    assert response.status_code == 401


async def test_get_current_actor_rejects_invalid_api_key(app: FastAPI) -> None:
    app.include_router(_test_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor", headers={"X-API-Key": "not-a-real-key"})

    assert response.status_code == 401


async def test_get_current_actor_accepts_valid_api_key(
    app: FastAPI, db_session: AsyncSession
) -> None:
    app.include_router(_test_router)
    workspace_id = await login_to_default_workspace(
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test"),
        db_session,
        "actor-apikey@example.com",
    )
    api_key = await create_api_key_for(db_session, workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/_test/actor", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["user_id"] is None


async def test_get_current_actor_accepts_valid_session_cookie(
    app: FastAPI, db_session: AsyncSession
) -> None:
    app.include_router(_test_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        workspace_id = await login_to_default_workspace(
            client, db_session, "actor-cookie@example.com"
        )
        response = await client.get("/_test/actor")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_id"] == str(workspace_id)
    assert body["user_id"] is not None
```

Note: `login_to_default_workspace(client, session, email)` both registers the user/membership *and* logs the given `client` in via cookie (see its docstring in `backend/tests/conftest.py:172`) — the third test relies on reusing the same already-logged-in `client` for the probe request, not a fresh one, which is why it's structured differently from the API-key test (API-key auth needs no cookie, so a fresh client is fine there).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_deps.py -v`
Expected: FAIL — `get_current_actor` doesn't exist in `flowsage_backend.deps` yet (`ImportError`).

- [ ] **Step 3: Implement `get_current_actor` in `deps.py`**

Add after the existing `require_workspace_api_key` function (`backend/src/flowsage_backend/deps.py`):

```python
async def get_current_actor(
    request: Request, session: AsyncSession = Depends(get_db_session)
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Resolves the acting workspace (plus the user, if session-authenticated) from
    either the browser's session cookie or an `X-API-Key` header. Lets a
    non-browser client (the Figma plugin) call routes that were previously
    cookie-only, without weakening the existing cookie-based auth those routes
    already had -- presence of the `X-API-Key` header decides which check runs;
    an invalid key still 401s rather than silently falling through to the
    cookie check."""
    if request.headers.get("X-API-Key") is not None:
        workspace_id = await require_workspace_api_key(request, session)
        return workspace_id, None

    _, membership = await get_current_membership(request, session)
    return membership.workspace_id, membership.user_id
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_deps.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire `get_current_actor` into `GET /personas`**

In `backend/src/flowsage_backend/api/personas.py`:

Remove the blanket router-level dependency (every route already redeclares its own auth dependency as a function parameter, so this was redundant):

```python
# Before
router = APIRouter(
    prefix="/personas", tags=["personas"], dependencies=[Depends(get_current_membership)]
)

# After
router = APIRouter(prefix="/personas", tags=["personas"])
```

Add the import:

```python
from flowsage_backend.deps import get_current_actor, get_current_membership, get_db_session
```

Change only `list_personas` (leave `get_persona`/`create_persona`/`update_persona`/`reset_persona`/`delete_persona` untouched — they keep cookie-only `get_current_membership`, since write/CRUD access isn't something the plugin needs and shouldn't gain API-key access as a side effect):

```python
@router.get("", response_model=list[PersonaOut])
async def list_personas(
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> list[Persona]:
    workspace_id, _ = actor
    result = await session.execute(
        select(Persona).where(Persona.workspace_id == workspace_id).order_by(Persona.name)
    )
    return list(result.scalars().all())
```

- [ ] **Step 6: Wire `get_current_actor` into `POST /simulations` and `GET /simulations/{run_id}`**

In `backend/src/flowsage_backend/api/simulations.py`:

```python
# Before
router = APIRouter(
    prefix="/simulations", tags=["simulations"], dependencies=[Depends(get_current_membership)]
)

# After
router = APIRouter(prefix="/simulations", tags=["simulations"])
```

```python
from flowsage_backend.deps import get_current_actor, get_current_membership, get_db_session
```

`create_simulation` (leave `files`/`Form(...)` params exactly as-is, only the auth param and its two usages change):

```python
@router.post("", response_model=SimulationRunOut, status_code=status.HTTP_201_CREATED)
async def create_simulation(
    request: Request,
    persona_id: uuid.UUID = Form(...),
    goal: str = Form(...),
    flow_name: str = Form(...),
    files: list[UploadFile] = File(...),
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> SimulationRun:
    workspace_id, user_id = actor
    await check_within_limits(session, workspace_id, "runs")
    settings = request.app.state.settings
    run_id = uuid.uuid4()
    screenshots_dir = Path(settings.upload_dir) / str(run_id)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        filename = Path(upload.filename or "").name
        if Path(filename).suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"Unsupported file type: {filename!r}"
            )
        (screenshots_dir / filename).write_bytes(await upload.read())

    try:
        run = await create_run(
            session,
            workspace_id=workspace_id,
            run_id=run_id,
            persona_id=persona_id,
            flow_name=flow_name,
            goal=goal,
            screenshots_dir=screenshots_dir,
        )
    except SimulationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    await request.app.state.arq_pool.enqueue_job("run_simulation_job", str(run.id))
    await record_audit_event(
        session,
        workspace_id,
        actor_user_id=user_id,
        action="simulation.started",
        target_type="simulation_run",
        target_id=str(run.id),
        extra_data={"flow_name": flow_name, "goal": goal},
    )
    return run
```

`get_simulation`:

```python
@router.get("/{run_id}", response_model=SimulationRunDetailOut)
async def get_simulation(
    run_id: uuid.UUID,
    actor: tuple[uuid.UUID, uuid.UUID | None] = Depends(get_current_actor),
    session: AsyncSession = Depends(get_db_session),
) -> SimulationRun:
    workspace_id, _ = actor
    run = await _load_run_with_children(session, workspace_id, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation run not found")
    return run
```

Leave `stream_simulation` (the SSE route) untouched — still cookie-only via `get_current_membership`; the plugin polls the plain GET, not the SSE stream.

- [ ] **Step 7: Add API-key-auth tests to the existing test files**

Append to `backend/tests/test_personas_api.py`:

```python
async def test_list_personas_accepts_api_key_auth(app: FastAPI, db_session: AsyncSession) -> None:
    from .conftest import create_api_key_for, login_to_default_workspace

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as setup_client:
        workspace_id = await login_to_default_workspace(
            setup_client, db_session, "personas-apikey@example.com"
        )
    await seed_baseline_personas(db_session, workspace_id)
    api_key = await create_api_key_for(db_session, workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/personas", headers={"X-API-Key": api_key})

    assert response.status_code == 200
    assert len(response.json()) > 0


async def test_create_persona_still_requires_cookie_not_api_key(
    app: FastAPI, db_session: AsyncSession
) -> None:
    from .conftest import create_api_key_for, login_to_default_workspace

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as setup_client:
        workspace_id = await login_to_default_workspace(
            setup_client, db_session, "personas-cookie-only@example.com"
        )
    api_key = await create_api_key_for(db_session, workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/personas", json=_create_payload("api-key-should-fail"), headers={"X-API-Key": api_key}
        )

    assert response.status_code == 401
```

Append to `backend/tests/test_simulations_api.py`:

```python
async def test_create_and_get_simulation_via_api_key(
    app: FastAPI, db_session: AsyncSession
) -> None:
    from .conftest import create_api_key_for, login_to_default_workspace

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as setup_client:
        workspace_id = await login_to_default_workspace(
            setup_client, db_session, "sim-apikey@example.com"
        )
    personas = await seed_baseline_personas(db_session, workspace_id)
    persona = personas[0]
    api_key = await create_api_key_for(db_session, workspace_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_response = await client.post(
            "/simulations",
            data={"persona_id": str(persona.id), "goal": "goal", "flow_name": "flow"},
            files={"files": ("001.png", _PNG_BYTES, "image/png")},
            headers={"X-API-Key": api_key},
        )
        assert create_response.status_code == 201
        run_id = create_response.json()["id"]

        get_response = await client.get(f"/simulations/{run_id}", headers={"X-API-Key": api_key})

    assert get_response.status_code == 200
    assert get_response.json()["flow_name"] == "flow"
```

- [ ] **Step 8: Run the full backend test suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS, including all existing personas/simulations tests (cookie-only paths unaffected) plus the new API-key tests.

- [ ] **Step 9: Type/lint check**

Run: `cd backend && uv run autoflake8 --check --recursive src && uv run mypy --strict src`
Expected: clean

- [ ] **Step 10: Commit**

```bash
git add backend/src/flowsage_backend/deps.py backend/src/flowsage_backend/api/personas.py backend/src/flowsage_backend/api/simulations.py backend/tests/test_deps.py backend/tests/test_personas_api.py backend/tests/test_simulations_api.py
git commit -m "feat: accept API-key auth on GET /personas and simulation run routes"
```

---

### Task 2: `figma-plugin/` package scaffold

**Files:**
- Create: `figma-plugin/package.json`
- Create: `figma-plugin/tsconfig.json`
- Create: `figma-plugin/manifest.json`
- Create: `figma-plugin/esbuild.mjs`
- Create: `figma-plugin/ui-template.html`
- Create: `figma-plugin/src/code.ts`
- Create: `figma-plugin/src/ui/main.tsx`
- Create: `figma-plugin/.gitignore`

**Interfaces:**
- Produces: a buildable skeleton — `npm run build` inside `figma-plugin/` emits `dist/code.js` and `dist/ui.html`. Later tasks add real logic on top of this shell without changing its structure.

- [ ] **Step 1: `package.json`**

```json
{
  "name": "figma-plugin",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "build": "node esbuild.mjs",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "lint": "oxlint"
  },
  "dependencies": {
    "react": "^19.2.7",
    "react-dom": "^19.2.7"
  },
  "devDependencies": {
    "@figma/plugin-typings": "^1.114.0",
    "@testing-library/jest-dom": "^6.9.1",
    "@testing-library/react": "^16.3.2",
    "@types/react": "^19.2.17",
    "@types/react-dom": "^19.2.3",
    "esbuild": "^0.24.0",
    "jsdom": "^29.1.1",
    "oxlint": "^1.71.0",
    "typescript": "~6.0.2",
    "vitest": "^4.1.10"
  }
}
```

- [ ] **Step 2: `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["ES2017", "DOM"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "types": ["@figma/plugin-typings", "vitest/globals"]
  },
  "include": ["src"]
}
```

- [ ] **Step 3: `manifest.json`**

```json
{
  "name": "FlowSage",
  "id": "flowsage-friction-annotator",
  "api": "1.0.0",
  "main": "dist/code.js",
  "ui": "dist/ui.html",
  "editorType": ["figma"],
  "networkAccess": {
    "allowedDomains": ["*"],
    "reasoning": "Backend URL is user-configured at runtime (self-hosted FlowSage instance), not fixed at publish time. This plugin is sideloaded, not published to Figma Community."
  }
}
```

- [ ] **Step 4: `.gitignore`**

```
node_modules/
dist/
```

- [ ] **Step 5: Minimal `src/code.ts` placeholder**

```typescript
// figma-plugin/src/code.ts
figma.showUI(__html__, { width: 340, height: 480 });

figma.ui.onmessage = (msg: { id: string; type: string; payload?: unknown }) => {
  console.log("received message", msg.type);
};
```

- [ ] **Step 6: Minimal `src/ui/main.tsx` placeholder**

```tsx
// figma-plugin/src/ui/main.tsx
import { createRoot } from "react-dom/client";

function App() {
  return <div style={{ fontFamily: "sans-serif", padding: 12 }}>FlowSage plugin loading…</div>;
}

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(<App />);
}
```

- [ ] **Step 7: `ui-template.html`**

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
  </head>
  <body>
    <div id="root"></div>
    <script>
      /* __UI_SCRIPT__ */
    </script>
  </body>
</html>
```

- [ ] **Step 8: `esbuild.mjs` build script**

```javascript
// figma-plugin/esbuild.mjs
import { build } from "esbuild";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";

mkdirSync("dist", { recursive: true });

await build({
  entryPoints: ["src/code.ts"],
  bundle: true,
  outfile: "dist/code.js",
  target: "es2017",
  format: "iife",
});

await build({
  entryPoints: ["src/ui/main.tsx"],
  bundle: true,
  outfile: "dist/ui.js",
  target: "es2017",
  format: "iife",
  jsx: "automatic",
});

const template = readFileSync("ui-template.html", "utf-8");
const uiScript = readFileSync("dist/ui.js", "utf-8");
const html = template.replace("/* __UI_SCRIPT__ */", uiScript);
writeFileSync("dist/ui.html", html);

console.log("Built dist/code.js and dist/ui.html");
```

- [ ] **Step 9: Install dependencies and build**

Run: `cd figma-plugin && npm install && npm run build`
Expected: `dist/code.js` and `dist/ui.html` are created with no errors. `dist/ui.html` should contain an inlined `<script>` with the bundled React app, not the `/* __UI_SCRIPT__ */` placeholder comment.

- [ ] **Step 10: Typecheck**

Run: `cd figma-plugin && npx tsc --noEmit`
Expected: clean

- [ ] **Step 11: Commit**

```bash
git add figma-plugin/
git commit -m "feat: scaffold figma-plugin package with esbuild + manifest"
```

---

### Task 3: Shared pure logic — binary encoding, screen matching, upload payload, card layout

**Files:**
- Create: `figma-plugin/src/shared/binary.ts`
- Create: `figma-plugin/src/shared/binary.test.ts`
- Create: `figma-plugin/src/shared/types.ts`
- Create: `figma-plugin/src/shared/simulationClient.ts`
- Create: `figma-plugin/src/shared/simulationClient.test.ts`
- Create: `figma-plugin/src/shared/annotationLayout.ts`
- Create: `figma-plugin/src/shared/annotationLayout.test.ts`

**Interfaces:**
- Consumes: nothing (pure functions, no Figma/browser globals).
- Produces: `uint8ArrayToBase64`, `base64ToUint8Array` (`binary.ts`); `Persona`, `FrictionIssue`, `SimulationRun`, `RunStatus` types (`types.ts`); `buildScreenFilename(index: number): string`, `parseScreenIndex(screen: string): number`, `groupIssuesByFrameIndex(issues: FrictionIssue[]): Map<number, FrictionIssue[]>` (`simulationClient.ts`); `computeCardPosition(source: Bounds, stackIndex: number): { x: number; y: number }` (`annotationLayout.ts`) — all consumed by `code.ts` (Task 6) and `ui/App.tsx` (Task 5).

- [ ] **Step 1: Write the failing tests for `binary.ts`**

```typescript
// figma-plugin/src/shared/binary.test.ts
import { describe, expect, it } from "vitest";
import { base64ToUint8Array, uint8ArrayToBase64 } from "./binary";

describe("binary encoding", () => {
  it("round-trips arbitrary bytes through base64", () => {
    const original = new Uint8Array([0, 1, 2, 137, 80, 78, 71, 255, 254, 13, 10]);
    const encoded = uint8ArrayToBase64(original);
    const decoded = base64ToUint8Array(encoded);
    expect(Array.from(decoded)).toEqual(Array.from(original));
  });

  it("handles an empty array", () => {
    expect(uint8ArrayToBase64(new Uint8Array([]))).toBe("");
    expect(Array.from(base64ToUint8Array(""))).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd figma-plugin && npx vitest run src/shared/binary.test.ts`
Expected: FAIL — `binary.ts` doesn't exist yet.

- [ ] **Step 3: Implement `binary.ts`**

No `Buffer`/`btoa`/`atob` dependency — those aren't reliably available in Figma's main-thread sandbox, so this is a manual, dependency-free base64 codec:

```typescript
// figma-plugin/src/shared/binary.ts
const CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

export function uint8ArrayToBase64(bytes: Uint8Array): string {
  let result = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const b0 = bytes[i];
    const b1 = i + 1 < bytes.length ? bytes[i + 1] : undefined;
    const b2 = i + 2 < bytes.length ? bytes[i + 2] : undefined;

    result += CHARS[b0 >> 2];
    result += CHARS[((b0 & 0x03) << 4) | (b1 !== undefined ? b1 >> 4 : 0)];
    result += b1 !== undefined ? CHARS[((b1 & 0x0f) << 2) | (b2 !== undefined ? b2 >> 6 : 0)] : "=";
    result += b2 !== undefined ? CHARS[b2 & 0x3f] : "=";
  }
  return result;
}

export function base64ToUint8Array(base64: string): Uint8Array {
  const clean = base64.replace(/=+$/, "");
  const bytes: number[] = [];
  let buffer = 0;
  let bitsCollected = 0;

  for (const char of clean) {
    const value = CHARS.indexOf(char);
    if (value === -1) continue;
    buffer = (buffer << 6) | value;
    bitsCollected += 6;
    if (bitsCollected >= 8) {
      bitsCollected -= 8;
      bytes.push((buffer >> bitsCollected) & 0xff);
    }
  }
  return new Uint8Array(bytes);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd figma-plugin && npx vitest run src/shared/binary.test.ts`
Expected: PASS (2 tests)

- [ ] **Step 5: Write `types.ts`** (no test needed — types only, structurally checked by `tsc`)

```typescript
// figma-plugin/src/shared/types.ts
export interface Persona {
  id: string;
  slug: string;
  name: string;
}

export type RunStatus = "queued" | "running" | "completed" | "failed";

export interface FrictionIssue {
  id: string;
  screen: string;
  severity: "low" | "medium" | "high" | "critical";
  title: string;
  heuristic_violated: string;
  persona_impact: string;
  description: string;
  suggested_fix: string;
}

export interface SimulationRun {
  id: string;
  flow_name: string;
  goal: string;
  persona_id: string;
  status: RunStatus;
  error: string | null;
  issues?: FrictionIssue[];
}
```

- [ ] **Step 6: Write the failing tests for `simulationClient.ts`**

```typescript
// figma-plugin/src/shared/simulationClient.test.ts
import { describe, expect, it } from "vitest";
import { buildScreenFilename, groupIssuesByFrameIndex, parseScreenIndex } from "./simulationClient";
import type { FrictionIssue } from "./types";

describe("buildScreenFilename", () => {
  it("zero-pads a 1-based index to 3 digits", () => {
    expect(buildScreenFilename(0)).toBe("001.png");
    expect(buildScreenFilename(9)).toBe("010.png");
    expect(buildScreenFilename(998)).toBe("999.png");
  });
});

describe("parseScreenIndex", () => {
  it("parses a zero-padded screen string back to a 0-based index", () => {
    expect(parseScreenIndex("001")).toBe(0);
    expect(parseScreenIndex("010")).toBe(9);
  });

  it("throws on a non-numeric screen string", () => {
    expect(() => parseScreenIndex("checkout")).toThrow();
  });
});

describe("groupIssuesByFrameIndex", () => {
  it("groups issues by their 0-based source frame index, preserving order", () => {
    const issues: FrictionIssue[] = [
      { id: "a", screen: "002", severity: "high", title: "t1" } as FrictionIssue,
      { id: "b", screen: "001", severity: "low", title: "t2" } as FrictionIssue,
      { id: "c", screen: "002", severity: "critical", title: "t3" } as FrictionIssue,
    ];

    const grouped = groupIssuesByFrameIndex(issues);

    expect(grouped.get(0)?.map((i) => i.id)).toEqual(["b"]);
    expect(grouped.get(1)?.map((i) => i.id)).toEqual(["a", "c"]);
  });
});
```

- [ ] **Step 7: Run test to verify it fails**

Run: `cd figma-plugin && npx vitest run src/shared/simulationClient.test.ts`
Expected: FAIL — `simulationClient.ts` doesn't exist yet.

- [ ] **Step 8: Implement `simulationClient.ts`**

```typescript
// figma-plugin/src/shared/simulationClient.ts
import type { FrictionIssue } from "./types";

export function buildScreenFilename(index: number): string {
  return `${String(index + 1).padStart(3, "0")}.png`;
}

export function parseScreenIndex(screen: string): number {
  const parsed = Number.parseInt(screen, 10);
  if (Number.isNaN(parsed)) {
    throw new Error(`Cannot parse screen index from screen name: ${screen}`);
  }
  return parsed - 1;
}

export function groupIssuesByFrameIndex(issues: FrictionIssue[]): Map<number, FrictionIssue[]> {
  const grouped = new Map<number, FrictionIssue[]>();
  for (const issue of issues) {
    const index = parseScreenIndex(issue.screen);
    const existing = grouped.get(index);
    if (existing) {
      existing.push(issue);
    } else {
      grouped.set(index, [issue]);
    }
  }
  return grouped;
}
```

- [ ] **Step 9: Run test to verify it passes**

Run: `cd figma-plugin && npx vitest run src/shared/simulationClient.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 10: Write the failing test for `annotationLayout.ts`**

```typescript
// figma-plugin/src/shared/annotationLayout.test.ts
import { describe, expect, it } from "vitest";
import { computeCardPosition, CARD_GAP, CARD_HEIGHT, CARD_VERTICAL_GAP } from "./annotationLayout";

describe("computeCardPosition", () => {
  it("places the first card to the right of the source frame", () => {
    const position = computeCardPosition({ x: 100, y: 200, width: 400, height: 800 }, 0);
    expect(position).toEqual({ x: 100 + 400 + CARD_GAP, y: 200 });
  });

  it("stacks subsequent cards vertically below the first", () => {
    const position = computeCardPosition({ x: 100, y: 200, width: 400, height: 800 }, 2);
    expect(position).toEqual({
      x: 100 + 400 + CARD_GAP,
      y: 200 + 2 * (CARD_HEIGHT + CARD_VERTICAL_GAP),
    });
  });
});
```

- [ ] **Step 11: Run test to verify it fails**

Run: `cd figma-plugin && npx vitest run src/shared/annotationLayout.test.ts`
Expected: FAIL — `annotationLayout.ts` doesn't exist yet.

- [ ] **Step 12: Implement `annotationLayout.ts`**

```typescript
// figma-plugin/src/shared/annotationLayout.ts
export interface Bounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const CARD_WIDTH = 280;
export const CARD_HEIGHT = 160;
export const CARD_GAP = 60;
export const CARD_VERTICAL_GAP = 24;

export function computeCardPosition(source: Bounds, stackIndex: number): { x: number; y: number } {
  return {
    x: source.x + source.width + CARD_GAP,
    y: source.y + stackIndex * (CARD_HEIGHT + CARD_VERTICAL_GAP),
  };
}
```

- [ ] **Step 13: Run test to verify it passes**

Run: `cd figma-plugin && npx vitest run src/shared/annotationLayout.test.ts`
Expected: PASS (2 tests)

- [ ] **Step 14: Run the whole shared test suite + typecheck**

Run: `cd figma-plugin && npx vitest run src/shared && npx tsc --noEmit`
Expected: all clean

- [ ] **Step 15: Commit**

```bash
git add figma-plugin/src/shared/
git commit -m "feat: add shared binary/screen-matching/layout logic for figma plugin"
```

---

### Task 4: UI-side postMessage bridge + backend API client

**Files:**
- Create: `figma-plugin/src/ui/bridge.ts`
- Create: `figma-plugin/src/ui/bridge.test.ts`
- Create: `figma-plugin/src/ui/api.ts`
- Create: `figma-plugin/src/ui/api.test.ts`

**Interfaces:**
- Consumes: `Persona`, `SimulationRun` (`shared/types.ts`, Task 3).
- Produces: `callPlugin<T>(type: string, payload?: unknown): Promise<T>` (`bridge.ts`), consumed by `code.ts` (Task 6) as the message-passing counterpart and by `App.tsx` (Task 5). `listPersonas`, `createSimulationRun`, `getSimulationRun` (`api.ts`), consumed by `App.tsx` (Task 5).

- [ ] **Step 1: Write the failing test for `bridge.ts`**

```typescript
// figma-plugin/src/ui/bridge.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { callPlugin } from "./bridge";

describe("callPlugin", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts a message to the parent and resolves when a matching reply arrives", async () => {
    const postMessageSpy = vi.spyOn(window.parent, "postMessage").mockImplementation(() => {});

    const resultPromise = callPlugin<{ ok: boolean }>("get-settings");

    expect(postMessageSpy).toHaveBeenCalledTimes(1);
    const sentMessage = postMessageSpy.mock.calls[0][0] as {
      pluginMessage: { id: string; type: string };
    };
    expect(sentMessage.pluginMessage.type).toBe("get-settings");

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { pluginMessage: { id: sentMessage.pluginMessage.id, payload: { ok: true } } },
      }),
    );

    await expect(resultPromise).resolves.toEqual({ ok: true });
  });

  it("ignores messages with an unrecognized id", async () => {
    vi.spyOn(window.parent, "postMessage").mockImplementation(() => {});
    const resultPromise = callPlugin<{ ok: boolean }>("get-settings");

    window.dispatchEvent(
      new MessageEvent("message", {
        data: { pluginMessage: { id: "some-other-id", payload: { ok: false } } },
      }),
    );

    let resolved = false;
    resultPromise.then(() => {
      resolved = true;
    });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(resolved).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd figma-plugin && npx vitest run src/ui/bridge.test.ts`
Expected: FAIL — `bridge.ts` doesn't exist yet.

- [ ] **Step 3: Implement `bridge.ts`**

```typescript
// figma-plugin/src/ui/bridge.ts
let counter = 0;
const pending = new Map<string, (payload: unknown) => void>();

window.addEventListener("message", (event: MessageEvent) => {
  const message = (event.data as { pluginMessage?: { id: string; payload: unknown } })
    .pluginMessage;
  if (!message || !pending.has(message.id)) return;
  const resolve = pending.get(message.id);
  pending.delete(message.id);
  resolve?.(message.payload);
});

export function callPlugin<T>(type: string, payload?: unknown): Promise<T> {
  const id = `${type}-${counter++}`;
  return new Promise<T>((resolve) => {
    pending.set(id, resolve as (payload: unknown) => void);
    window.parent.postMessage({ pluginMessage: { id, type, payload } }, "*");
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd figma-plugin && npx vitest run src/ui/bridge.test.ts`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing test for `api.ts`**

```typescript
// figma-plugin/src/ui/api.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { createSimulationRun, getSimulationRun, listPersonas } from "./api";

const config = { baseUrl: "https://flowsage.example", apiKey: "fs_test_key" };

describe("listPersonas", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /personas with the X-API-Key header", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: "p1", slug: "novice", name: "Novice" }],
    });
    vi.stubGlobal("fetch", fetchMock);

    const personas = await listPersonas(config);

    expect(fetchMock).toHaveBeenCalledWith(
      "https://flowsage.example/personas",
      expect.objectContaining({ headers: { "X-API-Key": "fs_test_key" } }),
    );
    expect(personas).toEqual([{ id: "p1", slug: "novice", name: "Novice" }]);
  });

  it("throws with the response status on a non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(listPersonas(config)).rejects.toThrow("401");
  });
});

describe("createSimulationRun", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POSTs multipart form data with persona_id, goal, flow_name, and files", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "run-1", status: "queued" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const run = await createSimulationRun(config, {
      personaId: "p1",
      goal: "Buy a widget",
      flowName: "Checkout",
      files: [{ filename: "001.png", bytes: new Uint8Array([1, 2, 3]) }],
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://flowsage.example/simulations");
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBe("fs_test_key");
    expect(init.body).toBeInstanceOf(FormData);
    expect(run.id).toBe("run-1");
  });
});

describe("getSimulationRun", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs /simulations/{id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: "run-1", status: "completed", issues: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const run = await getSimulationRun(config, "run-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "https://flowsage.example/simulations/run-1",
      expect.objectContaining({ headers: { "X-API-Key": "fs_test_key" } }),
    );
    expect(run.status).toBe("completed");
  });
});
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd figma-plugin && npx vitest run src/ui/api.test.ts`
Expected: FAIL — `api.ts` doesn't exist yet.

- [ ] **Step 7: Implement `api.ts`**

```typescript
// figma-plugin/src/ui/api.ts
import type { Persona, SimulationRun } from "../shared/types";

export interface BackendConfig {
  baseUrl: string;
  apiKey: string;
}

export interface ExportedFile {
  filename: string;
  bytes: Uint8Array;
}

async function assertOk(response: { ok: boolean; status: number }): Promise<void> {
  if (!response.ok) {
    throw new Error(`FlowSage API request failed with status ${response.status}`);
  }
}

export async function listPersonas(config: BackendConfig): Promise<Persona[]> {
  const response = await fetch(`${config.baseUrl}/personas`, {
    headers: { "X-API-Key": config.apiKey },
  });
  await assertOk(response);
  return response.json();
}

export async function createSimulationRun(
  config: BackendConfig,
  params: { personaId: string; goal: string; flowName: string; files: ExportedFile[] },
): Promise<SimulationRun> {
  const formData = new FormData();
  formData.set("persona_id", params.personaId);
  formData.set("goal", params.goal);
  formData.set("flow_name", params.flowName);
  for (const file of params.files) {
    formData.append("files", new Blob([file.bytes], { type: "image/png" }), file.filename);
  }

  const response = await fetch(`${config.baseUrl}/simulations`, {
    method: "POST",
    headers: { "X-API-Key": config.apiKey },
    body: formData,
  });
  await assertOk(response);
  return response.json();
}

export async function getSimulationRun(
  config: BackendConfig,
  runId: string,
): Promise<SimulationRun> {
  const response = await fetch(`${config.baseUrl}/simulations/${runId}`, {
    headers: { "X-API-Key": config.apiKey },
  });
  await assertOk(response);
  return response.json();
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd figma-plugin && npx vitest run src/ui/api.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 9: Add a `vitest.config.ts` so `api.test.ts` (which needs `FormData`/`Blob`) and `bridge.test.ts` (which needs `window`) both run in a browser-like environment**

```typescript
// figma-plugin/vitest.config.ts
import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "jsdom",
    globals: true,
  },
});
```

Add `"vitest.config.ts"` alongside `package.json`; no changes needed to `package.json`'s `test` script since Vitest picks up the config file automatically.

- [ ] **Step 10: Re-run the full test suite + typecheck to confirm the jsdom environment didn't break Task 3's node-only tests**

Run: `cd figma-plugin && npx vitest run && npx tsc --noEmit`
Expected: all clean (jsdom is a superset environment — Task 3's pure-function tests don't reference browser globals, so they still pass unchanged)

- [ ] **Step 11: Commit**

```bash
git add figma-plugin/src/ui/bridge.ts figma-plugin/src/ui/bridge.test.ts figma-plugin/src/ui/api.ts figma-plugin/src/ui/api.test.ts figma-plugin/vitest.config.ts
git commit -m "feat: add postMessage bridge and backend API client for figma plugin UI"
```

---

### Task 5: UI React app — settings, persona picker, run form, status

**Files:**
- Create: `figma-plugin/src/ui/App.tsx`
- Create: `figma-plugin/src/ui/App.test.tsx`
- Modify: `figma-plugin/src/ui/main.tsx`

**Interfaces:**
- Consumes: `callPlugin` (`bridge.ts`, Task 4), `listPersonas`/`createSimulationRun`/`getSimulationRun`/`BackendConfig`/`ExportedFile` (`api.ts`, Task 4), `buildScreenFilename` (`shared/simulationClient.ts`, Task 3), `Persona`/`SimulationRun` (`shared/types.ts`, Task 3).
- Produces: `App` component, rendered by `main.tsx`. Sends `postMessage` types `"get-settings"`, `"save-settings"`, `"export-selection"`, `"annotate"` — these exact string literals are the contract Task 6's `code.ts` message handler must match.

- [ ] **Step 1: Write the failing tests for `App.tsx`**

```tsx
// figma-plugin/src/ui/App.test.tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import * as bridge from "./bridge";
import * as api from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("App", () => {
  it("loads saved settings and the persona list on mount", async () => {
    vi.spyOn(bridge, "callPlugin").mockResolvedValue({
      baseUrl: "https://flowsage.example",
      apiKey: "fs_saved_key",
    });
    vi.spyOn(api, "listPersonas").mockResolvedValue([{ id: "p1", slug: "novice", name: "Novice" }]);

    render(<App />);

    expect(await screen.findByDisplayValue("https://flowsage.example")).toBeInTheDocument();
    expect(await screen.findByText("Novice")).toBeInTheDocument();
  });

  it("saves settings via the bridge when the Save button is clicked", async () => {
    const callPluginSpy = vi
      .spyOn(bridge, "callPlugin")
      .mockResolvedValueOnce({ baseUrl: "", apiKey: "" })
      .mockResolvedValueOnce(undefined);
    vi.spyOn(api, "listPersonas").mockResolvedValue([]);

    render(<App />);
    await waitFor(() => expect(callPluginSpy).toHaveBeenCalledWith("get-settings"));

    fireEvent.change(screen.getByLabelText(/base url/i), {
      target: { value: "https://my-instance.example" },
    });
    fireEvent.change(screen.getByLabelText(/api key/i), { target: { value: "fs_new_key" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(callPluginSpy).toHaveBeenCalledWith("save-settings", {
        baseUrl: "https://my-instance.example",
        apiKey: "fs_new_key",
      }),
    );
  });

  it("runs the full export -> upload -> poll -> annotate flow and shows a success message", async () => {
    vi.spyOn(bridge, "callPlugin").mockImplementation(async (type: string) => {
      if (type === "get-settings") return { baseUrl: "https://flowsage.example", apiKey: "fs_key" };
      if (type === "export-selection") {
        return [{ index: 0, bytes: [1, 2, 3] }];
      }
      if (type === "annotate") return undefined;
      return undefined;
    });
    vi.spyOn(api, "listPersonas").mockResolvedValue([{ id: "p1", slug: "novice", name: "Novice" }]);
    vi.spyOn(api, "createSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "queued",
      error: null,
    });
    vi.spyOn(api, "getSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "completed",
      error: null,
      issues: [
        {
          id: "i1",
          screen: "001",
          severity: "high",
          title: "Confusing CTA",
          heuristic_violated: "Visibility of system status",
          persona_impact: "",
          description: "",
          suggested_fix: "Make the button more obvious.",
        },
      ],
    });

    render(<App />);
    await screen.findByText("Novice");

    fireEvent.change(screen.getByLabelText(/goal/i), { target: { value: "Buy" } });
    fireEvent.change(screen.getByLabelText(/flow name/i), { target: { value: "Checkout" } });
    fireEvent.click(screen.getByRole("button", { name: /run & annotate/i }));

    expect(await screen.findByText(/1 issue annotated/i)).toBeInTheDocument();
  });

  it("shows an error message when the simulation run fails", async () => {
    vi.spyOn(bridge, "callPlugin").mockImplementation(async (type: string) => {
      if (type === "get-settings") return { baseUrl: "https://flowsage.example", apiKey: "fs_key" };
      if (type === "export-selection") return [{ index: 0, bytes: [1, 2, 3] }];
      return undefined;
    });
    vi.spyOn(api, "listPersonas").mockResolvedValue([{ id: "p1", slug: "novice", name: "Novice" }]);
    vi.spyOn(api, "createSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "queued",
      error: null,
    });
    vi.spyOn(api, "getSimulationRun").mockResolvedValue({
      id: "run-1",
      flow_name: "Checkout",
      goal: "Buy",
      persona_id: "p1",
      status: "failed",
      error: "Vision call timed out",
    });

    render(<App />);
    await screen.findByText("Novice");
    fireEvent.change(screen.getByLabelText(/goal/i), { target: { value: "Buy" } });
    fireEvent.change(screen.getByLabelText(/flow name/i), { target: { value: "Checkout" } });
    fireEvent.click(screen.getByRole("button", { name: /run & annotate/i }));

    expect(await screen.findByText(/Vision call timed out/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd figma-plugin && npx vitest run src/ui/App.test.tsx`
Expected: FAIL — `App.tsx` doesn't exist yet.

- [ ] **Step 3: Implement `App.tsx`**

```tsx
// figma-plugin/src/ui/App.tsx
import { useEffect, useState } from "react";
import { callPlugin } from "./bridge";
import { createSimulationRun, getSimulationRun, listPersonas, type BackendConfig } from "./api";
import { buildScreenFilename, groupIssuesByFrameIndex } from "../shared/simulationClient";
import type { Persona, SimulationRun } from "../shared/types";

interface ExportedFrame {
  index: number;
  bytes: number[];
}

async function pollUntilDone(
  config: BackendConfig,
  runId: string,
  intervalMs = 1500,
): Promise<SimulationRun> {
  for (;;) {
    const run = await getSimulationRun(config, runId);
    if (run.status === "completed" || run.status === "failed") return run;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export function App() {
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [personaId, setPersonaId] = useState("");
  const [goal, setGoal] = useState("");
  const [flowName, setFlowName] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [message, setMessage] = useState("");

  useEffect(() => {
    callPlugin<{ baseUrl: string; apiKey: string }>("get-settings").then((settings) => {
      setBaseUrl(settings.baseUrl);
      setApiKey(settings.apiKey);
      if (settings.baseUrl && settings.apiKey) {
        listPersonas({ baseUrl: settings.baseUrl, apiKey: settings.apiKey })
          .then(setPersonas)
          .then(() => {
            // no-op: keeps the promise chain readable without an unused catch variable
          });
      }
    });
  }, []);

  async function handleSaveSettings() {
    await callPlugin("save-settings", { baseUrl, apiKey });
    const loadedPersonas = await listPersonas({ baseUrl, apiKey });
    setPersonas(loadedPersonas);
  }

  async function handleRun() {
    setStatus("running");
    setMessage("");
    try {
      const config: BackendConfig = { baseUrl, apiKey };
      const exported = await callPlugin<ExportedFrame[]>("export-selection");
      const files = exported.map((frame) => ({
        filename: buildScreenFilename(frame.index),
        bytes: new Uint8Array(frame.bytes),
      }));

      const created = await createSimulationRun(config, { personaId, goal, flowName, files });
      const finished = await pollUntilDone(config, created.id);

      if (finished.status === "failed") {
        setStatus("error");
        setMessage(finished.error ?? "Simulation run failed");
        return;
      }

      const issues = finished.issues ?? [];
      await callPlugin("annotate", { issues });
      setStatus("done");
      setMessage(`${issues.length} issue${issues.length === 1 ? "" : "s"} annotated`);
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  return (
    <div style={{ fontFamily: "sans-serif", padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
      <section>
        <h3>Settings</h3>
        <label htmlFor="base-url">Base URL</label>
        <input id="base-url" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        <label htmlFor="api-key">API key</label>
        <input id="api-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
        <button onClick={handleSaveSettings}>Save</button>
      </section>

      <section>
        <h3>Run a simulation</h3>
        <label htmlFor="persona">Persona</label>
        <select id="persona" value={personaId} onChange={(e) => setPersonaId(e.target.value)}>
          <option value="">Select a persona…</option>
          {personas.map((persona) => (
            <option key={persona.id} value={persona.id}>
              {persona.name}
            </option>
          ))}
        </select>
        <label htmlFor="goal">Goal</label>
        <input id="goal" value={goal} onChange={(e) => setGoal(e.target.value)} />
        <label htmlFor="flow-name">Flow name</label>
        <input id="flow-name" value={flowName} onChange={(e) => setFlowName(e.target.value)} />
        <button onClick={handleRun} disabled={status === "running"}>
          Run & Annotate
        </button>
      </section>

      {status !== "idle" && <p>{status === "running" ? "Running…" : message}</p>}
    </div>
  );
}
```

`groupIssuesByFrameIndex` is imported into `App.tsx`'s module for type-checking symmetry with `code.ts` (Task 6 actually calls it), but `App.tsx` itself doesn't need to call it directly — remove the import if `tsc --noEmit`'s `noUnusedLocals` flags it unused after Step 4.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd figma-plugin && npx vitest run src/ui/App.test.tsx`
Expected: PASS (4 tests). If `tsc` complains about the unused `groupIssuesByFrameIndex` import, remove that import line from `App.tsx` — confirmed above it isn't actually called here.

- [ ] **Step 5: Update `main.tsx` to render the real `App`**

```tsx
// figma-plugin/src/ui/main.tsx
import { createRoot } from "react-dom/client";
import { App } from "./App";

const container = document.getElementById("root");
if (container) {
  createRoot(container).render(<App />);
}
```

- [ ] **Step 6: Run the full test suite + typecheck + build**

Run: `cd figma-plugin && npx vitest run && npx tsc --noEmit && npm run build`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add figma-plugin/src/ui/App.tsx figma-plugin/src/ui/App.test.tsx figma-plugin/src/ui/main.tsx
git commit -m "feat: add figma plugin UI (settings, persona picker, run flow)"
```

---

### Task 6: `code.ts` — selection export and canvas annotation

**Files:**
- Modify: `figma-plugin/src/code.ts`

**Interfaces:**
- Consumes: `uint8ArrayToBase64`/`base64ToUint8Array` (`shared/binary.ts`, Task 3), `groupIssuesByFrameIndex` (`shared/simulationClient.ts`, Task 3), `computeCardPosition` (`shared/annotationLayout.ts`, Task 3), `FrictionIssue` (`shared/types.ts`, Task 3). Responds to message types `"get-settings"`, `"save-settings"`, `"export-selection"`, `"annotate"` — the exact contract `App.tsx` (Task 5) already calls against.
- Produces: nothing further downstream — this is the last piece of the round trip.

**Note before starting:** none of this task's `figma.*` calls can be exercised by an automated test in this environment (no Figma runtime here) — see the spec's and this plan's "Testing & verification limits" notes. This task is implemented directly (no red/green TDD cycle possible for the `figma.*` calls themselves) but every non-Figma-API computation it does (grouping, positioning, encoding) is delegated to the already-tested Task 3 functions rather than reimplemented inline, so the only untested surface is the thin glue that calls `figma.createFrame`/`exportAsync`/`clientStorage`.

- [ ] **Step 1: Replace the placeholder `code.ts` with the full message handler**

```typescript
// figma-plugin/src/code.ts
import { base64ToUint8Array, uint8ArrayToBase64 } from "./shared/binary";
import { computeCardPosition } from "./shared/annotationLayout";
import { groupIssuesByFrameIndex } from "./shared/simulationClient";
import type { FrictionIssue } from "./shared/types";

figma.showUI(__html__, { width: 360, height: 520 });

const SEVERITY_COLORS: Record<FrictionIssue["severity"], RGB> = {
  low: { r: 0.29, g: 0.58, b: 0.9 },
  medium: { r: 0.95, g: 0.7, b: 0.2 },
  high: { r: 0.92, g: 0.45, b: 0.2 },
  critical: { r: 0.85, g: 0.2, b: 0.2 },
};

interface PluginMessage {
  id: string;
  type: "get-settings" | "save-settings" | "export-selection" | "annotate";
  payload?: unknown;
}

let lastExportedFrames: SceneNode[] = [];

async function handleGetSettings(): Promise<{ baseUrl: string; apiKey: string }> {
  const baseUrl = (await figma.clientStorage.getAsync("baseUrl")) ?? "";
  const apiKey = (await figma.clientStorage.getAsync("apiKey")) ?? "";
  return { baseUrl, apiKey };
}

async function handleSaveSettings(payload: { baseUrl: string; apiKey: string }): Promise<void> {
  await figma.clientStorage.setAsync("baseUrl", payload.baseUrl);
  await figma.clientStorage.setAsync("apiKey", payload.apiKey);
}

async function handleExportSelection(): Promise<{ index: number; bytes: number[] }[]> {
  const exportable = figma.currentPage.selection.filter(
    (node): node is SceneNode & ExportMixin => "exportAsync" in node,
  );
  if (exportable.length === 0) {
    throw new Error("Select at least one frame before running a simulation.");
  }

  lastExportedFrames = exportable;
  const results: { index: number; bytes: number[] }[] = [];
  for (let index = 0; index < exportable.length; index++) {
    const bytes = await exportable[index].exportAsync({ format: "PNG" });
    const base64 = uint8ArrayToBase64(bytes);
    results.push({ index, bytes: Array.from(base64ToUint8Array(base64)) });
  }
  return results;
}

function createAnnotationCard(
  issue: FrictionIssue,
  position: { x: number; y: number },
): FrameNode {
  const card = figma.createFrame();
  card.name = `FlowSage: ${issue.title}`;
  card.layoutMode = "VERTICAL";
  card.itemSpacing = 6;
  card.paddingTop = 12;
  card.paddingBottom = 12;
  card.paddingLeft = 12;
  card.paddingRight = 12;
  card.resize(280, card.height);
  card.x = position.x;
  card.y = position.y;
  card.fills = [{ type: "SOLID", color: { r: 1, g: 1, b: 1 } }];
  card.strokes = [{ type: "SOLID", color: SEVERITY_COLORS[issue.severity] }];
  card.strokeWeight = 2;

  const severityPill = figma.createText();
  severityPill.characters = issue.severity.toUpperCase();
  severityPill.fills = [{ type: "SOLID", color: SEVERITY_COLORS[issue.severity] }];
  card.appendChild(severityPill);

  const title = figma.createText();
  title.characters = issue.title;
  card.appendChild(title);

  const heuristic = figma.createText();
  heuristic.characters = issue.heuristic_violated;
  card.appendChild(heuristic);

  const suggestedFix = figma.createText();
  suggestedFix.characters = issue.suggested_fix;
  suggestedFix.textAutoResize = "HEIGHT";
  suggestedFix.resize(256, suggestedFix.height);
  card.appendChild(suggestedFix);

  return card;
}

async function handleAnnotate(payload: { issues: FrictionIssue[] }): Promise<void> {
  await figma.loadFontAsync({ family: "Inter", style: "Regular" });
  const grouped = groupIssuesByFrameIndex(payload.issues);

  for (const [frameIndex, issues] of grouped) {
    const sourceNode = lastExportedFrames[frameIndex];
    if (!sourceNode || !("x" in sourceNode)) continue;
    const bounds = {
      x: sourceNode.x,
      y: sourceNode.y,
      width: "width" in sourceNode ? sourceNode.width : 0,
      height: "height" in sourceNode ? sourceNode.height : 0,
    };

    issues.forEach((issue, stackIndex) => {
      const position = computeCardPosition(bounds, stackIndex);
      createAnnotationCard(issue, position);
    });
  }

  figma.notify(`Annotated ${payload.issues.length} issue(s) on the canvas`);
}

figma.ui.onmessage = async (message: PluginMessage) => {
  try {
    let payload: unknown;
    switch (message.type) {
      case "get-settings":
        payload = await handleGetSettings();
        break;
      case "save-settings":
        await handleSaveSettings(message.payload as { baseUrl: string; apiKey: string });
        break;
      case "export-selection":
        payload = await handleExportSelection();
        break;
      case "annotate":
        await handleAnnotate(message.payload as { issues: FrictionIssue[] });
        break;
    }
    figma.ui.postMessage({ pluginMessage: { id: message.id, payload } });
  } catch (error) {
    figma.ui.postMessage({
      pluginMessage: {
        id: message.id,
        payload: undefined,
        error: error instanceof Error ? error.message : "Unknown plugin error",
      },
    });
  }
};
```

Note the message envelope from `code.ts` (`{ pluginMessage: { id, payload } }`) matches what `bridge.ts`'s `window.addEventListener("message", ...)` already expects (Task 4) — no change needed there. The `error` field added to the reply envelope here is forward-looking; `bridge.ts`'s `callPlugin` doesn't yet surface it distinctly from `payload` (it resolves with whatever `payload` is, `undefined` on error) — acceptable for this plan's scope since `App.tsx`'s `handleRun` already wraps the whole flow in a `try`/`catch` and `export-selection`'s thrown error (empty selection) is the main one users will hit; a `pending.reject` path in `bridge.ts` would be a natural follow-up but isn't required to ship a working round trip.

- [ ] **Step 2: Typecheck**

Run: `cd figma-plugin && npx tsc --noEmit`
Expected: clean — `@figma/plugin-typings` provides `figma`, `SceneNode`, `FrameNode`, `RGB`, `ExportMixin`, etc.

- [ ] **Step 3: Build**

Run: `cd figma-plugin && npm run build`
Expected: `dist/code.js` rebuilds with no bundling errors.

- [ ] **Step 4: Run the full automated test suite one more time (confirms Task 6 didn't regress anything Task 3/4/5 already covered)**

Run: `cd figma-plugin && npx vitest run`
Expected: PASS, same test count as after Task 5 — `code.ts` itself has no tests (see the note above), so this is a regression check, not new coverage.

- [ ] **Step 5: Commit**

```bash
git add figma-plugin/src/code.ts
git commit -m "feat: implement figma plugin selection export and canvas annotation"
```

---

### Task 7: README, manual QA checklist, full verification

**Files:**
- Create: `figma-plugin/README.md`

- [ ] **Step 1: Write `README.md`**

```markdown
# FlowSage Figma Plugin

Select frames in Figma, run a real FlowSage persona simulation against them, and get
friction findings placed back on the canvas as annotation cards next to each frame.

## Setup (sideloaded, dev-mode plugin)

1. `npm install && npm run build` (produces `dist/code.js` + `dist/ui.html`).
2. In Figma desktop: **Plugins → Development → Import plugin from manifest…**, select
   this directory's `manifest.json`.
3. Open the plugin, go to Settings, enter your FlowSage instance's Base URL (e.g.
   `https://127.0.0.1` for a local deploy) and an API key from
   **Settings → Integrations** in the FlowSage web app. Click Save.

## Using it

1. Select one or more frames on the canvas — selection order becomes walkthrough order.
2. Pick a persona, enter a goal and a flow name.
3. Click **Run & Annotate**. The plugin exports the selected frames, uploads them to
   FlowSage as a new simulation run, polls until it finishes, then creates an annotation
   card (severity, title, heuristic, suggested fix) next to each frame with a finding.

## Manual QA checklist (run after any change — no automated tool in this repo's dev
environment can drive a real Figma session)

- [ ] Sideload the plugin fresh; Settings loads empty on first run, no crash.
- [ ] Save Settings with a real Base URL + API key; close and reopen the plugin;
      confirm the values persisted (this exercises `figma.clientStorage`, which
      nothing here can unit test).
- [ ] Select zero frames, click Run & Annotate: shows the "Select at least one frame…"
      error, no network call happens, nothing appears on canvas.
- [ ] Select 2+ frames (in a deliberate order), pick a persona, run a real simulation
      against a live FlowSage backend: confirm a simulation run appears in the FlowSage
      web app's Predictive Engine with `screen` values `001`/`002`/… in walkthrough
      order matching your selection order.
- [ ] After completion, confirm one annotation card per finding appears on the canvas,
      positioned to the right of the correct source frame (not a different one),
      stacked vertically when a frame has 2+ findings, with the right severity color,
      title, heuristic, and suggested-fix text.
- [ ] Force a failed run (e.g. an invalid API key mid-run) and confirm the UI shows the
      run's error message instead of silently hanging or annotating nothing with no
      explanation.
```

- [ ] **Step 2: Full automated verification**

Run: `cd figma-plugin && npx oxlint && npx tsc --noEmit && npx vitest run && npm run build`
Expected: all clean.

Run: `cd backend && uv run pytest && uv run mypy --strict src && uv run autoflake8 --check --recursive src`
Expected: all clean (re-confirms Task 1's backend change still holds after later tasks).

- [ ] **Step 3: Commit**

```bash
git add figma-plugin/README.md
git commit -m "docs: add figma plugin README and manual QA checklist"
```

- [ ] **Step 4: Flag remaining manual verification to the user**

This plan closes every task that can be verified by an automated tool in this
environment. The README's manual QA checklist (Step 1 above) still needs a human
(or a future session with the `figma` MCP connector authorized) to actually sideload
the plugin in Figma desktop and run it against a live FlowSage backend — report this
clearly rather than claiming the plugin is fully verified end-to-end.

---

## Self-Review Notes (for the plan author, not a task)

- **Spec coverage:** backend dual-auth (Task 1) → plugin scaffold (Task 2) → shared pure logic: binary/screen-matching/layout (Task 3) → UI bridge + API client (Task 4) → UI React app (Task 5) → `code.ts` export/annotate (Task 6) → README/manual QA/full verification (Task 7). Every section of the spec (Architecture, Data flow, Screen-matching, Annotation node design, Config & auth UX, Testing limits) maps to a task.
- **Spec's "out of scope" list** (Comments/REST API, Community publishing, frame mutation, unrelated web-app changes) — confirmed no task touches any of them.
- **Placeholder scan:** no TBD/TODO; every step has real code, not a description of code.
- **Type consistency check:** `Persona` (`id`/`slug`/`name`) used identically in `types.ts` (Task 3), `api.ts` (Task 4), `App.tsx` (Task 5). `FrictionIssue` fields (`screen`/`severity`/`title`/`heuristic_violated`/`persona_impact`/`description`/`suggested_fix`) match `backend/src/flowsage_backend/api/simulations.py`'s `FrictionIssueOut` exactly and are used consistently in `types.ts`, `simulationClient.ts`'s `groupIssuesByFrameIndex`, `App.tsx`'s test fixtures, and `code.ts`'s `createAnnotationCard`. `SimulationRun.status` (`"queued"|"running"|"completed"|"failed"`) matches `RunStatus`'s actual enum values (lowercase, per `backend/src/flowsage_backend/models/simulation.py`'s `RunStatus(str, enum.Enum)`). Message type strings (`"get-settings"`/`"save-settings"`/`"export-selection"`/`"annotate"`) match exactly between `App.tsx` (Task 5, the caller) and `code.ts` (Task 6, the handler).
- **Known small gap, noted rather than silently fixed:** `bridge.ts`'s `callPlugin` doesn't distinguish an error reply from a success reply (Task 6's note) — acceptable for this plan since the one error path that matters most (empty selection) still surfaces correctly through `App.tsx`'s own `try`/`catch` around the whole flow, but a cleaner reject-on-error bridge would be a reasonable fast-follow, not required to ship a working round trip.
```
