# Phase 3 chunk 2: `/settings/integrations` — design spec

**Date:** 2026-07-23
**Scope:** Roadmap Phase 3 item 2 (`plans/full-project-coding-plan.md` line 77): "API key
issue/revoke (hashed, prefix display), webhook endpoints + delivery log, marketplace cards."

## Why now, and what it actually closes

Three places in the already-merged Phase 3 chunk 1 code explicitly defer to "Phase 3 chunk 2":

- `config.py`: `events_api_key` is one shared secret, so `POST /v1/events` is hardcoded to
  the single `"fs-default"`-slug workspace (`api/events.py`'s `_default_workspace_id`).
- `config.py`: `slack_webhook_url` / `jira_*` are global env vars, not per-workspace —
  every workspace in a multi-tenant deployment would post digests/exports to the same
  Slack channel and file tickets in the same Jira project.
- `worker.py`'s `run_digest_job`: scoped to the single `fs-default` workspace, "no
  per-workspace cron scheduling infrastructure yet."

This chunk closes all three: per-workspace API keys replace the shared secret, per-workspace
Slack/Jira config replaces the global env vars, and the digest job iterates every workspace.
Webhook endpoints + delivery log are a new capability (custom outbound integrations), and the
marketplace cards are the UI surface tying Slack/Jira/webhooks together in one settings page.

## Data model

Three new tables, one row per workspace for the first two (matching `CalibrationSettings`'
existing pattern of typed columns, no JSON blobs):

**`SlackIntegration`** (`slack_integrations`, unique on `workspace_id`)
`id, workspace_id, webhook_url, connected_at`. Presence of a row = connected. `PUT` upserts,
`DELETE` disconnects (drops the row).

**`JiraIntegration`** (`jira_integrations`, unique on `workspace_id`)
`id, workspace_id, base_url, email, api_token, project_key, connected_at`. Same upsert/
disconnect shape as Slack.

**`ApiKey`** (`api_keys`)
`id, workspace_id, name, key_prefix (str, first 12 chars, shown in the list UI),
key_hash (str, sha256 of the full key — Argon2 is deliberately *not* used here: keys are
high-entropy random tokens, not low-entropy user passwords, so a fast hash is correct and
lets auth-path lookups stay a single indexed equality query instead of re-hashing per row),
created_at, last_used_at (nullable), revoked_at (nullable)`.
Key format: `fs_live_<32 url-safe random chars>` (`secrets.token_urlsafe`). The raw key is
returned exactly once, in the `POST` response body — never stored, never shown again.

**`Webhook`** (`webhooks`)
`id, workspace_id, url, secret (str, random, used to HMAC-sign deliveries),
event_types (Postgres ARRAY(String) — one of {"alert.triggered"} for v1, see below),
enabled (bool, default True), created_at`.

**`WebhookDelivery`** (`webhook_deliveries`)
`id, webhook_id (FK, cascade delete), event_type, payload (Text, JSON-serialized),
status_code (int, nullable — null means the request itself failed, e.g. DNS/timeout),
success (bool), created_at`.

All five get `workspace_id` (or, for `WebhookDelivery`, inherit scoping via `webhook_id` →
`Webhook.workspace_id`) and are covered by the same cross-tenant isolation test pattern as
`backend/tests/test_workspace_isolation.py`.

**Event types (v1 scope):** just `"alert.triggered"` — fired whenever `build_alerts_report`
returns a non-empty report, from the same digest job that already computes that report. This
keeps the webhook feature's first version anchored to something that already exists (no new
event-detection logic) instead of speculatively wiring hooks into every mutation path.

## Auth: per-workspace API keys replace the shared secret

`deps.require_api_key` currently checks `X-API-Key` against one `Settings.events_api_key`.
It's replaced by `deps.require_workspace_api_key`, which:

1. Reads `X-API-Key`, rejects (401) if absent.
2. Hashes it (sha256) and looks up `ApiKey` by `key_hash` — indexed equality, not a scan.
3. Rejects (401) if not found or `revoked_at is not None`.
4. Updates `last_used_at`, returns the key's `workspace_id`.

`api/events.py`'s `_default_workspace_id` helper is deleted; `POST /v1/events` takes
`workspace_id: uuid.UUID = Depends(require_workspace_api_key)` instead of a hardcoded
lookup. No backwards-compat shim for the old shared secret — this is pre-launch, there are
no real external API consumers yet, and the codebase's own conventions (see CLAUDE.md /
build rules) are to change code directly rather than carry compatibility branches.

`flowsage-backend create-api-key <workspace-slug> <key-name>` is a new CLI command (same
shape as `create-user`) so `docker compose exec backend flowsage-backend create-api-key
fs-default "seed key"` can bootstrap one for local/demo use and the e2e suite.

## Slack/Jira: per-workspace config replaces global env vars

Three existing call sites read `request.app.state.settings.slack_webhook_url` /
`jira_*` today: `api/events.py` (node export), `api/exports.py` (friction-issue export),
`api/alerts.py` (`POST /alerts/digest/run`) — plus `worker.py`'s `run_digest_job`. All four
switch to a new `flowsage_backend.integrations_store` module (mirrors `settings_store.py`):

```python
async def get_slack_integration(session, workspace_id) -> SlackIntegration | None
async def get_jira_integration(session, workspace_id) -> JiraIntegration | None
```

`post_slack_message`/`create_jira_issue` themselves are unchanged (they already take the
webhook URL / credentials as plain arguments) — only the caller side changes, from
`settings.slack_webhook_url` to `(await get_slack_integration(session, workspace_id)) is
not None and integration.webhook_url`. `SlackNotConfiguredError`/`JiraNotConfiguredError`
still fire the same way when no row exists, so existing 400-response behavior at those three
endpoints is unchanged from the caller's perspective — only *where* the config comes from
moves.

`config.py`'s `slack_webhook_url`/`jira_*`/`events_api_key` fields are deleted entirely (no
dead fields left around "just in case").

## `run_digest_job` becomes per-workspace

Today it looks up the single `fs-default` workspace and returns early if it's missing. It
becomes: `SELECT id FROM workspaces WHERE NOT archived`, loop over each, and run the exact
same body (calibration settings due-check → build report → auto-retrain → Slack via that
workspace's `SlackIntegration`) per workspace, independently — one workspace's Slack failure
(`SlackNotConfiguredError`, already swallowed) doesn't stop the others from processing. Also
delivers to that workspace's enabled `Webhook` rows when `has_alerts(report)` is true (see
delivery mechanics below). Digest cadence (`digest_last_sent_at` due-check) is unchanged,
just evaluated per-workspace-row instead of once globally.

## Webhook delivery mechanics

`integrations/webhooks.py` (new, same shape as `integrations/slack.py`):

```python
async def deliver_webhook(url, secret, event_type, payload: dict, transport=None) -> tuple[int | None, bool]
```

POSTs `{"event": event_type, "data": payload}` as JSON, with header
`X-FlowSage-Signature: sha256=<hex hmac of the raw body using secret>` (same verify-on-receipt
pattern as Stripe/GitHub webhooks — a recognizable, well-understood scheme rather than a
bespoke one). Returns `(status_code, success)` where `success = status_code is not None and
200 <= status_code < 300`; never raises — the caller (`run_digest_job`, and the `POST
/settings/integrations/webhooks/{id}/test` endpoint) always writes a `WebhookDelivery` row
regardless of outcome, then moves on. No retry queue in v1 — the delivery log itself *is* the
retry-visibility mechanism (a user sees "3 failed deliveries" and can hit the existing
retry-free "Test" button, or fix the URL); adding automatic retries/backoff is real added
complexity (a retry-count column, backoff scheduling, idempotency-on-redelivery) that YAGNI
says to skip until a pilot customer actually asks for it.

## API surface (`/settings/integrations`, `Depends(get_current_membership)`)

```
GET    /settings/integrations/slack              -> {connected: bool, webhook_url_preview: str | None}
PUT    /settings/integrations/slack               body {webhook_url}                  -> same shape
DELETE /settings/integrations/slack                                                    -> 204

GET    /settings/integrations/jira               -> {connected: bool, base_url, email, project_key}  (no api_token echoed back)
PUT    /settings/integrations/jira                body {base_url, email, api_token, project_key} -> same shape
DELETE /settings/integrations/jira                                                     -> 204

GET    /settings/integrations/api-keys           -> [{id, name, key_prefix, created_at, last_used_at, revoked}]
POST   /settings/integrations/api-keys            body {name}  -> {id, name, key (raw, once), key_prefix, created_at}
DELETE /settings/integrations/api-keys/{id}                                            -> 204 (sets revoked_at)

GET    /settings/integrations/webhooks           -> [{id, url, event_types, enabled, created_at}]
POST   /settings/integrations/webhooks            body {url, event_types}  -> {..., secret (raw, once)}
PATCH  /settings/integrations/webhooks/{id}       body {url?, event_types?, enabled?} -> full row (no secret)
DELETE /settings/integrations/webhooks/{id}                                            -> 204
GET    /settings/integrations/webhooks/{id}/deliveries -> [{id, event_type, status_code, success, created_at}] (newest first, capped at 50)
POST   /settings/integrations/webhooks/{id}/test                                       -> {status_code, success} (also logs a WebhookDelivery with event_type="test")
```

Role gate: mutating endpoints (`PUT`/`POST`/`DELETE`/`PATCH`) require `Role.ADMIN`
(`require_role(Role.ADMIN)`, same dependency Task 4's member-management endpoints already
use) — integrations are workspace-wide security-relevant config, not something a Viewer or
Researcher should be able to change. `GET`s only require membership (any role can see what's
connected).

## Frontend

`routes/settings/IntegrationsSettingsPage.tsx` (new), reachable via a new "Integrations" nav
entry under Settings (`Sidebar.tsx`) and route (`App.tsx`), same structure as
`GeneralSettingsPage`/`TeamSettingsPage`. Three sections on one page:

- **Marketplace cards** (Slack, Jira): a card each, showing connected/not-connected state.
  Not-connected → "Connect" opens an inline form (webhook URL for Slack; base URL/email/
  token/project key for Jira). Connected → shows a masked preview + "Disconnect" (with
  confirm, same pattern as `GeneralSettingsPage`'s archive-workspace confirm).
- **API Keys**: table (name, prefix, created, last used, revoke button) + "Create key" →
  modal showing the raw key exactly once with a copy button and an explicit "you won't see
  this again" warning, matching how GitHub/Stripe present new tokens.
- **Webhooks**: table (URL, event types, enabled toggle, created, delete) + "Add webhook"
  form (URL + event-type checkboxes, v1 has just the one type) → on create, same one-time
  secret-reveal modal as API keys. Each row expands (or links) to a delivery-log drawer:
  timestamp, event type, status code, success/fail badge. A "Send test" button per row hits
  the `/test` endpoint and the drawer refetches to show the new delivery.

`lib/types.ts` / `lib/api.ts` get the corresponding types and client functions, following the
existing `Workspace`/`Member` pattern exactly (same request helper, same error handling).

## Testing

- Backend: key issuance returns the raw key once and a correct prefix/hash; auth via a valid
  key resolves the right workspace; revoked/unknown keys reject with 401; Slack/Jira
  connect/disconnect + the three existing export endpoints reading per-workspace config
  instead of global settings (existing tests for those three endpoints get updated fixtures,
  not rewritten expectations); webhook CRUD; HMAC signature is verifiable by recomputing it
  over the exact captured body; delivery log records both success and failure (via
  `httpx.MockTransport`, same idiom `integrations/slack.py`'s tests already use); the digest
  job delivers to two different workspaces' two different Slack URLs independently, and
  `WebhookDelivery` rows never leak across workspaces (extends
  `test_workspace_isolation.py`'s pattern). CLI test for `create-api-key`.
- Frontend: Vitest for `IntegrationsSettingsPage` (connect/disconnect Slack, create/revoke a
  key, add a webhook and see it in the table) matching `TeamSettingsPage.test.tsx`'s shape.
- e2e (Playwright): connect Slack via the UI, create an API key and use it to `curl
  POST /v1/events` in the test setup (or assert the key row appears — whichever the existing
  e2e conventions favor once written), add a webhook pointed at a local test HTTP server the
  spec spins up, trigger `/alerts/digest/run`, and assert a delivery row appears.

## Out of scope (explicitly deferred)

- Webhook retry/backoff — logged as a delivery failure, not auto-retried (see above).
- Additional marketplace providers beyond Slack/Jira (e.g. Zapier) — the full-project plan
  mentions "marketplace cards" generically but only Slack/Jira integrations exist in the
  codebase today; adding placeholder cards for integrations that don't exist yet is scope
  creep with no working feature behind it.
- Per-workspace digest *cadence* beyond what `CalibrationSettings.digest_frequency` already
  provides — only the iteration-over-workspaces part of `run_digest_job` was actually
  deferred to this chunk; the cadence mechanism itself already works per-workspace (Phase 2
  chunk 4).
- Rate limiting and the audit-log/Security-Logs view — those are the *next* Phase 3 chunk
  (roadmap item 3, "SOC2-track"), not this one.
