# Phase 3 chunk 1: Workspace multi-tenancy (Workspace, Membership/roles, /settings/general + /settings/team)

Date: 2026-07-19
Status: Approved, pending implementation plan.

## Context

Phase 2 (all 4 chunks) is done, merged, pushed. This starts Phase 3 ("Beta:
multi-tenant") per `plans/full-project-coding-plan.md` Phase 3 item 1:
"Workspace model everywhere (row-level scoping), invites, roles enforcement
(Admin/Researcher/Viewer), `/settings/team` + `/settings/general` (archive
workspace, retention policy, region)."

Survey of current state confirmed the app is 100% single-tenant today: no
`Workspace`, no `Membership`, no role enum, no `workspace_id` column on any
of the 9 existing tables (`users`, `events`, `personas`, `persona_memories`,
`simulation_runs`, `simulation_steps`, `friction_issues`, `retraining_jobs`,
`calibration_settings`). One seeded admin user, one shared `settings` row.
Auth is a single JWT httpOnly cookie carrying only `user_id`
(`security.py`, `deps.py:23-42`). No email-sending infra exists anywhere in
the backend.

This chunk is scoped to workspace/roles/general+team settings only. API
keys, webhook delivery log, `/settings/integrations`, SOC2 audit log, rate
limiting, per-workspace Neo4j label isolation, and pilot onboarding tooling
are explicitly deferred to Phase 3 chunks 2-4.

## Decisions made during brainstorming

1. **Invites: instant-add by email, no token/email-send flow.** Admin enters
   an existing user's email + role → `Membership` row created immediately.
   Matches the reality that no SMTP/email-provider integration exists yet.
   Inviting an email with no matching `User` row returns a 404 ("no account
   with that email") rather than creating a pending invite record — a
   pending-invite table is out of scope for this chunk.
2. **Active workspace lives in the JWT**, not a header or URL path param.
   JWT payload gains `workspace_id` alongside `user_id`. A new
   `POST /auth/switch-workspace` endpoint validates the caller has a
   `Membership` in the target workspace and reissues the cookie. This avoids
   touching every existing frontend API call (no header injection, no route
   restructuring) at the cost of one extra endpoint + a cookie reissue on
   switch.
3. **Migration backfills a single "Default" workspace.** All existing rows
   across the 9 tables get `workspace_id` pointed at one auto-created
   `Workspace`, and the existing seeded user becomes its `admin` `Membership`.
   Column is added nullable first, backfilled, then tightened to `NOT NULL`
   in a follow-up migration in the same task (two migration files, run
   back-to-back, not deployed separately).
4. **Neo4j gets a `workspace_id` property on ingested nodes, not label-per-
   workspace isolation.** True per-workspace Neo4j label/DB isolation is
   explicitly Phase 3 chunk 3 (SOC2-track) scope. This chunk only tags
   ingested graph data so that later chunk can filter/isolate by it.
5. **Role matrix is fixed code (`require_role` dependency ordinal check),
   not a configurable permissions table.** Ordinal: `admin` (3) >
   `researcher` (2) > `viewer` (1).
   - **viewer**: read-only on personas, simulations, journey graph,
     calibration, alerts, friction issues.
   - **researcher**: viewer + create/run simulations, create/edit personas,
     resolve/export friction issues, trigger digests.
   - **admin**: researcher + invite/remove members, change member roles,
     edit workspace settings, archive workspace.
6. **Last-admin guard**: removing or demoting a `Membership` is rejected
   (400) if it would leave a workspace with zero `admin` memberships.

## Architecture

### Data model

New `backend/src/flowsage_backend/models/workspace.py`:

- `Workspace`: `id` (UUID pk), `name`, `slug` (unique, `fs-…`),
  `description`, `avatar_url`, `privacy` (enum: `private`/`restricted`),
  `region`, `retention_days` (int), `archived` (bool, default False),
  `created_at`.
- `Membership`: `id` (UUID pk), `user_id` (FK → `users.id`), `workspace_id`
  (FK → `workspaces.id`), `role` (enum: `admin`/`researcher`/`viewer`),
  `created_at`. Unique constraint on `(user_id, workspace_id)`.

Existing tables (`personas`, `persona_memories`, `simulation_runs`,
`simulation_steps`, `friction_issues`, `retraining_jobs`,
`calibration_settings`, `events`) each gain a `workspace_id` FK column
(NOT NULL after backfill). `users` does NOT get a `workspace_id` column —
a user's workspaces are derived via `Membership`, since one user can belong
to multiple workspaces.

### Auth changes

- JWT payload (`security.py`) gains `workspace_id: str` alongside the
  existing `sub` (user id).
- `deps.py`'s `get_current_user` is replaced by `get_current_membership`,
  which: decodes the cookie, loads the `User`, loads the `Membership` row
  for `(user_id, workspace_id)` from the token, 401s if either is missing,
  403s if the `Membership`'s workspace is archived. Returns
  `tuple[User, Membership]`.
- A new `require_role(min_role: Role)` dependency factory wraps
  `get_current_membership`, comparing role ordinals, 403 if insufficient.
- `POST /auth/switch-workspace {workspace_id: UUID}`: validates a
  `Membership` exists for the caller in that workspace, reissues the JWT
  cookie with the new `workspace_id`. Reuses existing cookie-setting code
  from `auth.py`'s login handler.
- `GET /auth/me` response gains `workspace_id`, `role`, and a list of the
  user's other workspaces (id + name), for a workspace switcher.

### Router changes

Every existing router (`personas.py`, `simulations.py`, `events.py`'s
`graph_router`, `calibration.py`, `alerts.py`, `exports.py`) swaps its
`Depends(get_current_user)` for `Depends(get_current_membership)` (or
`Depends(require_role(...))` where the endpoint mutates state), and every
query gains `.where(Model.workspace_id == membership.workspace_id)`. New
rows created by these routers set `workspace_id = membership.workspace_id`
on insert.

`events.py`'s `events_router` (`POST /v1/events`, API-key gated, not
user-session gated) is unaffected by JWT changes; its ingested `Event` rows
still need `workspace_id` — resolved by looking up the single API key's
owning workspace (today's shared-secret `require_api_key` maps to the one
Default workspace; proper per-workspace API keys are chunk 2 scope).

`settings.py`'s existing singleton `Settings`/`CalibrationSettings` row
gains `workspace_id` and becomes one row per workspace instead of a global
singleton.

### New endpoints — `backend/src/flowsage_backend/api/workspaces.py`

- `GET /workspaces` — list workspaces the caller has a `Membership` in.
- `POST /workspaces` — create a workspace; caller becomes its `admin`.
- `GET /workspaces/current` — current workspace's general settings fields.
- `PATCH /workspaces/current` — update name/description/avatar_url/privacy/
  region/retention_days. `require_role(admin)`.
- `POST /workspaces/current/archive` — sets `archived = True`.
  `require_role(admin)`.
- `GET /workspaces/current/members` — list `Membership` rows joined with
  `User.email`, for the team table.
- `POST /workspaces/current/members` — body `{email, role}`; 404 if no
  `User` with that email exists; 409 if already a member; creates
  `Membership`. `require_role(admin)`.
- `PATCH /workspaces/current/members/{membership_id}` — body `{role}`;
  rejects if it would remove the last admin. `require_role(admin)`.
- `DELETE /workspaces/current/members/{membership_id}` — rejects if it
  would remove the last admin. `require_role(admin)`.

### Neo4j tagging

`flowsage_graph.ingest.Neo4jGraphSink` calls (invoked from `events.py`) gain
a `workspace_id` property on ingested `Screen`/`Session`/`TRANSITION` data,
sourced from the resolved workspace of the ingesting API key.

## Frontend

- `frontend/src/routes/settings/GeneralSettingsPage.tsx` — Workspace
  Identity form (name, description, avatar, workspace ID display,
  established date), Configuration Parameters (privacy toggle, region,
  retention policy), Danger Zone (archive workspace with confirm), matching
  `design-hifi-prototypes/settings_general_flowsage`.
- `frontend/src/routes/settings/TeamSettingsPage.tsx` — member table
  (name/email, role dropdown, last active), Invite Member modal (email +
  role select), Total Seats / Active Research / Live Sessions stat tiles,
  matching `design-hifi-prototypes/settings_team_access_flowsage`. The
  prototype's "Security Logs" card is a stub (non-functional) link — real
  audit log view is Phase 3 chunk 3.
- Settings nav (shared shell used by `ModelCalibrationSettingsPage`) gains
  "General" and "Team Access" entries.
- A workspace-switcher dropdown (near the existing profile chip in the top
  bar) lists the user's workspaces from `GET /auth/me`, posts to
  `/auth/switch-workspace` on selection, then reloads current-page data.
- API client (`frontend/src/api/`) gains typed functions for all new
  `/workspaces*` and `/auth/switch-workspace` endpoints, following the
  existing `fetch`-with-typed-response convention (no new HTTP library).

## Testing

- `models/workspace.py`: covered implicitly via the endpoint tests below;
  no standalone model unit tests (matches existing convention — no bare
  model tests elsewhere in the codebase).
- Migration: a dedicated test spins up the migration against a fresh test
  DB, seeds pre-migration-shaped rows in each of the 9 tables, runs the
  backfill migration, and asserts every row now has the Default workspace's
  `workspace_id` and the seeded user has an `admin` `Membership`.
- `deps.py`: unit tests for `get_current_membership` (valid cookie, missing
  membership → 401, archived workspace → 403) and `require_role` (ordinal
  comparison, insufficient role → 403).
- `api/workspaces.py`: endpoint tests covering list/create/patch/archive,
  member add (success, 404 unknown email, 409 duplicate), role change and
  removal (including the last-admin-guard 400 case).
- Every existing router test file (`test_personas.py`, `test_simulations.py`,
  etc.) gets updated fixtures so its authenticated test client carries a
  `workspace_id` in the JWT, and gains one new test asserting a second
  workspace's data is NOT visible to the first workspace's membership
  (cross-tenant isolation check) — this is the most important regression
  test in this chunk.
- Frontend: Vitest component tests for `GeneralSettingsPage` and
  `TeamSettingsPage` (render, form submit, invite modal, role change, last-
  admin removal disabled in UI). Playwright e2e: log in, switch workspace,
  confirm dashboard data changes; invite a member, change their role.
- Full `docker compose up -d --build` verification pass: run the migration
  against seeded demo data, curl all new endpoints, confirm cross-tenant
  isolation manually (create a second workspace, confirm its personas list
  is empty), drive `/settings/general` and `/settings/team` in a real
  Playwright browser session.

## Out of scope (deferred to Phase 3 chunks 2-4)

- Per-workspace `ApiKey` model (hashed, prefixed) and `/settings/integrations`
  UI, `WebhookEndpoint` + delivery log, marketplace cards.
- SOC2-track: real audit log table + Security Logs view, rate limiting,
  secrets hygiene pass, true per-workspace Neo4j label/DB isolation.
- Pending-invite records for not-yet-registered emails, any email-sending
  integration.
- Pilot onboarding tooling (sample-data import, setup guide page).
