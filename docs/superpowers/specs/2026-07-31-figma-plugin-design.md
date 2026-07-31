# Figma Plugin Design Spec

**Phase:** 4, item 3 — "Figma plugin (separate `figma-plugin/` package): select frames → call FlowSage API → inline friction annotations ('Apply to Figma' round-trip)." Last unbuilt item in the entire coding plan.

## Problem

FlowSage's predictive engine today only accepts pre-exported screenshots uploaded through the web app (`/predictive`). Designers working directly in Figma have no way to run a persona walkthrough against frames still in the design file, or to see friction findings placed back on the canvas next to the frames they came from.

## Design

### Architecture

Two pieces of work: a small backend auth extension, and a new standalone package.

**Backend:** `GET /personas`, `POST /simulations`, and `GET /simulations/{run_id}` are currently cookie-session-only (`Depends(get_current_membership)` in `backend/src/flowsage_backend/deps.py`). A Figma plugin's UI iframe cannot carry the app's httpOnly session cookie cross-origin, so it needs the same `X-API-Key` credential already used by `/v1/events` and `/v1/insights/*` (`require_workspace_api_key`).

Add one new dependency to `deps.py`, `get_current_actor`, that accepts *either* a valid session cookie *or* `X-API-Key`, returning `(workspace_id: uuid.UUID, user_id: uuid.UUID | None)`. Swap it in on those three routes in place of `get_current_membership`; the web app's cookie flow is unaffected (it still resolves a `user_id`). `record_audit_event`'s `actor_user_id` call sites in `simulations.py` pass `None` when the actor came from an API key — mirrors how `/v1/events` already has no user actor. No new tables, no change to any other route.

**`figma-plugin/`** — new top-level package (own `package.json`/`tsconfig.json`, esbuild for bundling; not a uv workspace member, not part of the `frontend/` npm project). Follows Figma's mandatory two-context plugin model:

- **`src/code.ts`** (main/sandbox thread): reads `figma.currentPage.selection`, calls `node.exportAsync({ format: "PNG" })` per selected frame, and — once results come back from the UI — creates the annotation card nodes on the canvas. No network access here.
- **`src/ui/`** (small React app rendered in `ui.html`, a real iframe with `fetch`): settings screen (Base URL + API key fields, persisted via `figma.clientStorage`), persona dropdown (`GET /personas`), goal/flow-name text inputs, a "Run & Annotate" button, and a progress state while polling. Communicates with `code.ts` only via `figma.ui.postMessage` / `window.onmessage`.

### Data flow

1. User selects 1+ frames in Figma, opens the plugin, picks a persona, types a goal + flow name.
2. UI asks `code.ts` (postMessage) to export the current selection, in selection order.
3. `code.ts` runs `exportAsync` per frame, returns an array of `{ index, bytes }` to the UI, while keeping its own local `index → SceneNode` map in memory.
4. UI builds `multipart/form-data` — `persona_id`, `goal`, `flow_name`, and `files[]` named `001.png`, `002.png`, … (zero-padded index only, no frame-name text in the filename) — and `POST`s to `{baseUrl}/simulations` with `X-API-Key`.
5. UI polls `GET /simulations/{run_id}` (also `X-API-Key`) until `status` is `COMPLETED` or `FAILED`.
6. On `COMPLETED`, UI sends the returned `FrictionIssue[]` to `code.ts`, which matches each `issue.screen` (e.g. `"001"`) back to its `index → SceneNode` map and creates one annotation card per issue next to its source frame.
7. On `FAILED` or a network/HTTP error, the UI shows the error inline (`run.error` if present) — no partial/silent annotation.

### Screen-matching

The backend already derives `screen` from the uploaded filename's stem (`Path(filename).stem`, sorted lexicographically for walk order — `scripts/flowsage-predict/src/flowsage_predict/agent.py`/`cli.py`). Rather than sanitizing Figma frame names (which can contain `/`, emoji, arbitrary Unicode) into safe filenames and parsing them back, the plugin exports as purely index-based names (`001.png`, `002.png`, …) and matches results back to the in-memory `index → SceneNode` array built at export time. No name-parsing, no sanitization, no collision risk. Trade-off, stated plainly: a user who later opens this same run in the FlowSage web app sees screen names `"001"`/`"002"` instead of a friendly label — acceptable since the plugin session is this run's primary consumer.

### Annotation node design

Per approved design: native Figma nodes, not Figma's separate REST-API-only comments feature. For each matched issue, `code.ts` creates an auto-layout frame ("card") positioned to the right of its source frame's bounding box (stacked vertically with a fixed gap when multiple issues land on the same frame), containing:
- a severity pill (fill color keyed by `low`/`medium`/`high`/`critical`, same four buckets the web app uses),
- `title` (bold text),
- `heuristic_violated` (small caption text),
- `suggested_fix` (wrapped body text).

Built with plain Plugin API calls (`figma.createFrame`, `figma.createText`, autolayout properties) — no dependency on the web app's design tokens/components.

### Config & auth UX

Plugin settings panel (in the UI iframe): `Base URL` (text input, e.g. `https://127.0.0.1` or a real deployed domain) and `API Key` (password-style input, the workspace's existing `fs_prod_*`/`fs_stg_*` key from `/settings/integrations`). Both persisted via `figma.clientStorage.setAsync`, read back on plugin open. No OAuth, no separate Figma-side credential — the plugin is sideloaded (dev-mode `manifest.json`, not published to Figma Community), matching the self-hosted, configurable-backend nature of the tool.

### Out of scope

- Figma's native Comments feature / REST API (would need a second, Figma-side credential; explicitly rejected in brainstorming).
- Publishing to Figma Community (would push toward a single hardcoded backend, conflicting with the configurable-URL design).
- Editing/mutating the selected frames themselves (no auto-fix, no design mutation) — annotations only.
- Any change to the web app beyond the backend auth extension described above.

### Testing & verification limits

- Unit-testable in isolation (with a lightweight `fetch` mock, same spirit as the backend's `httpx.MockTransport`-based Slack/Jira client tests): index-based filename generation, multipart payload construction, poll/response parsing, and the `issue.screen → index` matching logic.
- Backend: new tests for `get_current_actor` (cookie-only, API-key-only, neither → 401) plus updated tests on the three affected routes confirming both auth paths still work and existing cookie-based tests are unaffected.
- **Not automatable in this environment:** the actual Figma Scene API (`exportAsync`, node creation, `clientStorage`) only runs inside Figma's real desktop/web runtime. No tool available in this session can sideload a plugin into Figma or drive its UI, unlike the browser-based Playwright/Chrome DevTools passes used for every previous phase's frontend work. Verification of the plugin's actual in-Figma behavior (frame export produces valid images, cards land in the right place, polling/annotation round-trip works end to end against the real deployed backend) will be a **manual checklist run by the user** after implementation, or a session where the `figma` MCP connector has been authorized interactively. This will be called out again explicitly in the implementation plan's verification section rather than silently assumed done.

## Verification

- Backend: `pytest` (new + existing simulations/personas tests) + `mypy --strict` + `autoflake8` clean.
- Plugin: unit tests (vitest or node's built-in test runner) for all pure logic listed above; `tsc` strict clean; manifest validated against Figma's schema.
- Manual (user-run, see limits above): sideload in Figma desktop, select real frames, run against the live local deploy stack, confirm cards appear correctly positioned with correct content for each severity.
