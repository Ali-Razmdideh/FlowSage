# Phase 3 chunk 4 — Pilot Onboarding Tooling (sample-data import, setup guide page)

**Status:** Approved 2026-07-24. Closes the last item of `plans/full-project-coding-plan.md` Phase 3 ("Pilot onboarding tooling: sample-data import, setup guide page"). This closes out Phase 3 (Beta: multi-tenant) entirely — workspace multi-tenancy, integrations, security hardening, and now onboarding tooling are all shipped.

## Context

`scripts/sample_data/` (44-event `events.jsonl` + 3 mock checkout screenshots) has existed since Phase 0 and powers the CLI's own demo, but the web app has never had a way to load it. The Journey Graph empty state (`frontend/src/routes/journey/JourneyGraphPage.tsx`'s `EmptyState`) is currently text-only ("Awaiting Event Ingestion... `POST /v1/events`"), with no "Import Sample Data" button — even though the plan's screen route map explicitly calls that button out for this screen. No setup guide page exists anywhere in the frontend, and no hi-fi design prototype covers either piece (same situation Security Logs was in last chunk — designed fresh here, not mapped onto a mock).

## Scope

**In scope:**
- Bundle `scripts/sample_data/events.jsonl` + its 3 screenshots into the backend package (`backend/src/flowsage_backend/resources/sample_data/`) so the Docker-built backend image can read them without reaching into a sibling package's directory.
- `POST /onboarding/import-sample-data`: ingests the bundled events into the caller's workspace via the existing `ingest_events()` (same function `POST /v1/events` uses) and enqueues one demo `SimulationRun` against the bundled screenshots using the `novice_user` baseline persona and a fixed goal string, so both Journey Graph and Predictive Engine have populated examples after one click.
- `GET /onboarding/status`: compute-on-demand (no new table, same pattern as `calibration.py`/`churn.py`) — `{has_api_key, has_events, has_completed_simulation, has_multiple_members}` for the caller's workspace.
- New `/getting-started` frontent route + sidebar entry: 4-item checklist reflecting `GET /onboarding/status`, each item linking to its relevant existing settings/action page, plus an "Import Sample Data" button.
- Wire the same "Import Sample Data" button into `JourneyGraphPage.tsx`'s `EmptyState`, matching the plan's literal UI copy for that screen.

**Out of scope (explicitly deferred):**
- Re-timestamping the sample events to look "live" (e.g. shifted to the last 24 hours) — ingested as-is, matching how the CLI's own demo already treats this dataset. Not a checklist/onboarding concern.
- Any "undo"/re-import guard — importing sample data twice just ingests the same 44 events twice and queues a second demo simulation; this is a low-stakes pilot/demo action, not something worth building idempotency guards for.
- Auto-dismissing or hiding `/getting-started` once all 4 items are complete — it stays as a permanent reference page (accessible any time from the sidebar), not a one-time wizard that disappears.

## Architecture & Data Flow

**Bundling**: `backend/src/flowsage_backend/resources/sample_data/events.jsonl` and `.../screenshots/*.png` are a build-time copy of `scripts/sample_data/`'s contents (single source of truth stays `scripts/sample_data/`), included in the backend Docker image the same way `flowsage-predict`'s baseline persona YAMLs are already bundled as package data.

**Import endpoint**: `POST /onboarding/import-sample-data` (behind `get_current_membership`) reads the bundled `events.jsonl`, calls `ingest_events(session, workspace_id, graph_events)` synchronously (this is a small, fixed 44-row dataset — no need for the arq queue here, unlike the real `/v1/events` path which has to handle arbitrary-sized production payloads), then creates one `SimulationRun` row via the existing `create_run()` helper (copying the bundled screenshots into that run's upload directory first, exactly like a real upload would) and enqueues `run_simulation_job` on arq — reusing the exact same async simulation pipeline a real user-uploaded run goes through, so there's no second code path to maintain for "demo" runs.

**Status endpoint**: `GET /onboarding/status` runs 4 independent, cheap queries scoped to `membership.workspace_id`: `EXISTS` on `ApiKey` (unrevoked), `EXISTS` on `Event`, `EXISTS` on `SimulationRun` where `status == COMPLETED`, and a `COUNT` on `Membership >= 2`.

**Frontend**: `GettingStartedPage.tsx` fetches `GET /onboarding/status` on mount, renders 4 checklist rows (checked/unchecked icon + label + link: "Create an API key" → `/settings/integrations`, "Ingest your first event" → same page's Import Sample Data button, "Run your first simulation" → `/predictive`, "Invite a teammate" → `/settings/team`), plus the Import Sample Data button calling the new endpoint and re-fetching status on success. `JourneyGraphPage.tsx`'s `EmptyState` gets the same button (extracted into a small shared component, `ImportSampleDataButton.tsx`, so both places call the identical endpoint/loading/error logic rather than duplicating it).

## Components

| File | Purpose |
|---|---|
| `backend/src/flowsage_backend/resources/sample_data/events.jsonl`, `.../screenshots/*.png` | Bundled copy of the CLI's sample dataset |
| `backend/src/flowsage_backend/onboarding.py` | `get_onboarding_status()`, `import_sample_data()` — pure functions, no new tables |
| `backend/src/flowsage_backend/api/onboarding.py` | `GET /onboarding/status`, `POST /onboarding/import-sample-data` |
| `backend/pyproject.toml` / Dockerfile | package-data inclusion for the new `resources/` dir |
| `frontend/src/routes/GettingStartedPage.tsx` | `/getting-started` checklist page |
| `frontend/src/components/ImportSampleDataButton.tsx` | Shared button used by both `GettingStartedPage` and `JourneyGraphPage`'s empty state |
| `frontend/src/routes/journey/JourneyGraphPage.tsx` | `EmptyState` gains the shared import button |
| Sidebar / `App.tsx` | new nav entry + route |

## Error Handling

- Import endpoint: if a workspace has no `ApiKey`/events yet this is the expected first-run state, not an error. Failure modes are ordinary — malformed bundled file would be a packaging bug (caught by a test), not a runtime condition to handle gracefully.
- Status endpoint: no failure modes beyond normal DB errors already handled by the existing session/dependency layer.

## Testing

- `onboarding.py`: unit tests for each of the 4 status booleans (true/false cases), and for `import_sample_data` (asserts the right event count lands in the workspace and a `SimulationRun` row is created).
- `api/onboarding.py`: cross-tenant isolation test (importing into workspace A must not affect workspace B's status).
- Frontend: `GettingStartedPage.test.tsx` (renders 4 items, reflects mocked status), `ImportSampleDataButton` covered indirectly through both consumers' tests.
- Playwright e2e: click Import Sample Data from the Journey Graph empty state, confirm the funnel renders afterward.
- Full `docker-compose up -d --build` pass: confirm the bundled files exist inside the built backend image (not just locally), click through `/getting-started` and the Journey Graph empty state in a real browser.
