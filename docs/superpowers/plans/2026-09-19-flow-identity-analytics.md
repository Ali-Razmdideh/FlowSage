# Flow identity and analytics lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Attribute analytics to immutable flow versions and align graph retention with workspace data controls.

**Architecture:** Introduce nullable flow references compatibly, use them as filters through ingestion and analytics, then surface coverage and evidence in the UI. Graph projection cleanup shares the retention worker boundary.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, Pydantic, Neo4j, React, TypeScript, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-09-19-flow-identity-analytics-design.md`

## Global Constraints

- Existing events and simulations remain readable as unassigned legacy data.
- All flow identity lookups are workspace-scoped.
- All flow/version filters remain optional for all-flows views.
- Graph deletion and filesystem deletion remain restricted to workspace-owned data.

## Review Focus

- Same screen names in two flows never share a graph node or calibration comparison.
- A forged flow ID from another workspace is rejected before writing data.
- Legacy rows remain readable after migration.
- Partial graph cleanup failure does not prevent relational retention.
- A deletion confirmation cannot delete a different workspace.

---

### Task 1: Flow schema and compatible ingestion

**Files:** models, Alembic migration, events API/service, simulations API/service, tests.

- [ ] Write failing tests for workspace-scoped flow creation, cross-workspace rejection, legacy event reads, and version assignment.
- [ ] Run focused backend tests; verify missing model/columns fail.
- [ ] Add `Flow` and `FlowVersion` records plus nullable `flow_id`/version columns to events and simulation runs. Add migration indexes and workspace foreign keys.
- [ ] Extend event and simulation payloads with optional flow key/version; resolve before writes.
- [ ] Run focused tests and commit `feat(flows): add versioned flow identity`.

### Task 2: Scoped analytics and graph projection

**Files:** event query service, graph models/ingest, funnel/calibration/alerts APIs, tests.

- [ ] Write failing tests proving identical screen names in distinct flows produce isolated funnels and graph transitions.
- [ ] Run focused tests; verify current all-workspace aggregation fails isolation.
- [ ] Add optional flow/version filters through event queries, funnel, calibration, alerts, and Neo4j merge keys.
- [ ] Preserve all-flow aggregation and expose unassigned legacy filtering.
- [ ] Run focused tests and commit `feat(analytics): scope reports by flow version`.

### Task 3: Graph retention and workspace data controls

**Files:** graph sink, worker, workspace API, export/delete service, tests.

- [ ] Write failing tests for graph purge parameters, expired projection cleanup, export workspace ownership, and deletion confirmation.
- [ ] Add graph sink deletion by workspace/cutoff; invoke independently from relational retention.
- [ ] Add administrator-only export job and confirmed workspace deletion job, both audit logged and path-confined.
- [ ] Run focused tests and commit `feat(data): add lifecycle controls`.

### Task 4: Flow filters, quality metrics, and session evidence UI

**Files:** frontend API/types, journey and calibration routes, tests.

- [ ] Write failing UI tests for flow selection, legacy labeling, coverage metrics, and friction evidence navigation.
- [ ] Add flow/version selectors and display covered versus unmatched screens, sessions, and event counts.
- [ ] Link friction findings to a filtered session-evidence view.
- [ ] Run frontend tests, lint, typecheck, build; commit `feat(ui): expose flow analytics evidence`.

### Task 5: Migration verification and delivery

- [ ] Run migration upgrade/downgrade compatibility checks against a populated fixture.
- [ ] Run backend, graph, frontend, and existing end-to-end suites.
- [ ] Review migration and permission diff; fetch, rebase, push normally to `origin/main`.
